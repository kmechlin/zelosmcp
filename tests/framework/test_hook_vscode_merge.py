"""Tests for the VS Code hooks JSON merge helper."""
from __future__ import annotations

import json

import pytest

from zelosmcp.framework.assetstore.kinds.hook import merge_vscode_hooks_json, _ZELOSMCP_OWNER_TAG


def _entry(key: str, event: str = "PostToolUse", command: str = "cmd") -> dict:
    return {
        "event": event,
        "command": command,
        "_owner": _ZELOSMCP_OWNER_TAG,
        "_key": key,
    }


class TestMergeVscodeHooksJson:
    def test_insert_into_empty_file(self):
        entry = _entry("lint", "PostToolUse", "ruff check .")
        result = merge_vscode_hooks_json("", entry)
        data = json.loads(result)
        hooks = data["hooks"]
        assert "PostToolUse" in hooks
        assert hooks["PostToolUse"][0]["command"] == "ruff check ."
        assert hooks["PostToolUse"][0]["_owner"] == _ZELOSMCP_OWNER_TAG

    def test_insert_into_existing_file_preserves_user_entries(self):
        existing = json.dumps({
            "hooks": {
                "PostToolUse": [
                    {"type": "command", "command": "user-command"}
                ]
            }
        })
        entry = _entry("lint", "PostToolUse", "ruff check .")
        result = merge_vscode_hooks_json(existing, entry)
        data = json.loads(result)
        commands = [h["command"] for h in data["hooks"]["PostToolUse"]]
        assert "user-command" in commands
        assert "ruff check ." in commands

    def test_update_existing_owned_entry(self):
        existing = json.dumps({
            "hooks": {
                "PostToolUse": [
                    {"type": "command", "command": "old-cmd", "_owner": _ZELOSMCP_OWNER_TAG, "_key": "lint"}
                ]
            }
        })
        entry = _entry("lint", "PostToolUse", "new-cmd")
        result = merge_vscode_hooks_json(existing, entry)
        data = json.loads(result)
        cmds = [h["command"] for h in data["hooks"]["PostToolUse"]]
        assert "old-cmd" not in cmds
        assert "new-cmd" in cmds

    def test_move_entry_to_different_event(self):
        existing = json.dumps({
            "hooks": {
                "PreToolUse": [
                    {"type": "command", "command": "check", "_owner": _ZELOSMCP_OWNER_TAG, "_key": "mykey"}
                ]
            }
        })
        # Re-insert under PostToolUse (e.g. user changed vscode event)
        entry = _entry("mykey", "PostToolUse", "check")
        result = merge_vscode_hooks_json(existing, entry)
        data = json.loads(result)
        # Old bucket should be removed (was empty after removal)
        assert "PreToolUse" not in data["hooks"]
        assert "PostToolUse" in data["hooks"]

    def test_invalid_existing_json_treated_as_empty(self):
        entry = _entry("x", "Stop", "echo done")
        result = merge_vscode_hooks_json("not-valid-json", entry)
        data = json.loads(result)
        assert "Stop" in data["hooks"]

    def test_different_events_coexist(self):
        result1 = merge_vscode_hooks_json("", _entry("pre", "PreToolUse", "pre-check"))
        result2 = merge_vscode_hooks_json(result1, _entry("post", "PostToolUse", "post-check"))
        data = json.loads(result2)
        assert "PreToolUse" in data["hooks"]
        assert "PostToolUse" in data["hooks"]

    def test_multiple_zelosmcp_entries_under_same_event(self):
        result1 = merge_vscode_hooks_json("", _entry("a", "PostToolUse", "cmd-a"))
        result2 = merge_vscode_hooks_json(result1, _entry("b", "PostToolUse", "cmd-b"))
        data = json.loads(result2)
        keys = {h["_key"] for h in data["hooks"]["PostToolUse"] if "_key" in h}
        assert "a" in keys
        assert "b" in keys

    def test_empty_bucket_is_removed(self):
        existing = json.dumps({
            "hooks": {
                "PostToolUse": [
                    {"type": "command", "command": "only", "_owner": _ZELOSMCP_OWNER_TAG, "_key": "only"}
                ]
            }
        })
        # Reinsert under a different event so the old bucket becomes empty.
        entry = _entry("only", "Stop", "only")
        result = merge_vscode_hooks_json(existing, entry)
        data = json.loads(result)
        # PostToolUse bucket empty → removed
        assert "PostToolUse" not in data["hooks"]
        assert "Stop" in data["hooks"]
