"""Integration tests for the MCPDoc backend registration.

Verifies that the mcpdoc config entry in default-zelosmcp.json is
correctly parsed and that the aggregator exposes mcpdoc tools when
started.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mcp.types import Tool
from zelosmcp.config import parse_config
from zelosmcp.app import create_app
from zelosmcp.manager import ProxyManager
from tests.conftest import (
    FakeResult,
    make_mock_session,
    fake_stdio_client,
    fake_sse_client,
    fake_http_client,
)

_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "default-zelosmcp.json"
)


def _load_default_config() -> dict:
    return json.loads(_DEFAULT_CONFIG_PATH.read_text())


def _fresh():
    manager = ProxyManager(mandatory_config_path="")
    app = create_app(manager)
    return app, manager


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _mcpdoc_patches():
    """Patches that make the mcpdoc session return its two tools."""
    mock_session = make_mock_session()
    mock_session.list_tools = AsyncMock(return_value=FakeResult(
        tools=[
            Tool(
                name="list_doc_sources",
                description="List configured documentation sources",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="fetch_docs",
                description="Fetch documentation from a URL",
                inputSchema={
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            ),
        ],
    ))

    @asynccontextmanager
    async def patched_client_session(read, write):
        yield mock_session

    @asynccontextmanager
    async def patched_run(self):
        yield

    return (
        mock_session,
        patch("zelosmcp.proxy.stdio_client", side_effect=fake_stdio_client),
        patch("zelosmcp.proxy.sse_client", side_effect=fake_sse_client),
        patch("zelosmcp.proxy.streamablehttp_client", side_effect=fake_http_client),
        patch("zelosmcp.proxy.ClientSession", side_effect=patched_client_session),
        patch("zelosmcp.proxy.StreamableHTTPSessionManager.run", patched_run),
    )


_MCPDOC_CONFIG = {
    "mcpServers": {
        "mcpdoc": {
            "command": "uvx",
            "args": [
                "--from", "mcpdoc", "mcpdoc",
                "--urls",
                "LangGraph:https://langchain-ai.github.io/langgraph/llms.txt",
                "--transport", "stdio",
            ],
            "compress": {"level": "low"},
        }
    }
}


class TestMcpdocConfigParsing:
    """Verify the mcpdoc entry in default-zelosmcp.json is well-formed."""

    def test_mcpdoc_entry_exists(self):
        cfg = _load_default_config()
        assert "mcpdoc" in cfg["mcpServers"]

    def test_mcpdoc_is_stdio_backend(self):
        cfg = _load_default_config()
        entry = cfg["mcpServers"]["mcpdoc"]
        assert "command" in entry
        assert entry["command"] == "uvx"

    def test_mcpdoc_started_false(self):
        cfg = _load_default_config()
        entry = cfg["mcpServers"]["mcpdoc"]
        assert entry["started"] is False

    def test_mcpdoc_has_urls_args(self):
        cfg = _load_default_config()
        args = cfg["mcpServers"]["mcpdoc"]["args"]
        assert "--urls" in args
        url_args = args[args.index("--urls") + 1:]
        url_strs = [a for a in url_args if ":" in a and a != "--transport"]
        assert len(url_strs) >= 2

    def test_mcpdoc_config_parses_without_error(self):
        cfg = _load_default_config()
        specs, primary, _ = parse_config(cfg)
        names = [s.name for s in specs]
        assert "mcpdoc" in names

    def test_mcpdoc_compress_level_low(self):
        cfg = _load_default_config()
        entry = cfg["mcpServers"]["mcpdoc"]
        assert entry.get("compress", {}).get("level") == "low"


class TestMcpdocAggregatorIntegration:
    """Verify mcpdoc tools appear in the aggregator when started."""

    @pytest.mark.asyncio
    async def test_mcpdoc_tools_namespaced_in_aggregator(self):
        mock_session, *patches = _mcpdoc_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                await c.post("/api/start", json=_MCPDOC_CONFIG)
                r = await c.get("/api/status")
                data = r.json()
                mcpdoc_row = next(
                    (s for s in data["servers"] if s["name"] == "mcpdoc"),
                    None,
                )
                assert mcpdoc_row is not None
                assert mcpdoc_row["running"] is True

                catalog = await c.get("/api/catalog")
                catalog_data = catalog.json()
                assert "mcpdoc" in catalog_data
                mcpdoc_tools = catalog_data["mcpdoc"]["tools"]
                tool_names = {t["name"] for t in mcpdoc_tools}
                assert "list_doc_sources" in tool_names
                assert "fetch_docs" in tool_names
            await manager.stop_all()

    @pytest.mark.asyncio
    async def test_mcpdoc_call_tool_dispatches(self):
        mock_session, *patches = _mcpdoc_patches()
        mock_session.call_tool = AsyncMock(return_value=FakeResult(
            content=[{"type": "text", "text": "LangGraph: https://..."}],
            isError=False,
        ))
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app, manager = _fresh()
            async with _client(app) as c:
                await c.post("/api/start", json=_MCPDOC_CONFIG)
                r = await c.get("/api/status")
            assert r.json()["running"] is True
            mcpdoc_state = manager.get("mcpdoc")
            assert mcpdoc_state is not None
            assert mcpdoc_state.running is True
            await manager.stop_all()
