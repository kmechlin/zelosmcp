from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from mcp.types import (
    GetPromptResult,
    PromptMessage,
    TextContent,
    Tool,
)
from zelosmcp.proxy import ProxyState


class FakeResult:
    """Generic fake result that acts like an MCP result object."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def make_pincher_call_result(
    *,
    text: str = "ok",
    tokens_used: int = 100,
    tokens_saved: int = 900,
    cost_avoided: float = 0.0042,
    location: str = "result",
):
    """Build a fake CallToolResult-shaped object with the pincher `_meta`
    envelope placed at one of three known locations.

    ``location`` selects which carrier holds the envelope:
    - ``"result"`` — directly on the result via ``meta``.
    - ``"structured"`` — on ``structuredContent``.
    - ``"annotation"`` — on ``content[0].annotations``.
    """
    meta_payload = {
        "tokens_used": tokens_used,
        "tokens_saved": tokens_saved,
        "cost_avoided": cost_avoided,
    }
    text_block = TextContent(type="text", text=text)
    if location == "annotation":
        text_block = FakeResult(
            type="text",
            text=text,
            annotations=meta_payload,
        )
    structured = None
    meta_attr = None
    if location == "result":
        meta_attr = meta_payload
    elif location == "structured":
        structured = {"_meta": meta_payload}
    return FakeResult(
        content=[text_block],
        structuredContent=structured,
        isError=False,
        meta=meta_attr,
    )


def _tool(name: str) -> Tool:
    return Tool(name=name, description="desc", inputSchema={"type": "object", "properties": {}})


def make_mock_session() -> AsyncMock:
    session = AsyncMock(spec_set=[
        "initialize",
        "list_tools",
        "call_tool",
        "list_resources",
        "list_resource_templates",
        "read_resource",
        "list_prompts",
        "get_prompt",
    ])
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=FakeResult(
        tools=[_tool("echo"), _tool("add")],
    ))
    session.call_tool = AsyncMock(return_value=FakeResult(
        content=[{"type": "text", "text": "hello"}],
        isError=False,
    ))
    session.list_resources = AsyncMock(return_value=FakeResult(resources=[]))
    session.list_resource_templates = AsyncMock(return_value=FakeResult(resourceTemplates=[]))
    session.read_resource = AsyncMock(return_value=FakeResult(contents=[]))
    session.list_prompts = AsyncMock(return_value=FakeResult(prompts=[]))
    session.get_prompt = AsyncMock(return_value=GetPromptResult(
        description="test",
        messages=[PromptMessage(role="user", content=TextContent(type="text", text="hi"))],
    ))
    return session


@asynccontextmanager
async def fake_stdio_client(params, **kwargs):
    read = MagicMock()
    write = MagicMock()
    yield read, write


@asynccontextmanager
async def fake_sse_client(url, *args, **kwargs):
    read = MagicMock()
    write = MagicMock()
    yield read, write


@asynccontextmanager
async def fake_http_client(url, *args, **kwargs):
    """Mirrors streamablehttp_client which yields (read, write, get_session_id)."""
    read = MagicMock()
    write = MagicMock()
    get_session_id = MagicMock()
    yield read, write, get_session_id


@asynccontextmanager
async def fake_client_session(read, write):
    yield make_mock_session()


@asynccontextmanager
async def fake_session_manager_run(self_ref=None):
    yield


def patch_proxy_deps():
    """Return a combined patch context manager that mocks all external MCP deps."""
    mock_session = make_mock_session()

    @asynccontextmanager
    async def patched_client_session(read, write):
        yield mock_session

    @asynccontextmanager
    async def patched_session_manager_run():
        yield

    patches = [
        patch("zelosmcp.proxy.stdio_client", side_effect=fake_stdio_client),
        patch("zelosmcp.proxy.ClientSession", side_effect=patched_client_session),
        patch.object(
            __import__("mcp.server.streamable_http_manager", fromlist=["StreamableHTTPSessionManager"]).StreamableHTTPSessionManager,
            "run",
            side_effect=patched_session_manager_run,
        ),
    ]
    return patches, mock_session
