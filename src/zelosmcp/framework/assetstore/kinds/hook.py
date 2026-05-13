"""``hook`` asset kind.

Hook assets contain Cursor hook entries (event → command).  When pushed
to a repo they are merged into ``.cursor/hooks.json``, preserving
user-added entries.  Only entries tagged ``"_owner": "zelosmcp"`` are
managed by zelosMCP; everything else in the file is left untouched.

Unified YAML section format (top-level ``hooks:`` key):

.. code-block:: yaml

    hooks:
      pre_commit_lint:
        name: "Pre-commit lint"
        event: pre_commit
        command: "ruff check ."
        targets: [cursor]
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


def _validate(row: AssetRow) -> None:
    if not row.backend:
        raise ValueError("hook asset must have a non-empty 'backend'")
    if not row.name:
        raise ValueError("hook asset must have a non-empty 'name'")
    meta = row.meta or {}
    if not meta.get("event"):
        raise ValueError(f"hook '{row.name}': must have a non-empty 'event'")
    if not meta.get("command"):
        raise ValueError(f"hook '{row.name}': must have a non-empty 'command'")


def _render_for_project(row: AssetRow, ctx: RepoCtx) -> list[ProjectFile]:
    meta = row.meta or {}
    hook_entry = {
        "name": meta.get("name") or row.name,
        "event": meta.get("event"),
        "command": meta.get("command"),
        "_owner": _ZELOSMCP_OWNER_TAG,
        "_key": row.name,
    }
    return [
        ProjectFile(
            rel_path=".cursor/hooks.json",
            body=json.dumps(hook_entry, indent=2),
            mode="merge",
        )
    ]


def merge_hooks_json(existing_text: str, new_entry: dict[str, Any]) -> str:
    """Merge one zelosMCP hook entry into an existing ``.cursor/hooks.json``."""
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


def _parse_section(section: dict, backend: str, seed_version: int) -> list[AssetRow]:
    """Parse the ``hooks:`` section dict from a unified YAML file."""
    rows: list[AssetRow] = []

    if not isinstance(section, dict):
        return rows

    for hook_name, hook_data in section.items():
        if not isinstance(hook_data, dict):
            continue

        meta: dict[str, Any] = {
            "name": hook_data.get("name") or hook_name,
            "event": hook_data.get("event") or "",
            "command": hook_data.get("command") or "",
            "targets": hook_data.get("targets") or ["cursor"],
        }

        hook_entry = {
            "name": meta["name"],
            "event": meta["event"],
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
        result[row.name] = entry
    return result


HOOK_KIND = AssetKind(
    id=KIND_ID,
    section_key="hooks",
    label="Hooks",
    description=(
        "Cursor hook entries (event → command). "
        "Pushed to `.cursor/hooks.json` with safe merge — "
        "zelosMCP only updates entries it owns."
    ),
    parse_section=_parse_section,
    validate=_validate,
    render_for_project=_render_for_project,
)

_register(HOOK_KIND)
