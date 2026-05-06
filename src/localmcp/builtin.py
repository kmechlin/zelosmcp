"""Always-on, in-process MCP server exposed at ``/localmcp/mcp`` and aggregated
into ``/mcp`` as ``localmcp__*``.

The builtin is structurally a :class:`localmcp.proxy.ProxyState` look-alike:
it carries the same attributes the dispatcher in :mod:`localmcp.app` and the
aggregator in :mod:`localmcp.aggregator` already iterate over (``name``,
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
    ``stop_one``; refuse ``name == "localmcp"`` (would deadlock).
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

if TYPE_CHECKING:
    from localmcp.manager import ProxyManager

logger = logging.getLogger("localmcp")

NAME = "localmcp"


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
            "returns the plain body for `.github/copilot-instructions.md`."
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
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="list_loaded_servers",
        description=(
            "Compact view of every backend currently registered with "
            "localmcp: name, transport, running state, error, and "
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
            "`localmcp` (the builtin can't be stopped/started)."
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
            "Stop a single backend by name. Refuses `localmcp` (the "
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
                        "(`localmcp`, `mcp`, `api`, ...) are rejected."
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

# Tool name prefixes that strongly imply state-mutating semantics; used
# as a fallback when a tool's MCP annotations don't set readOnlyHint.
_MUTATING_PREFIXES: tuple[str, ...] = (
    "create_", "update_", "set_", "delete_", "remove_", "start_", "stop_",
    "restart_", "run_", "push_", "pull_", "build_", "write_", "edit_",
    "move_", "configure_", "reload_",
)


def _classify_tool(tool: dict[str, Any]) -> str:
    """Return a mutability marker string for ``tool``: one of
    ``"readonly"``, ``"destructive"``, ``"mutates"``, or ``"?"``.

    Precedence:
      1. ``annotations.destructiveHint == True`` -> destructive (most dangerous).
      2. ``annotations.readOnlyHint == True``    -> readonly.
      3. tool name starts with a known mutation prefix -> mutates.
      4. otherwise -> ``"?"`` (unknown; conservative read-only blocks call).
    """
    ann = tool.get("annotations") or {}
    if ann.get("destructiveHint") is True:
        return "destructive"
    if ann.get("readOnlyHint") is True:
        return "readonly"
    name = (tool.get("name") or "").lower()
    if any(name.startswith(p) for p in _MUTATING_PREFIXES):
        return "mutates"
    return "?"


def _format_args(input_schema: Any) -> str:
    """Return a parenthesized arg summary for a tool's ``inputSchema``,
    e.g. ``"(path, head?, tail?)"``. Required args first (in their
    declared order), then optionals with a trailing ``?``.

      - Empty / non-object schema -> ``"()"``.
      - Object schema with no declared properties -> ``"(...)"`` to flag
        "accepts an arbitrary object".
      - Otherwise: required (in ``required`` order, falling back to
        ``properties`` insertion order for any required key without a
        properties entry) followed by optionals (in ``properties`` order).
    """
    if not isinstance(input_schema, dict):
        return "()"
    if input_schema.get("type") not in (None, "object"):
        return f"({input_schema.get('type', 'value')})"
    props = input_schema.get("properties")
    if not isinstance(props, dict) or not props:
        # ``object`` with no declared properties: still accepts an object.
        return "(...)" if input_schema else "()"
    required = input_schema.get("required") or []
    required_set = set(required) if isinstance(required, list) else set()
    parts: list[str] = []
    seen: set[str] = set()
    if isinstance(required, list):
        for r in required:
            if isinstance(r, str) and r not in seen:
                parts.append(r)
                seen.add(r)
    for k in props.keys():
        if k in seen:
            continue
        parts.append(f"{k}?" if k not in required_set else k)
        seen.add(k)
    return "(" + ", ".join(parts) + ")"


def _backend_intro(server_name: str, tool_count: int) -> str:
    """One-line per-backend header used right under each section title."""
    return (
        f"`{server_name}` exposes {tool_count} "
        f"tool{'s' if tool_count != 1 else ''} via the aggregator at "
        f"`/mcp` (namespaced `{server_name}__<tool>`). Prefer these over "
        f"equivalent shell commands."
    )


def _frontmatter(*, style: str, globs: str | None, access: str) -> str:
    desc_suffix = (
        " (read-only mode)" if access == "read-only" else " (read-write mode)"
    )
    if style == "scoped":
        if not globs:
            globs = "**/*"
        return (
            "---\n"
            f"description: LocalMCP backend tool catalog{desc_suffix}\n"
            f"globs: {globs}\n"
            "alwaysApply: false\n"
            "---\n"
        )
    return (
        "---\n"
        f"description: LocalMCP backend tool catalog{desc_suffix}\n"
        "alwaysApply: true\n"
        "---\n"
    )


_DIRECTIVE_READ_ONLY = (
    "## Access mode: READ-ONLY\n\n"
    "**Do not call** any tool tagged `[mutates]`, `[destructive]`, or "
    "`[?]`. They modify backend state, and this rule is currently "
    "configured for safe inspection only. If a task requires mutation, "
    "ask the user to regenerate the rule with `access=read-write` "
    "(e.g. via the Cursor rule panel in the LocalMCP web UI at "
    "`http://localhost:8000`).\n"
)

_DIRECTIVE_READ_WRITE = (
    "## Access mode: READ-WRITE\n\n"
    "Tools tagged `[mutates]` and `[destructive]` change backend state. "
    "Confirm with the user before calling `[destructive]` tools "
    "(irreversible). Tools tagged `[?]` have ambiguous mutability — "
    "call only when context makes it clear they're inspection-only.\n"
)


def render_comprehensive_rule(
    catalog: dict[str, dict[str, Any]],
    *,
    access: str = "read-only",
    style: str = "always-apply",
    globs: str | None = None,
    fmt: str = "cursor-mdc",
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
    """
    if access not in ("read-only", "read-write"):
        raise ValueError(f"Unknown access mode: {access!r}")
    if fmt not in ("cursor-mdc", "copilot-instructions"):
        raise ValueError(f"Unknown format: {fmt!r}")

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
            "\n# LocalMCP backends\n\n"
            "No user backends are currently loaded. POST a config to "
            "`/api/start` (or click START in the web UI) and regenerate "
            "this rule to get tool-specific guidance.\n\n"
            f"{directive}"
        )
        return fm + body

    backend_list = ", ".join(f"`{n}`" for n in user_backends)
    lines: list[str] = [
        "",
        "# LocalMCP backend tool catalog",
        "",
        (
            "Generated from the LocalMCP aggregator at "
            "`http://localhost:8000/mcp`. Every tool below is reachable "
            "as `<server>__<tool>` (double underscore) on that single "
            "Cursor entry. Prefer these over shelling out — they return "
            "structured data and keep paths inside the container's "
            "`/user_data_rw` (read-write) and `/user_data_ro` "
            "(kernel-enforced read-only) mounts."
        ),
        "",
        f"Currently-loaded backends: {backend_list}.",
        "",
        directive,
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

    for server_name, data in user_backends.items():
        tools = data.get("tools") or []
        if not isinstance(tools, list):
            continue
        lines.append(f"## `{server_name}`")
        lines.append("")
        lines.append(_backend_intro(server_name, len(tools)))
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
# the HTTP `/api/catalog` endpoint and the MCP `localmcp__get_aggregated
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
    globs = args.get("globs")
    catalog = await collect_backend_full_catalog(self_.manager, skip_self=True)
    return _text(
        render_comprehensive_rule(
            catalog, access=access, style=style, globs=globs, fmt=fmt
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
                "localmcp-aggregate": {
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
            servers[f"localmcp-{name}"] = {
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
                message="Refusing to start/stop the builtin `localmcp` backend",
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
                message="Refusing to start/stop the builtin `localmcp` backend",
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
    from localmcp.compression import compress_for_catalog, COMPRESS_LEVELS

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
    :class:`localmcp.proxy.ProxyState` so the dispatcher and aggregator
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

        Pattern mirrors :meth:`localmcp.proxy.ProxyState._run_backend`: every
        async context is entered and exited in this single task to dodge
        anyio cancel-scope cross-task issues. Two surfaces share one
        :class:`Server` instance:

          - HTTP at ``/localmcp/mcp`` via a :class:`StreamableHTTPSessionManager`.
          - In-memory :class:`ClientSession` consumed by the aggregator's
            fan-out at ``/mcp``.
        """
        self._emit_log("Starting builtin MCP...")

        # Lowlevel Server with our handlers. Reused across both transports.
        srv = Server(self.name)
        self._register_handlers(srv)

        try:
            async with AsyncExitStack() as stack:
                # ── Transport 1: streamable-HTTP for /localmcp/mcp ──
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
                self._emit_log("Builtin MCP live (/localmcp/mcp + aggregator)")
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
