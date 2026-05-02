"""Integration tests for the localmcp ASGI app (multi-MCP version)."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from localmcp.app import create_app
from localmcp.manager import ProxyManager
from tests.conftest import (
    fake_stdio_client,
    fake_sse_client,
    fake_http_client,
    make_mock_session,
)


def _fresh():
    """Create a fresh ProxyManager + ASGI app pair."""
    manager = ProxyManager()
    app = create_app(manager)
    return app, manager


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _apply_patches():
    mock_session = make_mock_session()

    @asynccontextmanager
    async def patched_client_session(read, write):
        yield mock_session

    @asynccontextmanager
    async def patched_run(self):
        yield

    return (
        mock_session,
        patch("localmcp.proxy.stdio_client", side_effect=fake_stdio_client),
        patch("localmcp.proxy.sse_client", side_effect=fake_sse_client),
        patch("localmcp.proxy.streamablehttp_client", side_effect=fake_http_client),
        patch("localmcp.proxy.ClientSession", side_effect=patched_client_session),
        patch("localmcp.proxy.StreamableHTTPSessionManager.run", patched_run),
    )


_STDIO_CONFIG = {
    "mcpServers": {
        "alpha": {"command": "echo", "args": ["alpha"]},
        "beta":  {"command": "echo", "args": ["beta"]},
    },
}


# ── UI route ────────────────────────────────────────────────────────────

class TestUIRoute:
    @pytest.mark.asyncio
    async def test_index_returns_html(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "LOCALMCP" in r.text

    @pytest.mark.asyncio
    async def test_index_contains_mcp_json_snippet(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        assert "mcpServers" in r.text

    @pytest.mark.asyncio
    async def test_index_contains_copy_button(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        assert "copySnippet" in r.text

    @pytest.mark.asyncio
    async def test_index_contains_config_textarea(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        assert "config-textarea" in r.text
        assert "config-input" in r.text

    @pytest.mark.asyncio
    async def test_index_contains_parse_config_function(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        assert "parseConfig" in r.text

    @pytest.mark.asyncio
    async def test_index_links_to_docs(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/")
        assert 'href="/docs"' in r.text


# ── OpenAPI explorer ────────────────────────────────────────────────────

class TestOpenAPI:
    @pytest.mark.asyncio
    async def test_openapi_json_returns_spec(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/openapi.json")
        assert r.status_code == 200
        spec = r.json()
        assert spec["openapi"].startswith("3.")
        paths = spec["paths"]
        assert "/api/start" in paths
        assert "/api/stop" in paths
        assert "/api/status" in paths
        assert "/api/logs" in paths
        assert "/api/servers/{name}" in paths
        assert "/api/servers/{name}/start" in paths
        assert "/api/servers/{name}/stop" in paths

    @pytest.mark.asyncio
    async def test_swagger_ui_renders(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/docs")
        assert r.status_code == 200
        assert "swagger-ui" in r.text
        assert "/openapi.json" in r.text

    @pytest.mark.asyncio
    async def test_redoc_renders(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/redoc")
        assert r.status_code == 200
        assert "redoc" in r.text.lower()


# ── Status API ──────────────────────────────────────────────────────────

class TestStatusAPI:
    @pytest.mark.asyncio
    async def test_status_when_stopped(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is False
        assert data["primary"] is None
        assert data["servers"] == []

    @pytest.mark.asyncio
    async def test_status_when_running(self):
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                await c.post("/api/start", json=_STDIO_CONFIG)
                r = await c.get("/api/status")
            data = r.json()
            assert data["running"] is True
            assert data["primary"] is None
            names = [s["name"] for s in data["servers"]]
            assert names == ["alpha", "beta"]
            for s in data["servers"]:
                assert s["primary"] is False
            await manager.stop_all()


# ── Start API ───────────────────────────────────────────────────────────

class TestStartAPI:
    @pytest.mark.asyncio
    async def test_start_success_multi(self):
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                r = await c.post("/api/start", json=_STDIO_CONFIG)
            assert r.status_code == 200
            data = r.json()
            assert data["ok"] is True
            assert data["primary"] is None
            assert set(data["servers"].keys()) == {"alpha", "beta"}
            assert all(s["ok"] for s in data["servers"].values())
            assert manager.get("alpha") is not None
            assert manager.get("beta") is not None
            assert manager.aggregator.running is True
            await manager.stop_all()

    @pytest.mark.asyncio
    async def test_start_invalid_json(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/api/start", content="not json")
        assert r.status_code == 400
        assert "Invalid JSON" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_start_missing_mcpServers(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/api/start", json={})
        assert r.status_code == 400
        assert "mcpServers" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_start_with_deprecated_primary_is_accepted(self):
        # primaryMCP is deprecated — accepted for back-compat, ignored at runtime.
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                r = await c.post("/api/start", json={
                    "primaryMCP": "ghost",
                    "mcpServers": {"alpha": {"command": "echo", "args": ["a"]}},
                })
            assert r.status_code == 200
            assert r.json()["ok"] is True
            assert manager.primary is None
            await manager.stop_all()

    @pytest.mark.asyncio
    async def test_start_reserved_name_rejected(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/api/start", json={
                "mcpServers": {"api": {"command": "echo", "args": ["x"]}},
            })
        assert r.status_code == 400
        assert "reserved" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_start_replaces_previous_set(self):
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                await c.post("/api/start", json=_STDIO_CONFIG)
                assert set(manager.names()) == {"alpha", "beta"}
                await c.post("/api/start", json={
                    "mcpServers": {"gamma": {"command": "echo", "args": ["g"]}},
                })
                assert set(manager.names()) == {"gamma"}
            await manager.stop_all()

    @pytest.mark.asyncio
    async def test_start_with_env_passed_through(self):
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                r = await c.post("/api/start", json={
                    "mcpServers": {
                        "alpha": {
                            "command": "uvx",
                            "args": ["code-index-mcp"],
                            "env": {"API_KEY": "sk-test"},
                        }
                    }
                })
            assert r.status_code == 200
            assert manager.get("alpha").backend_info["env"] == {"API_KEY": "sk-test"}
            await manager.stop_all()


# ── Per-server endpoints ────────────────────────────────────────────────

class TestPerServerEndpoints:
    @pytest.mark.asyncio
    async def test_get_unknown_returns_404(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/api/servers/ghost")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_get_known_server(self):
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                await c.post("/api/start", json=_STDIO_CONFIG)
                r = await c.get("/api/servers/alpha")
            assert r.status_code == 200
            data = r.json()
            assert data["name"] == "alpha"
            assert data["primary"] is False
            await manager.stop_all()

    @pytest.mark.asyncio
    async def test_stop_then_start_one(self):
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                await c.post("/api/start", json=_STDIO_CONFIG)
                r = await c.post("/api/servers/beta/stop")
                assert r.status_code == 200
                assert manager.get("beta").running is False
                r = await c.post("/api/servers/beta/start")
                assert r.status_code == 200
                assert manager.get("beta").running is True
            await manager.stop_all()

    @pytest.mark.asyncio
    async def test_start_unknown_server_404(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/api/servers/ghost/start")
        assert r.status_code == 404


# ── Stop API ────────────────────────────────────────────────────────────

class TestStopAPI:
    @pytest.mark.asyncio
    async def test_stop_when_not_running(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/api/stop")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_stop_after_start(self):
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                await c.post("/api/start", json=_STDIO_CONFIG)
                r = await c.post("/api/stop")
            assert r.status_code == 200
            assert manager.names() == []


# ── MCP routing ─────────────────────────────────────────────────────────

class TestMCPRouting:
    @pytest.mark.asyncio
    async def test_root_mcp_503_when_no_servers(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/mcp")
        assert r.status_code == 503
        assert "No MCP servers" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_named_mcp_503_when_unknown(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/ghost/mcp")
        assert r.status_code == 503
        assert "ghost" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_named_mcp_dispatches_when_running(self):
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                await c.post("/api/start", json=_STDIO_CONFIG)
                # The session manager's handle_request is a real coroutine; we
                # only assert that the dispatcher chose it (i.e. the request
                # passes through to the manager rather than 503).
                manager.get("alpha").session_manager.handle_request = AsyncMock(
                    return_value=None
                )

                async def _send(msg): pass
                async def _recv():
                    return {"type": "http.disconnect"}

                scope = {
                    "type": "http",
                    "method": "POST",
                    "path": "/alpha/mcp",
                    "raw_path": b"/alpha/mcp",
                    "headers": [],
                }
                await app(scope, _recv, _send)
                manager.get("alpha").session_manager.handle_request.assert_awaited_once()
            await manager.stop_all()

    @pytest.mark.asyncio
    async def test_root_mcp_dispatches_to_aggregator(self):
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                await c.post("/api/start", json=_STDIO_CONFIG)
                # Spy on every session_manager: only the aggregator's should fire.
                manager.aggregator.session_manager.handle_request = AsyncMock(
                    return_value=None
                )
                manager.get("alpha").session_manager.handle_request = AsyncMock(
                    return_value=None
                )
                manager.get("beta").session_manager.handle_request = AsyncMock(
                    return_value=None
                )

                async def _send(msg): pass
                async def _recv():
                    return {"type": "http.disconnect"}

                scope = {
                    "type": "http",
                    "method": "POST",
                    "path": "/mcp",
                    "raw_path": b"/mcp",
                    "headers": [],
                }
                await app(scope, _recv, _send)
                manager.aggregator.session_manager.handle_request.assert_awaited_once()
                manager.get("alpha").session_manager.handle_request.assert_not_called()
                manager.get("beta").session_manager.handle_request.assert_not_called()
            await manager.stop_all()

    @pytest.mark.asyncio
    async def test_unrelated_path_passes_to_starlette(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/nonexistent")
        assert r.status_code == 404


# ── Logs SSE endpoint ───────────────────────────────────────────────────

class TestLogsEndpoint:
    @pytest.mark.asyncio
    async def test_logs_returns_sse_content_type(self):
        app, _ = _fresh()

        async def check():
            async with _client(app) as c:
                async with c.stream("GET", "/api/logs") as r:
                    assert r.status_code == 200
                    assert "text/event-stream" in r.headers["content-type"]

        try:
            await asyncio.wait_for(check(), timeout=2.0)
        except (asyncio.TimeoutError, httpx.ReadError):
            pass  # SSE stream is infinite

    @pytest.mark.asyncio
    async def test_logs_subscriber_receives_emitted_messages(self):
        _, manager = _fresh()
        q = manager.subscribe_logs()
        manager._broadcast("integration-test-msg")
        msg = q.get_nowait()
        assert "integration-test-msg" in msg
        manager.unsubscribe_logs(q)


# ── Full lifecycle ──────────────────────────────────────────────────────

class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_start_status_stop_status(self):
        _, *patches = _apply_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                r = await c.get("/api/status")
                assert r.json()["running"] is False

                r = await c.post("/api/start", json=_STDIO_CONFIG)
                assert r.json()["ok"] is True

                r = await c.get("/api/status")
                data = r.json()
                assert data["running"] is True
                assert data["primary"] is None

                r = await c.post("/api/stop")
                assert r.json()["ok"] is True

                r = await c.get("/api/status")
                assert r.json()["running"] is False
                assert r.json()["servers"] == []
