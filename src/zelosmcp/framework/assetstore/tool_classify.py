"""Shared tool mutability classifier.

Lifted from :mod:`zelosmcp.builtin` so both the rule renderer and the
dynamic default generator (:mod:`~zelosmcp.framework.assetstore.defaults`)
can use the same logic without a circular dependency.
"""
from __future__ import annotations

from typing import Any

# Tool name prefixes that strongly imply state-mutating semantics; used
# as a fallback when a tool's MCP annotations don't set readOnlyHint.
MUTATING_PREFIXES: tuple[str, ...] = (
    "create_", "update_", "set_", "delete_", "remove_", "start_", "stop_",
    "restart_", "run_", "push_", "pull_", "build_", "write_", "edit_",
    "move_", "configure_", "reload_",
)


def classify_tool(tool: dict[str, Any]) -> str:
    """Return a mutability marker for *tool*: ``"readonly"``, ``"destructive"``,
    ``"mutates"``, or ``"?"``.

    Precedence:
      1. ``annotations.destructiveHint == True`` → destructive.
      2. ``annotations.readOnlyHint == True``    → readonly.
      3. Tool name starts with a known mutation prefix → mutates.
      4. Otherwise → ``"?"`` (unknown; conservative read-only blocks call).
    """
    ann = tool.get("annotations") or {}
    if ann.get("destructiveHint") is True:
        return "destructive"
    if ann.get("readOnlyHint") is True:
        return "readonly"
    name = (tool.get("name") or "").lower()
    if any(name.startswith(p) for p in MUTATING_PREFIXES):
        return "mutates"
    return "?"


def format_args(input_schema: Any) -> str:
    """Return a parenthesized arg summary for a tool's ``inputSchema``.

    Examples: ``"(path, head?, tail?)"``; ``"()"``.
    """
    if not isinstance(input_schema, dict):
        return "()"
    if input_schema.get("type") not in (None, "object"):
        return f"({input_schema.get('type', 'value')})"
    props = input_schema.get("properties")
    if not isinstance(props, dict) or not props:
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
