"""Tests for the VS Code mcp.json merge helper.

Covers the safe-merge semantics used when zelosMCP writes
``.vscode/mcp.json`` to a repo on every VS Code-target push: the
aggregator entry is upserted under ``servers`` while any user-authored
entries are preserved.
"""
from __future__ import annotations

import json

import pytest

from zelosmcp.framework.assetstore.push import (
    _build_vscode_mcp_json,
    _merge_vscode_mcp_json,
    _AGGREGATOR_ENTRY_NAME,
)


class TestBuildVscodeMcpJson:
    def test_uses_servers_key_not_mcpservers(self):
        body = _build_vscode_mcp_json()
        data = json.loads(body)
        assert "servers" in data
        assert "mcpServers" not in data, (
            "VS Code uses 'servers'; 'mcpServers' is the Cursor key"
        )

    def test_aggregator_entry_present(self):
        data = json.loads(_build_vscode_mcp_json())
        agg = data["servers"][_AGGREGATOR_ENTRY_NAME]
        assert agg["type"] == "http"
        assert agg["url"].endswith("/mcp")

    def test_default_url_when_no_public_url_env(self, monkeypatch):
        monkeypatch.delenv("ZELOSMCP_PUBLIC_URL", raising=False)
        data = json.loads(_build_vscode_mcp_json())
        assert data["servers"][_AGGREGATOR_ENTRY_NAME]["url"] == \
            "http://localhost:8000/mcp"

    def test_public_url_env_var_honoured(self, monkeypatch):
        monkeypatch.setenv("ZELOSMCP_PUBLIC_URL", "https://mcp.example.com")
        data = json.loads(_build_vscode_mcp_json())
        assert data["servers"][_AGGREGATOR_ENTRY_NAME]["url"] == \
            "https://mcp.example.com/mcp"

    def test_public_url_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("ZELOSMCP_PUBLIC_URL", "https://mcp.example.com/")
        data = json.loads(_build_vscode_mcp_json())
        assert data["servers"][_AGGREGATOR_ENTRY_NAME]["url"] == \
            "https://mcp.example.com/mcp"


class TestMergeVscodeMcpJson:
    def test_empty_existing_writes_new_body(self):
        new = _build_vscode_mcp_json()
        merged = _merge_vscode_mcp_json("", new)
        data = json.loads(merged)
        assert _AGGREGATOR_ENTRY_NAME in data["servers"]

    def test_preserves_user_added_entries(self):
        existing = json.dumps({
            "servers": {
                "user-server": {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"]},
            },
        })
        merged = _merge_vscode_mcp_json(existing, _build_vscode_mcp_json())
        data = json.loads(merged)
        assert "user-server" in data["servers"]
        assert _AGGREGATOR_ENTRY_NAME in data["servers"]
        # User entry untouched
        assert data["servers"]["user-server"]["command"] == "uvx"

    def test_overwrites_existing_aggregator_entry(self):
        existing = json.dumps({
            "servers": {
                _AGGREGATOR_ENTRY_NAME: {"type": "http", "url": "http://stale:1/mcp"},
            },
        })
        merged = _merge_vscode_mcp_json(existing, _build_vscode_mcp_json())
        data = json.loads(merged)
        assert data["servers"][_AGGREGATOR_ENTRY_NAME]["url"] != "http://stale:1/mcp"

    def test_invalid_existing_json_falls_back_to_new_body(self):
        merged = _merge_vscode_mcp_json("not json", _build_vscode_mcp_json())
        data = json.loads(merged)
        assert _AGGREGATOR_ENTRY_NAME in data["servers"]

    def test_existing_is_array_falls_back_to_new_body(self):
        merged = _merge_vscode_mcp_json("[1,2,3]", _build_vscode_mcp_json())
        data = json.loads(merged)
        assert _AGGREGATOR_ENTRY_NAME in data["servers"]

    def test_existing_with_inputs_block_preserved(self):
        existing = json.dumps({
            "inputs": [{"id": "api_key", "type": "promptString"}],
            "servers": {
                "user-server": {"type": "http", "url": "https://user/mcp"},
            },
        })
        merged = _merge_vscode_mcp_json(existing, _build_vscode_mcp_json())
        data = json.loads(merged)
        # `inputs` is a sibling of `servers` and must be preserved.
        assert "inputs" in data
        assert data["inputs"][0]["id"] == "api_key"
        assert "user-server" in data["servers"]
        assert _AGGREGATOR_ENTRY_NAME in data["servers"]

    def test_servers_key_missing_is_fine(self):
        existing = json.dumps({"inputs": []})
        merged = _merge_vscode_mcp_json(existing, _build_vscode_mcp_json())
        data = json.loads(merged)
        assert "servers" in data
        assert _AGGREGATOR_ENTRY_NAME in data["servers"]

    def test_servers_key_wrong_type_replaced(self):
        existing = json.dumps({"servers": "not a dict"})
        merged = _merge_vscode_mcp_json(existing, _build_vscode_mcp_json())
        data = json.loads(merged)
        assert isinstance(data["servers"], dict)
        assert _AGGREGATOR_ENTRY_NAME in data["servers"]
