"""Unit tests for localmcp.proxy.ProxyState."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from localmcp.proxy import ProxyState
from tests.conftest import (
    fake_stdio_client,
    fake_sse_client,
    fake_http_client,
    make_mock_session,
)


# ── Initialization ──────────────────────────────────────────────────────

class TestInit:
    def test_defaults(self):
        p = ProxyState()
        assert p.running is False
        assert p.session_manager is None
        assert p.client_session is None
        assert p.backend_info == {}
        assert p.error is None
        assert p._log_subscribers == []
        assert p._task is None


# ── Logging ─────────────────────────────────────────────────────────────

class TestLogging:
    def test_subscribe_creates_queue(self):
        p = ProxyState()
        q = p.subscribe_logs()
        assert q in p._log_subscribers
        assert isinstance(q, asyncio.Queue)

    def test_unsubscribe_removes_queue(self):
        p = ProxyState()
        q = p.subscribe_logs()
        p.unsubscribe_logs(q)
        assert q not in p._log_subscribers

    def test_unsubscribe_unknown_queue_no_error(self):
        p = ProxyState()
        q = asyncio.Queue()
        p.unsubscribe_logs(q)

    def test_emit_log_delivers_to_subscribers(self):
        p = ProxyState()
        q1 = p.subscribe_logs()
        q2 = p.subscribe_logs()
        p._emit_log("hello")
        assert not q1.empty()
        assert not q2.empty()
        msg1 = q1.get_nowait()
        assert "hello" in msg1

    def test_emit_log_skips_full_queue(self):
        p = ProxyState()
        q = asyncio.Queue(maxsize=1)
        p._log_subscribers.append(q)
        q.put_nowait("filler")
        p._emit_log("overflow")
        assert q.qsize() == 1


# ── Start validation ────────────────────────────────────────────────────

class TestStartValidation:
    @pytest.mark.asyncio
    async def test_start_already_running_raises(self):
        p = ProxyState()
        p.running = True
        with pytest.raises(RuntimeError, match="already running"):
            await p.start("stdio", command="echo hi")

    @pytest.mark.asyncio
    async def test_start_unknown_transport_raises(self):
        p = ProxyState()
        with pytest.raises(ValueError, match="Unknown transport"):
            await p.start("websocket")

    @pytest.mark.asyncio
    async def test_start_stdio_missing_command_raises(self):
        p = ProxyState()
        with pytest.raises(ValueError, match="Command is required"):
            await p.start("stdio", command=None)

    @pytest.mark.asyncio
    async def test_start_stdio_empty_command_raises(self):
        p = ProxyState()
        with pytest.raises(ValueError, match="Command is required"):
            await p.start("stdio", command="")

    @pytest.mark.asyncio
    async def test_start_sse_missing_url_raises(self):
        p = ProxyState()
        with pytest.raises(ValueError, match="URL is required"):
            await p.start("sse", url=None)

    @pytest.mark.asyncio
    async def test_start_http_missing_url_raises(self):
        p = ProxyState()
        with pytest.raises(ValueError, match="URL is required"):
            await p.start("http", url=None)


# ── Stop ────────────────────────────────────────────────────────────────

class TestStop:
    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_noop(self):
        p = ProxyState()
        await p.stop()
        assert p.running is False


# ── Start/stop lifecycle (mocked) ───────────────────────────────────────

def _apply_patches():
    """Shared helper: patches stdio_client, ClientSession, and SessionManager.run."""
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
        patch(
            "localmcp.proxy.StreamableHTTPSessionManager.run",
            patched_run,
        ),
    )


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop_stdio(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            p = ProxyState()
            await p.start("stdio", command="echo hello")
            assert p.running is True
            assert p.backend_info["transport"] == "stdio"
            assert p.session_manager is not None
            mock_session.initialize.assert_awaited_once()

            await p.stop()
            assert p.running is False
            assert p.session_manager is None
            assert p.client_session is None
            assert p.backend_info == {}

    @pytest.mark.asyncio
    async def test_start_stdio_with_env(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            p = ProxyState()
            env = {"API_KEY": "sk-test", "DEBUG": "1"}
            await p.start("stdio", command="uvx code-index-mcp", env=env)
            assert p.running is True
            assert p.backend_info["transport"] == "stdio"
            assert p.backend_info["env"] == env
            await p.stop()

    @pytest.mark.asyncio
    async def test_start_stdio_without_env_omits_key(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            p = ProxyState()
            await p.start("stdio", command="echo hello")
            assert "env" not in p.backend_info
            await p.stop()

    @pytest.mark.asyncio
    async def test_start_stop_sse(self):
        mock_session = make_mock_session()

        @asynccontextmanager
        async def patched_client_session(read, write):
            yield mock_session

        @asynccontextmanager
        async def patched_run(self):
            yield

        with (
            patch("localmcp.proxy.sse_client", side_effect=fake_sse_client),
            patch("localmcp.proxy.ClientSession", side_effect=patched_client_session),
            patch("localmcp.proxy.StreamableHTTPSessionManager.run", patched_run),
        ):
            p = ProxyState()
            await p.start("sse", url="http://fake/sse")
            assert p.running is True
            assert p.backend_info["transport"] == "sse"
            await p.stop()
            assert p.running is False

    @pytest.mark.asyncio
    async def test_start_stop_http(self):
        mock_session = make_mock_session()

        @asynccontextmanager
        async def patched_client_session(read, write):
            yield mock_session

        @asynccontextmanager
        async def patched_run(self):
            yield

        with (
            patch("localmcp.proxy.streamablehttp_client", side_effect=fake_http_client),
            patch("localmcp.proxy.ClientSession", side_effect=patched_client_session),
            patch("localmcp.proxy.StreamableHTTPSessionManager.run", patched_run),
        ):
            p = ProxyState()
            await p.start("http", url="http://fake/mcp")
            assert p.running is True
            assert p.backend_info["transport"] == "http"
            await p.stop()
            assert p.running is False

    @pytest.mark.asyncio
    async def test_logs_emitted_during_lifecycle(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            p = ProxyState()
            q = p.subscribe_logs()
            await p.start("stdio", command="echo hello")

            logs = []
            while not q.empty():
                logs.append(q.get_nowait())

            assert any("Starting backend" in l for l in logs)
            assert any("Spawned subprocess" in l for l in logs)
            assert any("Proxy is live" in l for l in logs)

            await p.stop()

            while not q.empty():
                logs.append(q.get_nowait())
            assert any("Stopping" in l or "stopped" in l.lower() for l in logs)

    @pytest.mark.asyncio
    async def test_start_failure_sets_error(self):
        @asynccontextmanager
        async def failing_stdio(params):
            raise ConnectionError("boom")
            yield  # pragma: no cover

        with patch("localmcp.proxy.stdio_client", side_effect=failing_stdio):
            p = ProxyState()
            with pytest.raises(ConnectionError, match="boom"):
                await p.start("stdio", command="bad command")
            assert p.running is False
            assert p.error == "boom"

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            p = ProxyState()
            await p.start("stdio", command="echo hello")
            await p.stop()
            await p.stop()
            assert p.running is False


# ── Handler registration ────────────────────────────────────────────────

class TestHandlers:
    """Tests that _register_handlers properly wires up forwarding to the client session."""

    @pytest.mark.asyncio
    async def test_list_tools_forwarded(self):
        mock_session, p1, p2, p3 = _apply_patches()
        with p1, p2, p3:
            p = ProxyState()
            await p.start("stdio", command="echo hello")

            from mcp.server.lowlevel.server import Server
            server = Server("test")
            p._register_handlers(server)

            handlers = server.request_handlers
            from mcp.types import ListToolsRequest
            handler = handlers.get(ListToolsRequest)
            assert handler is not None

            await p.stop()

    @pytest.mark.asyncio
    async def test_register_handlers_directly(self):
        """Exercise all handler closures by calling them through the registered lambdas."""
        mock_session = make_mock_session()
        p = ProxyState()
        p.client_session = mock_session

        from mcp.server.lowlevel.server import Server
        server = Server("test")
        p._register_handlers(server)

        list_tools_fn = None
        call_tool_fn = None
        list_resources_fn = None
        list_templates_fn = None
        read_resource_fn = None
        list_prompts_fn = None
        get_prompt_fn = None

        for handler in server.request_handlers.values():
            name = getattr(handler, "__name__", "") or getattr(handler, "func", lambda: None).__name__
            if "list_tools" in str(handler) or "list_tools" in name:
                list_tools_fn = handler
            elif "call_tool" in str(handler) or "call_tool" in name:
                call_tool_fn = handler
            elif "list_resource_templates" in str(handler) or "list_resource_templates" in name:
                list_templates_fn = handler
            elif "list_resources" in str(handler) or "list_resources" in name:
                list_resources_fn = handler
            elif "read_resource" in str(handler) or "read_resource" in name:
                read_resource_fn = handler
            elif "list_prompts" in str(handler) or "list_prompts" in name:
                list_prompts_fn = handler
            elif "get_prompt" in str(handler) or "get_prompt" in name:
                get_prompt_fn = handler

        p.client_session = None


class TestHandlersDirect:
    """Directly test the handler closures registered by _register_handlers."""

    def _setup(self):
        mock_session = make_mock_session()
        p = ProxyState()
        p.client_session = mock_session

        from mcp.server.lowlevel.server import Server
        server = Server("test-handlers")
        p._register_handlers(server)
        return p, mock_session, server

    @pytest.mark.asyncio
    async def test_list_tools_handler(self):
        p, mock_session, server = self._setup()
        from mcp.types import ListToolsRequest
        handler = server.request_handlers.get(ListToolsRequest)
        if handler:
            result = await handler(None)
            mock_session.list_tools.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_tool_handler(self):
        p, mock_session, server = self._setup()
        from mcp.types import CallToolRequest, CallToolRequestParams
        handler = server.request_handlers.get(CallToolRequest)
        if handler:
            req = CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(name="echo", arguments={"msg": "hi"}),
            )
            result = await handler(req)
            mock_session.call_tool.assert_awaited_once_with("echo", {"msg": "hi"})

    @pytest.mark.asyncio
    async def test_call_tool_error_handler(self):
        from tests.conftest import FakeResult
        mock_session = make_mock_session()
        mock_session.call_tool = AsyncMock(return_value=FakeResult(
            content=[{"type": "text", "text": "err"}], isError=True,
        ))
        p = ProxyState()
        p.client_session = mock_session

        from mcp.server.lowlevel.server import Server
        server = Server("test-err")
        p._register_handlers(server)

        from mcp.types import CallToolRequest, CallToolRequestParams
        handler = server.request_handlers.get(CallToolRequest)
        if handler:
            req = CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(name="bad", arguments={}),
            )
            result = await handler(req)

    @pytest.mark.asyncio
    async def test_list_resources_handler(self):
        p, mock_session, server = self._setup()
        from mcp.types import ListResourcesRequest
        handler = server.request_handlers.get(ListResourcesRequest)
        if handler:
            result = await handler(None)
            mock_session.list_resources.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_resource_templates_handler(self):
        p, mock_session, server = self._setup()
        from mcp.types import ListResourceTemplatesRequest
        handler = server.request_handlers.get(ListResourceTemplatesRequest)
        if handler:
            result = await handler(None)
            mock_session.list_resource_templates.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_read_resource_handler(self):
        p, mock_session, server = self._setup()
        from mcp.types import ReadResourceRequest, ReadResourceRequestParams
        handler = server.request_handlers.get(ReadResourceRequest)
        if handler:
            req = ReadResourceRequest(
                method="resources/read",
                params=ReadResourceRequestParams(uri="file:///test"),
            )
            result = await handler(req)
            mock_session.read_resource.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_prompts_handler(self):
        p, mock_session, server = self._setup()
        from mcp.types import ListPromptsRequest
        handler = server.request_handlers.get(ListPromptsRequest)
        if handler:
            result = await handler(None)
            mock_session.list_prompts.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_prompt_handler(self):
        p, mock_session, server = self._setup()
        from mcp.types import GetPromptRequest, GetPromptRequestParams
        handler = server.request_handlers.get(GetPromptRequest)
        if handler:
            req = GetPromptRequest(
                method="prompts/get",
                params=GetPromptRequestParams(name="test-prompt"),
            )
            result = await handler(req)
            mock_session.get_prompt.assert_awaited_once()
