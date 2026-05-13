"""``extension`` asset kind.

Extension assets describe UI buttons/actions that invoke an MCP tool
through the zelosMCP aggregator.

Unified YAML section format (top-level ``extensions:`` key):

.. code-block:: yaml

    extensions:
      index_project:
        label: "Index in pincher"
        tool: index
        args_template: { path: "{ctx.repo.ro_path}" }
        targets: [repos_row]
        requires_running: true

``type: link`` opens a URL instead of calling a tool:

.. code-block:: yaml

    view_dashboard:
      type: link
      href: "{ctx.proxy.mount}/v1/dashboard"
      targets: [server_details]

The ``meta`` column of the AssetRow stores the full structured definition
so the invoke dispatcher and UI builder can read it without re-parsing.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from zelosmcp.framework.assetstore.registry import AssetKind, register as _register
from zelosmcp.framework.assetstore.row import AssetRow

logger = logging.getLogger("zelosmcp.assets.extension")

KIND_ID = "extension"

_VALID_TARGETS = frozenset({
    "repos_row",
    "server_details",
    "server_row",
    "assets_panel",
})


def _validate(row: AssetRow) -> None:
    if not row.backend:
        raise ValueError("extension asset must have a non-empty 'backend'")
    if not row.name:
        raise ValueError("extension asset must have a non-empty 'name'")
    meta = row.meta or {}
    ext_type = meta.get("type", "tool")
    if ext_type == "link":
        if not meta.get("href"):
            raise ValueError(f"extension '{row.name}': link type requires 'href'")
    elif ext_type == "tool":
        if not meta.get("tool"):
            raise ValueError(f"extension '{row.name}': tool type requires 'tool' name")


def _parse_section(section: dict, backend: str, seed_version: int) -> list[AssetRow]:
    """Parse the ``extensions:`` section dict from a unified YAML file."""
    rows: list[AssetRow] = []

    if not isinstance(section, dict):
        return rows

    for ext_name, ext_data in section.items():
        if not isinstance(ext_data, dict):
            logger.warning(
                "extension parse_section: '%s' has unexpected format; skipping", ext_name
            )
            continue

        ext_type = ext_data.get("type", "tool")
        meta: dict[str, Any] = {
            "type": ext_type,
            "label": ext_data.get("label", ext_name),
            "description": ext_data.get("description", ""),
            "targets": ext_data.get("targets") or [],
            "requires_running": bool(ext_data.get("requires_running", True)),
            "confirm": bool(ext_data.get("confirm", False)),
        }

        if ext_type == "tool":
            meta["tool"] = ext_data.get("tool") or ext_name
            meta["args_template"] = ext_data.get("args_template") or {}
            meta["success"] = ext_data.get("success") or {}
            meta["error"] = ext_data.get("error") or {}
        elif ext_type == "link":
            meta["href"] = ext_data.get("href", "")

        body = json.dumps(ext_data, indent=2)
        rows.append(AssetRow(
            kind=KIND_ID,
            backend=backend,
            name=ext_name,
            target="",
            body=body,
            meta=meta,
            source="seed",
            seed_version=seed_version,
        ))

    return rows


def dump_section(rows: list[AssetRow]) -> dict:
    """Convert extension rows back into the unified YAML section dict."""
    result: dict = {}
    for row in rows:
        try:
            result[row.name] = json.loads(row.body)
        except (ValueError, TypeError):
            result[row.name] = {"label": row.name}
    return result


EXTENSION_KIND = AssetKind(
    id=KIND_ID,
    section_key="extensions",
    label="Extensions",
    description=(
        "UI action buttons that invoke MCP tools or open links. "
        "Shown inline in the Repos panel, Server Details view, "
        "and the Assets pane."
    ),
    parse_section=_parse_section,
    validate=_validate,
    render_for_project=None,  # Extensions run in-UI; not pushed to repos
)

_register(EXTENSION_KIND)
