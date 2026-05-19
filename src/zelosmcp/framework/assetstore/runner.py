"""Extension runner.

:func:`invoke_extension` executes the MCP tool described by an
``extension`` :class:`~row.AssetRow` against the zelosMCP aggregator
and returns a structured result.

Template substitution uses a simple ``{ctx.<dot.path>}`` syntax; any key
not found in the context dict is left unchanged rather than raising.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("zelosmcp.assets.runner")

_TEMPLATE_RE = re.compile(r"\{ctx\.([^}]+)\}")


def _fill_template(template: Any, ctx: dict[str, Any]) -> Any:
    """Recursively fill ``{ctx.<key>}`` references in a dict/list/str."""
    if isinstance(template, str):
        def _replace(m: re.Match) -> str:
            path = m.group(1).split(".")
            val: Any = ctx
            for part in path:
                if isinstance(val, dict):
                    val = val.get(part, m.group(0))
                else:
                    val = m.group(0)
                    break
            return str(val) if val is not None else m.group(0)
        return _TEMPLATE_RE.sub(_replace, template)
    if isinstance(template, dict):
        return {k: _fill_template(v, ctx) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill_template(v, ctx) for v in template]
    return template


def _render_message(template: str, **kwargs: Any) -> str:
    """Render a success/error message template.

    Supports simple ``{key}`` substitution using :meth:`str.format_map`.
    Unknown keys are left as ``{key}`` rather than raising.
    """
    class _SafeMap(dict):
        def __missing__(self, key: str) -> str:
            return f"{{{key}}}"

    try:
        return template.format_map(_SafeMap(kwargs))
    except Exception:
        return template


@dataclass
class ExtensionResult:
    """Result of one extension invocation."""

    ok: bool
    message: str
    result: Any = None
    error: str = ""


async def invoke_extension(
    store: Any,
    manager: Any,
    *,
    backend: str,
    name: str,
    ctx: dict[str, Any] | None = None,
) -> ExtensionResult:
    """Invoke the MCP tool described by an ``extension`` asset.

    Parameters
    ----------
    store:
        Open :class:`~sqlite.SQLiteAssetStore`.
    manager:
        The running :class:`~zelosmcp.manager.ProxyManager` instance.
    backend:
        Backend the extension belongs to (e.g. ``"pincher"``).
    name:
        Extension asset name (e.g. ``"index_project"``).
    ctx:
        Context dict for template substitution.  Nested keys are
        accessed with ``{ctx.repo.ro_path}`` etc.

    Returns
    -------
    :class:`ExtensionResult`
    """
    row = await store.get("extension", backend, name)
    if row is None:
        return ExtensionResult(
            ok=False,
            message="",
            error=f"extension '{backend}/{name}' not found",
        )

    meta = row.meta or {}
    ext_type = meta.get("type", "tool")
    ctx = ctx or {}

    if ext_type == "link":
        href = _fill_template(meta.get("href", ""), ctx)
        return ExtensionResult(
            ok=True,
            message=f"Open {href}",
            result={"href": href},
        )

    if ext_type != "tool":
        return ExtensionResult(
            ok=False,
            message="",
            error=f"unknown extension type '{ext_type}'",
        )

    tool_name = meta.get("tool")
    if not tool_name:
        return ExtensionResult(
            ok=False, message="", error="extension has no 'tool' configured"
        )

    requires_running = meta.get("requires_running", True)
    if requires_running:
        state = manager.servers.get(backend)
        if state is None or not getattr(state, "running", False):
            return ExtensionResult(
                ok=False,
                message="",
                error=f"backend '{backend}' is not running",
            )

    args_template = meta.get("args_template") or {}
    filled_args = _fill_template(args_template, ctx)

    qualified_tool = f"{backend}__{tool_name}"

    try:
        state = manager.servers.get(backend)
        session = getattr(state, "client_session", None)
        if session is None:
            return ExtensionResult(
                ok=False, message="", error=f"no client session for '{backend}'"
            )
        call_result = await session.call_tool(tool_name, filled_args)
    except Exception as exc:
        err_msg = str(exc)
        error_tpl = meta.get("error", {}).get("message", "Extension failed: {error}")
        return ExtensionResult(
            ok=False,
            message=_render_message(error_tpl, error=err_msg),
            error=err_msg,
        )

    # Extract text content from the MCP result.
    from zelosmcp.app import _flatten_call_result
    result_payload = _flatten_call_result(call_result)

    success_tpl = meta.get("success", {}).get(
        "message", f"'{qualified_tool}' completed."
    )
    message = _render_message(
        success_tpl,
        result=result_payload if isinstance(result_payload, dict) else {},
        backend=backend,
        tool=tool_name,
    )
    return ExtensionResult(ok=True, message=message, result=result_payload)
