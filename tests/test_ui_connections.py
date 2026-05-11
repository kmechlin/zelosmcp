"""Smoke tests for the PR 5 Connections UI additions in
:mod:`zelosmcp.ui`.

These don't render JS — that lives in browsers, not Python — but
they verify the static HTML/CSS structure is present so a
malformed string substitution can't ship undetected. The actual
end-to-end flow runs through the PR 4 HTTP route tests.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from zelosmcp.app import create_app
from zelosmcp.manager import ProxyManager


def _fresh():
    manager = ProxyManager(mandatory_config_path="")
    app = create_app(manager)
    return app, manager


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


class TestConnectionsViewMarkup:
    @pytest.mark.asyncio
    async def test_nav_item_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        assert r.status_code == 200
        assert 'data-view="connections"' in r.text
        assert ">Connections<" in r.text

    @pytest.mark.asyncio
    async def test_view_section_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        # The section element + the loading placeholder live there.
        assert 'class="view" data-view="connections"' in r.text
        assert 'id="connections-list"' in r.text
        assert 'id="connections-meta"' in r.text

    @pytest.mark.asyncio
    async def test_modal_present(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        assert 'id="connect-modal-backdrop"' in r.text
        assert 'id="connect-modal-code"' in r.text
        assert 'id="connect-modal-authorize-link"' in r.text
        assert 'id="connect-modal-hint"' in r.text

    @pytest.mark.asyncio
    async def test_load_connections_helper_referenced(self):
        # The setView dispatch should call loadConnections() when the
        # user activates the view.
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        assert "loadConnections()" in r.text
        assert 'name === "connections"' in r.text

    @pytest.mark.asyncio
    async def test_membership_hint_handling_in_js(self):
        # The card renderer should branch on membership_hint and the
        # modal should render the hint above the Authorize button.
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        assert "entry.membership_hint" in r.text
        assert "Membership required:" in r.text


class TestConnectionsViewEndToEnd:
    """Verify the JS card renderer maps cleanly onto the JSON shape
    the PR 4 routes return — no contract drift between front-end
    and back-end."""

    @pytest.mark.asyncio
    async def test_providers_endpoint_returns_shape_card_expects(self):
        app, manager = _fresh()
        await manager.start_auth_store(db_path=":memory:")
        try:
            await manager.start_auth_providers({
                "providers": {
                    "gh": {
                        "type": "github_device_flow",
                        "client_id": "Iv1.test",
                    }
                }
            })
            async with _client(app) as c:
                r = await c.get("/api/auth/providers")
            assert r.status_code == 200
            body = r.json()
            providers = body["providers"]
            assert len(providers) == 1
            entry = providers[0]
            # Shape the JS card renderer reads.
            for key in (
                "name", "type", "ready", "identity",
                "membership_hint", "supports_device_flow",
                "supports_authorization_code",
            ):
                assert key in entry, f"missing key {key!r}"
            assert entry["supports_device_flow"] is True
        finally:
            await manager.stop_auth_store()
