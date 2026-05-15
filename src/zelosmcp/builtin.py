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


# ── YAML-based rule asset loading ──────────────────────────────────────
#
# When the asset store is unavailable (tests, standalone runs), load
# rule directives from the bundled YAML files so the rule generator
# can still produce correct output without hardcoded string constants.

_yaml_cache: "dict[str, Any] | None" = None


def _load_yaml_rule_assets() -> "dict[str, Any]":
    """Load rule directives from ``configs/assets/*.yaml``.

    Returns a ``{backend: BackendRuleAssets}`` dict mirroring what
    :func:`~zelosmcp.framework.assetstore.kinds.rule.load_all_rule_assets`
    returns from the SQLite store.  Cached at module level.
    """
    global _yaml_cache
    if _yaml_cache is not None:
        return _yaml_cache

    import pathlib
    import yaml
    from zelosmcp.framework.assetstore.kinds.rule import BackendRuleAssets

    configs_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "configs" / "assets"
    result: dict[str, Any] = {}

    for yaml_path in sorted(configs_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_path.read_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        backend = data.get("backend", yaml_path.stem)
        rules = data.get("rules") or {}
        sections = rules.get("sections") or {}
        tool_instructions: dict[str, str] = {}
        ti_raw = rules.get("tool_instructions") or {}
        for tname, tdata in ti_raw.items():
            if isinstance(tdata, dict):
                tool_instructions[tname] = tdata.get("body", "")
            elif isinstance(tdata, str):
                tool_instructions[tname] = tdata

        def _s(key: str) -> str:
            v = sections.get(key)
            if isinstance(v, dict):
                return v.get("body", "")
            return v or ""

        result[backend] = BackendRuleAssets(
            backend=backend,
            tool_instructions=tool_instructions,
            directive_read_only=_s("directive_read_only"),
            directive_read_write=_s("directive_read_write"),
            directive_tool_use_priority=_s("directive_tool_use_priority"),
            self_check_gate=_s("self_check_gate"),
            compressed_rules=_s("compressed_rules"),
        )

    _yaml_cache = result
    return result


def _compressed_wrapper_entries(
    server_name: str,
    n_tools: int,
    level: str,
    tool_instr: dict[str, str],
) -> list[str]:
    """Render the wire-level wrapper-tool bullet entries for a compressed
    backend section. These are the only tools the aggregator exposes for
    this backend; underlying tools are listed separately and must be
    called via ``invoke_tool``."""
    lines: list[str] = []
    if level == "max":
        qualified = f"{server_name}__list_tools"
        lines.append(f"- `{qualified}` `()` [readonly]")
        lines.append(
            f"  List all {n_tools} underlying tools (one short summary "
            f"line per tool). Follow up with `{server_name}__invoke_tool` "
            f"to run one."
        )
    else:
        schema_q = f"{server_name}__get_tool_schema"
        search_q = f"{server_name}__search_tools"
        invoke_q = f"{server_name}__invoke_tool"
        lines.append(f"- `{schema_q}` `(tool_name)` [readonly]")
        lines.append(
            f"  Return the full JSON schema for one tool exposed by "
            f"`{server_name}`. The description embeds a compact "
            f"level={level} catalog of all {n_tools} underlying tools "
            f"for browsing without round-trips."
        )
        instr = tool_instr.get("get_tool_schema", "").strip()
        if instr:
            for il in instr.splitlines():
                lines.append(f"  {il}")
        lines.append(f"- `{search_q}` `(query, limit?)` [readonly]")
        lines.append(
            f"  Search `{server_name}`'s {n_tools} underlying tools by "
            f"name, description, or top-level parameter name."
        )
        instr = tool_instr.get("search_tools", "").strip()
        if instr:
            for il in instr.splitlines():
                lines.append(f"  {il}")
        lines.append(f"- `{invoke_q}` `(tool_name, tool_input)` [?]")
        lines.append(
            f"  Invoke any of `{server_name}`'s {n_tools} underlying "
            f"tools by name. Use `{schema_q}` first if you need the "
            f"full schema."
        )
        instr = tool_instr.get("invoke_tool", "").strip()
        if instr:
            for il in instr.splitlines():
                lines.append(f"  {il}")
    return lines


_DEFAULT_MANDATORY_NAMES: frozenset[str] = frozenset({"filesystem", "pincher"})


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
    compressed_backends: "dict[str, dict[str, Any]] | None" = None,
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

    ``compressed_backends`` maps backend names to their compression
    metadata (``{"level": ..., "scope": ...}``) for backends where the
    aggregator at ``/mcp`` exposes only the wrapper trio
    (``get_tool_schema`` / ``search_tools`` / ``invoke_tool``) instead
    of the full tool surface. When a backend is listed here its section
    in the generated rule shows the wrapper trio as the callable tools
    and the underlying tools as a reference sub-list, and the mandatory
    playbook is switched to the compressed variant.  Pass ``None``
    (the default) for no compression-aware rendering.
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
    directive = ""  # Will be resolved by _pick below

    # When rule_assets is available, pull directives from the store;
    # otherwise fall through to YAML-loaded defaults.
    _default_assets = rule_assets.get("zelosmcp") if rule_assets else None
    _yaml_assets = _load_yaml_rule_assets()
    _yaml_default = _yaml_assets.get("zelosmcp")

    def _pick(section: str) -> str:
        if _default_assets is not None:
            store_body = getattr(_default_assets, section, "") or ""
            if store_body:
                return store_body
        if _yaml_default is not None:
            yaml_body = getattr(_yaml_default, section, "") or ""
            if yaml_body:
                return yaml_body
        return ""

    directive = _pick(
        "directive_read_only" if access == "read-only" else "directive_read_write",
    )

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

    _compressed = compressed_backends or {}

    if tool_use == "priority":
        lines.append(
            _pick("directive_tool_use_priority")
        )
        lines.append(_pick("self_check_gate"))

        # Emit a single compressed-backends explanation block when any
        # user backend is wire-compressed. The block is pulled from the
        # global zelosmcp asset store row when available; otherwise the
        # YAML-loaded default is used.
        compressed_user_backends = {
            n: v for n, v in _compressed.items() if n in user_backends
        }
        if compressed_user_backends:
            lines.append(_pick("compressed_rules"))

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

        # Per-backend rule assets (tool instructions).
        backend_assets = rule_assets.get(server_name) if rule_assets else None
        tool_instr: dict[str, str] = (
            backend_assets.tool_instructions
            if backend_assets is not None
            else {}
        )

        compress_info = _compressed.get(server_name)
        if compress_info is not None:
            # ── Compressed backend ───────────────────────────────────────
            # Show the wrapper trio as the actual callable tools, then
            # list the underlying tools as a reference sub-section.
            level = compress_info.get("level", "medium")
            scope = compress_info.get("scope", "aggregator")
            n_total = len(tools)
            lines.append(f"## `{server_name}`")
            lines.append("")
            _compressed_ref = (
                " See the **Compressed backends** section above."
                if tool_use == "priority"
                else ""
            )
            lines.append(
                f"`{server_name}` exposes {n_total} "
                f"tool{'s' if n_total != 1 else ''} via compressed "
                f"wrappers at `/mcp` (level={level}, scope={scope}). "
                f"Use the wrapper trio only — do NOT call underlying "
                f"tools directly.{_compressed_ref}"
            )
            lines.append("")
            if not tools:
                lines.append("- _(no underlying tools advertised)_")
                lines.append("")
                continue
            lines.extend(
                _compressed_wrapper_entries(server_name, n_total, level, tool_instr)
            )
            lines.append("")
            lines.append(
                f"### Underlying tools (invoke via "
                f"`{server_name}__invoke_tool`)"
            )
            lines.append("")
            for t in tools:
                if not isinstance(t, dict):
                    continue
                tool_name = t.get("name") or "(unnamed)"
                args = _format_args(t.get("inputSchema"))
                marker = _classify_tool(t)
                desc = (t.get("description") or "").strip().replace("\n", " ")
                if not desc:
                    desc = "_(no description)_"
                lines.append(f"- `{tool_name}` `{args}` [{marker}]")
                lines.append(f"  {desc}")
                instr = tool_instr.get(tool_name, "").strip()
                if instr:
                    for instr_line in instr.splitlines():
                        lines.append(f"  {instr_line}")
            lines.append("")
        else:
            # ── Uncompressed backend ─────────────────────────────────────
            lines.append(f"## `{server_name}`")
            lines.append("")
            lines.append(_backend_intro(server_name, len(tools), tool_use=tool_use))
            lines.append("")
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

    # Build the compression metadata map for all user backends that are
    # wire-compressed (scope ∈ {aggregator, global}, level ≠ low).
    compressed_backends: dict[str, dict[str, Any]] = {}
    for name, spec in self_.manager._specs.items():
        if spec is None or spec.compress is None:
            continue
        c = spec.compress
        if c.level == "low":
            continue
        if c.scope not in ("aggregator", "global"):
            continue
        compressed_backends[name] = {"level": c.level, "scope": c.scope}

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
            compressed_backends=compressed_backends or None,
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
