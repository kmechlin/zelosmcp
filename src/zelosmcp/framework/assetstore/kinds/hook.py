"""``hook`` asset kind.

Hook assets contain agent hook entries (event → command).  When pushed to a
repo they are merged into their respective hook files:

* **Cursor** — ``.cursor/hooks.json`` (flat array, owner-tagged merge)
* **VS Code** — ``.github/hooks/hooks.json`` (VS Code
  ``{"hooks": {"EventName": [...]}}`` schema, merge)

Only entries tagged ``"_owner": "zelosmcp"`` are managed by zelosMCP;
everything else in these files is left untouched.

Unified YAML section format (top-level ``hooks:`` key):

.. code-block:: yaml

    hooks:
      pre_commit_lint:
        name: "Pre-commit lint"
        command: "ruff check ."
        targets: [cursor, vscode]
        # Optional per-IDE event overrides:
        cursor: { event: "afterFileEdit" }
        vscode: { event: "PostToolUse" }

If ``cursor:`` / ``vscode:`` blocks are omitted the ``event`` field is used
for Cursor and ``PostToolUse`` is used as the VS Code default.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from zelosmcp.framework.assetstore.registry import (
    AssetKind,
    ProjectFile,
    RepoCtx,
    register as _register,
)
from zelosmcp.framework.assetstore.row import AssetRow

logger = logging.getLogger("zelosmcp.assets.hook")

KIND_ID = "hook"
_ZELOSMCP_OWNER_TAG = "zelosmcp"

# Best-effort mapping from common Cursor event names to VS Code hook events.
# VS Code events: PreToolUse, PostToolUse, SessionStart, UserPromptSubmit,
#                 Stop, PreCompact, SubagentStart, SubagentStop
_CURSOR_TO_VSCODE_EVENT: dict[str, str] = {
    "afterFileEdit": "PostToolUse",
    "after_file_edit": "PostToolUse",
    "beforeCommand": "PreToolUse",
    "before_command": "PreToolUse",
    "beforePrompt": "UserPromptSubmit",
    "before_prompt": "UserPromptSubmit",
    "afterPrompt": "Stop",
    "after_prompt": "Stop",
    "pre_commit": "PreToolUse",
    "post_edit": "PostToolUse",
    "session_start": "SessionStart",
    "sessionStart": "SessionStart",
}
_DEFAULT_VSCODE_EVENT = "PostToolUse"


# ── Validate / merge helpers ─────────────────────────────────────────────


def _validate(row: AssetRow) -> None:
    if not row.backend:
        raise ValueError("hook asset must have a non-empty 'backend'")
    if not row.name:
        raise ValueError("hook asset must have a non-empty 'name'")
    meta = row.meta or {}
    if not meta.get("command"):
        raise ValueError(f"hook '{row.name}': must have a non-empty 'command'")
    targets = meta.get("targets") or ["cursor"]
    if "cursor" in targets and not meta.get("event") and not meta.get("cursor_event"):
        raise ValueError(f"hook '{row.name}': cursor target requires a non-empty 'event'")


def merge_hooks_json(existing_text: str, new_entry: dict[str, Any]) -> str:
    """Merge one zelosMCP hook entry into an existing ``.cursor/hooks.json``.

    Format: ``{"hooks": [<flat array>]}``.  Only entries owned by zelosMCP
    (``_owner == "zelosmcp"``) matching the same ``_key`` are replaced.
    """
    try:
        data = json.loads(existing_text) if existing_text.strip() else {}
    except (ValueError, TypeError):
        data = {}

    hooks: list[dict[str, Any]] = data.get("hooks") or []
    if not isinstance(hooks, list):
        hooks = []

    key = new_entry.get("_key", new_entry.get("name", ""))
    hooks = [
        h for h in hooks
        if not (h.get("_owner") == _ZELOSMCP_OWNER_TAG and h.get("_key") == key)
    ]
    hooks.append(new_entry)
    data["hooks"] = hooks
    return json.dumps(data, indent=2)


def merge_vscode_hooks_json(existing_text: str, new_entry: dict[str, Any]) -> str:
    """Merge one zelosMCP hook entry into a VS Code-format hooks JSON file.

    VS Code hook format::

        {
          "hooks": {
            "PostToolUse": [
              {"type": "command", "command": "...", "_owner": "zelosmcp", "_key": "..."}
            ]
          }
        }

    Entries with ``_owner == "zelosmcp"`` and a matching ``_key`` are
    removed from every event bucket before the new entry is inserted.
    """
    try:
        data = json.loads(existing_text) if existing_text.strip() else {}
    except (ValueError, TypeError):
        data = {}

    hooks_map: dict[str, list] = data.get("hooks") or {}
    if not isinstance(hooks_map, dict):
        hooks_map = {}

    event = new_entry.get("event", _DEFAULT_VSCODE_EVENT)
    key = new_entry.get("_key", new_entry.get("name", ""))

    # Remove any stale entry for this owner+key across all event buckets.
    for ev_name in list(hooks_map.keys()):
        hooks_map[ev_name] = [
            h for h in (hooks_map[ev_name] or [])
            if not (h.get("_owner") == _ZELOSMCP_OWNER_TAG and h.get("_key") == key)
        ]
        if not hooks_map[ev_name]:
            del hooks_map[ev_name]

    # VS Code entry — include _owner/_key for round-trip tracking.
    vs_entry: dict[str, Any] = {
        "type": "command",
        "command": new_entry.get("command", ""),
        "_owner": _ZELOSMCP_OWNER_TAG,
        "_key": key,
    }
    if new_entry.get("name"):
        vs_entry["name"] = new_entry["name"]

    hooks_map.setdefault(event, []).append(vs_entry)
    data["hooks"] = hooks_map
    return json.dumps(data, indent=2)


# ── Render ────────────────────────────────────────────────────────────────


def _render_for_project(row: AssetRow, ctx: RepoCtx) -> list[ProjectFile]:
    meta = row.meta or {}
    targets: list[str] = list(meta.get("targets") or ["cursor"])

    cursor_event = meta.get("cursor_event") or meta.get("event") or ""
    vscode_event = meta.get("vscode_event") or _DEFAULT_VSCODE_EVENT

    files: list[ProjectFile] = []

    if "cursor" in targets:
        hook_entry: dict[str, Any] = {
            "name": meta.get("name") or row.name,
            "event": cursor_event,
            "command": meta.get("command"),
            "_owner": _ZELOSMCP_OWNER_TAG,
            "_key": row.name,
        }
        files.append(ProjectFile(
            rel_path=".cursor/hooks.json",
            body=json.dumps(hook_entry, indent=2),
            mode="merge",
        ))

    if "vscode" in targets:
        vscode_entry: dict[str, Any] = {
            "event": vscode_event,
            "command": meta.get("command"),
            "name": meta.get("name") or row.name,
            "_owner": _ZELOSMCP_OWNER_TAG,
            "_key": row.name,
        }
        vscode_body = json.dumps(vscode_entry, indent=2)
        files.append(ProjectFile(
            rel_path=".github/hooks/hooks.json",
            body=vscode_body,
            mode="merge",
        ))

    return files


# ── Section parser / dumper ──────────────────────────────────────────────


def _parse_section(section: dict, backend: str, seed_version: int) -> list[AssetRow]:
    """Parse the ``hooks:`` section dict from a unified YAML file."""
    rows: list[AssetRow] = []

    if not isinstance(section, dict):
        return rows

    for hook_name, hook_data in section.items():
        if not isinstance(hook_data, dict):
            continue

        # Per-IDE event overrides (optional dicts with an "event" key).
        cursor_cfg = hook_data.get("cursor") or {}
        vscode_cfg = hook_data.get("vscode") or {}

        cursor_event: str = (
            (cursor_cfg.get("event") if isinstance(cursor_cfg, dict) else None)
            or hook_data.get("event")
            or ""
        )
        vscode_event: str = (
            (vscode_cfg.get("event") if isinstance(vscode_cfg, dict) else None)
            or _CURSOR_TO_VSCODE_EVENT.get(cursor_event, _DEFAULT_VSCODE_EVENT)
        )

        meta: dict[str, Any] = {
            "name": hook_data.get("name") or hook_name,
            "event": cursor_event,
            "command": hook_data.get("command") or "",
            "targets": hook_data.get("targets") or ["cursor", "vscode"],
            "cursor_event": cursor_event,
            "vscode_event": vscode_event,
        }

        # Store cursor-format body for backward compat and UI display.
        hook_entry: dict[str, Any] = {
            "name": meta["name"],
            "event": cursor_event,
            "command": meta["command"],
            "_owner": _ZELOSMCP_OWNER_TAG,
            "_key": hook_name,
        }
        body = json.dumps(hook_entry, indent=2)

        rows.append(AssetRow(
            kind=KIND_ID,
            backend=backend,
            name=hook_name,
            target="cursor",
            body=body,
            meta=meta,
            source="seed",
            seed_version=seed_version,
        ))

    return rows


def dump_section(rows: list[AssetRow]) -> dict:
    """Convert hook rows back into the unified YAML section dict."""
    result: dict = {}
    for row in rows:
        meta = row.meta or {}
        entry: dict = {
            "event": meta.get("event", ""),
            "command": meta.get("command", ""),
        }
        if meta.get("name") and meta["name"] != row.name:
            entry["name"] = meta["name"]
        if meta.get("targets"):
            entry["targets"] = meta["targets"]
        cursor_event = meta.get("cursor_event") or ""
        vscode_event = meta.get("vscode_event") or ""
        if cursor_event or vscode_event:
            if cursor_event and cursor_event != meta.get("event", ""):
                entry["cursor"] = {"event": cursor_event}
            if vscode_event and vscode_event != _CURSOR_TO_VSCODE_EVENT.get(cursor_event, _DEFAULT_VSCODE_EVENT):
                entry["vscode"] = {"event": vscode_event}
        result[row.name] = entry
    return result


HOOK_KIND = AssetKind(
    id=KIND_ID,
    section_key="hooks",
    label="Hooks",
    description=(
        "Agent hook entries (event → command). "
        "Pushed to `.cursor/hooks.json` (Cursor flat-array format) and "
        "`.github/hooks/hooks.json` (VS Code event-keyed format) with safe merge — "
        "zelosMCP only updates entries it owns."
    ),
    parse_section=_parse_section,
    validate=_validate,
    render_for_project=_render_for_project,
)

_register(HOOK_KIND)
