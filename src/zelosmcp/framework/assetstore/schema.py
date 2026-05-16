"""JSON Schema for the unified per-backend asset YAML format.

:data:`ASSET_FILE_SCHEMA` validates the top-level document structure and
each kind's section with ``additionalProperties: false`` at every level
so that typos like ``extentions:`` or ``playbook_readonly:`` are surfaced
as hard errors rather than silently no-op-seeding.

:func:`validate_asset_file` returns a list of :class:`SchemaError` objects
(empty when the document is valid).  It is called by:

- :func:`~yaml_io.parse_backend_yaml` before any DB writes.
- ``POST /api/assets/yaml/{backend}/validate`` for live client-side lint.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import jsonschema
import jsonschema.validators

logger = logging.getLogger("zelosmcp.assets.schema")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SECTION_BODY_ENTRY = {
    "type": "object",
    "required": ["body"],
    "additionalProperties": False,
    "properties": {
        "body": {"type": "string"},
        "targets": {
            "type": "array",
            "items": {"enum": ["cursor", "vscode"]},
        },
    },
}

_TOOL_INSTRUCTION_ENTRY = {
    "type": "object",
    "required": ["body"],
    "additionalProperties": False,
    "properties": {
        "body": {"type": "string"},
    },
}

# Pattern matching all known rule section names.
_RULE_SECTION_PATTERN = (
    "^(playbook|compressed_rules|directive)_(read_only|read_write)$"
    "|^(self_check_gate|directive_tool_use_priority|directive_path_translation)$"
    "|^playbook_.*$"   # allow custom playbook_ variants
)

_RULES_SECTION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sections": {
            "type": "object",
            "additionalProperties": _SECTION_BODY_ENTRY,
        },
        "tool_instructions": {
            "type": "object",
            "additionalProperties": _TOOL_INSTRUCTION_ENTRY,
        },
    },
}

_EXTENSION_ENTRY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label": {"type": "string"},
        "description": {"type": "string"},
        "type": {"enum": ["tool", "link"]},
        "tool": {"type": "string"},
        "href": {"type": "string"},
        "args_template": {"type": "object"},
        "targets": {
            "type": "array",
            "items": {
                "enum": ["repos_row", "server_details", "server_row", "assets_panel"],
            },
        },
        "requires_running": {"type": "boolean"},
        "confirm": {"type": "boolean"},
        "success": {"type": "object"},
        "error": {"type": "object"},
    },
}

_EXTENSIONS_SECTION = {
    "type": "object",
    "additionalProperties": _EXTENSION_ENTRY,
}

_AGENT_ENTRY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "tools": {
            "type": "array",
            "items": {"type": "string"},
        },
        "model": {"type": "string"},
        "readonly": {"type": "boolean"},
        "is_background": {"type": "boolean"},
        "agents": {
            "oneOf": [
                {"type": "array", "items": {"type": "string"}},
                {"const": "*"},
            ],
        },
        "user_invocable": {"type": "boolean"},
        "disable_model_invocation": {"type": "boolean"},
        "handoffs": {
            "type": "array",
            "items": {"type": "object"},
        },
        "targets": {
            "type": "array",
            "items": {"enum": ["cursor", "vscode"]},
        },
        "push": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "body": {"type": "string"},
    },
}

_AGENTS_SECTION = {
    "type": "object",
    "additionalProperties": _AGENT_ENTRY,
}

_HOOK_ENTRY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "event": {"type": "string"},
        "command": {"type": "string"},
        "targets": {
            "type": "array",
            "items": {"enum": ["cursor", "vscode"]},
        },
    },
    "required": ["event", "command"],
}

_HOOKS_SECTION = {
    "type": "object",
    "additionalProperties": _HOOK_ENTRY,
}

_SKILL_ENTRY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "targets": {
            "type": "array",
            "items": {"enum": ["cursor", "vscode"]},
        },
        "push": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "body": {"type": "string"},
    },
}

_SKILLS_SECTION = {
    "type": "object",
    "additionalProperties": _SKILL_ENTRY,
}

_PROMPT_ARG_ENTRY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "required": {"type": "boolean"},
    },
    "required": ["name"],
}

_PROMPT_ENTRY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "args": {
            "type": "array",
            "items": _PROMPT_ARG_ENTRY,
        },
        "targets": {
            "type": "array",
            "items": {"enum": ["cursor"]},
        },
        "push": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "body": {"type": "string"},
    },
}

_PROMPTS_SECTION = {
    "type": "object",
    "additionalProperties": _PROMPT_ENTRY,
}

ASSET_FILE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["backend", "seed_version"],
    "additionalProperties": False,
    "properties": {
        "backend": {
            "type": "string",
            "pattern": "^[a-zA-Z][a-zA-Z0-9_.-]*$",
            "minLength": 1,
        },
        "seed_version": {
            "type": "integer",
            "minimum": 0,
        },
        "rules": _RULES_SECTION,
        "extensions": _EXTENSIONS_SECTION,
        "agents": _AGENTS_SECTION,
        "hooks": _HOOKS_SECTION,
        "prompts": _PROMPTS_SECTION,
        "skills": _SKILLS_SECTION,
    },
}

# ---------------------------------------------------------------------------
# Validator helper
# ---------------------------------------------------------------------------

_VALIDATOR_CLASS = jsonschema.validators.validator_for(ASSET_FILE_SCHEMA)
_VALIDATOR = _VALIDATOR_CLASS(ASSET_FILE_SCHEMA)


@dataclass
class SchemaError:
    """One schema validation error."""

    path: str        # dot-notation path, e.g. "extensions.index_project.targets[0]"
    message: str     # human-readable description from jsonschema
    line: int | None = None  # source line (only populated when ruamel.yaml is available)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"path": self.path, "message": self.message}
        if self.line is not None:
            d["line"] = self.line
        return d


def _error_path(error: Any) -> str:
    """Convert a jsonschema ValidationError's ``absolute_path`` to a dot-path."""
    parts: list[str] = []
    for segment in error.absolute_path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            if parts and not parts[-1].startswith("["):
                parts.append(f".{segment}")
            else:
                parts.append(str(segment))
    return "".join(parts).lstrip(".")


def validate_asset_file(
    data: dict[str, Any],
    *,
    backend_name: str | None = None,
) -> list[SchemaError]:
    """Validate *data* against :data:`ASSET_FILE_SCHEMA`.

    Parameters
    ----------
    data:
        Decoded YAML document (``yaml.safe_load`` output).
    backend_name:
        When provided, also checks that ``data["backend"]`` matches this
        value.

    Returns
    -------
    List of :class:`SchemaError` objects — empty when the document is
    valid.
    """
    errors: list[SchemaError] = []

    for err in sorted(_VALIDATOR.iter_errors(data), key=lambda e: list(e.absolute_path)):
        errors.append(SchemaError(path=_error_path(err), message=err.message))

    if backend_name and isinstance(data, dict):
        doc_backend = data.get("backend")
        if doc_backend and doc_backend != backend_name:
            errors.append(SchemaError(
                path="backend",
                message=(
                    f"document backend '{doc_backend}' does not match "
                    f"URL backend '{backend_name}'"
                ),
            ))

    return errors
