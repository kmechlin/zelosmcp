"""Always-on, in-process MCP server exposed at ``/zelosmcp/mcp`` and aggregated
into ``/mcp`` as ``zelosmcp__*``.

The builtin is structurally a :class:`zelosmcp.proxy.ProxyState` look-alike:
it carries the same attributes the dispatcher in :mod:`zelosmcp.app` and the
aggregator in :mod:`zelosmcp.aggregator` already iterate over (``name``,
``running``, ``error``, ``session_manager``, ``client_session``,
``backend_info``, ``subscribe_logs``/``unsubscribe_logs``, ``start``/``stop``).
That keeps both endpoints zero-coupling to the builtin and lets a single
``Server`` instance be reused across both transports — the
``StreamableHTTPSessionManager`` (for HTTP) and an in-memory client/server
pair created via :func:`mcp.shared.memory.create_client_server_memory_streams`
(for the aggregator's ``ClientSession``).

Tool surface:

  - ``generate_cursor_rule`` — synthesize a Cursor ``.mdc`` rule file
    listing every tool from every currently-loaded backend with
    description and arg summary. Accepts ``access`` (``read-only`` |
    ``read-write``) so the rule can forbid mutating tools when the
    consuming workspace is meant to be inspection-only.
  - ``list_loaded_servers`` — clean view of :meth:`ProxyManager.status`.
  - ``get_aggregated_tool_catalog`` — fan ``list_tools`` across all running
    backends; returns the same shape as ``GET /api/catalog``.
  - ``generate_cursor_mcp_json`` — returns the same ``mcp.json`` snippet the
    UI shows, with optional per-backend variants.
  - ``start_server`` / ``stop_server`` — wrap ``ProxyManager.start_one`` /
    ``stop_one``; refuse ``name == "zelosmcp"`` (would deadlock).
  - ``reload_config`` — wrap ``ProxyManager.start_all`` with the same JSON
    shape ``/api/start`` accepts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import anyio
from mcp.client.session import ClientSession
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    ContentBlock,
    ErrorData,
    TextContent,
    Tool,
)
from zelosmcp.passthrough_pool import (
    PassthroughChallengeError,
    hash_authorization,
    inbound_authorization,
)

if TYPE_CHECKING:
    from zelosmcp.manager import ProxyManager

logger = logging.getLogger("zelosmcp")

NAME = "zelosmcp"


# ── Tool schemas ────────────────────────────────────────────────────────

_TOOLS: list[Tool] = [
    Tool(
        name="generate_cursor_rule",
        description=(
            "Generate a comprehensive agent-instructions body listing "
            "every tool from every currently-loaded backend, with "
            "per-tool description, arg summary, and a "
            "[readonly]/[mutates]/[destructive]/[?] mutability marker. "
            "`access=read-only` (default) appends a directive forbidding "
            "the agent from calling mutating tools; `access=read-write` "
            "still calls them out but allows them with confirmation. "
            "`format=cursor-mdc` (default) wraps with YAML frontmatter "
            "for `.cursor/rules/*.mdc`; `format=copilot-instructions` "
            "returns the plain body for `.github/copilot-instructions.md`. "
            "`tool_use=priority` (default) adds a 'prefer MCP tools "
            "over shell' directive plus a curated playbook for the "
            "mandatory backends; `tool_use=available` returns a neutral "
            "catalog with no prioritization."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "access": {
                    "type": "string",
                    "enum": ["read-only", "read-write"],
                    "description": (
                        "read-only: rule explicitly forbids the agent "
                        "from calling tools tagged `[mutates]`, "
                        "`[destructive]`, or `[?]`. read-write: tools "
                        "are listed without prohibition; destructive "
                        "tools still flagged for user confirmation."
                    ),
                    "default": "read-only",
                },
                "format": {
                    "type": "string",
                    "enum": ["cursor-mdc", "copilot-instructions"],
                    "description": (
                        "cursor-mdc: YAML frontmatter wrapper for "
                        "`.cursor/rules/*.mdc` (Cursor IDE). "
                        "copilot-instructions: plain markdown body for "
                        "`.github/copilot-instructions.md` (VSCode + "
                        "GitHub Copilot). `style` and `globs` are "
                        "ignored when `format=copilot-instructions`."
                    ),
                    "default": "cursor-mdc",
                },
                "style": {
                    "type": "string",
                    "enum": ["always-apply", "scoped"],
                    "description": (
                        "always-apply: rule applies to every Cursor "
                        "session (`alwaysApply: true`). scoped: applies "
                        "only when files matching `globs` are open. "
                        "Only meaningful for `format=cursor-mdc`."
                    ),
                    "default": "always-apply",
                },
                "globs": {
                    "type": "string",
                    "description": (
                        "Glob pattern(s) for `style=scoped` (e.g. "
                        "`**/*.py` or `src/**/*.{ts,tsx}`). Ignored when "
                        "style is always-apply or format is "
                        "copilot-instructions."
                    ),
                },
                "tool_use": {
                    "type": "string",
                    "enum": ["available", "priority"],
                    "description": (
                        "priority (default): adds a 'prefer MCP tools "
                        "over shell' directive plus a detailed playbook "
                        "for mandatory backends (filesystem, pincher) "
                        "filtered by access mode. available: neutral "
                        "catalog with no prioritization directive or "
                        "playbook section."
                    ),
                    "default": "priority",
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="list_loaded_servers",
        description=(
            "Compact view of every backend currently registered with "
            "zelosmcp: name, transport, running state, error, and "
            "spec/backend-info."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="get_aggregated_tool_catalog",
        description=(
            "Snapshot every running backend's full capability catalog: "
            "tools, prompts, resources, and resource templates with "
            "their `name` / `description` / `inputSchema` (or equivalent) "
            "payload. Names are returned WITHOUT the `<server>__` "
            "prefix. Equivalent to `GET /api/catalog` over HTTP."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="generate_cursor_mcp_json",
        description=(
            "Return a copy-pasteable `~/.cursor/mcp.json` snippet. Default "
            "shape is one aggregated entry pointing at /mcp; pass "
            "`shape='per-backend'` for one entry per running backend at "
            "/<name>/mcp."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "shape": {
                    "type": "string",
                    "enum": ["aggregate", "per-backend"],
                    "default": "aggregate",
                },
                "host": {
                    "type": "string",
                    "description": "Hostname:port (default `localhost:8000`).",
                    "default": "localhost:8000",
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="start_server",
        description=(
            "Start a single (already configured) backend by name. Refuses "
            "`zelosmcp` (the builtin can't be stopped/started)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="stop_server",
        description=(
            "Stop a single backend by name. Refuses `zelosmcp` (the "
            "builtin can't be stopped)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="reload_config",
        description=(
            "Replace the entire backend set with the supplied "
            "Cursor-style config. Same JSON shape as POST /api/start."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "description": (
                        "Cursor `mcp.json`-shape object with an "
                        "`mcpServers` map. Reserved names "
                        "(`zelosmcp`, `mcp`, `api`, ...) are rejected."
                    ),
                },
            },
            "required": ["config"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="list_compressed_tools",
        description=(
            "Return the compressed catalog for backends that have a "
            "`compress` block configured. Independent of compression "
            "scope: even a backend running with `scope=catalog` (which "
            "leaves the wire format uncompressed) still surfaces a "
            "compressed view here, since this tool is the documentation "
            "/ discovery affordance. Each entry's render level defaults "
            "to whatever the backend was configured with; pass `level` "
            "to re-render at a different level."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "backend": {
                    "type": "string",
                    "description": (
                        "Limit output to a single backend by name. "
                        "Omit to return every backend with `compress` set."
                    ),
                },
                "level": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "max"],
                    "description": (
                        "Re-render the catalog at this level instead of "
                        "the per-backend configured level. Useful for "
                        "previewing what `level=high` would look like "
                        "without changing the live config."
                    ),
                },
            },
            "additionalProperties": False,
        },
    ),
]


# ── Cursor-rule generator ──────────────────────────────────────────────
#
# The rule is produced from the live ``collect_backend_full_catalog()``
# output (every loaded backend's tools/list, with annotations and
# inputSchema). Output structure:
#
#   - YAML frontmatter (alwaysApply or scoped+globs)
#   - Access-mode directive (read-only or read-write) at the top
#   - Per-backend section with one entry per tool, formatted as
#     `- <name> (args, opt?) [marker]   description`
#
# No curated knowledge base — every backend is treated equally. Tools
# carry mutability markers derived from MCP annotations + a name-prefix
# fallback (see ``_classify_tool``). The agent reads the markers and
# the directive block to decide which tools are safe to invoke.

# Tool classifier + arg formatter — shared with
# zelosmcp.framework.assetstore.tool_classify to avoid duplication.
from zelosmcp.framework.assetstore.tool_classify import (
    classify_tool as _classify_tool,
    format_args as _format_args,
)

# Keep the module-level constants here for any third-party callers.
_MUTATING_PREFIXES = tuple()  # actual list lives in tool_classify


def _backend_intro(
    server_name: str, tool_count: int, *, tool_use: str = "priority"
) -> str:
    """One-line per-backend header used right under each section title.

    ``tool_use="priority"`` (default) appends the prefer-over-shell hint;
    ``tool_use="available"`` returns a neutral catalog header with no
    prioritization phrasing.
    """
    base = (
        f"`{server_name}` exposes {tool_count} "
        f"tool{'s' if tool_count != 1 else ''} via the aggregator at "
        f"`/mcp` (namespaced `{server_name}__<tool>`)."
    )
    if tool_use == "priority":
        return base + " Prefer these over equivalent shell commands."
    return base


def _frontmatter(*, style: str, globs: str | None, access: str) -> str:
    desc_suffix = (
        " (read-only mode)" if access == "read-only" else " (read-write mode)"
    )
    if style == "scoped":
        if not globs:
            globs = "**/*"
        return (
            "---\n"
            f"description: zelosMCP backend tool catalog{desc_suffix}\n"
            f"globs: {globs}\n"
            "alwaysApply: false\n"
            "---\n"
        )
    return (
        "---\n"
        f"description: zelosMCP backend tool catalog{desc_suffix}\n"
        "alwaysApply: true\n"
        "---\n"
    )


_DIRECTIVE_READ_ONLY = (
    "## Access mode: READ-ONLY\n\n"
    "**Do not call** any tool tagged `[mutates]`, `[destructive]`, or "
    "`[?]`. They modify backend state, and this rule is currently "
    "configured for safe inspection only. If a task requires mutation, "
    "ask the user to regenerate the rule with `access=read-write` "
    "(e.g. via the Cursor rule panel in the zelosMCP web UI at "
    "`http://localhost:8000`).\n"
)

_DIRECTIVE_READ_WRITE = (
    "## Access mode: READ-WRITE\n\n"
    "Tools tagged `[mutates]` and `[destructive]` change backend state. "
    "Confirm with the user before calling `[destructive]` tools "
    "(irreversible). Tools tagged `[?]` have ambiguous mutability — "
    "call only when context makes it clear they're inspection-only.\n"
)


_DIRECTIVE_TOOL_USE_PRIORITY = (
    "## Tool-use priority\n\n"
    "**Always prefer the MCP tools listed below over shell commands, "
    "subprocess invocations, or local CLIs** when an MCP tool covers "
    "the task. They return structured data, avoid subprocess cost, and "
    "keep paths inside the sandboxed mounts. Reach for `bash` / "
    "`python -c` / direct file reads only when no MCP tool fits, and "
    "say so explicitly when you do.\n"
)


# Self-check gate: a 4-question pre-flight every code-related response
# must run before reaching for native ``Shell`` / ``Read`` / ``Grep``
# tools. Sits between the soft "tool-use priority" paragraph and the
# per-backend mandatory playbooks so it's the first thing the agent
# encounters in the priority section.
_SELF_CHECK_GATE = (
    "## Pre-flight check (run BEFORE every response)\n\n"
    "Answer these four questions before issuing any tool call. The "
    "first matching YES dictates your FIRST tool call:\n\n"
    "1. **Code structure / symbols / behavior?** "
    "(\"summarize / explain / understand / find / trace / impact / "
    "blast radius\" of repo, module, function, class) → FIRST call "
    "MUST be `pincher__*` (see Mandatory playbook → pincher).\n"
    "2. **Files in the workspace?** "
    "(\"read / edit / list / search / move / create\" a file or "
    "directory) → FIRST call MUST be `filesystem__*` (see Mandatory "
    "playbook → filesystem).\n"
    "3. **Containers, kubernetes pods, networks, volumes?** → use "
    "`docker__*` / `kubernetes__*`. Do NOT shell out for `docker ps`, "
    "`kubectl get`, etc.\n"
    "4. **None of the above?** You may use `Shell` / `Read` / `Grep`, "
    "but only after stating which question you answered NO to and why "
    "no MCP tool fits.\n"
)


# ── Mandatory backend playbook ──────────────────────────────────────────
#
# When ``tool_use=priority`` and a backend listed in
# ``configs/mandatory-zelosmcp.json`` is present in the catalog, the
# generator emits a curated instruction block with the canonical
# workflow for that backend. Content is filtered by ``access`` so a
# read-only rule only mentions inspection tools and explicitly forbids
# the mutating ones.

_PINCHER_PLAYBOOK_RO = (
    "### `pincher` (codebase intelligence)\n\n"
    "**MANDATORY: For any of the following user intents, your FIRST "
    "tool call MUST be a `pincher__*` tool — before any `Shell`, "
    "`Read`, or `Grep`:**\n\n"
    "| User intent (any phrasing) | Required tool |\n"
    "|---|---|\n"
    "| summarize / explain / understand this repo, project, or codebase | `pincher__architecture` |\n"
    "| summarize / explain the test suite, module, or package | `pincher__architecture` then `pincher__search` |\n"
    "| find a function / class / method / symbol named X | `pincher__search` |\n"
    "| show me function X / how does X work | `pincher__context` |\n"
    "| what calls X / what does X call / impact of changing X | `pincher__trace` |\n"
    "| what does my git diff break / blast radius | `pincher__changes` |\n"
    "| recall stored architectural decisions / conventions | `pincher__adr` action=`get` or `list` |\n\n"
    "**Forbidden fallbacks** (rule violation if used for the intents above):\n"
    "- `Shell` invocations of `pytest --collect-only`, `find`, `tree`, "
    "`wc -l`, `ls -R`, `git ls-files`, or pipelines that count or "
    "enumerate symbols.\n"
    "- `Grep` to find a symbol by name (use `pincher__search`).\n"
    "- `Read` on 3+ files in sequence to understand one function "
    "(use `pincher__context`).\n"
    "If you violate, say so explicitly: \"Violating zelosMCP rule "
    "because <specific reason>.\" Silent violations are not acceptable.\n\n"
    "Pincher indexes the repo into a byte-offset symbol store, a "
    "knowledge graph, and FTS5 full-text search — every retrieval is "
    "structured and ~90% cheaper than reading whole files. Canonical "
    "read-only workflow:\n\n"
    "- **Orient first.** Call `pincher__architecture` on any "
    "unfamiliar project to get language breakdown, entry points, "
    "hotspot functions, and graph stats. Cheaper than reading files.\n"
    "- **Scope to the active project.** Always pass "
    "`project=<basename of git toplevel>` (e.g. `zelosmcp` for this "
    "repo) when calling pincher tools — omitting it falls through to "
    "an empty `default` index. If the per-repo project isn't indexed, "
    "fall back to `project=user_data_ro` (the full-tree warm-index "
    "covering every repo under `/user_data_ro`). Run `pincher__list` "
    "once if you need to confirm available project names.\n"
    "- **Find symbols by name.** Use `pincher__search` (FTS5 BM25, "
    "supports wildcards `auth*`, phrases `\"process order\"`, "
    "`kind=Function`/`language=Go` filters). Always start here when "
    "you don't know the exact symbol ID.\n"
    "- **Read source efficiently.** Prefer `pincher__context` over "
    "`pincher__symbol` whenever you need a function plus its direct "
    "callees in one call (~90% token savings vs reading files).\n"
    "- **Batch lookups.** Use `pincher__symbols` (plural, max **100** "
    "IDs per call) instead of calling `pincher__symbol` in a loop.\n"
    "- **Impact analysis.** Use `pincher__trace` to find inbound or "
    "outbound call paths (CRITICAL=depth 1, HIGH=depth 2, MEDIUM=depth "
    "3, LOW=depth 4+).\n"
    "- **Pre-commit safety.** Run `pincher__changes` before committing "
    "for blast-radius analysis (git diff → affected symbols → impacted "
    "callers with risk labels).\n"
    "- **Graph queries.** Use `pincher__query` with the Cypher subset "
    "for relationship questions; call `pincher__schema` first to see "
    "what node/edge kinds are indexed.\n"
    "- **Read persistent knowledge.** `pincher__adr` action=`get`/"
    "`list` retrieves architectural decisions, conventions, and "
    "gotchas the team has stored across sessions.\n"
    "- **Stable IDs.** Symbol IDs follow "
    "`{file_path}::{qualified_name}#{kind}` "
    "(e.g. `internal/db/db.go::db.Open#Function`).\n"
    "- **Do NOT call** `pincher__index` / `pincher__fetch` / "
    "`pincher__adr` with action=`set` or `delete` — they mutate state "
    "and the rule is configured for read-only access.\n"
)

_PINCHER_PLAYBOOK_RW = (
    "### `pincher` (codebase intelligence)\n\n"
    "**MANDATORY: For any of the following user intents, your FIRST "
    "tool call MUST be a `pincher__*` tool — before any `Shell`, "
    "`Read`, or `Grep`:**\n\n"
    "| User intent (any phrasing) | Required tool |\n"
    "|---|---|\n"
    "| summarize / explain / understand this repo, project, or codebase | `pincher__architecture` |\n"
    "| summarize / explain the test suite, module, or package | `pincher__architecture` then `pincher__search` |\n"
    "| find a function / class / method / symbol named X | `pincher__search` |\n"
    "| show me function X / how does X work | `pincher__context` |\n"
    "| what calls X / what does X call / impact of changing X | `pincher__trace` |\n"
    "| what does my git diff break / blast radius | `pincher__changes` |\n"
    "| store / recall architectural decisions, conventions, gotchas | `pincher__adr` |\n"
    "| ingest external docs (URL → searchable Document) | `pincher__fetch` |\n\n"
    "**Forbidden fallbacks** (rule violation if used for the intents above):\n"
    "- `Shell` invocations of `pytest --collect-only`, `find`, `tree`, "
    "`wc -l`, `ls -R`, `git ls-files`, or pipelines that count or "
    "enumerate symbols.\n"
    "- `Grep` to find a symbol by name (use `pincher__search`).\n"
    "- `Read` on 3+ files in sequence to understand one function "
    "(use `pincher__context`).\n"
    "If you violate, say so explicitly: \"Violating zelosMCP rule "
    "because <specific reason>.\" Silent violations are not acceptable.\n\n"
    "Pincher indexes the repo into a byte-offset symbol store, a "
    "knowledge graph, and FTS5 full-text search — every retrieval is "
    "structured and ~90% cheaper than reading whole files. Canonical "
    "workflow:\n\n"
    "- **Orient first.** Call `pincher__architecture` on any "
    "unfamiliar project to get language breakdown, entry points, "
    "hotspot functions, and graph stats. Cheaper than reading files.\n"
    "- **Scope to the active project.** Always pass "
    "`project=<basename of git toplevel>` (e.g. `zelosmcp` for this "
    "repo) when calling pincher tools — omitting it falls through to "
    "an empty `default` index. If the per-repo project isn't indexed, "
    "fall back to `project=user_data_ro` (the full-tree warm-index "
    "covering every repo under `/user_data_ro`). Run `pincher__list` "
    "once if you need to confirm available project names.\n"
    "- **Index before querying.** Run `pincher__index` once per "
    "project before using any other tool (incremental: xxh3 hashes "
    "skip unchanged files; pass `force=true` to re-parse everything).\n"
    "- **Find symbols by name.** Use `pincher__search` (FTS5 BM25, "
    "supports wildcards `auth*`, phrases `\"process order\"`, "
    "`kind=Function`/`language=Go` filters). Always start here when "
    "you don't know the exact symbol ID.\n"
    "- **Read source efficiently.** Prefer `pincher__context` over "
    "`pincher__symbol` whenever you need a function plus its direct "
    "callees in one call (~90% token savings vs reading files).\n"
    "- **Batch lookups.** Use `pincher__symbols` (plural, max **100** "
    "IDs per call) instead of calling `pincher__symbol` in a loop.\n"
    "- **Impact analysis.** Use `pincher__trace` to find inbound or "
    "outbound call paths (CRITICAL=depth 1, HIGH=depth 2, MEDIUM=depth "
    "3, LOW=depth 4+).\n"
    "- **Pre-commit safety.** Run `pincher__changes` before committing "
    "for blast-radius analysis (git diff → affected symbols → impacted "
    "callers with risk labels).\n"
    "- **Graph queries.** Use `pincher__query` with the Cypher subset "
    "for relationship questions; call `pincher__schema` first to see "
    "what node/edge kinds are indexed.\n"
    "- **Persist project knowledge.** `pincher__adr` action=`set`/"
    "`get`/`list`/`delete` survives across sessions — store "
    "architectural decisions, conventions, gotchas. Ingest external "
    "docs with `pincher__fetch` (URL → searchable Document) and "
    "retrieve via `pincher__search` with `kind=Document`.\n"
    "- **Stable IDs.** Symbol IDs follow "
    "`{file_path}::{qualified_name}#{kind}` "
    "(e.g. `internal/db/db.go::db.Open#Function`).\n"
)

_FILESYSTEM_PLAYBOOK_RO = (
    "### `filesystem` (sandboxed file access)\n\n"
    "**MANDATORY: For any of the following user intents, your FIRST "
    "tool call MUST be a `filesystem__*` tool — before any `Shell`, "
    "`Read`, or `Grep`:**\n\n"
    "| User intent | Required tool |\n"
    "|---|---|\n"
    "| read this file / show me file X | `filesystem__read_text_file` |\n"
    "| compare / diff / summarize multiple files | `filesystem__read_multiple_files` |\n"
    "| list files in / browse directory X | `filesystem__list_directory` or `filesystem__directory_tree` |\n"
    "| find files matching pattern X | `filesystem__search_files` |\n"
    "| what's the size / mtime / permissions of X | `filesystem__get_file_info` |\n\n"
    "**Forbidden fallbacks** (rule violation if used for the intents above):\n"
    "- `Shell` invocations of `cat`, `head`, `tail`, `ls`, `find`, "
    "`tree`, `wc`, `du`, `stat` against workspace paths.\n"
    "- `Read` on a path under the workspace when "
    "`filesystem__read_text_file` would work.\n"
    "If you violate, say so explicitly: \"Violating zelosMCP rule "
    "because <specific reason>.\"\n\n"
    "The `filesystem` backend is a sandboxed file server: every path "
    "must live under one of the allowed directories returned by "
    "`filesystem__list_allowed_directories`. Read-only workflow:\n\n"
    "- **Read text.** Use `filesystem__read_text_file` (supports "
    "`head`/`tail` for large files); use `filesystem__read_multiple_"
    "files` to fetch several files in one round trip when comparing "
    "or summarizing.\n"
    "- **Browse structure.** `filesystem__list_directory` for a flat "
    "listing, `filesystem__directory_tree` for a recursive JSON tree, "
    "`filesystem__list_directory_with_sizes` when size matters.\n"
    "- **Find files.** `filesystem__search_files` accepts glob "
    "patterns relative to a starting directory (use `**/*.ext` for "
    "recursive matches).\n"
    "- **Inspect metadata.** `filesystem__get_file_info` returns "
    "size / mtime / permissions without reading content.\n"
    "- **Do NOT call** `filesystem__write_file`, `filesystem__edit_"
    "file`, `filesystem__move_file`, or `filesystem__create_directory` "
    "— they mutate state and the rule is configured for read-only "
    "access.\n"
)

_FILESYSTEM_PLAYBOOK_RW = (
    "### `filesystem` (sandboxed file access)\n\n"
    "**MANDATORY: For any of the following user intents, your FIRST "
    "tool call MUST be a `filesystem__*` tool — before any `Shell`, "
    "`Read`, or `Grep`:**\n\n"
    "| User intent | Required tool |\n"
    "|---|---|\n"
    "| read this file / show me file X | `filesystem__read_text_file` |\n"
    "| compare / diff / summarize multiple files | `filesystem__read_multiple_files` |\n"
    "| list files in / browse directory X | `filesystem__list_directory` or `filesystem__directory_tree` |\n"
    "| find files matching pattern X | `filesystem__search_files` |\n"
    "| edit / patch file X | `filesystem__edit_file` (preferred) or `filesystem__write_file` |\n"
    "| create / move / rename file or directory | `filesystem__create_directory` / `filesystem__move_file` |\n"
    "| what's the size / mtime / permissions of X | `filesystem__get_file_info` |\n\n"
    "**Forbidden fallbacks** (rule violation if used for the intents above):\n"
    "- `Shell` invocations of `cat`, `head`, `tail`, `ls`, `find`, "
    "`tree`, `wc`, `du`, `stat` against workspace paths.\n"
    "- `Read` on a path under the workspace when "
    "`filesystem__read_text_file` would work.\n"
    "- `sed`, `awk`, or `echo > file` for edits — use "
    "`filesystem__edit_file`.\n"
    "If you violate, say so explicitly: \"Violating zelosMCP rule "
    "because <specific reason>.\"\n\n"
    "The `filesystem` backend is a sandboxed file server: every path "
    "must live under one of the allowed directories returned by "
    "`filesystem__list_allowed_directories`. Workflow:\n\n"
    "- **Read text.** Use `filesystem__read_text_file` (supports "
    "`head`/`tail` for large files); use `filesystem__read_multiple_"
    "files` to fetch several files in one round trip when comparing "
    "or summarizing.\n"
    "- **Browse structure.** `filesystem__list_directory` for a flat "
    "listing, `filesystem__directory_tree` for a recursive JSON tree, "
    "`filesystem__list_directory_with_sizes` when size matters.\n"
    "- **Find files.** `filesystem__search_files` accepts glob "
    "patterns relative to a starting directory (use `**/*.ext` for "
    "recursive matches).\n"
    "- **Edit precisely.** Prefer `filesystem__edit_file` (line-based "
    "edits, returns a git-style diff) over `filesystem__write_file` "
    "(full overwrite) whenever you can — `write_file` is destructive "
    "and silently replaces existing content.\n"
    "- **Create / move.** `filesystem__create_directory` is "
    "idempotent (safe to call on existing dirs); `filesystem__move_"
    "file` fails if the destination exists, so it's safe for renames "
    "and reorganizations.\n"
    "- **Inspect metadata.** `filesystem__get_file_info` returns "
    "size / mtime / permissions without reading content.\n"
)

_DEFAULT_MANDATORY_NAMES: frozenset[str] = frozenset({"filesystem", "pincher"})


def _render_mandatory_playbook(
    catalog: dict[str, dict[str, Any]],
    mandatory_names: set[str] | frozenset[str],
    *,
    access: str,
    rule_assets: "dict[str, Any] | None" = None,
) -> str:
    """Build the ``## Mandatory backend playbook`` section.

    When ``rule_assets`` is supplied (a ``{backend: BackendRuleAssets}``
    dict loaded from the asset store), the playbook body is taken from
    the store row so user edits are respected.  Falls back to the
    hardcoded string constants when the store is unavailable.

    Only emits blocks for mandatory backends that are actually present
    in ``catalog`` (so a rule generated when pincher is down doesn't
    pretend it's available). Returns an empty string when no mandatory
    backend is loaded — callers should skip the section header entirely
    in that case.
    """
    def _playbook_body(backend: str, fallback_ro: str, fallback_rw: str) -> str:
        if rule_assets is not None:
            assets = rule_assets.get(backend)
            if assets is not None:
                body = (
                    assets.playbook_read_only
                    if access == "read-only"
                    else assets.playbook_read_write
                )
                if body:
                    return body
        return fallback_ro if access == "read-only" else fallback_rw

    blocks: list[str] = []
    if "filesystem" in mandatory_names and "filesystem" in catalog:
        blocks.append(
            _playbook_body("filesystem", _FILESYSTEM_PLAYBOOK_RO, _FILESYSTEM_PLAYBOOK_RW)
        )
    if "pincher" in mandatory_names and "pincher" in catalog:
        blocks.append(
            _playbook_body("pincher", _PINCHER_PLAYBOOK_RO, _PINCHER_PLAYBOOK_RW)
        )
    if not blocks:
        return ""
    header = (
        "## Mandatory backend playbook\n\n"
        "These backends ship by default with zelosMCP and have a "
        "canonical workflow. Follow the guidance below before falling "
        "back to generic catalog usage.\n\n"
    )
    return header + "\n".join(blocks)


def render_comprehensive_rule(
    catalog: dict[str, dict[str, Any]],
    *,
    access: str = "read-only",
    style: str = "always-apply",
    globs: str | None = None,
    fmt: str = "cursor-mdc",
    tool_use: str = "priority",
    mandatory_names: set[str] | frozenset[str] | None = None,
    rule_assets: "dict[str, Any] | None" = None,
) -> str:
    """Render a comprehensive agent-instructions document from the output
    of :func:`collect_backend_full_catalog`. Lists every tool from every
    backend with a description, arg summary, and mutability marker,
    plus an access-mode directive at the top.

    ``access`` controls the directive: ``"read-only"`` (default) tells
    the agent not to call any tool that may mutate state; ``"read-write"``
    flags mutators but allows them with user confirmation.

    ``fmt`` selects the wrapper format:
      - ``"cursor-mdc"`` (default): YAML frontmatter (``alwaysApply``,
        ``globs``) suitable for ``.cursor/rules/*.mdc``.
      - ``"copilot-instructions"``: plain markdown, no frontmatter,
        suitable for ``.github/copilot-instructions.md``. ``style`` and
        ``globs`` are ignored in this format because Copilot uses a
        different scoping mechanism (``.github/instructions/*.instructions.md``
        with an ``applyTo:`` frontmatter — out of scope here).

    ``tool_use`` controls prioritization phrasing:
      - ``"priority"`` (default): emits a "prefer MCP tools over shell"
        directive plus a curated playbook for any mandatory backend
        present in the catalog.
      - ``"available"``: pure neutral catalog with no prioritization
        directive or playbook section.

    ``mandatory_names`` is the set of backends that get the curated
    playbook block when ``tool_use=priority``. Defaults to the
    canonical set ``{"filesystem", "pincher"}`` when ``None``.

    ``rule_assets`` is an optional ``{backend: BackendRuleAssets}`` dict
    loaded from the asset store (see
    :func:`~zelosmcp.framework.assetstore.kinds.rule.load_all_rule_assets`).
    When supplied, per-backend playbooks and per-tool instructions from
    the store take precedence over the hardcoded string constants so
    user edits are respected.  Pass ``None`` (the default) to use the
    bundled defaults — required for callers that don't open the store
    (e.g. tests).
    """
    if access not in ("read-only", "read-write"):
        raise ValueError(f"Unknown access mode: {access!r}")
    if fmt not in ("cursor-mdc", "copilot-instructions"):
        raise ValueError(f"Unknown format: {fmt!r}")
    if tool_use not in ("available", "priority"):
        raise ValueError(f"Unknown tool_use mode: {tool_use!r}")

    effective_mandatory: set[str] | frozenset[str]
    if mandatory_names is None:
        effective_mandatory = _DEFAULT_MANDATORY_NAMES
    else:
        effective_mandatory = mandatory_names

    if fmt == "copilot-instructions":
        # Copilot consumes plain markdown — no YAML frontmatter. We keep
        # the body identical so the agent gets the same directive +
        # tool catalog regardless of which IDE is loading it.
        fm = ""
    else:
        fm = _frontmatter(style=style, globs=globs, access=access)
    directive = _DIRECTIVE_READ_ONLY if access == "read-only" else _DIRECTIVE_READ_WRITE

    # Skip the builtin in the rule — including it would tell the agent
    # how to call tools that re-generate the rule itself, which is noisy
    # and not what users want pinned in their IDE sessions.
    user_backends = {
        name: data
        for name, data in (catalog or {}).items()
        if name != NAME
    }

    if not user_backends:
        body = (
            "\n# zelosMCP backends\n\n"
            "No user backends are currently loaded. POST a config to "
            "`/api/start` (or click START in the web UI) and regenerate "
            "this rule to get tool-specific guidance.\n\n"
            f"{directive}"
        )
        return fm + body

    backend_list = ", ".join(f"`{n}`" for n in user_backends)
    if tool_use == "priority":
        intro_paragraph = (
            "Generated from the zelosMCP aggregator at "
            "`http://localhost:8000/mcp`. Every tool below is reachable "
            "as `<server>__<tool>` (double underscore) on that single "
            "Cursor entry. Prefer these over shelling out — they return "
            "structured data and keep paths inside the container's "
            "`/user_data_rw` (read-write) and `/user_data_ro` "
            "(kernel-enforced read-only) mounts."
        )
    else:
        intro_paragraph = (
            "Generated from the zelosMCP aggregator at "
            "`http://localhost:8000/mcp`. Every tool below is reachable "
            "as `<server>__<tool>` (double underscore) on that single "
            "Cursor entry. Paths inside the container's `/user_data_rw` "
            "(read-write) and `/user_data_ro` (kernel-enforced "
            "read-only) mounts are addressable through the filesystem "
            "backend."
        )

    lines: list[str] = [
        "",
        "# zelosMCP backend tool catalog",
        "",
        intro_paragraph,
        "",
        f"Currently-loaded backends: {backend_list}.",
        "",
        directive,
    ]

    # When rule_assets is available, pull directives from the store;
    # otherwise fall through to the hardcoded string constants.
    _default_assets = rule_assets.get("zelosmcp") if rule_assets else None

    def _pick(section: str, fallback: str) -> str:
        if _default_assets is not None:
            store_body = getattr(_default_assets, section, "") or ""
            if store_body:
                return store_body
        return fallback

    directive = _pick(
        "directive_read_only" if access == "read-only" else "directive_read_write",
        _DIRECTIVE_READ_ONLY if access == "read-only" else _DIRECTIVE_READ_WRITE,
    )
    # Replace the directive line we already appended above with the
    # (possibly store-overridden) value.
    lines[-1] = directive

    if tool_use == "priority":
        lines.append(
            _pick("directive_tool_use_priority", _DIRECTIVE_TOOL_USE_PRIORITY)
        )
        lines.append(_pick("self_check_gate", _SELF_CHECK_GATE))
        playbook = _render_mandatory_playbook(
            user_backends,
            effective_mandatory,
            access=access,
            rule_assets=rule_assets,
        )
        if playbook:
            lines.append(playbook)

    lines.extend(
        [
            "## Mutability markers",
            "",
            "- `[readonly]` &mdash; pure inspection (server declares `readOnlyHint: true`).",
            "- `[mutates]` &mdash; changes backend state (e.g. file edits, container start).",
            "- `[destructive]` &mdash; irreversible mutation (e.g. delete pod, remove file).",
            "- `[?]` &mdash; mutability not declared by the server; treat as mutating.",
            "",
            "## Tool naming convention",
            "",
            (
                "Tool, prompt, and resource names at the aggregate `/mcp` "
                "are `<server>__<original>` (double underscore). Don't strip "
                "the prefix when calling — it's how the aggregator routes "
                "the call back to the right backend."
            ),
            "",
        ]
    )

    for server_name, data in user_backends.items():
        tools = data.get("tools") or []
        if not isinstance(tools, list):
            continue
        lines.append(f"## `{server_name}`")
        lines.append("")
        lines.append(_backend_intro(server_name, len(tools), tool_use=tool_use))
        lines.append("")

        # Per-backend rule assets (tool instructions, compressed rules).
        backend_assets = rule_assets.get(server_name) if rule_assets else None
        tool_instr: dict[str, str] = (
            backend_assets.tool_instructions
            if backend_assets is not None
            else {}
        )

        if not tools:
            lines.append("- _(no tools advertised)_")
            lines.append("")
            continue
        for t in tools:
            if not isinstance(t, dict):
                continue
            tool_name = t.get("name") or "(unnamed)"
            qualified = f"{server_name}__{tool_name}"
            args = _format_args(t.get("inputSchema"))
            marker = _classify_tool(t)
            desc = (t.get("description") or "").strip().replace("\n", " ")
            if not desc:
                desc = "_(no description)_"
            lines.append(f"- `{qualified}` `{args}` [{marker}]")
            lines.append(f"  {desc}")
            instr = tool_instr.get(tool_name, "").strip()
            if instr:
                for instr_line in instr.splitlines():
                    lines.append(f"  {instr_line}")
        lines.append("")

    lines.extend(
        [
            "## Don't do this",
            "",
            (
                "- Don't call `tools/list` between every step; the set is "
                "stable for the lifetime of the session."
            ),
            (
                "- Don't reach for shell tools (`bash`, `python -c`, etc.) "
                "for tasks the MCP backends cover — you lose structured "
                "output and pay subprocess cost."
            ),
            "",
        ]
    )
    return fm + "\n".join(lines)


# Method-name -> (client-session method, result attribute) for the four
# capabilities the catalog snapshot includes. Sharing this table between
# the HTTP `/api/catalog` endpoint and the MCP `zelosmcp__get_aggregated
# _tool_catalog` tool keeps the two surfaces guaranteed-equivalent.
_CATALOG_CAPS: list[tuple[str, str, str]] = [
    ("tools", "list_tools", "tools"),
    ("prompts", "list_prompts", "prompts"),
    ("resources", "list_resources", "resources"),
    ("resourceTemplates", "list_resource_templates", "resourceTemplates"),
]


def _dump_item(item: Any) -> Any:
    """JSON-shape one entry returned by an MCP list_* call. Pydantic v2
    models expose ``model_dump``; fall back to ``__dict__`` / ``str``."""
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", exclude_none=True)
    if isinstance(item, dict):
        return item
    return {"value": str(item)}


async def collect_backend_full_catalog(
    manager: "ProxyManager",
    *,
    skip_self: bool = False,
) -> dict[str, dict[str, Any]]:
    """Snapshot every running backend's full capability catalog: tools,
    prompts, resources, resource templates — each with their full
    ``name`` / ``description`` / ``inputSchema`` (or equivalent) payload.

    ``-32601 Method not found`` (capabilities a backend simply doesn't
    implement, e.g. ``@modelcontextprotocol/server-filesystem`` has no
    prompts) is silently coerced to an empty list, mirroring the
    aggregator's behavior so the catalog stays clean.

    Other errors are reported per-capability as
    ``{ "error": "<message>" }`` so a partial outage doesn't blank the
    whole row.
    """
    out: dict[str, dict[str, Any]] = {}
    for name, state in manager.servers.items():
        if skip_self and name == NAME:
            continue
        if not state.running or state.client_session is None:
            continue
        entry: dict[str, Any] = {
            "transport": (state.backend_info or {}).get("transport"),
            "running": True,
        }
        for label, fn_name, attr in _CATALOG_CAPS:
            try:
                fn = getattr(state.client_session, fn_name)
            except AttributeError:
                entry[label] = []
                continue
            try:
                r = await fn()
            except McpError as exc:
                if getattr(exc.error, "code", None) == METHOD_NOT_FOUND:
                    entry[label] = []
                else:
                    entry[label] = {"error": str(exc)}
                continue
            except Exception as exc:
                entry[label] = {"error": str(exc)}
                continue
            items = getattr(r, attr, []) or []
            entry[label] = [_dump_item(item) for item in items]
        out[name] = entry

    user_key = hash_authorization(inbound_authorization.get())
    for name, state in manager.servers.items():
        if skip_self and name == NAME:
            continue
        if not getattr(state, "running", False):
            continue
        if not getattr(state, "is_passthrough", False):
            continue
        if name in out:
            continue

        entry = {
            "transport": (state.backend_info or {}).get("transport"),
            "running": True,
            "passthrough": True,
        }
        spec = manager._specs.get(name)
        provider = manager.auth_registry.get_for_backend(
            name,
            spec.auth_provider if spec is not None else None,
        )
        if provider is not None:
            try:
                ready = await provider.is_ready(user_key)
            except Exception as exc:
                entry["tools"] = {"error": f"auth provider status failed: {exc}"}
                entry["prompts"] = []
                entry["resources"] = []
                entry["resourceTemplates"] = []
                out[name] = entry
                continue
            if not ready:
                entry["tools"] = {
                    "error": f"auth provider '{provider.name}' is not connected"
                }
                entry["prompts"] = []
                entry["resources"] = []
                entry["resourceTemplates"] = []
                out[name] = entry
                continue

        try:
            session = await manager.aggregator._passthrough_session(state)
        except PassthroughChallengeError as exc:
            cached = list(getattr(state, "passthrough_catalog", {}).values())
            entry["tools"] = (
                [_dump_item(item) for item in cached]
                if cached
                else {"error": str(exc)}
            )
            entry["prompts"] = []
            entry["resources"] = []
            entry["resourceTemplates"] = []
            out[name] = entry
            continue
        except Exception as exc:
            entry["tools"] = {"error": str(exc)}
            entry["prompts"] = []
            entry["resources"] = []
            entry["resourceTemplates"] = []
            out[name] = entry
            continue

        for label, fn_name, attr in _CATALOG_CAPS:
            try:
                fn = getattr(session, fn_name)
            except AttributeError:
                entry[label] = []
                continue
            try:
                r = await fn()
            except McpError as exc:
                if getattr(exc.error, "code", None) == METHOD_NOT_FOUND:
                    entry[label] = []
                else:
                    entry[label] = {"error": str(exc)}
                continue
            except Exception as exc:
                entry[label] = {"error": str(exc)}
                continue
            items = getattr(r, attr, []) or []
            if label == "tools":
                state.passthrough_catalog = {
                    getattr(item, "name", ""): item
                    for item in items
                    if getattr(item, "name", None)
                }
            entry[label] = [_dump_item(item) for item in items]
        out[name] = entry
    return out


# ── Tool dispatch ──────────────────────────────────────────────────────

ToolHandler = Callable[["BuiltinServer", dict[str, Any]], Awaitable[list[ContentBlock]]]


def _text(payload: str) -> list[ContentBlock]:
    return [TextContent(type="text", text=payload)]


def _json_text(obj: Any) -> list[ContentBlock]:
    return _text(json.dumps(obj, indent=2, default=str))


async def _h_generate_cursor_rule(
    self_: "BuiltinServer", args: dict[str, Any]
) -> list[ContentBlock]:
    access = args.get("access", "read-only")
    if access not in ("read-only", "read-write"):
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message=f"Unknown access: {access!r}")
        )
    fmt = args.get("format", "cursor-mdc")
    if fmt not in ("cursor-mdc", "copilot-instructions"):
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message=f"Unknown format: {fmt!r}")
        )
    style = args.get("style", "always-apply")
    if style not in ("always-apply", "scoped"):
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message=f"Unknown style: {style!r}")
        )
    tool_use = args.get("tool_use", "priority")
    if tool_use not in ("available", "priority"):
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS, message=f"Unknown tool_use: {tool_use!r}"
            )
        )
    globs = args.get("globs")
    catalog = await collect_backend_full_catalog(self_.manager, skip_self=True)
    mandatory = self_.manager.mandatory_names()
    rule_assets: dict[str, Any] | None = None
    if self_.manager.assets is not None:
        try:
            from zelosmcp.framework.assetstore.kinds.rule import load_all_rule_assets
            backends = list(catalog.keys()) + ["zelosmcp"]
            rule_assets = await load_all_rule_assets(self_.manager.assets, backends)
        except Exception:
            rule_assets = None
    return _text(
        render_comprehensive_rule(
            catalog,
            access=access,
            style=style,
            globs=globs,
            fmt=fmt,
            tool_use=tool_use,
            mandatory_names=mandatory,
            rule_assets=rule_assets,
        )
    )


async def _h_list_loaded_servers(
    self_: "BuiltinServer", args: dict[str, Any]
) -> list[ContentBlock]:
    return _json_text(self_.manager.status())


async def _h_get_aggregated_tool_catalog(
    self_: "BuiltinServer", args: dict[str, Any]
) -> list[ContentBlock]:
    payload = await collect_backend_full_catalog(self_.manager, skip_self=False)
    return _json_text(payload)


async def _h_generate_cursor_mcp_json(
    self_: "BuiltinServer", args: dict[str, Any]
) -> list[ContentBlock]:
    shape = args.get("shape", "aggregate")
    host = args.get("host", "localhost:8000")
    if shape == "aggregate":
        snippet = {
            "mcpServers": {
                "zelosmcp-aggregate": {
                    "type": "streamable-http",
                    "url": f"http://{host}/mcp",
                }
            }
        }
    elif shape == "per-backend":
        servers: dict[str, dict[str, str]] = {}
        for name, state in self_.manager.servers.items():
            if not state.running:
                continue
            servers[f"zelosmcp-{name}"] = {
                "type": "streamable-http",
                "url": f"http://{host}/{name}/mcp",
            }
        snippet = {"mcpServers": servers}
    else:
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message=f"Unknown shape: {shape!r}")
        )
    return _text(json.dumps(snippet, indent=2))


async def _h_start_server(
    self_: "BuiltinServer", args: dict[str, Any]
) -> list[ContentBlock]:
    name = args.get("name")
    if not isinstance(name, str) or not name:
        raise McpError(ErrorData(code=INVALID_PARAMS, message="`name` is required"))
    if name == NAME:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="Refusing to start/stop the builtin `zelosmcp` backend",
            )
        )
    try:
        await self_.manager.start_one(name)
    except KeyError as exc:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(exc))) from exc
    return _json_text({"ok": True, "name": name})


async def _h_stop_server(
    self_: "BuiltinServer", args: dict[str, Any]
) -> list[ContentBlock]:
    name = args.get("name")
    if not isinstance(name, str) or not name:
        raise McpError(ErrorData(code=INVALID_PARAMS, message="`name` is required"))
    if name == NAME:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="Refusing to start/stop the builtin `zelosmcp` backend",
            )
        )
    try:
        await self_.manager.stop_one(name)
    except KeyError as exc:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(exc))) from exc
    return _json_text({"ok": True, "name": name})


async def _h_reload_config(
    self_: "BuiltinServer", args: dict[str, Any]
) -> list[ContentBlock]:
    cfg = args.get("config")
    if not isinstance(cfg, dict):
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message="`config` must be an object")
        )
    try:
        result = await self_.manager.start_all(cfg)
    except Exception as exc:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(exc))) from exc
    return _json_text(result)


async def _h_list_compressed_tools(
    self_: "BuiltinServer", args: dict[str, Any]
) -> list[ContentBlock]:
    from zelosmcp.compression import compress_for_catalog, COMPRESS_LEVELS

    backend_filter = args.get("backend")
    level_override = args.get("level")
    if level_override is not None and (
        not isinstance(level_override, str) or level_override not in COMPRESS_LEVELS
    ):
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    f"`level` must be one of {sorted(COMPRESS_LEVELS)} "
                    f"(got {level_override!r})"
                ),
            )
        )
    if backend_filter is not None and (
        not isinstance(backend_filter, str) or not backend_filter
    ):
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message="`backend` must be a non-empty string")
        )

    catalog = self_.manager.aggregator.compressed_catalog
    out: dict[str, Any] = {}
    for name, tools in catalog.items():
        if backend_filter is not None and name != backend_filter:
            continue
        spec = self_.manager._specs.get(name)
        configured_level = (
            spec.compress.level if spec is not None and spec.compress is not None else "medium"
        )
        configured_scope = (
            spec.compress.scope if spec is not None and spec.compress is not None else "aggregator"
        )
        render_level = level_override or configured_level
        out[name] = {
            "configured": {"level": configured_level, "scope": configured_scope},
            "render_level": render_level,
            "tool_count": len(tools),
            "catalog": [compress_for_catalog(t, render_level) for t in tools.values()],
        }
    return _json_text(out)


_HANDLERS: dict[str, ToolHandler] = {
    "generate_cursor_rule": _h_generate_cursor_rule,
    "list_loaded_servers": _h_list_loaded_servers,
    "get_aggregated_tool_catalog": _h_get_aggregated_tool_catalog,
    "generate_cursor_mcp_json": _h_generate_cursor_mcp_json,
    "start_server": _h_start_server,
    "stop_server": _h_stop_server,
    "reload_config": _h_reload_config,
    "list_compressed_tools": _h_list_compressed_tools,
}


# ── BuiltinServer (ProxyState-shaped) ──────────────────────────────────


class BuiltinServer:
    """Always-on, in-process MCP server. Shape-compatible with
    :class:`zelosmcp.proxy.ProxyState` so the dispatcher and aggregator
    treat it as just another backend."""

    name = NAME

    def __init__(self, manager: "ProxyManager") -> None:
        self.manager = manager
        self.session_manager: StreamableHTTPSessionManager | None = None
        self.client_session: ClientSession | None = None
        self.running: bool = False
        self.error: str | None = None
        self.backend_info: dict[str, Any] = {"transport": "builtin"}
        self._log_subscribers: list[asyncio.Queue[str]] = []
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._startup_error: BaseException | None = None

    # ── Log plumbing (mirrors ProxyState's API) ────────────────────────

    def _emit_log(self, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [{self.name}] {message}"
        logger.info("[%s] %s", self.name, message)
        for q in list(self._log_subscribers):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                pass

    def subscribe_logs(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        self._log_subscribers.append(q)
        return q

    def unsubscribe_logs(self, q: asyncio.Queue[str]) -> None:
        try:
            self._log_subscribers.remove(q)
        except ValueError:
            pass

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        if self.running:
            return
        self.error = None
        self._ready = asyncio.Event()
        self._startup_error = None
        self._task = asyncio.create_task(self._run())
        await self._ready.wait()
        if self._startup_error is not None:
            raise self._startup_error

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        """Lifecycle task — owns both transports of the in-process MCP.

        Pattern mirrors :meth:`zelosmcp.proxy.ProxyState._run_backend`: every
        async context is entered and exited in this single task to dodge
        anyio cancel-scope cross-task issues. Two surfaces share one
        :class:`Server` instance:

          - HTTP at ``/zelosmcp/mcp`` via a :class:`StreamableHTTPSessionManager`.
          - In-memory :class:`ClientSession` consumed by the aggregator's
            fan-out at ``/mcp``.
        """
        self._emit_log("Starting builtin MCP...")

        # Lowlevel Server with our handlers. Reused across both transports.
        srv = Server(self.name)
        self._register_handlers(srv)

        try:
            async with AsyncExitStack() as stack:
                # ── Transport 1: streamable-HTTP for /zelosmcp/mcp ──
                self.session_manager = StreamableHTTPSessionManager(
                    app=srv,
                    event_store=None,
                    json_response=True,
                    stateless=True,
                )
                await stack.enter_async_context(self.session_manager.run())

                # ── Transport 2: in-memory pair for the aggregator ──
                client_streams, server_streams = await stack.enter_async_context(
                    create_client_server_memory_streams()
                )
                client_read, client_write = client_streams
                server_read, server_write = server_streams

                # Run a Server instance against the in-memory streams in a
                # background task. This is the same pattern
                # `mcp.shared.memory.create_connected_server_and_client_session`
                # uses; we just split it out so the ClientSession and Server
                # task share this `_run` lifecycle.
                tg = await stack.enter_async_context(anyio.create_task_group())

                async def _serve_inproc() -> None:
                    try:
                        await srv.run(
                            server_read,
                            server_write,
                            srv.create_initialization_options(),
                            raise_exceptions=False,
                        )
                    except (asyncio.CancelledError, anyio.get_cancelled_exc_class()):
                        raise

                tg.start_soon(_serve_inproc)

                self.client_session = await stack.enter_async_context(
                    ClientSession(read_stream=client_read, write_stream=client_write)
                )
                await self.client_session.initialize()

                self.running = True
                self._emit_log("Builtin MCP live (/zelosmcp/mcp + aggregator)")
                self._ready.set()

                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self._emit_log("Stopping builtin MCP...")
                    tg.cancel_scope.cancel()

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.error = str(exc)
            self._emit_log(f"ERROR: {exc}")
            self._startup_error = exc
            self._ready.set()
        finally:
            self.session_manager = None
            self.client_session = None
            self.running = False
            self._emit_log("Builtin MCP stopped")

    # ── MCP handler registration ───────────────────────────────────────

    def _register_handlers(self, srv: Server) -> None:
        @srv.list_tools()
        async def list_tools() -> list[Tool]:
            return list(_TOOLS)

        @srv.call_tool(validate_input=False)
        async def call_tool(
            name: str, arguments: dict[str, Any]
        ) -> list[ContentBlock]:
            handler = _HANDLERS.get(name)
            if handler is None:
                raise McpError(
                    ErrorData(
                        code=METHOD_NOT_FOUND,
                        message=f"Unknown tool: {name!r}",
                    )
                )
            self._emit_log(f"call_tool: {name}")
            return await handler(self, arguments or {})
