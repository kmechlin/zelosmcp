"""UI smoke tests: Assets pane HTML and key JS functions present."""
from __future__ import annotations

import pytest
import httpx

from zelosmcp.app import create_app
from zelosmcp.manager import ProxyManager


def _fresh():
    manager = ProxyManager(mandatory_config_path="")
    app = create_app(manager)
    return app, manager


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


class TestAssetsBackendView:
    @pytest.mark.asyncio
    async def test_assets_backend_view_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        html = r.text
        assert 'data-view="assets-backend"' in html
        assert 'assets-backend-content' in html

    @pytest.mark.asyncio
    async def test_tab_bar_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        html = r.text
        assert 'data-tab="rule"' in html
        assert 'data-tab="extension"' in html
        assert 'data-tab="agent"' in html
        assert 'data-tab="hook"' in html

    @pytest.mark.asyncio
    async def test_yaml_editor_controls_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        html = r.text
        assert 'assets-yaml-edit-btn' in html
        assert 'assets-yaml-export-btn' in html
        assert 'assets-yaml-import-input' in html
        assert 'assets-yaml-textarea' in html

    @pytest.mark.asyncio
    async def test_yaml_editor_js_functions_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        html = r.text
        assert 'function openYamlEditor' in html
        assert 'function saveYamlEditor' in html
        assert 'function exportYaml' in html
        assert 'function importYaml' in html
        assert 'function lintYaml' in html
        assert 'function addStubRow' in html

    @pytest.mark.asyncio
    async def test_lint_status_panel_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        html = r.text
        assert 'assets-yaml-lint-status' in html
        assert 'assets-yaml-save-btn' in html


class TestAssetsServerRow:
    @pytest.mark.asyncio
    async def test_assets_button_on_server_row(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        html = r.text
        assert 'showBackendAssets' in html

    @pytest.mark.asyncio
    async def test_show_backend_assets_function_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        html = r.text
        assert 'function showBackendAssets' in html
        assert 'switchBackendAssetsTab' in html


class TestRepoDetailsPushButtons:
    @pytest.mark.asyncio
    async def test_push_buttons_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        html = r.text
        assert 'pushComprehensive' in html
        assert 'pushAllAssets' in html
        assert 'Push all' in html
        assert 'Push rules' in html
        assert 'Push agents' in html
        assert 'Push hooks' in html

    @pytest.mark.asyncio
    async def test_repo_asset_actions_container_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        html = r.text
        assert 'repo-asset-actions' in html
        assert 'loadRepoAssetActions' in html

    @pytest.mark.asyncio
    async def test_running_hint_element_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        html = r.text
        assert 'repo-push-running-hint' in html
