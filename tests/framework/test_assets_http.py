"""Integration tests for the /api/assets/* HTTP routes."""
from __future__ import annotations

import pytest
import httpx

from zelosmcp.app import create_app
from zelosmcp.manager import ProxyManager
from zelosmcp.framework.assetstore.row import AssetRow
from zelosmcp.framework.assetstore.sqlite import SQLiteAssetStore


def _fresh():
    manager = ProxyManager(mandatory_config_path="")
    app = create_app(manager)
    return app, manager


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
async def app_with_assets():
    app, manager = _fresh()
    store = SQLiteAssetStore(":memory:")
    await store.open()
    manager._assets_store = store
    manager.assets = store
    # Seed a couple of rows
    await store.upsert(AssetRow(kind="rule", backend="pincher", name="playbook_ro", body="PO content", source="seed", seed_version=1))
    await store.upsert(AssetRow(kind="extension", backend="pincher", name="index_project", body="{}", meta={"type": "tool", "tool": "index", "label": "Index", "targets": ["repos_row"]}, source="seed", seed_version=1))
    yield app, manager, store
    await store.close()


@pytest.mark.asyncio
class TestAssetsListRoute:
    async def test_returns_all_rows(self, app_with_assets):
        app, _, _ = app_with_assets
        async with _client(app) as c:
            r = await c.get("/api/assets")
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list)
        assert len(rows) == 2

    async def test_filter_by_kind(self, app_with_assets):
        app, _, _ = app_with_assets
        async with _client(app) as c:
            r = await c.get("/api/assets?kind=rule")
        rows = r.json()
        assert all(row["kind"] == "rule" for row in rows)

    async def test_503_when_store_not_initialised(self):
        app, manager = _fresh()
        async with _client(app) as c:
            r = await c.get("/api/assets")
        assert r.status_code == 503


@pytest.mark.asyncio
class TestAssetsGetRoute:
    async def test_get_existing_row(self, app_with_assets):
        app, _, _ = app_with_assets
        async with _client(app) as c:
            r = await c.get("/api/assets/rule/pincher/playbook_ro")
        assert r.status_code == 200
        assert r.json()["body"] == "PO content"

    async def test_get_missing_row_404(self, app_with_assets):
        app, _, _ = app_with_assets
        async with _client(app) as c:
            r = await c.get("/api/assets/rule/ghost/no_such")
        assert r.status_code == 404


@pytest.mark.asyncio
class TestAssetsPutRoute:
    async def test_put_creates_user_row(self, app_with_assets):
        app, _, store = app_with_assets
        async with _client(app) as c:
            r = await c.put(
                "/api/assets/rule/pincher/playbook_ro",
                json={"body": "my custom body"},
            )
        assert r.status_code == 200
        updated = r.json()
        assert updated["body"] == "my custom body"
        assert updated["source"] == "user"

    async def test_put_new_row(self, app_with_assets):
        app, _, _ = app_with_assets
        async with _client(app) as c:
            r = await c.put(
                "/api/assets/rule/pincher/new_section",
                json={"body": "brand new"},
            )
        assert r.status_code == 200


@pytest.mark.asyncio
class TestAssetsDeleteRoute:
    async def test_delete_removes_row(self, app_with_assets):
        app, _, store = app_with_assets
        # First write a user row
        await store.upsert(AssetRow(kind="rule", backend="pincher", name="to_delete", body="x", source="user"))
        async with _client(app) as c:
            r = await c.delete("/api/assets/rule/pincher/to_delete")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_delete_nonexistent_returns_ok_false(self, app_with_assets):
        app, _, _ = app_with_assets
        async with _client(app) as c:
            r = await c.delete("/api/assets/rule/ghost/missing")
        assert r.status_code == 200
        assert r.json()["ok"] is False


@pytest.mark.asyncio
class TestAssetsSummaryRoute:
    async def test_summary_returns_stats(self, app_with_assets):
        app, _, _ = app_with_assets
        async with _client(app) as c:
            r = await c.get("/api/assets/summary")
        assert r.status_code == 200
        s = r.json()
        assert s["total"] == 2
        assert "rule" in s["by_kind"]


@pytest.mark.asyncio
class TestAssetsKindsRoute:
    async def test_kinds_returns_list(self, app_with_assets):
        app, _, _ = app_with_assets
        async with _client(app) as c:
            r = await c.get("/api/assets/kinds")
        assert r.status_code == 200
        kinds = r.json()
        assert isinstance(kinds, list)
        ids = {k["id"] for k in kinds}
        assert "rule" in ids
        assert "extension" in ids
        assert "agent" in ids
        assert "hook" in ids


@pytest.mark.asyncio
class TestYamlEditorRoutes:
    async def test_get_yaml_returns_yaml_text(self, app_with_assets):
        app, _, _ = app_with_assets
        async with _client(app) as c:
            r = await c.get("/api/assets/yaml/pincher")
        assert r.status_code == 200
        assert "pincher" in r.text
        import yaml
        data = yaml.safe_load(r.text)
        assert data["backend"] == "pincher"

    async def test_put_yaml_replaces_rows(self, app_with_assets):
        app, _, store = app_with_assets
        import yaml
        new_yaml = yaml.dump({
            "backend": "pincher",
            "seed_version": 2,
            "rules": {
                "sections": {
                    "playbook_read_only": {"body": "NEW content"},
                }
            },
        })
        async with _client(app) as c:
            r = await c.put(
                "/api/assets/yaml/pincher",
                content=new_yaml.encode(),
                headers={"Content-Type": "text/yaml"},
            )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        row = await store.get("rule", "pincher", "playbook_read_only")
        assert row is not None
        assert "NEW content" in row.body

    async def test_put_yaml_rejects_invalid_schema(self, app_with_assets):
        app, _, _ = app_with_assets
        bad_yaml = "backend: pincher\nseed_version: 1\nextentions: {}\n"  # typo
        async with _client(app) as c:
            r = await c.put(
                "/api/assets/yaml/pincher",
                content=bad_yaml.encode(),
                headers={"Content-Type": "text/yaml"},
            )
        assert r.status_code == 400
        data = r.json()
        assert data["ok"] is False
        assert len(data["errors"]) >= 1
        assert any("extentions" in e["message"] or "additional" in e["message"].lower()
                   for e in data["errors"])

    async def test_validate_returns_ok_for_valid_yaml(self, app_with_assets):
        app, _, _ = app_with_assets
        import yaml
        valid = yaml.dump({"backend": "pincher", "seed_version": 1})
        async with _client(app) as c:
            r = await c.post(
                "/api/assets/yaml/pincher/validate",
                content=valid.encode(),
                headers={"Content-Type": "text/yaml"},
            )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["errors"] == []

    async def test_validate_returns_errors_for_invalid_yaml(self, app_with_assets):
        app, _, _ = app_with_assets
        bad = "backend: pincher\nseed_version: 1\nbad_key: true\n"
        async with _client(app) as c:
            r = await c.post(
                "/api/assets/yaml/pincher/validate",
                content=bad.encode(),
                headers={"Content-Type": "text/yaml"},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert len(data["errors"]) >= 1

    async def test_validate_does_not_mutate_store(self, app_with_assets):
        app, _, store = app_with_assets
        original = await store.list()
        original_count = len(original)
        bad = "backend: pincher\nseed_version: 1\nbad_key: true\n"
        async with _client(app) as c:
            await c.post(
                "/api/assets/yaml/pincher/validate",
                content=bad.encode(),
                headers={"Content-Type": "text/yaml"},
            )
        assert len(await store.list()) == original_count

    async def test_delete_yaml_removes_backend_rows(self, app_with_assets):
        app, _, store = app_with_assets
        async with _client(app) as c:
            r = await c.delete("/api/assets/yaml/pincher")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        rows = await store.list(backend="pincher")
        assert rows == []
