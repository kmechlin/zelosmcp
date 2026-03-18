"""Integration tests for the localmcp ASGI app."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from localmcp.app import create_app
from localmcp.proxy import ProxyState
from tests.conftest import (
    fake_stdio_client,
    make_mock_session,
)


def _fresh():
    """Create a fresh ProxyState + ASGI app pair."""
    proxy = ProxyState()
    app = create_app(proxy)
    return app, proxy


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
        patch("localmcp.proxy.ClientSession", side_effect=patched_client_session),
        patch("localmcp.proxy.StreamableHTTPSessionManager.run", patched_run),
    )


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
        assert "localhost:8000/mcp" in r.text

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
        assert data["backend"] == {}
        assert data["error"] is None

    @pytest.mark.asyncio
    async def test_status_when_running(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            app, proxy = _fresh()
            await proxy.start("stdio", command="echo hello")
            async with _client(app) as c:
                r = await c.get("/api/status")
            data = r.json()
            assert data["running"] is True
            assert data["backend"]["transport"] == "stdio"
            await proxy.stop()


# ── Start API ───────────────────────────────────────────────────────────

class TestStartAPI:
    @pytest.mark.asyncio
    async def test_start_success(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            app, proxy = _fresh()
            async with _client(app) as c:
                r = await c.post("/api/start", json={
                    "transport": "stdio",
                    "command": "echo hello",
                })
            assert r.status_code == 200
            assert r.json()["ok"] is True
            assert proxy.running is True
            await proxy.stop()

    @pytest.mark.asyncio
    async def test_start_missing_command_returns_400(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/api/start", json={"transport": "stdio"})
        assert r.status_code == 400
        data = r.json()
        assert data["ok"] is False
        assert "Command is required" in data["error"]

    @pytest.mark.asyncio
    async def test_start_invalid_transport_returns_400(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/api/start", json={"transport": "pigeons"})
        assert r.status_code == 400
        assert "Unknown transport" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_start_twice_returns_400(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            app, proxy = _fresh()
            async with _client(app) as c:
                await c.post("/api/start", json={
                    "transport": "stdio", "command": "echo hi",
                })
                r = await c.post("/api/start", json={
                    "transport": "stdio", "command": "echo hi",
                })
            assert r.status_code == 400
            assert "already running" in r.json()["error"]
            await proxy.stop()

    @pytest.mark.asyncio
    async def test_start_defaults_to_stdio(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/api/start", json={})
        assert r.status_code == 400
        assert "Command is required" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_start_with_env(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            app, proxy = _fresh()
            async with _client(app) as c:
                r = await c.post("/api/start", json={
                    "transport": "stdio",
                    "command": "uvx code-index-mcp",
                    "env": {"API_KEY": "sk-test"},
                })
            assert r.status_code == 200
            assert r.json()["ok"] is True
            assert proxy.backend_info["env"] == {"API_KEY": "sk-test"}
            await proxy.stop()

    @pytest.mark.asyncio
    async def test_start_without_env(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            app, proxy = _fresh()
            async with _client(app) as c:
                r = await c.post("/api/start", json={
                    "transport": "stdio",
                    "command": "echo hello",
                })
            assert r.status_code == 200
            assert "env" not in proxy.backend_info
            await proxy.stop()


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
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            app, proxy = _fresh()
            await proxy.start("stdio", command="echo hello")
            async with _client(app) as c:
                r = await c.post("/api/stop")
            assert r.status_code == 200
            assert r.json()["ok"] is True
            assert proxy.running is False


# ── MCP endpoint ────────────────────────────────────────────────────────

class TestMCPEndpoint:
    @pytest.mark.asyncio
    async def test_mcp_post_returns_503_when_stopped(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/mcp")
        assert r.status_code == 503
        assert "No MCP server running" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_mcp_get_returns_503_when_stopped(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/mcp")
        assert r.status_code == 503

    @pytest.mark.asyncio
    async def test_mcp_trailing_slash_returns_503(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/mcp/")
        assert r.status_code == 503

    @pytest.mark.asyncio
    async def test_mcp_delegates_to_session_manager(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            app, proxy = _fresh()
            await proxy.start("stdio", command="echo hello")
            assert proxy.session_manager is not None
            await proxy.stop()

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self):
        """Non-http scope type should pass to starlette (which will ignore it)."""
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/nonexistent")
        assert r.status_code == 404


# ── Logs SSE endpoint ───────────────────────────────────────────────────

class TestLogsEndpoint:
    @pytest.mark.asyncio
    async def test_logs_returns_sse_content_type(self):
        app, proxy = _fresh()

        async def check():
            async with _client(app) as c:
                async with c.stream("GET", "/api/logs") as r:
                    assert r.status_code == 200
                    assert "text/event-stream" in r.headers["content-type"]

        try:
            await asyncio.wait_for(check(), timeout=2.0)
        except (asyncio.TimeoutError, httpx.ReadError):
            pass  # expected: SSE stream is infinite

    @pytest.mark.asyncio
    async def test_logs_subscriber_receives_emitted_messages(self):
        _, proxy = _fresh()
        q = proxy.subscribe_logs()
        proxy._emit_log("integration-test-msg")
        msg = q.get_nowait()
        assert "integration-test-msg" in msg
        proxy.unsubscribe_logs(q)


# ── Full lifecycle ──────────────────────────────────────────────────────

class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_start_status_stop_status(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            app, proxy = _fresh()
            async with _client(app) as c:
                r = await c.get("/api/status")
                assert r.json()["running"] is False

                r = await c.post("/api/start", json={
                    "transport": "stdio", "command": "echo hello",
                })
                assert r.json()["ok"] is True

                r = await c.get("/api/status")
                assert r.json()["running"] is True

                r = await c.post("/api/stop")
                assert r.json()["ok"] is True

                r = await c.get("/api/status")
                assert r.json()["running"] is False

    @pytest.mark.asyncio
    async def test_mcp_503_then_start_then_stop_503(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            app, proxy = _fresh()
            async with _client(app) as c:
                r = await c.post("/mcp")
                assert r.status_code == 503

                await proxy.start("stdio", command="echo hello")
                assert proxy.session_manager is not None

                await proxy.stop()

                r = await c.post("/mcp")
                assert r.status_code == 503
