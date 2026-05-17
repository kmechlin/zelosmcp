"""``rule`` asset kind.

Rule assets contain the per-backend markdown playbooks and per-tool
guidance blocks that the Cursor rule generator embeds in
``.cursor/rules/zelosmcp.mdc`` and ``.github/copilot-instructions.md``.

Unified YAML section format (top-level ``rules:`` key in the per-backend
file):

.. code-block:: yaml

    rules:
      sections:
        playbook_read_only:
          body: |
            ### `pincher` ...
        playbook_read_write:
          body: |
            ...
      tool_instructions:
        search:
          body: |
            Filter with `kind=Function` ...

Section entries become ``AssetRow(kind='rule', name='<section>')`` rows.
Tool-instruction entries become ``AssetRow(kind='rule', name='tool:<tool>')``
rows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from zelosmcp.framework.assetstore.registry import (
    AssetKind,
    ProjectFile,
    RepoCtx,
    register as _register,
)
from zelosmcp.framework.assetstore.row import AssetRow

logger = logging.getLogger("zelosmcp.assets.rule")

KIND_ID = "rule"

# ── BackendRuleAssets ──────────────────────────────────────────────────


@dataclass
class BackendRuleAssets:
    """Resolved rule content for one MCP backend."""

    backend: str
    compressed_rules: str = ""
    tool_instructions: dict[str, str] = field(default_factory=dict)
    directive_read_only: str = ""
    directive_read_write: str = ""
    directive_tool_use_priority: str = ""
    directive_path_translation: str = ""
    self_check_gate: str = ""


async def load_backend_rule_assets(
    store: Any,
    backend: str,
) -> BackendRuleAssets:
    """Load rule assets for one backend, falling back to ``zelosmcp`` global."""
    rows_backend = await store.list(kind=KIND_ID, backend=backend)
    rows_global = await store.list(kind=KIND_ID, backend="zelosmcp")

    by_name: dict[str, str] = {}
    for row in rows_global:
        by_name[row.name] = row.body
    for row in rows_backend:
        by_name[row.name] = row.body

    tool_instructions: dict[str, str] = {}
    for name, body in by_name.items():
        if name.startswith("tool:"):
            tool_instructions[name[5:]] = body

    return BackendRuleAssets(
        backend=backend,
        compressed_rules=by_name.get("compressed_rules", ""),
        tool_instructions=tool_instructions,
        directive_read_only=by_name.get("directive_read_only", ""),
        directive_read_write=by_name.get("directive_read_write", ""),
        directive_tool_use_priority=by_name.get("directive_tool_use_priority", ""),
        directive_path_translation=by_name.get("directive_path_translation", ""),
        self_check_gate=by_name.get("self_check_gate", ""),
    )


async def load_all_rule_assets(
    store: Any | None,
    backends: list[str],
) -> dict[str, BackendRuleAssets]:
    if store is None:
        return {}
    return {b: await load_backend_rule_assets(store, b) for b in backends}


# ── Section parser ─────────────────────────────────────────────────────


def _validate(row: AssetRow) -> None:
    if not row.backend:
        raise ValueError("rule asset must have a non-empty 'backend'")
    if not row.name:
        raise ValueError("rule asset must have a non-empty 'name'")


def _render_for_project(row: AssetRow, ctx: RepoCtx) -> list[ProjectFile]:
    """Push rule assets to the target repo.

    The body stored is the pre-rendered markdown blob; the push writer
    writes it directly.  A ``target`` of ``""`` or ``"cursor"`` writes the
    Cursor ``.mdc`` file.  A ``target`` of ``""`` or ``"vscode"`` writes
    the ``.github/copilot-instructions.md`` path for VS Code workspace
    discovery.
    """
    files: list[ProjectFile] = []
    target = row.target or ""
    if target in ("cursor", ""):
        files.append(ProjectFile(
            rel_path=".cursor/rules/zelosmcp.mdc",
            body=row.body,
            mode="overwrite",
        ))
    if target in ("vscode", ""):
        files.append(ProjectFile(
            rel_path=".github/copilot-instructions.md",
            body=row.body,
            mode="overwrite",
        ))
    return files


def _parse_section(section: dict, backend: str, seed_version: int) -> list[AssetRow]:
    """Parse the ``rules:`` section dict from a unified YAML file."""
    rows: list[AssetRow] = []

    sections = section.get("sections") or {}
    if isinstance(sections, dict):
        for section_name, section_data in sections.items():
            if isinstance(section_data, dict):
                body = section_data.get("body") or ""
                targets = section_data.get("targets") or [""]
            elif isinstance(section_data, str):
                body = section_data
                targets = [""]
            else:
                logger.warning(
                    "rule parse_section: section '%s' has unexpected format; skipping",
                    section_name,
                )
                continue

            if not isinstance(targets, list):
                targets = [targets]

            for target in targets:
                rows.append(AssetRow(
                    kind=KIND_ID,
                    backend=backend,
                    name=section_name,
                    target=target or "",
                    body=body,
                    meta={},
                    source="seed",
                    seed_version=seed_version,
                ))

    tool_instructions = section.get("tool_instructions") or {}
    if isinstance(tool_instructions, dict):
        for tool_name, ti_data in tool_instructions.items():
            if isinstance(ti_data, dict):
                body = ti_data.get("body") or ""
            elif isinstance(ti_data, str):
                body = ti_data
            else:
                continue

            rows.append(AssetRow(
                kind=KIND_ID,
                backend=backend,
                name=f"tool:{tool_name}",
                target="",
                body=body,
                meta={"tool": tool_name},
                source="seed",
                seed_version=seed_version,
            ))

    return rows


def dump_section(rows: list[AssetRow]) -> dict:
    """Convert a list of rule rows back into the unified YAML section dict."""
    sections: dict = {}
    tool_instructions: dict = {}

    for row in rows:
        if row.name.startswith("tool:"):
            tool_name = row.name[5:]
            tool_instructions[tool_name] = {"body": row.body}
        else:
            entry: dict = {"body": row.body}
            if row.target:
                entry["targets"] = [row.target]
            sections[row.name] = entry

    result: dict = {}
    if sections:
        result["sections"] = sections
    if tool_instructions:
        result["tool_instructions"] = tool_instructions
    return result


RULE_KIND = AssetKind(
    id=KIND_ID,
    section_key="rules",
    label="Rules",
    description=(
        "Cursor `.mdc` and VS Code `copilot-instructions.md` rule content — "
        "playbooks, per-tool guidance, and access-mode directives.  "
        "VS Code target writes `.github/copilot-instructions.md`."
    ),
    parse_section=_parse_section,
    validate=_validate,
    render_for_project=_render_for_project,
    stub_body="",  # defaults are generated dynamically by ensure_default_assets
)

_register(RULE_KIND)
