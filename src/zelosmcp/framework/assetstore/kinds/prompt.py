"""``prompt`` asset kind.

Prompt assets contain reusable prompt templates that can be surfaced as
MCP prompts and pushed to editor-specific command locations.

Push paths:

* **Cursor** — ``.cursor/commands/<slug>.md``

Unified YAML section format (top-level ``prompts:`` key):

.. code-block:: yaml

    prompts:
      find-callers:
        description: "Find callers of a symbol."
        args:
          - name: symbol_name
            description: "Function or class name"
            required: true
        targets: [cursor]
        body: |
          Use pincher to trace {{symbol_name}} ...
"""
from __future__ import annotations

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

KIND_ID = "prompt"

_SLUG_MAX = 64
_DESC_MAX = 1024


@dataclass
class PromptArg:
    """One MCP prompt argument."""

    name: str
    description: str = ""
    required: bool = False


def _slug(name: str) -> str:
    """Convert *name* to a prompt/command-compatible slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:_SLUG_MAX] or "prompt"


def _normalise_args(raw: Any) -> list[dict[str, Any]]:
    """Validate-ish YAML arg entries and normalise to JSON dictionaries."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        arg: dict[str, Any] = {
            "name": name,
            "description": str(item.get("description") or ""),
            "required": bool(item.get("required", False)),
        }
        out.append(arg)
    return out


def _cursor_prompt_body(row: AssetRow, meta: dict[str, Any]) -> str:
    """Build a Cursor slash-command file."""
    desc = (meta.get("description") or row.name)[:_DESC_MAX]
    return "\n".join([
        "---",
        f"description: {desc}",
        "---",
        "",
        row.body,
    ])


def _substitute_args(body: str, arguments: dict[str, Any] | None) -> str:
    """Substitute ``{{name}}`` placeholders in *body*.

    This intentionally performs only argument replacement. Compression
    branching remains plain text inside the prompt body so the same
    prompt works in compressed and uncompressed modes.
    """
    arguments = arguments or {}

    def repl(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        value = arguments.get(name)
        return "" if value is None else str(value)

    return re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}", repl, body)


def _prompt_args(meta: dict[str, Any]) -> list[PromptArg]:
    args: list[PromptArg] = []
    for item in meta.get("args") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        args.append(PromptArg(
            name=name,
            description=str(item.get("description") or ""),
            required=bool(item.get("required", False)),
        ))
    return args


async def load_all_prompt_rows(store: Any | None) -> list[AssetRow]:
    """Return every prompt row from the store."""
    if store is None:
        return []
    return await store.list(kind=KIND_ID)


async def find_prompt_row(
    store: Any | None,
    *,
    backend: str,
    name: str,
) -> AssetRow | None:
    """Find a prompt row by backend and slug-or-name."""
    if store is None:
        return None
    rows = await store.list(kind=KIND_ID, backend=backend)
    for row in rows:
        if row.name == name or _slug(row.name) == name:
            return row
    return None


def render_prompt(row: AssetRow, arguments: dict[str, Any] | None = None) -> str:
    """Render a stored prompt body with argument placeholders substituted."""
    return _substitute_args(row.body, arguments)


def prompt_arguments(row: AssetRow) -> list[PromptArg]:
    """Return normalised argument definitions for one row."""
    return _prompt_args(row.meta or {})


def prompt_slug(row: AssetRow) -> str:
    """Return the stable slug for one prompt row."""
    return _slug(row.name)


def prompt_description(row: AssetRow) -> str:
    """Return the short description for one prompt row."""
    meta = row.meta or {}
    return (meta.get("description") or row.name)[:_DESC_MAX]


def _validate(row: AssetRow) -> None:
    if not row.backend:
        raise ValueError("prompt asset must have a non-empty 'backend'")
    if not row.name:
        raise ValueError("prompt asset must have a non-empty 'name'")


def _render_for_project(row: AssetRow, ctx: RepoCtx) -> list[ProjectFile]:
    meta = row.meta or {}
    push = meta.get("push") or {}
    targets: list[str] = list(meta.get("targets") or ["cursor"])
    slug = _slug(row.name)
    files: list[ProjectFile] = []

    if "cursor" in targets:
        cursor_path: str = push.get("cursor") or f".cursor/commands/{slug}.md"
        files.append(ProjectFile(
            rel_path=cursor_path,
            body=_cursor_prompt_body(row, meta),
            mode="overwrite",
        ))

    return files


def _parse_section(section: dict, backend: str, seed_version: int) -> list[AssetRow]:
    """Parse the ``prompts:`` section dict from a unified YAML file."""
    rows: list[AssetRow] = []
    if not isinstance(section, dict):
        return rows

    for prompt_name, prompt_data in section.items():
        if not isinstance(prompt_data, dict):
            continue
        body = prompt_data.get("body") or ""
        slug = _slug(prompt_name)
        meta: dict[str, Any] = {
            "name": prompt_data.get("name") or prompt_name,
            "description": prompt_data.get("description", ""),
            "args": _normalise_args(prompt_data.get("args")),
            "targets": prompt_data.get("targets") or ["cursor"],
            "push": prompt_data.get("push") or {
                "cursor": f".cursor/commands/{slug}.md",
            },
        }
        rows.append(AssetRow(
            kind=KIND_ID,
            backend=backend,
            name=prompt_name,
            target="cursor",
            body=body,
            meta=meta,
            source="seed",
            seed_version=seed_version,
        ))
    return rows


def dump_section(rows: list[AssetRow]) -> dict:
    """Convert prompt rows back into the unified YAML section dict."""
    result: dict = {}
    for row in rows:
        meta = row.meta or {}
        entry: dict = {"body": row.body}
        if meta.get("name") and meta["name"] != row.name:
            entry["name"] = meta["name"]
        if meta.get("description"):
            entry["description"] = meta["description"]
        if meta.get("args"):
            entry["args"] = meta["args"]
        if meta.get("targets"):
            entry["targets"] = meta["targets"]
        if meta.get("push"):
            entry["push"] = meta["push"]
        result[row.name] = entry
    return result


PROMPT_KIND = AssetKind(
    id=KIND_ID,
    section_key="prompts",
    label="Prompts",
    description=(
        "Reusable prompt templates surfaced as MCP prompts and pushed to "
        "`.cursor/commands/<name>.md` for Cursor slash commands."
    ),
    parse_section=_parse_section,
    validate=_validate,
    render_for_project=_render_for_project,
)

_register(PROMPT_KIND)
