"""YAML serialisation / deserialisation helpers for the asset store.

:func:`dump_backend_as_yaml` renders a backend's current DB rows as a
single unified YAML document (matching the file schema) that can be
sent to the browser or exported to disk.

:func:`parse_backend_yaml` parses a YAML text body, validates it against
:data:`~schema.ASSET_FILE_SCHEMA`, and returns a list of
:class:`~row.AssetRow` objects ready to upsert into the store.
"""
from __future__ import annotations

import logging
from typing import Any

import yaml

from zelosmcp.framework.assetstore.row import AssetRow
from zelosmcp.framework.assetstore.schema import SchemaError, validate_asset_file

logger = logging.getLogger("zelosmcp.assets.yaml_io")


class YAMLValidationError(ValueError):
    """Raised by :func:`parse_backend_yaml` when the document is invalid."""

    def __init__(self, errors: list[SchemaError]) -> None:
        super().__init__(f"{len(errors)} schema error(s)")
        self.errors = errors


async def dump_backend_as_yaml(store: Any, backend: str) -> str:
    """Render the backend's current DB rows as a unified YAML document.

    The output matches the file schema used by the seeder, so the user
    can download it, edit it, and re-upload it via the YAML editor API.

    Parameters
    ----------
    store:
        An open :class:`~sqlite.SQLiteAssetStore`.
    backend:
        The backend name to dump.

    Returns
    -------
    UTF-8 YAML text.
    """
    from zelosmcp.framework.assetstore import registry as _registry

    # Build a section lookup: section_key -> kind
    section_lookup = {
        kind.section_key: kind
        for kind in _registry.known()
        if kind.section_key
    }

    rows = await store.list(backend=backend)
    if not rows:
        # Return a minimal valid template
        seed_version = 1
    else:
        seed_version = max(
            (r.seed_version or 0 for r in rows),
            default=1,
        )
        seed_version = max(seed_version, 1)

    doc: dict[str, Any] = {
        "backend": backend,
        "seed_version": seed_version,
    }

    # Group rows by kind.section_key, then delegate to the kind's dump_section
    from zelosmcp.framework.assetstore.kinds import rule as _rule_mod
    from zelosmcp.framework.assetstore.kinds import extension as _ext_mod
    from zelosmcp.framework.assetstore.kinds import agent as _agent_mod
    from zelosmcp.framework.assetstore.kinds import hook as _hook_mod
    from zelosmcp.framework.assetstore.kinds import skill as _skill_mod

    kind_dump_map = {
        "rule": ("rules", _rule_mod.dump_section),
        "extension": ("extensions", _ext_mod.dump_section),
        "agent": ("agents", _agent_mod.dump_section),
        "hook": ("hooks", _hook_mod.dump_section),
        "skill": ("skills", _skill_mod.dump_section),
    }

    rows_by_kind: dict[str, list[AssetRow]] = {}
    for row in rows:
        rows_by_kind.setdefault(row.kind, []).append(row)

    for kind_id, (section_key, dump_fn) in kind_dump_map.items():
        kind_rows = rows_by_kind.get(kind_id, [])
        section = dump_fn(kind_rows)
        doc[section_key] = section

    return yaml.dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False)


def parse_backend_yaml(
    text: str,
    backend: str | None = None,
    *,
    source: str = "user",
) -> list[AssetRow]:
    """Parse a YAML text body and return :class:`AssetRow` objects.

    Parameters
    ----------
    text:
        Raw YAML document text.
    backend:
        When provided, the parsed ``backend`` field must match; also used
        for schema cross-validation.
    source:
        Row source stamp — ``"user"`` (default) or ``"seed"``.

    Raises
    ------
    :class:`YAMLValidationError`
        When the document fails schema validation.
    :class:`yaml.YAMLError`
        When the text is not valid YAML.
    """
    from zelosmcp.framework.assetstore import registry as _registry

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise YAMLValidationError([
            SchemaError(path="", message="document must be a YAML mapping")
        ])

    errors = validate_asset_file(data, backend_name=backend)
    if errors:
        raise YAMLValidationError(errors)

    doc_backend = data.get("backend", backend or "")
    seed_version = data.get("seed_version", 1)

    section_map = {
        kind.section_key: kind
        for kind in _registry.known()
        if kind.section_key and kind.parse_section is not None
    }

    rows: list[AssetRow] = []
    for section_key, kind in section_map.items():
        section_data = data.get(section_key)
        if section_data is None or not isinstance(section_data, dict):
            continue
        parsed = kind.parse_section(section_data, doc_backend, seed_version)
        for row in parsed:
            row.source = source
        rows.extend(parsed)

    return rows


def validate_yaml_text(
    text: str,
    backend: str | None = None,
) -> list[SchemaError]:
    """Parse and validate YAML text; return errors without raising.

    Returns an empty list when the document is valid.
    A parse error (invalid YAML) becomes a single :class:`SchemaError`
    with ``path=""`` so callers don't need to handle two different
    exception types.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [SchemaError(path="", message=f"YAML parse error: {exc}")]

    if not isinstance(data, dict):
        return [SchemaError(path="", message="document must be a YAML mapping")]

    return validate_asset_file(data, backend_name=backend)
