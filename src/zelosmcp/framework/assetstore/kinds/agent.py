"""``agent`` asset kind.

Agent assets contain Cursor Subagent / Skill definitions.

Unified YAML section format (top-level ``agents:`` key):

.. code-block:: yaml

    agents:
      code_reviewer:
        name: "Code Reviewer"
        description: "Reviews diffs for bugs and style issues"
        targets: [cursor]
        push:
          cursor: ".cursor/skills/code_reviewer/SKILL.md"
        body: |
          # Code Reviewer
          You are a careful code reviewer ...
"""
from __future__ import annotations

import logging
from typing import Any

from zelosmcp.framework.assetstore.registry import (
    AssetKind,
    ProjectFile,
    RepoCtx,
    register as _register,
)
from zelosmcp.framework.assetstore.row import AssetRow

logger = logging.getLogger("zelosmcp.assets.agent")

KIND_ID = "agent"


def _validate(row: AssetRow) -> None:
    if not row.backend:
        raise ValueError("agent asset must have a non-empty 'backend'")
    if not row.name:
        raise ValueError("agent asset must have a non-empty 'name'")


def _render_for_project(row: AssetRow, ctx: RepoCtx) -> list[ProjectFile]:
    meta = row.meta or {}
    push = meta.get("push") or {}
    cursor_path: str = push.get("cursor") or f".cursor/skills/{row.name}/SKILL.md"
    return [ProjectFile(rel_path=cursor_path, body=row.body, mode="overwrite")]


def _parse_section(section: dict, backend: str, seed_version: int) -> list[AssetRow]:
    """Parse the ``agents:`` section dict from a unified YAML file."""
    rows: list[AssetRow] = []

    if not isinstance(section, dict):
        return rows

    for agent_name, agent_data in section.items():
        if not isinstance(agent_data, dict):
            continue

        body = agent_data.get("body") or ""
        meta: dict[str, Any] = {
            "name": agent_data.get("name") or agent_name,
            "description": agent_data.get("description", ""),
            "targets": agent_data.get("targets") or ["cursor"],
            "push": agent_data.get("push") or {
                "cursor": f".cursor/skills/{agent_name}/SKILL.md"
            },
        }

        rows.append(AssetRow(
            kind=KIND_ID,
            backend=backend,
            name=agent_name,
            target="cursor",
            body=body,
            meta=meta,
            source="seed",
            seed_version=seed_version,
        ))

    return rows


def dump_section(rows: list[AssetRow]) -> dict:
    """Convert agent rows back into the unified YAML section dict."""
    result: dict = {}
    for row in rows:
        meta = row.meta or {}
        entry: dict = {
            "name": meta.get("name") or row.name,
            "body": row.body,
        }
        if meta.get("description"):
            entry["description"] = meta["description"]
        if meta.get("targets"):
            entry["targets"] = meta["targets"]
        if meta.get("push"):
            entry["push"] = meta["push"]
        result[row.name] = entry
    return result


AGENT_KIND = AssetKind(
    id=KIND_ID,
    section_key="agents",
    label="Agents",
    description=(
        "Cursor Subagent / Skill definitions. "
        "Each agent can be pushed to `.cursor/skills/<name>/SKILL.md` "
        "in any indexed repo."
    ),
    parse_section=_parse_section,
    validate=_validate,
    render_for_project=_render_for_project,
)

_register(AGENT_KIND)
