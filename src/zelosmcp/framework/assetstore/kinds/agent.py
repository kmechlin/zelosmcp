"""``agent`` asset kind.

Agent assets contain Cursor Subagent / Skill definitions.  When a VS Code
target is included the generated SKILL.md receives the required YAML
frontmatter (``name:`` + ``description:``) so VS Code Copilot can discover
the skill under ``.github/skills/<slug>/SKILL.md`` or
``.vscode/skills/<slug>/SKILL.md``.

Unified YAML section format (top-level ``agents:`` key):

.. code-block:: yaml

    agents:
      code_reviewer:
        name: "Code Reviewer"
        description: "Reviews diffs for bugs and style issues"
        targets: [cursor, vscode]
        push:
          cursor: ".cursor/skills/code_reviewer/SKILL.md"
        body: |
          # Code Reviewer
          You are a careful code reviewer ...
"""
from __future__ import annotations

import logging
import re
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

# Maximum field lengths from the VS Code agent-skills spec.
_SLUG_MAX = 64
_DESC_MAX = 1024


# ── Helpers ─────────────────────────────────────────────────────────────


def _slug(name: str) -> str:
    """Convert *name* to a VS Code-compatible skill slug.

    Only lowercase letters, numbers, and hyphens; max 64 characters.
    Names with invalid characters cause the skill to silently fail to load
    in VS Code, so we normalise here rather than reject.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:_SLUG_MAX] or "skill"


def _vscode_skill_body(row: AssetRow, meta: dict) -> str:
    """Prepend VS Code SKILL.md frontmatter to *row.body*.

    The frontmatter ``name`` must match the parent directory name and the
    ``description`` guides Copilot's automatic skill-selection logic.
    """
    name = _slug(row.name)
    desc = (meta.get("description") or row.name)[:_DESC_MAX]
    frontmatter = f"---\nname: {name}\ndescription: {desc}\n---\n\n"
    return frontmatter + row.body


# ── Validate / render ────────────────────────────────────────────────────


def _validate(row: AssetRow) -> None:
    if not row.backend:
        raise ValueError("agent asset must have a non-empty 'backend'")
    if not row.name:
        raise ValueError("agent asset must have a non-empty 'name'")


def _render_for_project(row: AssetRow, ctx: RepoCtx) -> list[ProjectFile]:
    meta = row.meta or {}
    push = meta.get("push") or {}
    targets: list[str] = list(meta.get("targets") or ["cursor", "vscode"])

    files: list[ProjectFile] = []

    if "cursor" in targets:
        cursor_path: str = push.get("cursor") or f".cursor/skills/{row.name}/SKILL.md"
        files.append(ProjectFile(rel_path=cursor_path, body=row.body, mode="overwrite"))

    if "vscode" in targets:
        vscode_body = _vscode_skill_body(row, meta)
        slug = _slug(row.name)
        github_path: str = (
            push.get("vscode_github") or f".github/skills/{slug}/SKILL.md"
        )
        vscode_path: str = (
            push.get("vscode_vscode") or f".vscode/skills/{slug}/SKILL.md"
        )
        files.append(ProjectFile(rel_path=github_path, body=vscode_body, mode="overwrite"))
        files.append(ProjectFile(rel_path=vscode_path, body=vscode_body, mode="overwrite"))

    return files


# ── Section parser ───────────────────────────────────────────────────────


def _parse_section(section: dict, backend: str, seed_version: int) -> list[AssetRow]:
    """Parse the ``agents:`` section dict from a unified YAML file."""
    rows: list[AssetRow] = []

    if not isinstance(section, dict):
        return rows

    for agent_name, agent_data in section.items():
        if not isinstance(agent_data, dict):
            continue

        body = agent_data.get("body") or ""
        slug = _slug(agent_name)
        meta: dict[str, Any] = {
            "name": agent_data.get("name") or agent_name,
            "description": agent_data.get("description", ""),
            "targets": agent_data.get("targets") or ["cursor", "vscode"],
            "push": agent_data.get("push") or {
                "cursor": f".cursor/skills/{agent_name}/SKILL.md",
                "vscode_github": f".github/skills/{slug}/SKILL.md",
                "vscode_vscode": f".vscode/skills/{slug}/SKILL.md",
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
        "Each agent is pushed to `.cursor/skills/<name>/SKILL.md` (Cursor) and "
        "`.github/skills/<slug>/SKILL.md` + `.vscode/skills/<slug>/SKILL.md` "
        "(VS Code, with required YAML frontmatter)."
    ),
    parse_section=_parse_section,
    validate=_validate,
    render_for_project=_render_for_project,
)

_register(AGENT_KIND)
