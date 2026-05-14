"""End-to-end tests for push_kind_for_all_running with IDE targets.

Uses a fake filesystem session and a fake manager to verify that the
correct set of files is written for each combination of kind + targets.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from zelosmcp.framework.assetstore.push import (
    _resolve_targets,
    push_kind_for_all_running,
)
from zelosmcp.framework.assetstore.sqlite import SQLiteAssetStore


# ── Helpers ───────────────────────────────────────────────────────────


class FakeFsSession:
    """Minimal fake that records call_tool invocations."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.files: dict[str, str] = {}
        self.dirs: list[str] = []

    async def call_tool(self, tool_name: str, args: dict):
        self.calls.append((tool_name, args))
        if tool_name == "write_file":
            self.files[args["path"]] = args["content"]
        elif tool_name == "create_directory":
            self.dirs.append(args["path"])
        elif tool_name == "read_text_file":
            # Return stored content or empty.
            content = self.files.get(args["path"], "")
            result = MagicMock()
            result.content = [MagicMock(text=content)]
            return result
        return MagicMock(content=[])


class FakeManager:
    """Minimal fake ProxyManager."""

    def __init__(self, running_backends=None):
        self.servers = {}
        for name in (running_backends or []):
            s = MagicMock()
            s.running = True
            s.builtin = False
            self.servers[name] = s
        self._specs = {}

    @property
    def assets(self):
        return None  # Not used directly; the store is passed explicitly.


@pytest.fixture
async def store():
    s = SQLiteAssetStore(":memory:")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


# ── _resolve_targets ──────────────────────────────────────────────────

class TestResolveTargets:
    def test_explicit_targets_override_fmt(self):
        assert _resolve_targets(["cursor"], "copilot-instructions") == ["cursor"]
        assert _resolve_targets(["vscode"], "cursor-mdc") == ["vscode"]
        assert _resolve_targets(["cursor", "vscode"], "cursor-mdc") == ["cursor", "vscode"]

    def test_fmt_cursor_mdc_defaults_to_cursor(self):
        assert _resolve_targets(None, "cursor-mdc") == ["cursor"]

    def test_fmt_copilot_instructions_defaults_to_vscode(self):
        assert _resolve_targets(None, "copilot-instructions") == ["vscode"]

    def test_unknown_fmt_defaults_to_both(self):
        assert _resolve_targets(None, "unknown") == ["cursor", "vscode"]

    def test_empty_targets_list_returns_empty(self):
        assert _resolve_targets([], "cursor-mdc") == []

    def test_invalid_target_values_are_filtered(self):
        result = _resolve_targets(["cursor", "invalid", "vscode"], "cursor-mdc")
        assert "invalid" not in result
        assert "cursor" in result
        assert "vscode" in result


# ── Agent push targets ────────────────────────────────────────────────

class TestAgentPushTargets:
    @pytest.mark.asyncio
    async def test_cursor_target_writes_cursor_skill(self, store):
        from zelosmcp.framework.assetstore.row import AssetRow
        row = AssetRow(
            kind="agent", backend="zelosmcp", name="my_agent", target="cursor",
            body="# Agent\nDo stuff.",
            meta={
                "name": "my_agent",
                "description": "An agent",
                "targets": ["cursor"],
                "push": {},
            },
            source="seed", seed_version=1,
        )
        await store.upsert(row)
        fs = FakeFsSession()
        manager = FakeManager()
        pushed = await push_kind_for_all_running(
            store, fs, manager,
            kind="agent",
            repo_rw_path="/rw/repo",
        )
        written_paths = list(fs.files.keys())
        assert any(".cursor/skills" in p for p in written_paths)
        assert not any(".github/skills" in p for p in written_paths)

    @pytest.mark.asyncio
    async def test_vscode_target_writes_github_and_vscode_skills(self, store):
        from zelosmcp.framework.assetstore.row import AssetRow
        row = AssetRow(
            kind="agent", backend="zelosmcp", name="my_agent", target="cursor",
            body="# Agent\nDo stuff.",
            meta={
                "name": "my_agent",
                "description": "An agent",
                "targets": ["vscode"],
                "push": {},
            },
            source="seed", seed_version=1,
        )
        await store.upsert(row)
        fs = FakeFsSession()
        manager = FakeManager()
        pushed = await push_kind_for_all_running(
            store, fs, manager,
            kind="agent",
            repo_rw_path="/rw/repo",
        )
        written_paths = list(fs.files.keys())
        assert any(".github/skills" in p for p in written_paths)
        assert any(".vscode/skills" in p for p in written_paths)
        assert not any(".cursor/skills" in p for p in written_paths)

    @pytest.mark.asyncio
    async def test_both_targets_writes_all_three_paths(self, store):
        from zelosmcp.framework.assetstore.row import AssetRow
        row = AssetRow(
            kind="agent", backend="zelosmcp", name="my_agent", target="cursor",
            body="# Agent\nDo stuff.",
            meta={
                "name": "my_agent",
                "description": "A description",
                "targets": ["cursor", "vscode"],
                "push": {},
            },
            source="seed", seed_version=1,
        )
        await store.upsert(row)
        fs = FakeFsSession()
        manager = FakeManager()
        pushed = await push_kind_for_all_running(
            store, fs, manager,
            kind="agent",
            repo_rw_path="/rw/repo",
        )
        written_paths = list(fs.files.keys())
        assert any(".cursor/skills" in p for p in written_paths)
        assert any(".github/skills" in p for p in written_paths)
        assert any(".vscode/skills" in p for p in written_paths)


# ── Hook push targets ─────────────────────────────────────────────────

class TestHookPushTargets:
    @pytest.mark.asyncio
    async def test_cursor_target_writes_cursor_hooks(self, store):
        from zelosmcp.framework.assetstore.row import AssetRow
        row = AssetRow(
            kind="hook", backend="zelosmcp", name="lint", target="cursor",
            body=json.dumps({"name": "lint", "event": "afterFileEdit",
                             "command": "ruff check .", "_owner": "zelosmcp", "_key": "lint"}),
            meta={"name": "lint", "event": "afterFileEdit", "command": "ruff check .",
                  "targets": ["cursor"], "cursor_event": "afterFileEdit", "vscode_event": "PostToolUse"},
            source="seed", seed_version=1,
        )
        await store.upsert(row)
        fs = FakeFsSession()
        manager = FakeManager()
        await push_kind_for_all_running(
            store, fs, manager, kind="hook", repo_rw_path="/rw/repo"
        )
        written_paths = list(fs.files.keys())
        assert any(".cursor/hooks.json" in p for p in written_paths)
        assert not any(".vscode/hooks.json" in p for p in written_paths)

    @pytest.mark.asyncio
    async def test_vscode_target_writes_vscode_hook_files(self, store):
        from zelosmcp.framework.assetstore.row import AssetRow
        row = AssetRow(
            kind="hook", backend="zelosmcp", name="lint", target="cursor",
            body=json.dumps({"name": "lint", "event": "afterFileEdit",
                             "command": "ruff check .", "_owner": "zelosmcp", "_key": "lint"}),
            meta={"name": "lint", "event": "afterFileEdit", "command": "ruff check .",
                  "targets": ["vscode"], "cursor_event": "afterFileEdit", "vscode_event": "PostToolUse"},
            source="seed", seed_version=1,
        )
        await store.upsert(row)
        fs = FakeFsSession()
        manager = FakeManager()
        await push_kind_for_all_running(
            store, fs, manager, kind="hook", repo_rw_path="/rw/repo"
        )
        written_paths = list(fs.files.keys())
        assert not any(".cursor/hooks.json" in p for p in written_paths)
        assert any(".github/hooks" in p for p in written_paths)
        assert any(".vscode/hooks.json" in p for p in written_paths)

    @pytest.mark.asyncio
    async def test_create_directory_called_before_write(self, store):
        """Regression: _fs_write must call create_directory before write_file
        so .github/ and .vscode/ directories are created automatically."""
        from zelosmcp.framework.assetstore.row import AssetRow
        row = AssetRow(
            kind="hook", backend="zelosmcp", name="lint", target="cursor",
            body=json.dumps({"name": "lint", "event": "afterFileEdit",
                             "command": "ruff check .", "_owner": "zelosmcp", "_key": "lint"}),
            meta={"name": "lint", "event": "afterFileEdit", "command": "ruff check .",
                  "targets": ["vscode"], "cursor_event": "afterFileEdit", "vscode_event": "PostToolUse"},
            source="seed", seed_version=1,
        )
        await store.upsert(row)
        fs = FakeFsSession()
        manager = FakeManager()
        await push_kind_for_all_running(
            store, fs, manager, kind="hook", repo_rw_path="/rw/repo"
        )
        # create_directory must have been called for every written path's parent.
        dir_calls = [path for (tool, args) in fs.calls
                     if tool == "create_directory"
                     for path in [args.get("path", "")]]
        assert any(".github/hooks" in d for d in dir_calls), (
            "create_directory must be called for .github/hooks/ parent"
        )
        assert any(".vscode" in d for d in dir_calls), (
            "create_directory must be called for .vscode/ parent"
        )

    @pytest.mark.asyncio
    async def test_vscode_hook_file_uses_event_keyed_format(self, store):
        from zelosmcp.framework.assetstore.row import AssetRow
        row = AssetRow(
            kind="hook", backend="zelosmcp", name="fmt", target="cursor",
            body=json.dumps({"name": "fmt", "event": "afterFileEdit",
                             "command": "prettier --write .", "_owner": "zelosmcp", "_key": "fmt"}),
            meta={"name": "fmt", "event": "afterFileEdit", "command": "prettier --write .",
                  "targets": ["vscode"], "cursor_event": "afterFileEdit", "vscode_event": "PostToolUse"},
            source="seed", seed_version=1,
        )
        await store.upsert(row)
        fs = FakeFsSession()
        manager = FakeManager()
        await push_kind_for_all_running(
            store, fs, manager, kind="hook", repo_rw_path="/rw/repo"
        )
        gh_path = next(p for p in fs.files if ".github/hooks" in p)
        data = json.loads(fs.files[gh_path])
        assert "hooks" in data
        assert isinstance(data["hooks"], dict), "VS Code hooks should be event-keyed dict"
        assert "PostToolUse" in data["hooks"]
        entries = data["hooks"]["PostToolUse"]
        assert any(e.get("command") == "prettier --write ." for e in entries)
