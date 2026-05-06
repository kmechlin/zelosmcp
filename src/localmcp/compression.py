"""Tool-list compression helpers shared by the aggregator and the per-backend
``scope=global`` wrapper.

The compression scheme replaces a backend's full tool surface (N tools, each
with a description and JSON-schema arguments) with **at most two wrapper
tools**:

- ``get_tool_schema(tool_name)`` — returns the full schema for one underlying
  tool. The wrapper's *description* embeds a compressed catalog (one short
  line per tool) so the LLM can browse without paying schema-fetch latency
  for every name.
- ``invoke_tool(tool_name, tool_input)`` — runs the underlying tool by name.

At ``level="max"`` the catalog drops the ``get_tool_schema`` lookup entirely
and exposes a single ``list_tools()`` call instead — useful for very large
backends the agent rarely uses.

These helpers are pure functions: they don't reach into manager or aggregator
state. The caller passes in ``tools`` and ``dispatch`` and gets back wrapper
tool objects + a ``CallToolResult``-shaped response per request.

See [docs/compression.md](docs/compression.md) for the level/scope matrix.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.types import CallToolResult, ContentBlock, TextContent, Tool

from localmcp.config import COMPRESS_LEVELS, COMPRESS_SCOPES

# Re-exported so callers (aggregator, per-backend wrapper, builtin tool)
# don't have to import from config.py just for the level/scope lookup.
__all__ = [
    "COMPRESS_LEVELS",
    "COMPRESS_SCOPES",
    "compress_for_catalog",
    "compressed_tool_list",
    "make_get_schema_wrapper",
    "make_invoke_wrapper",
    "make_list_tools_wrapper",
    "handle_compressed_call",
    "wrapper_tool_names",
]


def wrapper_tool_names(level: str) -> tuple[str, ...]:
    """Names the compression layer reserves for one backend at ``level``.

    Useful for the call_tool dispatcher: anything in this set is a wrapper
    that should route through ``handle_compressed_call`` instead of the
    underlying backend.
    """
    if level == "max":
        return ("list_tools",)
    return ("get_tool_schema", "invoke_tool")


def _first_sentence(text: str) -> str:
    """Return the first sentence of ``text`` (best-effort).

    Splits on ``". "`` so abbreviations like ``"e.g."`` don't fragment the
    output. Trailing periods are stripped so the catalog line stays one
    sentence per tool.
    """
    text = (text or "").strip()
    if not text:
        return ""
    head, _, _ = text.partition(". ")
    return head.rstrip(".").strip()


def _param_names(tool: Tool) -> list[str]:
    """Top-level parameter names from the tool's JSON Schema."""
    schema = tool.inputSchema or {}
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return []
    return list(props.keys())


def compress_for_catalog(tool: Tool, level: str) -> str:
    """Render one catalog line for ``tool`` at the given ``level``.

    - ``low``: full description (no compression). Returned as a single line
      — multi-line descriptions are flattened.
    - ``medium``: ``- name: first-sentence``.
    - ``high``: ``- name(arg1, arg2, ...)``.
    - ``max``: same as ``medium`` (used by the standalone ``list_tools``
      wrapper that exposes the catalog as a tool call).
    """
    name = tool.name
    if level == "low":
        # Flatten newlines so the line stays readable inline.
        desc = " ".join((tool.description or "").split())
        return f"- {name}: {desc}" if desc else f"- {name}"
    if level == "high":
        params = ", ".join(_param_names(tool))
        return f"- {name}({params})"
    # medium / max
    sentence = _first_sentence(tool.description or "")
    return f"- {name}: {sentence}" if sentence else f"- {name}"


def _render_catalog(tools: list[Tool], level: str) -> str:
    """One blank-line-separated rendering of the per-tool lines."""
    return "\n".join(compress_for_catalog(t, level) for t in tools)


def _wrapper_name(prefix: str, base: str) -> str:
    """Compose a wrapper tool name. ``prefix`` is the backend name (when used
    by the aggregator) or ``""`` (when used by the per-backend wrapper that
    serves ``/<name>/mcp`` directly — clients there already know the backend
    by the URL path)."""
    return f"{prefix}__{base}" if prefix else base


def make_get_schema_wrapper(prefix: str, tools: list[Tool], level: str) -> Tool:
    """Build the ``get_tool_schema`` wrapper Tool.

    The wrapper's description embeds the compressed catalog (one line per
    underlying tool) so the LLM can browse without round-trips. Required
    arg: ``tool_name``.
    """
    catalog = _render_catalog(tools, level)
    label = f"'{prefix}'" if prefix else "this backend"
    description = (
        f"Return the full JSON schema for one tool exposed by {label}. "
        f"Pass `tool_name` exactly as listed in the catalog below.\n\n"
        f"Catalog ({len(tools)} tools, level={level}):\n{catalog}"
    )
    return Tool(
        name=_wrapper_name(prefix, "get_tool_schema"),
        description=description,
        inputSchema={
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the underlying tool to fetch the schema for.",
                }
            },
            "required": ["tool_name"],
        },
    )


def make_invoke_wrapper(prefix: str, n_tools: int) -> Tool:
    """Build the ``invoke_tool`` wrapper Tool. Required args: ``tool_name``,
    ``tool_input``."""
    label = f"'{prefix}'" if prefix else "this backend"
    schema_ref = (
        f"{prefix}__get_tool_schema" if prefix else "get_tool_schema"
    )
    description = (
        f"Invoke any of the {n_tools} tools exposed by {label}. "
        f"Pass `tool_name` (string) and `tool_input` (object matching the "
        f"tool's schema). Use `{schema_ref}` first if you need the schema."
    )
    return Tool(
        name=_wrapper_name(prefix, "invoke_tool"),
        description=description,
        inputSchema={
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "tool_input": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "Arguments to pass to the underlying tool.",
                },
            },
            "required": ["tool_name", "tool_input"],
        },
    )


def make_list_tools_wrapper(prefix: str, n_tools: int) -> Tool:
    """Build the ``list_tools`` wrapper Tool used at level=max.

    The result of calling this tool is the same compressed catalog
    (rendered at the ``medium`` level for readability). At level=max the
    LLM doesn't even see the catalog inline in tools/list — it has to
    explicitly call this tool to discover what's available, which is the
    point of ``max`` for very large backends.
    """
    label = f"'{prefix}'" if prefix else "this backend"
    description = (
        f"List the {n_tools} tools exposed by {label}. Returns one short "
        f"summary line per tool. Follow up with the matching get_tool_schema "
        f"and invoke_tool wrappers to actually call a tool."
    )
    return Tool(
        name=_wrapper_name(prefix, "list_tools"),
        description=description,
        inputSchema={"type": "object", "properties": {}},
    )


def compressed_tool_list(prefix: str, tools: list[Tool], level: str) -> list[Tool]:
    """Convenience: return the wrapper tools for one backend at ``level``.

    - ``max`` => ``[list_tools]``.
    - anything else => ``[get_tool_schema, invoke_tool]``.

    Caller is responsible for applying the prefix to wrapper names. ``low``
    is treated as "no compression" by callers and shouldn't reach this
    helper; if it does, we still produce the medium-style wrappers so the
    behaviour is at least defined.
    """
    if level == "max":
        return [make_list_tools_wrapper(prefix, len(tools))]
    return [
        make_get_schema_wrapper(prefix, tools, level),
        make_invoke_wrapper(prefix, len(tools)),
    ]


def _text_result(text: str, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        isError=is_error,
    )


def _unknown_tool_result(name: str, catalog: dict[str, Tool]) -> CallToolResult:
    available = ", ".join(sorted(catalog.keys())) or "(none)"
    msg = (
        f"Unknown tool name {name!r}. Available tools: {available}. "
        f"Use the catalog in get_tool_schema's description (or call list_tools "
        f"if level=max) to see the full list."
    )
    return _text_result(msg, is_error=True)


# Dispatch returns (content, structuredContent, isError) so the caller can
# preserve structuredContent verbatim — the MCP SDK's lowlevel server
# validates the response against any declared outputSchema, and dropping
# structuredContent for tools that have one trips that validation.
DispatchFn = Callable[[str, dict[str, Any]], Awaitable[Any]]


async def handle_compressed_call(
    catalog: dict[str, Tool],
    op: str,
    args: dict[str, Any] | None,
    dispatch: DispatchFn,
    *,
    level: str = "medium",
) -> CallToolResult:
    """Run one wrapper-tool call against ``catalog`` (the cached full tool
    list for one backend).

    - ``op="get_tool_schema"``: looks up ``args["tool_name"]`` in ``catalog``
      and returns the tool's pydantic dump as JSON text. Unknown name =>
      isError result with the available names.
    - ``op="invoke_tool"``: looks up the tool, awaits ``dispatch(name,
      tool_input)`` (which should be the backend's ``client_session.call_tool``
      bound method or equivalent), and returns the result verbatim — both
      content and structuredContent — so any outputSchema validation
      downstream still passes. The dispatch function is expected to return
      something with ``.content``, ``.structuredContent``, and ``.isError``
      attributes (i.e. an MCP CallToolResult-shaped object).
    - ``op="list_tools"``: returns the compressed catalog rendered at
      ``level`` as a text response.
    - Unknown ``op``: isError result.

    ``args`` is normalised to ``{}`` if None so callers don't have to.
    """
    args = args or {}

    if op == "list_tools":
        text = _render_catalog(list(catalog.values()), level)
        return _text_result(text or "(no tools)")

    if op == "get_tool_schema":
        name = args.get("tool_name")
        if not isinstance(name, str) or not name:
            return _text_result(
                "get_tool_schema requires `tool_name` (string).", is_error=True
            )
        tool = catalog.get(name)
        if tool is None:
            return _unknown_tool_result(name, catalog)
        body = tool.model_dump(by_alias=True, exclude_none=True)
        return _text_result(json.dumps(body, indent=2))

    if op == "invoke_tool":
        name = args.get("tool_name")
        tool_input = args.get("tool_input", {})
        if not isinstance(name, str) or not name:
            return _text_result(
                "invoke_tool requires `tool_name` (string).", is_error=True
            )
        if not isinstance(tool_input, dict):
            return _text_result(
                "invoke_tool's `tool_input` must be a JSON object.",
                is_error=True,
            )
        if name not in catalog:
            return _unknown_tool_result(name, catalog)
        result = await dispatch(name, tool_input)
        # Preserve structuredContent if the underlying tool set it (mirrors
        # the existing aggregator behaviour for outputSchema-aware tools).
        content_attr = getattr(result, "content", None)
        if isinstance(content_attr, list):
            content: list[ContentBlock] = list(content_attr)
        else:
            content = []
        return CallToolResult(
            content=content,
            structuredContent=getattr(result, "structuredContent", None),
            isError=bool(getattr(result, "isError", False)),
        )

    return _text_result(
        f"Unknown compression op {op!r}; expected get_tool_schema, invoke_tool, or list_tools.",
        is_error=True,
    )
