"""Unit tests for zelosmcp.compression — pure helpers shared by the
aggregator and the per-backend `scope=global` wrapper."""
from __future__ import annotations

import json

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from zelosmcp.compression import (
    compress_for_catalog,
    compressed_tool_list,
    handle_compressed_call,
    make_get_schema_wrapper,
    make_invoke_wrapper,
    make_list_tools_wrapper,
    wrapper_tool_names,
)


def _tool(name, *, description="", params=None) -> Tool:
    schema = {"type": "object", "properties": {p: {"type": "string"} for p in (params or [])}}
    return Tool(name=name, description=description, inputSchema=schema)


class TestWrapperToolNames:
    def test_max_returns_list_tools(self):
        assert wrapper_tool_names("max") == ("list_tools",)

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_non_max_returns_pair(self, level):
        assert wrapper_tool_names(level) == ("get_tool_schema", "invoke_tool")


class TestCompressionFormat:
    def test_medium_strips_to_first_sentence(self):
        t = _tool("foo", description="Do thing one. Then do thing two.")
        assert compress_for_catalog(t, "medium") == "- foo: Do thing one"

    def test_medium_handles_no_description(self):
        assert compress_for_catalog(_tool("bare"), "medium") == "- bare"

    def test_high_lists_param_names(self):
        t = _tool("query", params=["sql", "limit", "max_rows"])
        assert compress_for_catalog(t, "high") == "- query(sql, limit, max_rows)"

    def test_high_handles_no_params(self):
        assert compress_for_catalog(_tool("ping"), "high") == "- ping()"

    def test_low_keeps_full_description_flattened(self):
        t = _tool("foo", description="Line one.\nLine two.")
        assert compress_for_catalog(t, "low") == "- foo: Line one. Line two."

    def test_low_with_no_description_falls_back_to_name(self):
        assert compress_for_catalog(_tool("bare"), "low") == "- bare"

    def test_max_renders_like_medium(self):
        # max-level uses medium-style summary in the list_tools tool body.
        t = _tool("foo", description="Hello world.")
        assert compress_for_catalog(t, "max") == "- foo: Hello world"


class TestCompressedToolList:
    def test_medium_returns_pair(self):
        tools = [_tool("a"), _tool("b")]
        wrappers = compressed_tool_list(prefix="ws", tools=tools, level="medium")
        assert [w.name for w in wrappers] == ["ws__get_tool_schema", "ws__invoke_tool"]

    def test_high_returns_pair(self):
        tools = [_tool("a")]
        wrappers = compressed_tool_list(prefix="ws", tools=tools, level="high")
        assert [w.name for w in wrappers] == ["ws__get_tool_schema", "ws__invoke_tool"]

    def test_max_returns_single_list_tools(self):
        wrappers = compressed_tool_list(
            prefix="ws", tools=[_tool("a"), _tool("b"), _tool("c")], level="max"
        )
        assert len(wrappers) == 1
        assert wrappers[0].name == "ws__list_tools"

    def test_empty_prefix_drops_double_underscore(self):
        # Used by the per-backend wrapper at /<name>/mcp.
        wrappers = compressed_tool_list(prefix="", tools=[_tool("a")], level="medium")
        assert [w.name for w in wrappers] == ["get_tool_schema", "invoke_tool"]


class TestMakeWrappers:
    def test_get_schema_wrapper_inlines_catalog_in_description(self):
        tools = [
            _tool("create_thing", description="Create a thing. With detail."),
            _tool("delete_thing", description="Remove a thing irreversibly."),
        ]
        w = make_get_schema_wrapper(prefix="ws", tools=tools, level="medium")
        assert w.name == "ws__get_tool_schema"
        # Both tool summaries must appear in the wrapper's description.
        assert "create_thing: Create a thing" in w.description
        assert "delete_thing: Remove a thing irreversibly" in w.description
        # The wrapper accepts a single `tool_name` arg.
        assert w.inputSchema["required"] == ["tool_name"]

    def test_invoke_wrapper_schema(self):
        w = make_invoke_wrapper(prefix="ws", n_tools=5)
        assert w.name == "ws__invoke_tool"
        assert set(w.inputSchema["required"]) == {"tool_name", "tool_input"}
        assert w.inputSchema["properties"]["tool_input"]["type"] == "object"
        # Tool count is mentioned for the LLM's benefit.
        assert "5" in w.description

    def test_list_tools_wrapper_takes_no_args(self):
        w = make_list_tools_wrapper(prefix="ws", n_tools=3)
        assert w.name == "ws__list_tools"
        assert w.inputSchema == {"type": "object", "properties": {}}


def _catalog(*tools: Tool) -> dict[str, Tool]:
    return {t.name: t for t in tools}


class TestHandleCompressedCall:
    @pytest.mark.asyncio
    async def test_get_tool_schema_returns_tool_json(self):
        cat = _catalog(_tool("foo", description="A foo.", params=["x"]))

        async def dispatch(name, args):
            raise AssertionError("dispatch should not be called for get_tool_schema")

        result = await handle_compressed_call(cat, "get_tool_schema", {"tool_name": "foo"}, dispatch)
        assert isinstance(result, CallToolResult)
        assert result.isError is False
        assert isinstance(result.content[0], TextContent)
        body = json.loads(result.content[0].text)
        assert body["name"] == "foo"
        assert body["description"] == "A foo."
        assert "x" in body["inputSchema"]["properties"]

    @pytest.mark.asyncio
    async def test_get_tool_schema_unknown_tool_is_error(self):
        cat = _catalog(_tool("foo"))
        result = await handle_compressed_call(
            cat, "get_tool_schema", {"tool_name": "bar"}, dispatch=lambda *a: None  # type: ignore[arg-type]
        )
        assert result.isError is True
        assert "foo" in result.content[0].text  # available names are listed

    @pytest.mark.asyncio
    async def test_get_tool_schema_missing_arg_is_error(self):
        cat = _catalog(_tool("foo"))
        result = await handle_compressed_call(
            cat, "get_tool_schema", {}, dispatch=lambda *a: None  # type: ignore[arg-type]
        )
        assert result.isError is True
        assert "tool_name" in result.content[0].text

    @pytest.mark.asyncio
    async def test_invoke_tool_dispatches_with_tool_input(self):
        cat = _catalog(_tool("foo"))
        recorded: list = []

        async def dispatch(name, args):
            recorded.append((name, args))
            return CallToolResult(
                content=[TextContent(type="text", text="ok")],
                structuredContent={"x": 1},
                isError=False,
            )

        result = await handle_compressed_call(
            cat, "invoke_tool",
            {"tool_name": "foo", "tool_input": {"a": 1}},
            dispatch,
        )
        assert recorded == [("foo", {"a": 1})]
        assert result.structuredContent == {"x": 1}
        assert result.isError is False

    @pytest.mark.asyncio
    async def test_invoke_tool_unknown_name_skips_dispatch(self):
        cat = _catalog(_tool("foo"))

        async def dispatch(name, args):
            raise AssertionError("dispatch should not be called for unknown name")

        result = await handle_compressed_call(
            cat, "invoke_tool",
            {"tool_name": "ghost", "tool_input": {}},
            dispatch,
        )
        assert result.isError is True
        assert "foo" in result.content[0].text

    @pytest.mark.asyncio
    async def test_invoke_tool_non_object_input_is_error(self):
        cat = _catalog(_tool("foo"))
        result = await handle_compressed_call(
            cat, "invoke_tool",
            {"tool_name": "foo", "tool_input": "not an object"},
            dispatch=lambda *a: None,  # type: ignore[arg-type]
        )
        assert result.isError is True
        assert "object" in result.content[0].text

    @pytest.mark.asyncio
    async def test_list_tools_returns_text_catalog(self):
        cat = _catalog(
            _tool("alpha", description="First."),
            _tool("beta", description="Second."),
        )
        result = await handle_compressed_call(
            cat, "list_tools", {}, dispatch=lambda *a: None  # type: ignore[arg-type]
        )
        assert result.isError is False
        text = result.content[0].text
        assert "alpha: First" in text
        assert "beta: Second" in text

    @pytest.mark.asyncio
    async def test_unknown_op_is_error(self):
        cat = _catalog(_tool("foo"))
        result = await handle_compressed_call(
            cat, "ghost_op", {}, dispatch=lambda *a: None  # type: ignore[arg-type]
        )
        assert result.isError is True

    @pytest.mark.asyncio
    async def test_args_default_to_empty_dict(self):
        # `args=None` is normalised to `{}` so callers don't have to
        # pre-process. With no tool_name the result is a clear error.
        cat = _catalog(_tool("foo"))
        result = await handle_compressed_call(
            cat, "get_tool_schema", None, dispatch=lambda *a: None  # type: ignore[arg-type]
        )
        assert result.isError is True
