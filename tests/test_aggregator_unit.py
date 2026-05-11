"""Unit tests for zelosmcp.aggregator.Aggregator."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp.shared.exceptions import McpError
from mcp.types import (
    CallToolResult,
    ErrorData,
    GetPromptResult,
    METHOD_NOT_FOUND,
    Prompt,
    PromptMessage,
    Resource,
    ResourceTemplate,
    TextContent,
    TextResourceContents,
    Tool,
)

from zelosmcp.aggregator import Aggregator, _split_qualified, SEP
from zelosmcp.manager import ProxyManager
from zelosmcp.proxy import ProxyState
from tests.conftest import (
    FakeResult,
    fake_stdio_client,
    fake_sse_client,
    fake_http_client,
    make_mock_session,
)


# ── Pure helpers ────────────────────────────────────────────────────────

class TestSplitQualified:
    def test_well_formed(self):
        assert _split_qualified("alpha__search") == ("alpha", "search")

    def test_double_separator_keeps_remainder(self):
        # Split is on the FIRST `__`; trailing `__` stays in original.
        assert _split_qualified("alpha__a__b") == ("alpha", "a__b")

    def test_no_separator(self):
        assert _split_qualified("search") == ("", "")

    def test_empty(self):
        assert _split_qualified("") == ("", "")

    def test_non_string(self):
        assert _split_qualified(None) == ("", "")  # type: ignore[arg-type]

    def test_separator_constant(self):
        assert SEP == "__"


# ── Test fixtures ───────────────────────────────────────────────────────

def _tool(name: str, desc: str = "desc") -> Tool:
    return Tool(name=name, description=desc, inputSchema={"type": "object", "properties": {}})


def _prompt(name: str) -> Prompt:
    return Prompt(name=name, description="p")


def _resource(uri: str, name: str | None = None) -> Resource:
    return Resource(uri=uri, name=name or uri, mimeType="text/plain")


def _template(pattern: str, name: str) -> ResourceTemplate:
    return ResourceTemplate(uriTemplate=pattern, name=name, mimeType="text/plain")


def _text_contents(uri: str, text: str) -> TextResourceContents:
    return TextResourceContents(uri=uri, mimeType="text/plain", text=text)


def _make_manager_with_two_running_backends():
    """Create a manager with alpha and beta running, each with mocked sessions."""
    m = ProxyManager(mandatory_config_path="")

    sess_alpha = AsyncMock()
    sess_alpha.list_tools = AsyncMock(return_value=FakeResult(
        tools=[_tool("search"), _tool("read")]
    ))
    sess_alpha.list_prompts = AsyncMock(return_value=FakeResult(
        prompts=[_prompt("greet")]
    ))
    sess_alpha.call_tool = AsyncMock(return_value=FakeResult(
        content=[TextContent(type="text", text="from-alpha")],
        isError=False,
    ))
    sess_alpha.get_prompt = AsyncMock(return_value=GetPromptResult(
        description="alpha-desc",
        messages=[PromptMessage(role="user", content=TextContent(type="text", text="hi"))],
    ))
    sess_alpha.list_resources = AsyncMock(return_value=FakeResult(
        resources=[_resource("alpha://one"), _resource("alpha://two")]
    ))
    sess_alpha.list_resource_templates = AsyncMock(return_value=FakeResult(
        resourceTemplates=[_template("alpha://{slug}", "alpha-template")]
    ))
    sess_alpha.read_resource = AsyncMock(return_value=FakeResult(
        contents=[_text_contents("alpha://one", "alpha-body")]
    ))

    sess_beta = AsyncMock()
    sess_beta.list_tools = AsyncMock(return_value=FakeResult(
        tools=[_tool("search"), _tool("write")]
    ))
    sess_beta.list_prompts = AsyncMock(return_value=FakeResult(
        prompts=[_prompt("compose")]
    ))
    sess_beta.call_tool = AsyncMock(return_value=FakeResult(
        content=[TextContent(type="text", text="from-beta")],
        isError=False,
    ))
    sess_beta.get_prompt = AsyncMock(return_value=GetPromptResult(
        description="beta-desc",
        messages=[],
    ))
    sess_beta.list_resources = AsyncMock(return_value=FakeResult(
        resources=[_resource("beta://only")]
    ))
    sess_beta.list_resource_templates = AsyncMock(return_value=FakeResult(
        resourceTemplates=[_template("beta://{kind}/{id}", "beta-template")]
    ))
    sess_beta.read_resource = AsyncMock(return_value=FakeResult(
        contents=[_text_contents("beta://only", "beta-body")]
    ))

    pa = ProxyState(name="alpha")
    pa.client_session = sess_alpha
    pa.running = True
    m.servers["alpha"] = pa

    pb = ProxyState(name="beta")
    pb.client_session = sess_beta
    pb.running = True
    m.servers["beta"] = pb

    return m, sess_alpha, sess_beta


def _register_for_test(agg: Aggregator):
    """Build a low-level Server, register handlers on it, return the registered closures."""
    from mcp.server.lowlevel.server import Server
    server = Server("agg-test")
    agg._register_handlers(server)
    return server


def _find_handler(server, request_type_name: str):
    """Find a registered handler by its request type name."""
    for req_type, handler in server.request_handlers.items():
        if req_type.__name__ == request_type_name:
            return handler
    raise KeyError(request_type_name)


# ── Tools aggregation ───────────────────────────────────────────────────

class TestListTools:
    @pytest.mark.asyncio
    async def test_returns_union_with_prefixed_names(self):
        m, _, _ = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)

        # Handler returns a ServerResult wrapping ListToolsResult.tools
        names = sorted(t.name for t in result.root.tools)
        assert names == [
            "alpha__read",
            "alpha__search",
            "beta__search",
            "beta__write",
        ]

    @pytest.mark.asyncio
    async def test_skips_dead_or_uninitialized_backends(self):
        m, _, _ = _make_manager_with_two_running_backends()
        # Mark beta as not running.
        m.servers["beta"].running = False
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)

        names = sorted(t.name for t in result.root.tools)
        assert names == ["alpha__read", "alpha__search"]

    @pytest.mark.asyncio
    async def test_one_failing_backend_does_not_poison_the_others(self):
        m, sess_alpha, sess_beta = _make_manager_with_two_running_backends()
        sess_beta.list_tools = AsyncMock(side_effect=ConnectionError("nope"))
        agg = Aggregator(m)
        q = m.subscribe_logs()

        server = _register_for_test(agg)
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)

        names = sorted(t.name for t in result.root.tools)
        assert names == ["alpha__read", "alpha__search"]

        seen = []
        try:
            while True:
                seen.append(q.get_nowait())
        except asyncio.QueueEmpty:
            pass
        assert any("[aggregator] beta list_tools failed" in line for line in seen)

    @pytest.mark.asyncio
    async def test_empty_when_no_backends_running(self):
        m = ProxyManager(mandatory_config_path="")
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        assert result.root.tools == []


# ── Tool routing ────────────────────────────────────────────────────────

class TestCallTool:
    @pytest.mark.asyncio
    async def test_routes_by_prefix(self):
        m, sess_alpha, sess_beta = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "CallToolRequest")

        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="alpha__search", arguments={"q": "hi"},
            ),
        )
        await handler(req)

        sess_alpha.call_tool.assert_awaited_once_with("search", {"q": "hi"})
        sess_beta.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_prefix_returns_iserror(self):
        m, sess_alpha, sess_beta = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "CallToolRequest")

        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="ghost__search", arguments={}),
        )
        result = await handler(req)
        # ServerResult wraps a CallToolResult; its isError must be True.
        assert result.root.isError is True
        sess_alpha.call_tool.assert_not_called()
        sess_beta.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_unprefixed_name_returns_iserror(self):
        m, sess_alpha, sess_beta = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "CallToolRequest")

        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="search", arguments={}),
        )
        result = await handler(req)
        assert result.root.isError is True

    @pytest.mark.asyncio
    async def test_backend_iserror_bubbles_up(self):
        m, sess_alpha, _ = _make_manager_with_two_running_backends()
        sess_alpha.call_tool = AsyncMock(return_value=FakeResult(
            content=[TextContent(type="text", text="boom")],
            isError=True,
        ))
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "CallToolRequest")

        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="alpha__search", arguments={}),
        )
        result = await handler(req)
        assert result.root.isError is True

    @pytest.mark.asyncio
    async def test_passes_through_structured_content(self):
        """Backend-provided structuredContent must reach the client unchanged.

        Regression: previously, the aggregator returned only ``r.content``,
        dropping ``structuredContent``. The MCP SDK then replaced the
        response with a validation error for any tool that declared an
        ``outputSchema`` (filesystem, anything FastMCP-based).
        """
        m, sess_alpha, _ = _make_manager_with_two_running_backends()
        sess_alpha.call_tool = AsyncMock(return_value=FakeResult(
            content=[TextContent(type="text", text='{"foo":"bar"}')],
            structuredContent={"foo": "bar"},
            isError=False,
        ))
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "CallToolRequest")

        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="alpha__search", arguments={},
            ),
        )
        result = await handler(req)

        # structuredContent must be populated (not dropped), isError False.
        assert result.root.structuredContent == {"foo": "bar"}
        assert result.root.isError is False


# ── Prompts aggregation ────────────────────────────────────────────────

class TestPrompts:
    @pytest.mark.asyncio
    async def test_list_prompts_prefixes_names(self):
        m, _, _ = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListPromptsRequest")
        result = await handler(None)
        names = sorted(p.name for p in result.root.prompts)
        assert names == ["alpha__greet", "beta__compose"]

    @pytest.mark.asyncio
    async def test_get_prompt_routes_by_prefix(self):
        m, sess_alpha, sess_beta = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "GetPromptRequest")

        from mcp.types import GetPromptRequest, GetPromptRequestParams
        req = GetPromptRequest(
            method="prompts/get",
            params=GetPromptRequestParams(name="beta__compose", arguments={}),
        )
        await handler(req)

        sess_beta.get_prompt.assert_awaited_once_with("compose", {})
        sess_alpha.get_prompt.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_prompt_unknown_returns_error_message(self):
        m, sess_alpha, sess_beta = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "GetPromptRequest")

        from mcp.types import GetPromptRequest, GetPromptRequestParams
        req = GetPromptRequest(
            method="prompts/get",
            params=GetPromptRequestParams(name="ghost__x", arguments={}),
        )
        result = await handler(req)
        assert "Unknown" in result.root.description
        sess_alpha.get_prompt.assert_not_called()
        sess_beta.get_prompt.assert_not_called()


# ── Resources aggregation ──────────────────────────────────────────────

class TestListResources:
    @pytest.mark.asyncio
    async def test_returns_union_uris_unchanged(self):
        m, _, _ = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListResourcesRequest")
        result = await handler(None)
        uris = sorted(str(r.uri) for r in result.root.resources)
        assert uris == ["alpha://one", "alpha://two", "beta://only"]

    @pytest.mark.asyncio
    async def test_populates_origin_cache(self):
        m, _, _ = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListResourcesRequest")
        await handler(None)
        assert agg._resource_origin == {
            "alpha://one": "alpha",
            "alpha://two": "alpha",
            "beta://only": "beta",
        }

    @pytest.mark.asyncio
    async def test_one_failing_backend_does_not_poison_others(self):
        m, _, sess_beta = _make_manager_with_two_running_backends()
        sess_beta.list_resources = AsyncMock(side_effect=ConnectionError("nope"))
        agg = Aggregator(m)
        q = m.subscribe_logs()
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListResourcesRequest")
        result = await handler(None)
        uris = sorted(str(r.uri) for r in result.root.resources)
        assert uris == ["alpha://one", "alpha://two"]
        seen = []
        try:
            while True:
                seen.append(q.get_nowait())
        except asyncio.QueueEmpty:
            pass
        assert any("[aggregator] beta list_resources failed" in line for line in seen)

    @pytest.mark.asyncio
    async def test_method_not_found_is_silently_skipped(self):
        """Backends like @modelcontextprotocol/server-filesystem don't
        implement list_resources at all; they return JSON-RPC -32601. The
        aggregator must silently skip those (no log noise) and still merge
        results from backends that do implement the method."""
        m, _, sess_beta = _make_manager_with_two_running_backends()
        sess_beta.list_resources = AsyncMock(
            side_effect=McpError(ErrorData(code=METHOD_NOT_FOUND, message="Method not found"))
        )
        agg = Aggregator(m)
        q = m.subscribe_logs()
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListResourcesRequest")
        result = await handler(None)
        uris = sorted(str(r.uri) for r in result.root.resources)
        # alpha's resources still come through; beta is silently skipped.
        assert uris == ["alpha://one", "alpha://two"]
        # Drain the log queue and assert no "list_resources failed" line.
        seen = []
        try:
            while True:
                seen.append(q.get_nowait())
        except asyncio.QueueEmpty:
            pass
        assert not any("list_resources failed" in line for line in seen), (
            f"Method-not-found should be silent; got log lines: {seen}"
        )

    @pytest.mark.asyncio
    async def test_empty_when_no_backends_running(self):
        m = ProxyManager(mandatory_config_path="")
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListResourcesRequest")
        result = await handler(None)
        assert result.root.resources == []


class TestListResourceTemplates:
    @pytest.mark.asyncio
    async def test_returns_union(self):
        m, _, _ = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListResourceTemplatesRequest")
        result = await handler(None)
        patterns = sorted(t.uriTemplate for t in result.root.resourceTemplates)
        assert patterns == ["alpha://{slug}", "beta://{kind}/{id}"]

    @pytest.mark.asyncio
    async def test_failing_backend_skipped(self):
        m, sess_alpha, _ = _make_manager_with_two_running_backends()
        sess_alpha.list_resource_templates = AsyncMock(side_effect=RuntimeError("boom"))
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ListResourceTemplatesRequest")
        result = await handler(None)
        patterns = [t.uriTemplate for t in result.root.resourceTemplates]
        assert patterns == ["beta://{kind}/{id}"]


class TestReadResource:
    @staticmethod
    def _read_request(uri: str):
        from mcp.types import ReadResourceRequest, ReadResourceRequestParams
        return ReadResourceRequest(
            method="resources/read",
            params=ReadResourceRequestParams(uri=uri),
        )

    @pytest.mark.asyncio
    async def test_cached_owner_routes_directly(self):
        m, sess_alpha, sess_beta = _make_manager_with_two_running_backends()
        agg = Aggregator(m)
        agg._resource_origin["alpha://one"] = "alpha"
        server = _register_for_test(agg)
        handler = _find_handler(server, "ReadResourceRequest")
        await handler(self._read_request("alpha://one"))
        sess_alpha.read_resource.assert_awaited_once()
        sess_beta.read_resource.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_fans_out_first_success_wins_and_caches(self):
        m, sess_alpha, sess_beta = _make_manager_with_two_running_backends()
        # Alpha doesn't know this URI.
        sess_alpha.read_resource = AsyncMock(side_effect=KeyError("not mine"))
        # Beta serves it.
        sess_beta.read_resource = AsyncMock(return_value=FakeResult(
            contents=[_text_contents("templated://x", "beta-body")]
        ))
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ReadResourceRequest")

        result = await handler(self._read_request("templated://x"))
        sess_alpha.read_resource.assert_awaited_once()
        sess_beta.read_resource.assert_awaited_once()
        assert agg._resource_origin["templated://x"] == "beta"
        # Re-read: should now go straight to beta, not fan out again.
        sess_alpha.read_resource.reset_mock()
        sess_beta.read_resource.reset_mock()
        await handler(self._read_request("templated://x"))
        sess_alpha.read_resource.assert_not_called()
        sess_beta.read_resource.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cached_owner_unavailable_falls_back(self):
        m, _, sess_beta = _make_manager_with_two_running_backends()
        # Mark alpha as no longer running so the cache hit can't be used.
        m.servers["alpha"].running = False
        agg = Aggregator(m)
        agg._resource_origin["beta://only"] = "alpha"  # stale cache pointing at alpha
        server = _register_for_test(agg)
        handler = _find_handler(server, "ReadResourceRequest")
        await handler(self._read_request("beta://only"))
        sess_beta.read_resource.assert_awaited_once()
        assert agg._resource_origin["beta://only"] == "beta"

    @pytest.mark.asyncio
    async def test_cached_owner_errors_invalidates_cache(self):
        m, sess_alpha, sess_beta = _make_manager_with_two_running_backends()
        sess_alpha.read_resource = AsyncMock(side_effect=ConnectionError("alpha down"))
        sess_beta.read_resource = AsyncMock(return_value=FakeResult(
            contents=[_text_contents("shared://x", "beta-body")]
        ))
        agg = Aggregator(m)
        agg._resource_origin["shared://x"] = "alpha"
        server = _register_for_test(agg)
        handler = _find_handler(server, "ReadResourceRequest")

        await handler(self._read_request("shared://x"))
        sess_alpha.read_resource.assert_awaited_once()
        sess_beta.read_resource.assert_awaited_once()
        # Cache now points at beta.
        assert agg._resource_origin["shared://x"] == "beta"

    @pytest.mark.asyncio
    async def test_all_backends_fail_raises(self):
        m, sess_alpha, sess_beta = _make_manager_with_two_running_backends()
        sess_alpha.read_resource = AsyncMock(side_effect=KeyError("a"))
        sess_beta.read_resource = AsyncMock(side_effect=ValueError("b"))
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ReadResourceRequest")
        with pytest.raises((KeyError, ValueError, Exception)):
            await handler(self._read_request("ghost://nowhere"))

    @pytest.mark.asyncio
    async def test_no_backends_running_raises(self):
        m = ProxyManager(mandatory_config_path="")
        agg = Aggregator(m)
        server = _register_for_test(agg)
        handler = _find_handler(server, "ReadResourceRequest")
        with pytest.raises(RuntimeError, match="No backend"):
            await handler(self._read_request("ghost://nowhere"))


# ── Lifecycle ──────────────────────────────────────────────────────────

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        @asynccontextmanager
        async def patched_run(self):
            yield

        with patch("zelosmcp.aggregator.StreamableHTTPSessionManager.run", patched_run):
            m = ProxyManager(mandatory_config_path="")
            agg = Aggregator(m)
            await agg.start()
            assert agg.running is True
            assert agg.session_manager is not None
            await agg.stop()
            assert agg.running is False
            assert agg.session_manager is None

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        @asynccontextmanager
        async def patched_run(self):
            yield

        with patch("zelosmcp.aggregator.StreamableHTTPSessionManager.run", patched_run):
            m = ProxyManager(mandatory_config_path="")
            agg = Aggregator(m)
            await agg.start()
            first_sm = agg.session_manager
            await agg.start()
            assert agg.session_manager is first_sm
            await agg.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_started_is_noop(self):
        m = ProxyManager(mandatory_config_path="")
        agg = Aggregator(m)
        await agg.stop()
        assert agg.running is False


# ── Compression scopes ─────────────────────────────────────────────────

from zelosmcp.config import CompressSpec, ServerSpec  # noqa: E402


def _state_with_tools(name: str, tools: list[Tool]) -> tuple[ProxyState, AsyncMock]:
    """Build a fake ProxyState whose client_session.list_tools returns ``tools``
    and whose call_tool dispatch is observable via the returned mock."""
    sess = AsyncMock()
    sess.list_tools = AsyncMock(return_value=FakeResult(tools=tools))
    sess.call_tool = AsyncMock(return_value=FakeResult(
        content=[TextContent(type="text", text="dispatched")],
        isError=False,
    ))
    state = ProxyState(name=name)
    state.client_session = sess
    state.running = True
    return state, sess


def _spec_for(name: str, *, compress: CompressSpec | None) -> ServerSpec:
    return ServerSpec(
        name=name, transport="stdio", command="echo",
        compress=compress,
    )


def _setup(compress: CompressSpec | None, tool_count: int = 4):
    m = ProxyManager(mandatory_config_path="")
    tools = [
        Tool(
            name=f"tool_{i}",
            description=f"Tool number {i}. With more detail after the period.",
            inputSchema={"type": "object", "properties": {"arg": {"type": "string"}}},
        )
        for i in range(tool_count)
    ]
    state, sess = _state_with_tools("alpha", tools)
    m.servers["alpha"] = state
    m._specs["alpha"] = _spec_for("alpha", compress=compress)
    agg = Aggregator(m)
    server = _register_for_test(agg)
    return m, agg, server, state, sess, tools


class TestCompressedScopes:
    @pytest.mark.asyncio
    async def test_no_compress_block_returns_full_prefixed_list(self):
        m, agg, server, _, _, tools = _setup(compress=None, tool_count=3)
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = [t.name for t in result.root.tools]
        assert sorted(names) == ["alpha__tool_0", "alpha__tool_1", "alpha__tool_2"]
        # No catalog cached when compress is unset.
        assert agg.compressed_catalog == {}

    @pytest.mark.asyncio
    async def test_scope_catalog_keeps_full_list_but_caches(self):
        m, agg, server, _, _, tools = _setup(
            compress=CompressSpec(level="medium", scope="catalog"), tool_count=3
        )
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = [t.name for t in result.root.tools]
        # Full uncompressed surface is preserved on the wire.
        assert sorted(names) == ["alpha__tool_0", "alpha__tool_1", "alpha__tool_2"]
        # But the catalog cache is still populated for docs/discovery.
        assert "alpha" in agg.compressed_catalog
        assert set(agg.compressed_catalog["alpha"].keys()) == {"tool_0", "tool_1", "tool_2"}

    @pytest.mark.asyncio
    async def test_scope_aggregator_returns_wrappers(self):
        m, agg, server, _, _, _ = _setup(
            compress=CompressSpec(level="medium", scope="aggregator"), tool_count=4
        )
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = [t.name for t in result.root.tools]
        assert sorted(names) == [
            "alpha__get_tool_schema",
            "alpha__invoke_tool",
            "alpha__search_tools",
        ]
        assert "alpha" in agg.compressed_catalog

    @pytest.mark.asyncio
    async def test_scope_global_returns_wrappers_at_aggregator_too(self):
        m, agg, server, _, _, _ = _setup(
            compress=CompressSpec(level="medium", scope="global"), tool_count=4
        )
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = [t.name for t in result.root.tools]
        assert sorted(names) == [
            "alpha__get_tool_schema",
            "alpha__invoke_tool",
            "alpha__search_tools",
        ]

    @pytest.mark.asyncio
    async def test_level_low_skips_wrappers(self):
        # `level=low` is treated as "show full descriptions" — no wrappers,
        # but the catalog cache is still populated.
        m, agg, server, _, _, _ = _setup(
            compress=CompressSpec(level="low", scope="aggregator"), tool_count=2
        )
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = [t.name for t in result.root.tools]
        assert sorted(names) == ["alpha__tool_0", "alpha__tool_1"]
        assert "alpha" in agg.compressed_catalog

    @pytest.mark.asyncio
    async def test_level_max_returns_single_wrapper(self):
        m, agg, server, _, _, _ = _setup(
            compress=CompressSpec(level="max", scope="aggregator"), tool_count=15
        )
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = [t.name for t in result.root.tools]
        assert names == ["alpha__list_tools"]


class TestCompressedCallToolDispatch:
    @pytest.mark.asyncio
    async def test_get_tool_schema_returns_serialized_tool(self):
        m, agg, server, _, sess, _ = _setup(
            compress=CompressSpec(level="medium", scope="aggregator"), tool_count=3
        )
        # Populate the catalog by calling list_tools first.
        await _find_handler(server, "ListToolsRequest")(None)
        call_handler = _find_handler(server, "CallToolRequest")
        # Wrap into the request shape the handler expects.
        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="alpha__get_tool_schema",
                arguments={"tool_name": "tool_1"},
            ),
        )
        result = await call_handler(req)
        # No backend dispatch on get_tool_schema.
        sess.call_tool.assert_not_called()
        text = result.root.content[0].text
        assert "tool_1" in text

    @pytest.mark.asyncio
    async def test_search_tools_returns_matching_catalog_without_dispatch(self):
        m, agg, server, _, sess, _ = _setup(
            compress=CompressSpec(level="medium", scope="aggregator"), tool_count=4
        )
        await _find_handler(server, "ListToolsRequest")(None)
        call_handler = _find_handler(server, "CallToolRequest")
        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="alpha__search_tools",
                arguments={"query": "number 2"},
            ),
        )
        result = await call_handler(req)
        sess.call_tool.assert_not_called()
        assert result.root.isError is False
        text = result.root.content[0].text
        assert "tool_2: Tool number 2" in text
        assert "tool_1" not in text

    @pytest.mark.asyncio
    async def test_invoke_tool_dispatches_to_backend(self):
        m, agg, server, _, sess, _ = _setup(
            compress=CompressSpec(level="medium", scope="aggregator"), tool_count=3
        )
        await _find_handler(server, "ListToolsRequest")(None)
        call_handler = _find_handler(server, "CallToolRequest")
        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="alpha__invoke_tool",
                arguments={"tool_name": "tool_2", "tool_input": {"arg": "v"}},
            ),
        )
        result = await call_handler(req)
        # Underlying backend was called with the actual tool_name + tool_input.
        sess.call_tool.assert_awaited_once_with("tool_2", {"arg": "v"})
        # Response content forwarded.
        assert result.root.content[0].text == "dispatched"

    @pytest.mark.asyncio
    async def test_invoke_tool_unknown_name_skips_dispatch(self):
        m, agg, server, _, sess, _ = _setup(
            compress=CompressSpec(level="medium", scope="aggregator"), tool_count=2
        )
        await _find_handler(server, "ListToolsRequest")(None)
        call_handler = _find_handler(server, "CallToolRequest")
        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="alpha__invoke_tool",
                arguments={"tool_name": "ghost", "tool_input": {}},
            ),
        )
        result = await call_handler(req)
        sess.call_tool.assert_not_called()
        assert result.root.isError is True

    @pytest.mark.asyncio
    async def test_uncompressed_backend_gets_normal_dispatch(self):
        # No compress block — `<backend>__invoke_tool` is just a normal name
        # that gets forwarded to the backend if it exists; the wrapper
        # interception does NOT activate.
        m, agg, server, _, sess, _ = _setup(compress=None, tool_count=2)
        await _find_handler(server, "ListToolsRequest")(None)
        call_handler = _find_handler(server, "CallToolRequest")
        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="alpha__tool_0",
                arguments={"x": 1},
            ),
        )
        result = await call_handler(req)
        sess.call_tool.assert_awaited_once_with("tool_0", {"x": 1})
        assert result.root.content[0].text == "dispatched"
