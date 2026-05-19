"""Unit tests for the hook kind push-to-project logic (merge semantics)."""
from __future__ import annotations

import json

import pytest

from zelosmcp.framework.assetstore.kinds.hook import merge_hooks_json, _ZELOSMCP_OWNER_TAG


class TestMergeHooksJson:
    def test_insert_into_empty_file(self):
        entry = {"name": "lint", "event": "pre_commit", "command": "ruff check .", "_owner": _ZELOSMCP_OWNER_TAG, "_key": "lint"}
        result = merge_hooks_json("", entry)
        data = json.loads(result)
        assert len(data["hooks"]) == 1
        assert data["hooks"][0]["name"] == "lint"

    def test_insert_into_existing_file(self):
        existing = json.dumps({"hooks": [{"name": "my_hook"}]})
        entry = {"name": "lint", "event": "pre_commit", "command": "x", "_owner": _ZELOSMCP_OWNER_TAG, "_key": "lint"}
        result = merge_hooks_json(existing, entry)
        data = json.loads(result)
        names = [h["name"] for h in data["hooks"]]
        assert "my_hook" in names
        assert "lint" in names

    def test_update_existing_owned_entry(self):
        existing = json.dumps({"hooks": [
            {"name": "lint", "_owner": _ZELOSMCP_OWNER_TAG, "_key": "lint", "command": "old"},
            {"name": "user_hook"},
        ]})
        entry = {"name": "lint", "_owner": _ZELOSMCP_OWNER_TAG, "_key": "lint", "command": "new"}
        result = merge_hooks_json(existing, entry)
        data = json.loads(result)
        hooks_by_key = {h.get("_key"): h for h in data["hooks"]}
        assert hooks_by_key["lint"]["command"] == "new"
        # User hook preserved
        user_hooks = [h for h in data["hooks"] if h.get("name") == "user_hook"]
        assert len(user_hooks) == 1

    def test_invalid_existing_json_treated_as_empty(self):
        entry = {"name": "x", "_owner": _ZELOSMCP_OWNER_TAG, "_key": "x"}
        result = merge_hooks_json("not valid json", entry)
        data = json.loads(result)
        assert len(data["hooks"]) == 1
