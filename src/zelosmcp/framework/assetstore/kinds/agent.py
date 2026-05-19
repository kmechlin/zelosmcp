"""``agent`` asset kind.

Agent assets contain custom agent / subagent definitions.  Agents define
*who* the AI is — personas with tool restrictions, model preferences,
readonly mode, and handoffs between roles.

Push paths:

* **Cursor** — ``.cursor/agents/<name>.md``
* **VS Code / GitHub** — ``.github/agents/<name>.agent.md``

Unified YAML section format (top-level ``agents:`` key):

.. code-block:: yaml

    agents:
      zelosmcp-planner:
        description: "Read-only planning agent."
        tools: ['search', 'web', 'pincher__*']
        model: inherit
        readonly: true
        targets: [cursor, vscode]
        body: |
          You are a planning agent ...
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

# Maximum field lengths.
_SLUG_MAX = 64
_DESC_MAX = 1024


# ── Helpers ─────────────────────────────────────────────────────────────


def _slug(name: str) -> str:
    """Convert *name* to an agent-compatible slug.

    Only lowercase letters, numbers, and hyphens; max 64 characters.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:_SLUG_MAX] or "agent"


def _cursor_agent_body(row: AssetRow, meta: dict) -> str:
    """Build Cursor agent file with YAML frontmatter.

    Cursor agents live at ``.cursor/agents/<name>.md`` with frontmatter:
    name, description, model, readonly, is_background.
    """
    slug = _slug(row.name)
    desc = (meta.get("description") or row.name)[:_DESC_MAX]

    fm_lines = [
        "---",
        f"name: {slug}",
        f"description: {desc}",
    ]

    model = meta.get("model")
    if model:
        fm_lines.append(f"model: {model}")

    if meta.get("readonly"):
        fm_lines.append("readonly: true")

    if meta.get("is_background"):
        fm_lines.append("is_background: true")

    fm_lines.append("---")
    fm_lines.append("")
    return "\n".join(fm_lines) + row.body


def _vscode_agent_body(row: AssetRow, meta: dict) -> str:
    """Build VS Code agent file with YAML frontmatter.

    VS Code agents live at ``.github/agents/<name>.agent.md`` with
    frontmatter: name, description, tools, model, agents,
    user-invocable, disable-model-invocation, handoffs.
    """
    slug = _slug(row.name)
    desc = (meta.get("description") or row.name)[:_DESC_MAX]

    fm_lines = [
        "---",
        f"name: {slug}",
        f"description: {desc}",
    ]

    tools = meta.get("tools")
    if tools and isinstance(tools, list):
        tools_str = ", ".join(f"'{t}'" for t in tools)
        fm_lines.append(f"tools: [{tools_str}]")

    model = meta.get("model")
    if model:
        fm_lines.append(f"model: {model}")

    agents_list = meta.get("agents")
    if agents_list is not None:
        if isinstance(agents_list, list):
            agents_str = ", ".join(f"'{a}'" for a in agents_list)
            fm_lines.append(f"agents: [{agents_str}]")
        elif agents_list == "*":
            fm_lines.append("agents: '*'")

    if meta.get("user_invocable") is False:
        fm_lines.append("user-invocable: false")

    if meta.get("disable_model_invocation"):
        fm_lines.append("disable-model-invocation: true")

    handoffs = meta.get("handoffs")
    if handoffs and isinstance(handoffs, list):
        fm_lines.append("handoffs:")
        for h in handoffs:
            fm_lines.append(f"  - label: {h.get('label', '')}")
            fm_lines.append(f"    agent: {h.get('agent', '')}")
            if h.get("prompt"):
                fm_lines.append(f"    prompt: {h['prompt']}")
            if h.get("send"):
                fm_lines.append(f"    send: {str(h['send']).lower()}")
            if h.get("model"):
                fm_lines.append(f"    model: {h['model']}")

    fm_lines.append("---")
    fm_lines.append("")
    return "\n".join(fm_lines) + row.body


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
    slug = _slug(row.name)

    if "cursor" in targets:
        cursor_path: str = push.get("cursor") or f".cursor/agents/{slug}.md"
        files.append(ProjectFile(
            rel_path=cursor_path,
            body=_cursor_agent_body(row, meta),
            mode="overwrite",
        ))

    if "vscode" in targets:
        vscode_body = _vscode_agent_body(row, meta)
        github_path: str = (
            push.get("vscode_github") or f".github/agents/{slug}.agent.md"
        )
        files.append(ProjectFile(rel_path=github_path, body=vscode_body, mode="overwrite"))

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
                "cursor": f".cursor/agents/{slug}.md",
                "vscode_github": f".github/agents/{slug}.agent.md",
            },
        }

        # Optional agent-specific fields
        for key in ("tools", "model", "readonly", "is_background",
                     "agents", "user_invocable", "disable_model_invocation",
                     "handoffs"):
            if key in agent_data:
                meta[key] = agent_data[key]

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
            "body": row.body,
        }
        if meta.get("name") and meta["name"] != row.name:
            entry["name"] = meta["name"]
        if meta.get("description"):
            entry["description"] = meta["description"]
        if meta.get("targets"):
            entry["targets"] = meta["targets"]
        if meta.get("push"):
            entry["push"] = meta["push"]
        for key in ("tools", "model", "readonly", "is_background",
                     "agents", "user_invocable", "disable_model_invocation",
                     "handoffs"):
            if meta.get(key) is not None:
                entry[key] = meta[key]
        result[row.name] = entry
    return result


AGENT_KIND = AssetKind(
    id=KIND_ID,
    section_key="agents",
    label="Agents",
    description=(
        "Custom agent / subagent definitions — personas with tool restrictions, "
        "model preferences, and handoffs. Pushed to `.cursor/agents/<name>.md` "
        "(Cursor) and `.github/agents/<name>.agent.md` (VS Code)."
    ),
    parse_section=_parse_section,
    validate=_validate,
    render_for_project=_render_for_project,
)

_register(AGENT_KIND)
