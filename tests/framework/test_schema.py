"""Unit tests for the unified backend YAML schema validator."""
from __future__ import annotations

import pytest
import yaml

from zelosmcp.framework.assetstore.schema import validate_asset_file, SchemaError


def _validate(data: dict, backend_name=None) -> list[SchemaError]:
    return validate_asset_file(data, backend_name=backend_name)


def _paths(errors: list[SchemaError]) -> list[str]:
    return [e.path for e in errors]


class TestValidDocuments:
    def test_minimal_valid(self):
        assert _validate({"backend": "pincher", "seed_version": 1}) == []

    def test_full_valid_document(self):
        data = yaml.safe_load("""
backend: pincher
seed_version: 2
rules:
  sections:
    playbook_read_only:
      body: "### pincher"
  tool_instructions:
    search:
      body: "Search tip"
extensions:
  index_project:
    tool: index
    targets: [repos_row]
agents:
  reviewer:
    body: "I am an agent"
hooks:
  lint:
    event: pre_commit
    command: "ruff check ."
""")
        assert _validate(data) == []

    def test_global_yaml_file_is_valid(self):
        import pathlib
        global_yaml = pathlib.Path("configs/assets/global.yaml")
        if not global_yaml.exists():
            pytest.skip("global.yaml not found")
        data = yaml.safe_load(global_yaml.read_text())
        assert _validate(data) == []

    def test_pincher_yaml_file_is_valid(self):
        import pathlib
        pincher_yaml = pathlib.Path("configs/assets/pincher.yaml")
        if not pincher_yaml.exists():
            pytest.skip("pincher.yaml not found")
        data = yaml.safe_load(pincher_yaml.read_text())
        assert _validate(data) == []


class TestInvalidDocuments:
    def test_missing_backend(self):
        errors = _validate({"seed_version": 1})
        assert any("backend" in e.path or "backend" in e.message for e in errors)

    def test_missing_seed_version(self):
        errors = _validate({"backend": "x"})
        assert any("seed_version" in e.path or "seed_version" in e.message for e in errors)

    def test_misspelled_top_level_key(self):
        errors = _validate({
            "backend": "pincher",
            "seed_version": 1,
            "extentions": {},          # typo: should be 'extensions'
        })
        assert len(errors) >= 1
        assert any("extentions" in e.message or "additional" in e.message.lower() for e in errors)

    def test_misspelled_agents_key(self):
        errors = _validate({
            "backend": "x",
            "seed_version": 1,
            "agnts": {},               # typo: should be 'agents'
        })
        assert any("agnts" in e.message or "additional" in e.message.lower() for e in errors)

    def test_wrong_seed_version_type(self):
        errors = _validate({"backend": "x", "seed_version": "1"})
        assert any("seed_version" in e.path or "integer" in e.message for e in errors)

    def test_bad_extension_target_enum(self):
        data = {
            "backend": "x",
            "seed_version": 1,
            "extensions": {
                "ext": {
                    "tool": "do_it",
                    "targets": ["invalid_target"],
                }
            },
        }
        errors = _validate(data)
        assert len(errors) >= 1

    def test_rule_section_missing_body(self):
        data = {
            "backend": "x",
            "seed_version": 1,
            "rules": {
                "sections": {
                    "playbook_read_only": {"description": "no body key"},
                }
            },
        }
        errors = _validate(data)
        assert len(errors) >= 1

    def test_hook_missing_required_event(self):
        data = {
            "backend": "x",
            "seed_version": 1,
            "hooks": {
                "lint": {"command": "ruff check ."},  # missing event
            },
        }
        errors = _validate(data)
        assert any("event" in e.message for e in errors)

    def test_backend_mismatch_is_error(self):
        errors = _validate(
            {"backend": "pincher", "seed_version": 1},
            backend_name="kubernetes",
        )
        assert any("pincher" in e.message and "kubernetes" in e.message for e in errors)

    def test_no_error_when_backend_matches(self):
        errors = _validate(
            {"backend": "pincher", "seed_version": 1},
            backend_name="pincher",
        )
        assert errors == []
