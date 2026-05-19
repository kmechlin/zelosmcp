"""Unit tests for the unified seeder driver."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from zelosmcp.framework.assetstore.row import AssetRow
from zelosmcp.framework.assetstore.sqlite import SQLiteAssetStore
from zelosmcp.framework.assetstore.seeder import seed_all


@pytest.fixture
async def store():
    s = SQLiteAssetStore(":memory:")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _write_backend_yaml(
    path: Path,
    backend: str,
    seed_version: int,
    *,
    rules: dict | None = None,
    extensions: dict | None = None,
    agents: dict | None = None,
    hooks: dict | None = None,
):
    """Write a unified per-backend YAML file to *path*."""
    data: dict = {"backend": backend, "seed_version": seed_version}
    if rules is not None:
        data["rules"] = rules
    if extensions is not None:
        data["extensions"] = extensions
    if agents is not None:
        data["agents"] = agents
    if hooks is not None:
        data["hooks"] = hooks
    (path / f"{backend}.yaml").write_text(yaml.dump(data), encoding="utf-8")


@pytest.mark.asyncio
class TestSeedAll:
    async def test_seeds_rule_rows(self, store):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_backend_yaml(
                root, "pincher", 1,
                rules={"sections": {"playbook_read_only": {"body": "PO content"}}},
            )
            counts = await seed_all(store, config_root=root)
        assert counts.get("rule", 0) >= 1
        row = await store.get("rule", "pincher", "playbook_read_only")
        assert row is not None
        assert "PO content" in row.body

    async def test_tool_instructions_seeded_with_prefix(self, store):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_backend_yaml(
                root, "pincher", 1,
                rules={"tool_instructions": {"search": {"body": "FTS5 BM25 tip"}}},
            )
            await seed_all(store, config_root=root)
        row = await store.get("rule", "pincher", "tool:search")
        assert row is not None
        assert "FTS5" in row.body

    async def test_extension_seeded(self, store):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_backend_yaml(
                root, "pincher", 1,
                extensions={"index_project": {
                    "label": "Index",
                    "tool": "index",
                    "args_template": {"path": "{ctx.repo.ro_path}"},
                    "targets": ["repos_row"],
                }},
            )
            await seed_all(store, config_root=root)
        row = await store.get("extension", "pincher", "index_project")
        assert row is not None
        assert row.meta.get("tool") == "index"

    async def test_idempotent_same_version(self, store):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_backend_yaml(root, "b", 1, rules={"sections": {"x": {"body": "orig"}}})
            await seed_all(store, config_root=root)
            await seed_all(store, config_root=root)  # second pass
        row = await store.get("rule", "b", "x")
        assert row is not None
        assert row.body == "orig"

    async def test_higher_version_overwrites_seed(self, store):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_backend_yaml(root, "b", 1, rules={"sections": {"x": {"body": "v1"}}})
            await seed_all(store, config_root=root)
            _write_backend_yaml(root, "b", 2, rules={"sections": {"x": {"body": "v2"}}})
            await seed_all(store, config_root=root)
        row = await store.get("rule", "b", "x")
        assert row is not None
        assert row.body == "v2"

    async def test_user_row_not_overwritten(self, store):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_backend_yaml(root, "b", 1, rules={"sections": {"x": {"body": "seed"}}})
            await seed_all(store, config_root=root)
            await store.upsert(
                AssetRow(kind="rule", backend="b", name="x", body="user edit", source="user"),
            )
            _write_backend_yaml(root, "b", 2, rules={"sections": {"x": {"body": "seed v2"}}})
            await seed_all(store, config_root=root)
        row = await store.get("rule", "b", "x")
        assert row is not None
        assert row.body == "user edit"

    async def test_missing_yaml_dir_is_empty(self, store):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # No YAML files at all
            counts = await seed_all(store, config_root=root)
        assert all(v == 0 for v in counts.values())

    async def test_multiple_backends_one_pass(self, store):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_backend_yaml(root, "alpha", 1, rules={"sections": {"ro": {"body": "A"}}})
            _write_backend_yaml(root, "beta", 1, rules={"sections": {"ro": {"body": "B"}}})
            await seed_all(store, config_root=root)
        a = await store.get("rule", "alpha", "ro")
        b = await store.get("rule", "beta", "ro")
        assert a.body == "A"
        assert b.body == "B"


@pytest.mark.asyncio
class TestRealYamlFiles:
    """Verify the bundled YAML seed files parse cleanly."""

    async def test_pincher_yaml_seeds(self, store):
        await seed_all(store)
        rules = await store.list(kind="rule", backend="pincher")
        names = {r.name for r in rules}
        assert "tool:architecture" in names
        assert "tool:search" in names

    async def test_pincher_skill_copy_mentions_wrappers(self, store):
        await seed_all(store)
        row = await store.get("skill", "pincher", "codebase-explore", target="cursor")
        assert row is not None
        assert "pincher__invoke_tool" in row.body
        assert "pincher__search_tools" in row.body
        assert "pincher MCP wrappers" in (row.meta or {}).get("description", "")

    async def test_filesystem_yaml_seeds(self, store):
        await seed_all(store)
        rules = await store.list(kind="rule", backend="filesystem")
        names = {r.name for r in rules}
        assert "tool:read_text_file" in names

    async def test_flat_agent_descriptions_reference_skills(self, store):
        await seed_all(store)
        row = await store.get("agent", "zelosmcp", "zelos-agent-vscode", target="cursor")
        assert row is not None
        desc = (row.meta or {}).get("description", "")
        assert "`codebase-explore`" in desc
        assert "`file-operations`" in desc
        assert "`change-blast-radius`" in desc
        assert "`code-review`" in desc

    async def test_global_yaml_seeds(self, store):
        """global.yaml should seed backend=zelosmcp directive rows."""
        await seed_all(store)
        rows = await store.list(kind="rule", backend="zelosmcp")
        names = {r.name for r in rows}
        assert "directive_read_only" in names
        assert "self_check_gate" in names

    async def test_pincher_extension_seeds(self, store):
        await seed_all(store)
        exts = await store.list(kind="extension", backend="pincher")
        names = {r.name for r in exts}
        assert "index_project" in names
