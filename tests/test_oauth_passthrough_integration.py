"""End-to-end tests for OAuth-passthrough through the aggregator at /mcp.

Exercises the full Phase 2 path:

- Aggregator at ``/mcp`` discovers passthrough backends and fans
  ``tools/list`` / ``tools/call`` out via :class:`PassthroughSessionPool`.
- :class:`PassthroughChallengeError` raised inside a handler is rewritten
  to HTTP 401 + WWW-Authenticate by the ASGI middleware in
  :mod:`zelosmcp.app`, so the MCP client (Cursor) follows the upstream's
  resource_metadata directly.
- Different inbound Authorization headers map to different upstream
  sessions; the same Authorization shares one session.

The upstream MCP server is replaced by a fake streamablehttp_client +
ClientSession that records who called what so we can assert routing
without a real network.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zelosmcp.app import create_app
from zelosmcp.manager import ProxyManager
from zelosmcp.passthrough_pool import PassthroughChallengeError


# ── Fake upstream ───────────────────────────────────────────────────────


class _UpstreamRecord:
    """Records the inbound Authorization a fake session was created with
    so we can assert per-Cursor routing without a real network.
    """

    def __init__(self) -> None:
        # Each list entry is a tuple ``(authorization, session_id)``.
        self.opened_sessions: list[tuple[str | None, int]] = []
        self.tool_calls: list[tuple[str | None, str, dict[str, Any]]] = []
        self._counter = 0
        # Behaviour switches — the test toggles these to simulate the
        # upstream returning various status codes.
        self.fail_on_init: bool = False
        self.fail_with_response_headers: dict[str, str] | None = None
        self.tools_payload: list[dict[str, Any]] | None = None

    def make_streamablehttp(self):
        rec = self

        @asynccontextmanager
        async def fake_streamablehttp_client(url: str, *, headers=None, **_kwargs):
            # Capture the Authorization the pool passed in so the test
            # can verify static-bearer / per-Cursor logic. We thread
            # this into the session via a closure (set just below).
            current_auth = (headers or {}).get("Authorization")
            rec._counter += 1
            session_id = rec._counter

            class _Session:
                _captured_auth = current_auth
                _id = session_id

                async def initialize(self):
                    rec.opened_sessions.append((self._captured_auth, self._id))
                    if rec.fail_on_init:
                        response = MagicMock()
                        response.headers = (
                            rec.fail_with_response_headers or {}
                        )
                        err = RuntimeError("auth required")
                        err.response = response  # type: ignore[attr-defined]
                        raise err

                async def list_tools(self):
                    payload = rec.tools_payload or [
                        {"name": "create_issue", "description": "make an issue", "inputSchema": {"type": "object", "properties": {}}},
                    ]

                    class _R:
                        tools = [
                            type("T", (), {
                                "name": t["name"],
                                "description": t.get("description", ""),
                                "inputSchema": t.get("inputSchema", {"type": "object"}),
                                "model_copy": lambda self, update: type(self)(**{
                                    "name": update.get("name", self.name),
                                    "description": self.description,
                                    "inputSchema": self.inputSchema,
                                }),
                                "model_dump": lambda self, by_alias=False, exclude_none=False: {
                                    "name": self.name,
                                    "description": self.description,
                                    "inputSchema": self.inputSchema,
                                },
                                "__init__": (
                                    lambda self, name, description="", inputSchema=None: (
                                        setattr(self, "name", name),
                                        setattr(self, "description", description),
                                        setattr(self, "inputSchema", inputSchema or {"type": "object"}),
                                    ) and None
                                ),
                            })(name=t["name"], description=t.get("description", ""), inputSchema=t.get("inputSchema", {"type": "object"}))
                            for t in payload
                        ]

                    return _R()

                async def call_tool(self, name, arguments):
                    rec.tool_calls.append((self._captured_auth, name, arguments))

                    class _R:
                        content = [
                            type("Block", (), {
                                "type": "text",
                                "text": f"called {name} as {_Session._captured_auth}",
                                "model_dump": lambda self, by_alias=False, exclude_none=False: {
                                    "type": "text",
                                    "text": self.text,
                                },
                            })()
                        ]
                        structuredContent = None
                        isError = False
                        meta = None

                    return _R()

                async def list_prompts(self):
                    class _R:
                        prompts = []
                    return _R()

                async def list_resources(self):
                    class _R:
                        resources = []
                    return _R()

                async def list_resource_templates(self):
                    class _R:
                        resourceTemplates = []
                    return _R()

            yield MagicMock(), MagicMock(), MagicMock()

        return fake_streamablehttp_client

    def make_client_session(self):
        rec = self

        @asynccontextmanager
        async def fake_client_session(read, write):
            # We can't recover the Authorization from `read`/`write`
            # mocks, so the streamablehttp wrapper above stored the
            # captured auth on a closure that the per-session class
            # picks up. To avoid the closure dance, we use a singleton
            # session per pool entry and the pool's per-key isolation
            # gives us the correct routing — auth is matched on the
            # tool-call recording side.
            class _Session:
                async def initialize(self):
                    rec.opened_sessions.append((None, len(rec.opened_sessions) + 1))
                    if rec.fail_on_init:
                        response = MagicMock()
                        response.headers = (
                            rec.fail_with_response_headers or {}
                        )
                        err = RuntimeError("auth required")
                        err.response = response  # type: ignore[attr-defined]
                        raise err

                async def list_tools(self):
                    payload = rec.tools_payload or [
                        {
                            "name": "create_issue",
                            "description": "make an issue",
                            "inputSchema": {"type": "object", "properties": {}},
                        },
                    ]
                    from mcp.types import Tool

                    class _R:
                        tools = [
                            Tool(
                                name=t["name"],
                                description=t.get("description", ""),
                                inputSchema=t.get(
                                    "inputSchema", {"type": "object", "properties": {}}
                                ),
                            )
                            for t in payload
                        ]

                    return _R()

                async def call_tool(self, name, arguments):
                    # The per-key pool entry keeps the same
                    # _Session instance for the lifetime of the
                    # entry, but we don't have the original auth
                    # here. Instead the caller passes a 'sentinel'
                    # in arguments so the test can correlate.
                    rec.tool_calls.append(
                        (arguments.get("__test_token__"), name, arguments)
                    )

                    class _R:
                        from mcp.types import TextContent
                        content = [
                            TextContent(
                                type="text",
                                text=f"ok {arguments.get('__test_token__')}",
                            )
                        ]
                        structuredContent = None
                        isError = False
                        meta = None

                    return _R()

                async def list_prompts(self):
                    class _R:
                        prompts = []
                    return _R()

                async def list_resources(self):
                    class _R:
                        resources = []
                    return _R()

                async def list_resource_templates(self):
                    class _R:
                        resourceTemplates = []
                    return _R()

            yield _Session()

        return fake_client_session


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _fresh():
    manager = ProxyManager(mandatory_config_path="")
    app = create_app(manager)
    return app, manager


# ── Tests ──────────────────────────────────────────────────────────────


class TestAggregatorPassthrough:
    @pytest.mark.asyncio
    async def test_tools_list_emits_wrapper_pair_with_token(self):
        # When the inbound caller has a valid token, list_tools opens
        # the upstream session, fetches the catalog, caches it, and
        # emits the compressed wrapper pair (`github__get_tool_schema`
        # + `github__invoke_tool`) — NOT the unwrapped per-tool surface.
        rec = _UpstreamRecord()
        with patch(
            "zelosmcp.passthrough_pool.streamablehttp_client",
            side_effect=rec.make_streamablehttp(),
        ), patch(
            "zelosmcp.passthrough_pool.ClientSession",
            side_effect=rec.make_client_session(),
        ):
            app, manager = _fresh()
            await manager.start_all({
                "mcpServers": {
                    "github": {
                        "type": "streamable-http",
                        "url": "https://api.example.com/mcp",
                        "passthrough": True,
                    },
                },
            })
            try:
                async with _client(app) as c:
                    r = await c.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"},
                            },
                        },
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "Authorization": "Bearer caller-token",
                        },
                    )
                    assert r.status_code == 200
                    r = await c.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/list",
                        },
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "Authorization": "Bearer caller-token",
                        },
                    )
                    assert r.status_code == 200
                    body = r.json()
                    tool_names = [t["name"] for t in body["result"]["tools"]]
                    # Compressed wrappers, not raw per-tool surface.
                    assert "github__get_tool_schema" in tool_names
                    assert "github__invoke_tool" in tool_names
                    assert "github__create_issue" not in tool_names
            finally:
                await manager.stop_all()

    @pytest.mark.asyncio
    async def test_tools_list_emits_auth_pending_wrappers_when_unauth(self):
        # No inbound token AND no cached catalog: the aggregator MUST
        # still emit the wrapper pair so the agent has a known entry
        # point. The wrapper descriptions are decorated with the
        # auth-pending notice so the agent expects a 401 on first call.
        rec = _UpstreamRecord()
        rec.fail_on_init = True
        rec.fail_with_response_headers = {
            "WWW-Authenticate": (
                'Bearer resource_metadata='
                '"https://api.example.com/.well-known/oauth-protected-resource"'
            ),
        }
        with patch(
            "zelosmcp.passthrough_pool.streamablehttp_client",
            side_effect=rec.make_streamablehttp(),
        ), patch(
            "zelosmcp.passthrough_pool.ClientSession",
            side_effect=rec.make_client_session(),
        ):
            app, manager = _fresh()
            await manager.start_all({
                "mcpServers": {
                    "github": {
                        "type": "streamable-http",
                        "url": "https://api.example.com/mcp",
                        "passthrough": True,
                    },
                },
            })
            try:
                async with _client(app) as c:
                    await c.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"},
                            },
                        },
                        headers={"Accept": "application/json, text/event-stream"},
                    )
                    r = await c.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/list",
                        },
                        headers={"Accept": "application/json, text/event-stream"},
                    )
                    assert r.status_code == 200
                    body = r.json()
                    tool_names = [t["name"] for t in body["result"]["tools"]]
                    assert "github__get_tool_schema" in tool_names
                    assert "github__invoke_tool" in tool_names
                    invoke_desc = next(
                        t["description"] for t in body["result"]["tools"]
                        if t["name"] == "github__invoke_tool"
                    )
                    assert "OAuth" in invoke_desc
                    assert "401" in invoke_desc
            finally:
                await manager.stop_all()

    @pytest.mark.asyncio
    async def test_invoke_wrapper_propagates_401_with_www_authenticate(self):
        """When the agent invokes the compressed wrapper without auth,
        the aggregator middleware MUST surface 401 + WWW-Authenticate
        so Cursor's OAuth client triggers. Calling the wrapper IS the
        OAuth-entry mechanism — the agent doesn't need to know specific
        upstream tool names ahead of time.
        """
        rec = _UpstreamRecord()
        rec.fail_on_init = True
        rec.fail_with_response_headers = {
            "WWW-Authenticate": (
                'Bearer resource_metadata='
                '"https://api.example.com/.well-known/oauth-protected-resource"'
            ),
        }
        with patch(
            "zelosmcp.passthrough_pool.streamablehttp_client",
            side_effect=rec.make_streamablehttp(),
        ), patch(
            "zelosmcp.passthrough_pool.ClientSession",
            side_effect=rec.make_client_session(),
        ):
            app, manager = _fresh()
            await manager.start_all({
                "mcpServers": {
                    "github": {
                        "type": "streamable-http",
                        "url": "https://api.example.com/mcp",
                        "passthrough": True,
                    },
                },
            })
            try:
                async with _client(app) as c:
                    await c.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"},
                            },
                        },
                        headers={"Accept": "application/json, text/event-stream"},
                    )
                    r = await c.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "github__invoke_tool",
                                "arguments": {
                                    "tool_name": "anything",
                                    "tool_input": {},
                                },
                            },
                        },
                        headers={"Accept": "application/json, text/event-stream"},
                    )
                    assert r.status_code == 401
                    ww = r.headers.get("www-authenticate")
                    assert ww is not None
                    assert "resource_metadata" in ww
                    body = r.json()
                    assert body["error"] == "authentication_required"
                    assert body["backend"] == "github"
            finally:
                await manager.stop_all()

    @pytest.mark.asyncio
    async def test_invoke_wrapper_succeeds_after_auth(self):
        """With a valid token, github__invoke_tool dispatches through
        the existing handle_compressed_call path: the aggregator opens
        the upstream session, fetches + caches the catalog, looks up
        the tool name, calls the upstream, returns the result.
        """
        rec = _UpstreamRecord()
        with patch(
            "zelosmcp.passthrough_pool.streamablehttp_client",
            side_effect=rec.make_streamablehttp(),
        ), patch(
            "zelosmcp.passthrough_pool.ClientSession",
            side_effect=rec.make_client_session(),
        ):
            app, manager = _fresh()
            await manager.start_all({
                "mcpServers": {
                    "github": {
                        "type": "streamable-http",
                        "url": "https://api.example.com/mcp",
                        "passthrough": True,
                    },
                },
            })
            try:
                async with _client(app) as c:
                    await c.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"},
                            },
                        },
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "Authorization": "Bearer caller-token",
                        },
                    )
                    r = await c.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "github__invoke_tool",
                                "arguments": {
                                    "tool_name": "create_issue",
                                    "tool_input": {"__test_token__": "hello"},
                                },
                            },
                        },
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "Authorization": "Bearer caller-token",
                        },
                    )
                    assert r.status_code == 200
                    body = r.json()
                    # The fake upstream's call_tool returns text "ok <token>"
                    # so we know dispatch reached it (not stopped at the
                    # wrapper-validation step).
                    text_blocks = [
                        b for b in body["result"]["content"]
                        if b.get("type") == "text"
                    ]
                    assert text_blocks
                    assert "ok hello" in text_blocks[0]["text"]
                    # Cached catalog is shared across users now.
                    state = manager.get("github")
                    assert "create_issue" in state.passthrough_catalog
            finally:
                await manager.stop_all()

    @pytest.mark.asyncio
    async def test_per_cursor_session_isolation(self):
        """Two callers with DIFFERENT tokens get DIFFERENT sessions; the
        same token shares a session.

        We verify by counting the number of upstream session
        initialisations the pool performs. Three logical "Cursors"
        with two distinct tokens => exactly two upstream sessions.
        """
        rec = _UpstreamRecord()
        with patch(
            "zelosmcp.passthrough_pool.streamablehttp_client",
            side_effect=rec.make_streamablehttp(),
        ), patch(
            "zelosmcp.passthrough_pool.ClientSession",
            side_effect=rec.make_client_session(),
        ):
            app, manager = _fresh()
            await manager.start_all({
                "mcpServers": {
                    "github": {
                        "type": "streamable-http",
                        "url": "https://api.example.com/mcp",
                        "passthrough": True,
                    },
                },
            })
            try:
                async with _client(app) as c:
                    for token in ("Bearer A", "Bearer A", "Bearer B"):
                        await c.post(
                            "/mcp",
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "initialize",
                                "params": {
                                    "protocolVersion": "2025-03-26",
                                    "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "1"},
                                },
                            },
                            headers={
                                "Accept": "application/json, text/event-stream",
                                "Authorization": token,
                            },
                        )
                        await c.post(
                            "/mcp",
                            json={
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "tools/list",
                            },
                            headers={
                                "Accept": "application/json, text/event-stream",
                                "Authorization": token,
                            },
                        )
                # Three calls → two unique tokens → exactly two
                # upstream session initialisations.
                assert len(rec.opened_sessions) == 2
            finally:
                await manager.stop_all()
