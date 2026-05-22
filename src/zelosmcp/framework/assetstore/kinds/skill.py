"""``skill`` asset kind.

Skill assets contain Agent Skill definitions that are pushed to editor
skill directories where they auto-load on demand based on task relevance.

Skills define *what the AI knows* — domain knowledge loaded when the task
matches.  This is distinct from agents (which define *who* the AI is —
personas with tool restrictions and model preferences).

Push paths:

* **Cursor** — ``.cursor/skills/<name>/SKILL.md``
* **VS Code** — ``.github/skills/<slug>/SKILL.md``

Unified YAML section format (top-level ``skills:`` key):

.. code-block:: yaml

    skills:
      zelosmcp-pincher:
        description: "Codebase intelligence with pincher."
        paths:
          - "**/*.py"
        targets: [cursor, vscode]
        body: |
          # Pincher — Codebase Intelligence
          ...
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from zelosmcp.framework.assetstore.registry import (
    AssetKind,
    ProjectFile,
    RepoCtx,
    register as _register,
)
from zelosmcp.framework.assetstore.row import AssetRow

logger = logging.getLogger("zelosmcp.assets.skill")

KIND_ID = "skill"

_SLUG_MAX = 64
_DESC_MAX = 1024


# ── Skill summaries ─────────────────────────────────────────────────────


@dataclass
class SkillSummary:
    """Short skill row used by generated rules."""

    name: str
    slug: str
    description: str


async def load_all_skill_summaries(
    store: Any | None,
    backends: list[str],
) -> dict[str, list[SkillSummary]]:
    """Return skill summaries grouped by backend."""
    if store is None:
        return {}
    out: dict[str, list[SkillSummary]] = {}
    for backend in backends:
        rows = await store.list(kind=KIND_ID, backend=backend)
        summaries: list[SkillSummary] = []
        for row in rows:
            meta = row.meta or {}
            summaries.append(SkillSummary(
                name=row.name,
                slug=_slug(row.name),
                description=(meta.get("description") or row.name).strip(),
            ))
        if summaries:
            out[backend] = summaries
    return out


# ── Helpers ─────────────────────────────────────────────────────────────


def _slug(name: str) -> str:
    """Convert *name* to a skill-compatible slug.

    Only lowercase letters, numbers, and hyphens; max 64 characters.
    Must match the parent directory name in the push path.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:_SLUG_MAX] or "skill"


def _cursor_skill_body(row: AssetRow, meta: dict) -> str:
    """Prepend Cursor SKILL.md frontmatter to *row.body*.

    Cursor frontmatter fields: name, description, paths,
    disable-model-invocation, metadata.
    """
    slug = _slug(row.name)
    desc = (meta.get("description") or row.name)[:_DESC_MAX]

    fm_lines = [
        "---",
        f"name: {slug}",
        f"description: {desc}",
    ]

    paths = meta.get("paths")
    if paths:
        if isinstance(paths, list):
            fm_lines.append("paths:")
            for p in paths:
                fm_lines.append(f"  - {p}")
        else:
            fm_lines.append(f"paths: {paths}")

    if meta.get("disable_model_invocation"):
        fm_lines.append("disable-model-invocation: true")

    metadata = meta.get("metadata")
    if metadata and isinstance(metadata, dict):
        fm_lines.append("metadata:")
        for k, v in metadata.items():
            fm_lines.append(f"  {k}: {v}")

    fm_lines.append("---")
    fm_lines.append("")
    return "\n".join(fm_lines) + row.body


def _vscode_skill_body(row: AssetRow, meta: dict) -> str:
    """Prepend VS Code SKILL.md frontmatter to *row.body*.

    VS Code frontmatter fields: name, description, argument-hint,
    user-invocable, disable-model-invocation, context.
    """
    slug = _slug(row.name)
    desc = (meta.get("description") or row.name)[:_DESC_MAX]

    fm_lines = [
        "---",
        f"name: {slug}",
        f"description: {desc}",
    ]

    argument_hint = meta.get("argument_hint")
    if argument_hint:
        # Strip surrounding brackets so the value is never mistaken for
        # a YAML array in the rendered frontmatter.
        hint = argument_hint.strip("[]")
        fm_lines.append(f"argument-hint: {hint}")

    if meta.get("user_invocable") is False:
        fm_lines.append("user-invocable: false")

    if meta.get("disable_model_invocation"):
        fm_lines.append("disable-model-invocation: true")

    context = meta.get("context")
    if context and context != "inline":
        fm_lines.append(f"context: {context}")

    fm_lines.append("---")
    fm_lines.append("")
    return "\n".join(fm_lines) + row.body


# ── Validate / render ────────────────────────────────────────────────────


def _validate(row: AssetRow) -> None:
    if not row.backend:
        raise ValueError("skill asset must have a non-empty 'backend'")
    if not row.name:
        raise ValueError("skill asset must have a non-empty 'name'")


def _render_for_project(row: AssetRow, ctx: RepoCtx) -> list[ProjectFile]:
    meta = row.meta or {}
    push = meta.get("push") or {}
    targets: list[str] = list(meta.get("targets") or ["cursor", "vscode"])

    files: list[ProjectFile] = []
    slug = _slug(row.name)

    if "cursor" in targets:
        cursor_path: str = push.get("cursor") or f".cursor/skills/{slug}/SKILL.md"
        files.append(ProjectFile(
            rel_path=cursor_path,
            body=_cursor_skill_body(row, meta),
            mode="overwrite",
        ))

    if "vscode" in targets:
        vscode_body = _vscode_skill_body(row, meta)
        vscode_path: str = (
            push.get("vscode_vscode") or f".github/skills/{slug}/SKILL.md"
        )
        files.append(ProjectFile(rel_path=vscode_path, body=vscode_body, mode="overwrite"))

    return files


# ── Section parser ───────────────────────────────────────────────────────


def _parse_section(section: dict, backend: str, seed_version: int) -> list[AssetRow]:
    """Parse the ``skills:`` section dict from a unified YAML file."""
    rows: list[AssetRow] = []

    if not isinstance(section, dict):
        return rows

    for skill_name, skill_data in section.items():
        if not isinstance(skill_data, dict):
            continue

        body = skill_data.get("body") or ""
        slug = _slug(skill_name)
        meta: dict[str, Any] = {
            "name": skill_data.get("name") or skill_name,
            "description": skill_data.get("description", ""),
            "targets": skill_data.get("targets") or ["cursor", "vscode"],
            "push": skill_data.get("push") or {
                "cursor": f".cursor/skills/{slug}/SKILL.md",
                "vscode_vscode": f".github/skills/{slug}/SKILL.md",
            },
        }

        # Optional fields
        for key in ("paths", "disable_model_invocation", "argument_hint",
                     "context", "user_invocable", "metadata"):
            if key in skill_data:
                meta[key] = skill_data[key]

        rows.append(AssetRow(
            kind=KIND_ID,
            backend=backend,
            name=skill_name,
            target="cursor",
            body=body,
            meta=meta,
            source="seed",
            seed_version=seed_version,
        ))

    return rows


def dump_section(rows: list[AssetRow]) -> dict:
    """Convert skill rows back into the unified YAML section dict."""
    result: dict = {}
    for row in rows:
        meta = row.meta or {}
        entry: dict = {
            "body": row.body,
        }
        if meta.get("description"):
            entry["description"] = meta["description"]
        if meta.get("targets"):
            entry["targets"] = meta["targets"]
        if meta.get("push"):
            entry["push"] = meta["push"]
        for key in ("paths", "disable_model_invocation", "argument_hint",
                     "context", "user_invocable", "metadata"):
            if meta.get(key) is not None:
                entry[key] = meta[key]
        result[row.name] = entry
    return result


SKILL_KIND = AssetKind(
    id=KIND_ID,
    section_key="skills",
    label="Skills",
    description=(
        "Agent Skill definitions — on-demand knowledge modules that auto-load "
        "by task relevance. Pushed to `.cursor/skills/<name>/SKILL.md` (Cursor) "
        "and `.github/skills/<slug>/SKILL.md` (VS Code)."
    ),
    parse_section=_parse_section,
    validate=_validate,
    render_for_project=_render_for_project,
)

_register(SKILL_KIND)
