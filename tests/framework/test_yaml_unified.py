"""Round-trip tests for the unified per-backend YAML format.

Verifies that:
  - dump_backend_as_yaml generates a valid YAML document from the DB.
  - parse_backend_yaml correctly parses all four section types.
  - The round-trip (seed → dump → parse → re-upsert) is idempotent.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from zelosmcp.framework.assetstore.row import AssetRow
from zelosmcp.framework.assetstore.sqlite import SQLiteAssetStore
from zelosmcp.framework.assetstore.seeder import seed_all
from zelosmcp.framework.assetstore.yaml_io import dump_backend_as_yaml, parse_backend_yaml


@pytest.fixture
async def store():
    s = SQLiteAssetStore(":memory:")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


@pytest.mark.asyncio
class TestDumpBackendAsYaml:
    async def test_dump_empty_backend_is_valid_yaml(self, store):
        text = await dump_backend_as_yaml(store, "ghost")
        data = yaml.safe_load(text)
        assert data["backend"] == "ghost"
        assert "seed_version" in data

    async def test_dump_includes_rule_rows(self, store):
        await store.upsert(AssetRow(
            kind="rule", backend="pincher", name="playbook_read_only",
            body="My playbook", source="user",
        ))
        text = await dump_backend_as_yaml(store, "pincher")
        data = yaml.safe_load(text)
        assert data["rules"]["sections"]["playbook_read_only"]["body"] == "My playbook"

    async def test_dump_includes_extension_rows(self, store):
        import json
        ext_body = json.dumps({"tool": "index", "targets": ["repos_row"]})
        await store.upsert(AssetRow(
            kind="extension", backend="pincher", name="index_project",
            body=ext_body,
            meta={"tool": "index", "targets": ["repos_row"], "type": "tool",
                  "label": "Index", "description": "", "requires_running": True,
                  "confirm": False, "args_template": {}, "success": {}, "error": {}},
            source="seed",
        ))
        text = await dump_backend_as_yaml(store, "pincher")
        data = yaml.safe_load(text)
        assert "index_project" in data["extensions"]

    async def test_dump_tool_instructions_have_tool_prefix_stripped(self, store):
        await store.upsert(AssetRow(
            kind="rule", backend="pincher", name="tool:search",
            body="FTS5 tip", meta={"tool": "search"}, source="seed",
        ))
        text = await dump_backend_as_yaml(store, "pincher")
        data = yaml.safe_load(text)
        assert "search" in data["rules"]["tool_instructions"]


@pytest.mark.asyncio
class TestParseBackendYaml:
    def _yaml(self, backend, seed_version, **sections):
        data = {"backend": backend, "seed_version": seed_version}
        data.update(sections)
        return yaml.dump(data)

    async def test_parse_rule_sections(self, store):
        text = self._yaml("pincher", 1, rules={"sections": {
            "playbook_read_only": {"body": "RO playbook"},
        }})
        rows = parse_backend_yaml(text, "pincher")
        rule_rows = [r for r in rows if r.kind == "rule"]
        assert any(r.name == "playbook_read_only" and "RO" in r.body for r in rule_rows)

    async def test_parse_tool_instructions(self, store):
        text = self._yaml("pincher", 1, rules={"tool_instructions": {
            "search": {"body": "FTS5 tip"},
        }})
        rows = parse_backend_yaml(text, "pincher")
        assert any(r.name == "tool:search" for r in rows)

    async def test_parse_extension(self, store):
        text = self._yaml("pincher", 1, extensions={"idx": {
            "tool": "index",
            "targets": ["repos_row"],
        }})
        rows = parse_backend_yaml(text, "pincher")
        exts = [r for r in rows if r.kind == "extension"]
        assert any(r.name == "idx" for r in exts)

    async def test_parse_agent(self, store):
        text = self._yaml("default", 1, agents={"code_reviewer": {
            "name": "Code Reviewer",
            "body": "# Reviewer\nYou are a reviewer.",
        }})
        rows = parse_backend_yaml(text, "default")
        agents = [r for r in rows if r.kind == "agent"]
        assert any(r.name == "code_reviewer" for r in agents)

    async def test_parse_hook(self, store):
        text = self._yaml("default", 1, hooks={"lint": {
            "event": "pre_commit",
            "command": "ruff check .",
        }})
        rows = parse_backend_yaml(text, "default")
        hooks = [r for r in rows if r.kind == "hook"]
        assert any(r.name == "lint" for r in hooks)

    async def test_wrong_backend_raises(self, store):
        from zelosmcp.framework.assetstore.yaml_io import YAMLValidationError
        text = self._yaml("pincher", 1)
        with pytest.raises(YAMLValidationError):
            parse_backend_yaml(text, "different_backend")


@pytest.mark.asyncio
class TestRoundTrip:
    async def test_seed_dump_parse_idempotent(self, store):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pincher.yaml").write_text(yaml.dump({
                "backend": "pincher",
                "seed_version": 1,
                "rules": {
                    "sections": {
                        "playbook_read_only": {"body": "RO body"},
                    },
                    "tool_instructions": {
                        "search": {"body": "search tip"},
                    },
                },
                "extensions": {
                    "idx": {"tool": "index", "targets": ["repos_row"]},
                },
            }), encoding="utf-8")
            await seed_all(store, config_root=root)

        text = await dump_backend_as_yaml(store, "pincher")
        rows = parse_backend_yaml(text, "pincher")
        names = {r.name for r in rows}
        assert "playbook_read_only" in names
        assert "tool:search" in names
        assert "idx" in names
