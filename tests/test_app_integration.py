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
        # `localmcp` is the always-on builtin row; only user backends matter here.
        user_servers = [s for s in data["servers"] if not s.get("builtin")]
        assert user_servers == []

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
            user_names = [s["name"] for s in data["servers"] if not s.get("builtin")]
            assert user_names == ["alpha", "beta"]
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
                user = {n for n in manager.names() if n != "localmcp"}
                assert user == {"alpha", "beta"}
                await c.post("/api/start", json={
                    "mcpServers": {"gamma": {"command": "echo", "args": ["g"]}},
                })
                user = {n for n in manager.names() if n != "localmcp"}
                assert user == {"gamma"}
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
            user = [n for n in manager.names() if n != "localmcp"]
            assert user == []


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
                user = [s for s in r.json()["servers"] if not s.get("builtin")]
                assert user == []


# ── Built-in MCP (/localmcp/mcp + /api/cursor-rule + aggregate) ─────────


@asynccontextmanager
async def _lifespan(app):
    """Drive Starlette's ASGI lifespan protocol manually so the built-in
    actually starts (httpx.ASGITransport doesn't drive lifespan on its own).
    Yields once startup completes; cleanly shuts down on exit."""
    queue: asyncio.Queue = asyncio.Queue()
    sent: list = []

    async def receive():
        return await queue.get()

    async def send(msg):
        sent.append(msg)

    task = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    await queue.put({"type": "lifespan.startup"})
    # Wait for the startup.complete event before yielding to the test body.
    for _ in range(100):
        if any(m.get("type") == "lifespan.startup.complete" for m in sent):
            break
        await asyncio.sleep(0.02)
    else:
        raise RuntimeError("lifespan startup did not complete in 2s")
    try:
        yield
    finally:
        await queue.put({"type": "lifespan.shutdown"})
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()


class TestCursorRuleEndpoint:
    @pytest.mark.asyncio
    async def test_returns_markdown_with_no_user_backends(self):
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                r = await c.get("/api/cursor-rule")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/markdown")
            assert "alwaysApply: true" in r.text
            assert "# LocalMCP backends" in r.text
            # No user backends -> generator emits the "no backends loaded" body.
            assert "No user backends are currently loaded" in r.text
            # Default access is read-only -> directive is present.
            assert "Access mode: READ-ONLY" in r.text

    @pytest.mark.asyncio
    async def test_scoped_style_includes_globs(self):
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                r = await c.get(
                    "/api/cursor-rule",
                    params={"style": "scoped", "globs": "**/*.py"},
                )
            assert r.status_code == 200
            assert "alwaysApply: false" in r.text
            assert "globs: **/*.py" in r.text

    @pytest.mark.asyncio
    async def test_unknown_style_400(self):
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                r = await c.get("/api/cursor-rule", params={"style": "bogus"})
            assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_access_param_read_write_changes_directive(self):
        """`?access=read-write` swaps the directive block from the
        forbid-mutations text to the confirm-with-user text."""
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                ro = await c.get("/api/cursor-rule")
                rw = await c.get(
                    "/api/cursor-rule", params={"access": "read-write"}
                )
            assert ro.status_code == 200
            assert rw.status_code == 200
            assert "Access mode: READ-ONLY" in ro.text
            assert "Access mode: READ-ONLY" not in rw.text
            assert "Access mode: READ-WRITE" in rw.text
            assert "(read-write mode)" in rw.text
            assert "(read-only mode)" in ro.text

    @pytest.mark.asyncio
    async def test_unknown_access_400(self):
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                r = await c.get("/api/cursor-rule", params={"access": "bogus"})
            assert r.status_code == 400
            assert "Unknown access" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_format_copilot_instructions_no_frontmatter(self):
        """`?format=copilot-instructions` returns the same body as the
        cursor-mdc default but without the YAML frontmatter wrapper.
        The HTTP body matches the MCP tool's body byte-for-byte."""
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                mdc = await c.get("/api/cursor-rule")
                copi = await c.get(
                    "/api/cursor-rule",
                    params={"format": "copilot-instructions"},
                )
            assert mdc.status_code == 200
            assert copi.status_code == 200
            assert copi.headers["content-type"].startswith("text/markdown")
            # No frontmatter in the copilot-instructions body.
            assert not copi.text.startswith("---")
            assert "alwaysApply" not in copi.text
            # The cursor-mdc body's content (after frontmatter) matches.
            _, _, mdc_after = mdc.text.partition("---\n")
            _, _, mdc_body = mdc_after.partition("---\n")
            assert mdc_body == copi.text

    @pytest.mark.asyncio
    async def test_unknown_format_400(self):
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                r = await c.get("/api/cursor-rule", params={"format": "bogus"})
            assert r.status_code == 400
            assert "Unknown format" in r.json()["error"]


class TestBuiltinMcp:
    @pytest.mark.asyncio
    async def test_aggregate_exposes_localmcp_tools(self):
        """With no user backends configured, /mcp still serves the eight
        `localmcp__*` tools provided by the built-in."""
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                init = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                }
                headers = {"Accept": "application/json, text/event-stream"}
                await c.post("/mcp", json=init, headers=headers)
                r = await c.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    headers=headers,
                )
            data = r.json()
            names = {t["name"] for t in data["result"]["tools"]}
            expected = {
                f"localmcp__{n}"
                for n in (
                    "generate_cursor_rule",
                    "list_loaded_servers",
                    "get_aggregated_tool_catalog",
                    "generate_cursor_mcp_json",
                    "start_server",
                    "stop_server",
                    "reload_config",
                )
            }
            assert expected <= names

    @pytest.mark.asyncio
    async def test_localmcp_mcp_direct_route_unprefixed(self):
        """At /localmcp/mcp the same tools appear without the `localmcp__`
        namespace prefix (raw passthrough)."""
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                init = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                }
                headers = {"Accept": "application/json, text/event-stream"}
                await c.post("/localmcp/mcp", json=init, headers=headers)
                r = await c.post(
                    "/localmcp/mcp",
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    headers=headers,
                )
            data = r.json()
            names = {t["name"] for t in data["result"]["tools"]}
            assert "generate_cursor_rule" in names
            # No prefix at the direct route.
            assert not any(n.startswith("localmcp__") for n in names)

    @pytest.mark.asyncio
    async def test_aggregate_call_tool_round_trip(self):
        """Round-trip a `tools/call` of `localmcp__generate_cursor_rule`
        at /mcp and assert the body is the same shape /api/cursor-rule
        returns."""
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                init = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                }
                headers = {"Accept": "application/json, text/event-stream"}
                await c.post("/mcp", json=init, headers=headers)
                r = await c.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "localmcp__generate_cursor_rule",
                            "arguments": {},
                        },
                    },
                    headers=headers,
                )
                http_r = await c.get("/api/cursor-rule")
            data = r.json()
            content = data["result"]["content"]
            assert content
            assert content[0]["type"] == "text"
            mcp_body = content[0]["text"]
            # Same generator under both transports -> identical output.
            assert mcp_body == http_r.text


class TestCatalogEndpoint:
    @pytest.mark.asyncio
    async def test_api_catalog_includes_builtin_with_seven_tools(self):
        """/api/catalog must include the always-on builtin row with all
        7 tools and well-formed inputSchemas, even when no user backend
        is configured. (Was 8 prior to the comprehensive-rule rewrite,
        which dropped `list_supported_backends`.)"""
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                r = await c.get("/api/catalog")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("application/json")
            data = r.json()
            assert "localmcp" in data
            row = data["localmcp"]
            assert row["transport"] == "builtin"
            assert row["running"] is True
            tools = row["tools"]
            assert isinstance(tools, list) and len(tools) == 7
            # Each tool entry has the keys the UI consumes.
            for t in tools:
                assert "name" in t
                assert "description" in t
                assert "inputSchema" in t
                assert isinstance(t["inputSchema"], dict)
            # Empty capabilities are coerced to []
            assert row["prompts"] == []
            assert row["resources"] == []
            assert row["resourceTemplates"] == []

    @pytest.mark.asyncio
    async def test_catalog_html_page(self):
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                r = await c.get("/catalog")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/html")
            # Sanity: the page references the JSON endpoint and renders the title.
            assert "Tool catalog" in r.text
            assert "/api/catalog" in r.text

    @pytest.mark.asyncio
    async def test_api_catalog_matches_mcp_tool(self):
        """`/api/catalog` and `localmcp__get_aggregated_tool_catalog` use
        the same helper, so the JSON they emit must be identical."""
        app, _ = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                # HTTP catalog
                http_r = await c.get("/api/catalog")
                http_payload = http_r.json()

                # MCP tool round-trip via /mcp
                init = {
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"},
                    },
                }
                headers = {"Accept": "application/json, text/event-stream"}
                await c.post("/mcp", json=init, headers=headers)
                r = await c.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {
                            "name": "localmcp__get_aggregated_tool_catalog",
                            "arguments": {},
                        },
                    },
                    headers=headers,
                )
            mcp_text = r.json()["result"]["content"][0]["text"]
            assert json.loads(mcp_text) == http_payload


# ── Reverse-proxy dispatch ──────────────────────────────────────────────


def _install_mock_upstream(manager, handler):
    """Replace the manager's httpx client with one routed through a
    MockTransport. The lifespan's startup hook builds a real client first;
    this swap happens after the lifespan enters so there's something to
    swap. Tests are responsible for closing the mock client themselves —
    the lifespan shutdown closes whatever the manager currently holds.
    """
    transport = httpx.MockTransport(handler)
    manager._http_client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0),
    )


_PROXY_CONFIG = {
    "mcpServers": {
        "alpha": {
            "command": "echo",
            "args": ["a"],
            "reverseProxy": {
                "mount": "/alpha",
                "upstream": "http://upstream.test",
            },
        },
    },
}


class TestReverseProxy:
    @pytest.mark.asyncio
    async def test_forwards_request_and_injects_xff_headers(self):
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["method"] = req.method
            captured["url"] = str(req.url)
            captured["headers"] = dict(req.headers)
            captured["body"] = req.content
            return httpx.Response(
                200,
                content=b'{"hello":"world"}',
                headers={"Content-Type": "application/json"},
            )

        with _apply_patches()[1], _apply_patches()[2], _apply_patches()[3], _apply_patches()[4], _apply_patches()[5]:
            app, manager = _fresh()
            async with _lifespan(app):
                _install_mock_upstream(manager, handler)
                async with _client(app) as c:
                    start = await c.post("/api/start", json=_PROXY_CONFIG)
                    assert start.status_code == 200
                    r = await c.post(
                        "/alpha/v1/echo?foo=bar",
                        content=b'{"q":1}',
                        headers={"Content-Type": "application/json"},
                    )
                assert r.status_code == 200
                assert r.json() == {"hello": "world"}

                # Path/query/body forwarded verbatim (stripPrefix=False default).
                assert captured["method"] == "POST"
                assert captured["url"] == "http://upstream.test/alpha/v1/echo?foo=bar"
                assert captured["body"] == b'{"q":1}'

                hdrs = captured["headers"]
                assert hdrs["x-forwarded-prefix"] == "/alpha"
                assert hdrs["x-forwarded-proto"] == "http"
                assert hdrs["x-forwarded-host"] == "testserver"
                # Caller's content-type is preserved.
                assert hdrs.get("content-type") == "application/json"

    @pytest.mark.asyncio
    async def test_503_when_backend_not_running(self):
        app, manager = _fresh()
        async with _lifespan(app):
            # Skip _apply_patches so start_all is never called — the spec is
            # injected directly into _specs but no ProxyState exists.
            from localmcp.config import parse_config
            specs, _ = parse_config(_PROXY_CONFIG)
            manager._specs = {s.name: s for s in specs}

            async with _client(app) as c:
                r = await c.get("/alpha/v1/health")
            assert r.status_code == 503
            assert r.json()["error"] == "No MCP server 'alpha' is running"

    @pytest.mark.asyncio
    async def test_strip_prefix_removes_mount(self):
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["url"] = str(req.url)
            return httpx.Response(204)

        with _apply_patches()[1], _apply_patches()[2], _apply_patches()[3], _apply_patches()[4], _apply_patches()[5]:
            app, manager = _fresh()
            async with _lifespan(app):
                _install_mock_upstream(manager, handler)
                async with _client(app) as c:
                    cfg = {
                        "mcpServers": {
                            "alpha": {
                                "command": "echo",
                                "args": ["a"],
                                "reverseProxy": {
                                    "mount": "/alpha",
                                    "upstream": "http://upstream.test",
                                    "stripPrefix": True,
                                },
                            },
                        },
                    }
                    await c.post("/api/start", json=cfg)
                    r = await c.get("/alpha/v1/health")
                assert r.status_code == 204
                # Prefix stripped: upstream sees /v1/health, not /alpha/v1/health.
                assert captured["url"] == "http://upstream.test/v1/health"

    @pytest.mark.asyncio
    async def test_bearer_injected_when_caller_has_no_auth(self):
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["auth"] = req.headers.get("authorization")
            return httpx.Response(200, content=b"")

        with _apply_patches()[1], _apply_patches()[2], _apply_patches()[3], _apply_patches()[4], _apply_patches()[5]:
            app, manager = _fresh()
            async with _lifespan(app):
                _install_mock_upstream(manager, handler)
                async with _client(app) as c:
                    cfg = {
                        "mcpServers": {
                            "alpha": {
                                "command": "echo",
                                "args": ["a"],
                                "reverseProxy": {
                                    "mount": "/alpha",
                                    "upstream": "http://upstream.test",
                                    "auth": {"bearer": "s3cret"},
                                },
                            },
                        },
                    }
                    await c.post("/api/start", json=cfg)
                    await c.get("/alpha/v1/health")
                assert captured["auth"] == "Bearer s3cret"

    @pytest.mark.asyncio
    async def test_bearer_not_overridden_when_caller_has_auth(self):
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["auth"] = req.headers.get("authorization")
            return httpx.Response(200, content=b"")

        with _apply_patches()[1], _apply_patches()[2], _apply_patches()[3], _apply_patches()[4], _apply_patches()[5]:
            app, manager = _fresh()
            async with _lifespan(app):
                _install_mock_upstream(manager, handler)
                async with _client(app) as c:
                    cfg = {
                        "mcpServers": {
                            "alpha": {
                                "command": "echo",
                                "args": ["a"],
                                "reverseProxy": {
                                    "mount": "/alpha",
                                    "upstream": "http://upstream.test",
                                    "auth": {"bearer": "s3cret"},
                                },
                            },
                        },
                    }
                    await c.post("/api/start", json=cfg)
                    await c.get(
                        "/alpha/v1/health",
                        headers={"Authorization": "Bearer caller-token"},
                    )
                assert captured["auth"] == "Bearer caller-token"

    @pytest.mark.asyncio
    async def test_upstream_unreachable_returns_502(self):
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated dead upstream")

        with _apply_patches()[1], _apply_patches()[2], _apply_patches()[3], _apply_patches()[4], _apply_patches()[5]:
            app, manager = _fresh()
            async with _lifespan(app):
                _install_mock_upstream(manager, handler)
                async with _client(app) as c:
                    await c.post("/api/start", json=_PROXY_CONFIG)
                    r = await c.get("/alpha/v1/health")
                assert r.status_code == 502
                body = r.json()
                assert body["backend"] == "alpha"
                assert "simulated dead upstream" in body["detail"]

    @pytest.mark.asyncio
    async def test_named_mcp_takes_precedence_over_proxy(self):
        """A backend mounted at /alpha must keep /alpha/mcp routing to the
        MCP session, not the reverse proxy. The dispatcher checks /<name>/mcp
        before the proxy table.

        We intercept ``session_manager.handle_request`` with a sentinel so we
        can assert exactly which dispatch arm fired without standing up the
        full MCP task group (the existing test fakes patch ``run()`` to a
        no-op, which leaves the session manager's internal task group
        uninitialised).
        """
        with _apply_patches()[1], _apply_patches()[2], _apply_patches()[3], _apply_patches()[4], _apply_patches()[5]:
            app, manager = _fresh()
            async with _lifespan(app):
                proxy_hits: list[str] = []
                mcp_hits: list[str] = []

                def proxy_handler(req: httpx.Request) -> httpx.Response:
                    proxy_hits.append(str(req.url))
                    return httpx.Response(200, content=b"")

                _install_mock_upstream(manager, proxy_handler)
                async with _client(app) as c:
                    await c.post("/api/start", json=_PROXY_CONFIG)
                    state = manager.get("alpha")
                    assert state is not None and state.session_manager is not None

                    async def fake_handle_request(scope, receive, send):
                        mcp_hits.append(scope.get("path", ""))
                        # Minimal valid ASGI response so the test client doesn't choke.
                        await send({"type": "http.response.start", "status": 204, "headers": []})
                        await send({"type": "http.response.body", "body": b"", "more_body": False})

                    state.session_manager.handle_request = fake_handle_request

                    r = await c.post(
                        "/alpha/mcp",
                        json={"jsonrpc": "2.0", "id": 1, "method": "x", "params": {}},
                        headers={"Accept": "application/json, text/event-stream"},
                    )
                assert r.status_code == 204
                # Dispatcher chose the MCP arm, not the proxy arm.
                assert mcp_hits == ["/mcp"]  # path was rewritten by dispatcher
                assert proxy_hits == []

    @pytest.mark.asyncio
    async def test_status_exposes_reverse_proxy(self):
        with _apply_patches()[1], _apply_patches()[2], _apply_patches()[3], _apply_patches()[4], _apply_patches()[5]:
            app, _ = _fresh()
            async with _lifespan(app):
                async with _client(app) as c:
                    await c.post("/api/start", json=_PROXY_CONFIG)
                    r = await c.get("/api/status")
                row = next(s for s in r.json()["servers"] if s["name"] == "alpha")
                assert row["spec"]["reverseProxy"] == {
                    "mount": "/alpha",
                    "upstream": "http://upstream.test",
                }
