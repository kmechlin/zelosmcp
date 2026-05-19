"""Tests for the dynamic default rule generator."""
from __future__ import annotations

import pytest

from zelosmcp.framework.assetstore.defaults import generate_default_rule_rows
from zelosmcp.framework.assetstore.sqlite import SQLiteAssetStore
from zelosmcp.framework.assetstore.row import AssetRow


@pytest.fixture
async def store():
    s = SQLiteAssetStore(":memory:")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _tool(name: str, **annotations) -> dict:
    t = {"name": name, "description": f"{name} tool", "inputSchema": {
        "type": "object",
        "properties": {"arg1": {}, "arg2": {}},
        "required": ["arg1"],
    }}
    if annotations:
        t["annotations"] = annotations
    return t


class TestGenerateDefaultRuleRows:
    def test_returns_per_tool_rows(self):
        tools = [
            _tool("list_pods", readOnlyHint=True),
            _tool("delete_pod", destructiveHint=True),
            _tool("create_pod"),
        ]
        rows = generate_default_rule_rows("kubernetes", tools)
        names = {r.name for r in rows}
        assert "tool:list_pods" in names
        assert "tool:delete_pod" in names
        assert "tool:create_pod" in names

    def test_no_playbook_rows_emitted(self):
        tools = [_tool("list_pods", readOnlyHint=True)]
        rows = generate_default_rule_rows("kubernetes", tools)
        names = {r.name for r in rows}
        assert not any("playbook" in n for n in names)

    def test_per_tool_rows_emitted(self):
        tools = [_tool("list_pods", readOnlyHint=True), _tool("delete_pod")]
        rows = generate_default_rule_rows("kubernetes", tools)
        tool_names = {r.name for r in rows if r.name.startswith("tool:")}
        assert "tool:list_pods" in tool_names
        assert "tool:delete_pod" in tool_names

    def test_per_tool_row_contains_description(self):
        tools = [_tool("list_pods", readOnlyHint=True)]
        rows = generate_default_rule_rows("kubernetes", tools)
        tool_row = next(r for r in rows if r.name == "tool:list_pods")
        assert "list_pods tool" in tool_row.body

    def test_empty_catalog_produces_no_rows(self):
        rows = generate_default_rule_rows("ghost", [])
        assert len(rows) == 0


@pytest.mark.asyncio
class TestEnsureDefaultAssetsIdempotent:
    async def test_idempotent_when_rows_exist(self, store):
        await store.upsert(AssetRow(
            kind="rule", backend="kubernetes", name="playbook_read_only", body="existing"
        ))
        from zelosmcp.framework.assetstore.defaults import ensure_default_assets

        class FakeManager:
            pass

        n = await ensure_default_assets(store, FakeManager(), "kubernetes")
        assert n == 0
        # existing row untouched
        row = await store.get("rule", "kubernetes", "playbook_read_only")
        assert row.body == "existing"

    async def test_no_rows_with_none_store(self):
        from zelosmcp.framework.assetstore.defaults import ensure_default_assets

        class FakeManager:
            pass

        n = await ensure_default_assets(None, FakeManager(), "kubernetes")
        assert n == 0


# ── regenerate_default_assets ─────────────────────────────────────────


def _fake_catalog_module(monkeypatch, tools_per_backend: dict):
    """Patch ``collect_backend_full_catalog`` so the regenerate helper
    sees a synthetic catalog instead of fanning out over real client
    sessions.  ``tools_per_backend`` maps backend name → list of tool
    dicts (the shape returned by ``list_tools()``)."""
    async def _fake(manager, *, skip_self=False):
        return {
            name: {"tools": tools}
            for name, tools in tools_per_backend.items()
        }

    monkeypatch.setattr(
        "zelosmcp.builtin.collect_backend_full_catalog", _fake
    )


@pytest.mark.asyncio
class TestRegenerateDefaultAssets:
    async def test_overwrites_stale_auto_default_tool_rows(self, store, monkeypatch):
        """The reported bug: provider connects → live catalog goes 0→N,
        but the stored tool rows are frozen. The regenerate variant must
        overwrite the auto-default tool rows."""
        from zelosmcp.framework.assetstore.defaults import (
            generate_default_rule_rows,
            regenerate_default_assets,
        )

        # Stale state: no tool rows from empty catalog.
        for row in generate_default_rule_rows("github", [], seed_version=0):
            await store.upsert(row)

        existing = await store.list(kind="rule", backend="github")
        assert len(existing) == 0  # no tools → no rows

        # Auth provider just connected: live catalog now reports 2 tools.
        live_tools = [
            _tool("list_issues", readOnlyHint=True),
            _tool("create_issue"),
        ]
        _fake_catalog_module(monkeypatch, {"github": live_tools})

        n = await regenerate_default_assets(store, object(), "github")
        assert n >= 2  # tool:list_issues + tool:create_issue

        refreshed = await store.get("rule", "github", "tool:list_issues")
        assert refreshed is not None
        assert "list_issues" in refreshed.body

    async def test_inserts_per_tool_rows_for_newly_visible_tools(
        self, store, monkeypatch
    ):
        from zelosmcp.framework.assetstore.defaults import (
            generate_default_rule_rows,
            regenerate_default_assets,
        )

        for row in generate_default_rule_rows("github", [], seed_version=0):
            await store.upsert(row)

        live_tools = [_tool("list_issues", readOnlyHint=True)]
        _fake_catalog_module(monkeypatch, {"github": live_tools})

        await regenerate_default_assets(store, object(), "github")

        tool_row = await store.get("rule", "github", "tool:list_issues")
        assert tool_row is not None
        assert "github__list_issues" in tool_row.body

    async def test_preserves_user_edited_playbook(self, store, monkeypatch):
        """User edits the auto-default playbook in the Assets pane (the
        store marks the row source='user'). A subsequent auth-state
        transition must NOT overwrite the user's content."""
        from zelosmcp.framework.assetstore.defaults import (
            regenerate_default_assets,
        )

        await store.upsert(AssetRow(
            kind="rule",
            backend="github",
            name="playbook_read_only",
            body="USER CONTENT — do not overwrite",
            source="user",
            seed_version=None,
        ))

        _fake_catalog_module(
            monkeypatch,
            {"github": [_tool("list_issues", readOnlyHint=True)]},
        )

        await regenerate_default_assets(store, object(), "github")

        preserved = await store.get("rule", "github", "playbook_read_only")
        assert preserved.body == "USER CONTENT — do not overwrite"
        assert preserved.source == "user"

    async def test_preserves_yaml_seeded_playbook(self, store, monkeypatch):
        """YAML-seeded rows (seed_version >= 1) are authored content and
        must not be clobbered by an auto-default refresh."""
        from zelosmcp.framework.assetstore.defaults import (
            regenerate_default_assets,
        )

        await store.upsert(AssetRow(
            kind="rule",
            backend="github",
            name="playbook_read_only",
            body="YAML CONTENT (seed_version=3)",
            source="seed",
            seed_version=3,
        ))

        _fake_catalog_module(
            monkeypatch,
            {"github": [_tool("list_issues", readOnlyHint=True)]},
        )

        await regenerate_default_assets(store, object(), "github")

        preserved = await store.get("rule", "github", "playbook_read_only")
        assert preserved.body == "YAML CONTENT (seed_version=3)"
        assert preserved.seed_version == 3

    async def test_prunes_stale_auto_tool_rows(self, store, monkeypatch):
        """Tools previously visible but absent from the new catalog
        leave stale ``tool:*`` rows behind — the regenerate helper
        should sweep auto-default ones away so the Assets pane doesn't
        list phantom tools after a revoke or upstream catalog shrink."""
        from zelosmcp.framework.assetstore.defaults import (
            regenerate_default_assets,
        )

        # Previous run wrote rows for two tools (both auto-defaults).
        await store.upsert(AssetRow(
            kind="rule", backend="github", name="tool:gone",
            body="old", source="seed", seed_version=0,
        ))
        await store.upsert(AssetRow(
            kind="rule", backend="github", name="tool:kept",
            body="old", source="seed", seed_version=0,
        ))

        # New catalog: only `kept` survives.
        _fake_catalog_module(
            monkeypatch,
            {"github": [_tool("kept")]},
        )

        await regenerate_default_assets(store, object(), "github")

        assert await store.get("rule", "github", "tool:gone") is None
        kept = await store.get("rule", "github", "tool:kept")
        assert kept is not None

    async def test_preserves_user_edited_tool_row_when_pruning(
        self, store, monkeypatch
    ):
        """A user who curated a tool:* row keeps it even if the upstream
        catalog later drops that tool (a YAML seed or hand edit is a
        deliberate choice — only auto-defaults are sweep-eligible)."""
        from zelosmcp.framework.assetstore.defaults import (
            regenerate_default_assets,
        )

        await store.upsert(AssetRow(
            kind="rule", backend="github", name="tool:user_curated",
            body="user wrote this", source="user", seed_version=None,
        ))

        _fake_catalog_module(monkeypatch, {"github": []})

        await regenerate_default_assets(store, object(), "github")

        preserved = await store.get("rule", "github", "tool:user_curated")
        assert preserved is not None
        assert preserved.body == "user wrote this"

    async def test_none_store_is_noop(self):
        from zelosmcp.framework.assetstore.defaults import (
            regenerate_default_assets,
        )

        n = await regenerate_default_assets(None, object(), "github")
        assert n == 0

    async def test_revoke_path_drops_tools_to_zero(self, store, monkeypatch):
        """Reverse direction: provider was connected (N tools), user
        revokes, catalog becomes empty. The stored tool rows should
        be removed."""
        from zelosmcp.framework.assetstore.defaults import (
            generate_default_rule_rows,
            regenerate_default_assets,
        )

        live_tools = [_tool("list_issues", readOnlyHint=True)]
        for row in generate_default_rule_rows("github", live_tools, seed_version=0):
            await store.upsert(row)

        connected = await store.get("rule", "github", "tool:list_issues")
        assert connected is not None

        _fake_catalog_module(monkeypatch, {"github": []})

        await regenerate_default_assets(store, object(), "github")

        # After revoke, no tool rows remain
        remaining = await store.list(kind="rule", backend="github")
        tool_rows = [r for r in remaining if r.name.startswith("tool:")]
        assert len(tool_rows) == 0
