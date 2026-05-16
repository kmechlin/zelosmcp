"""End-to-end tests for push_kind_for_all_running with IDE targets.

Uses tmp_path for real filesystem I/O and a fake manager to verify that the
correct set of files is written for each combination of kind + targets.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from zelosmcp.framework.assetstore.push import (
    _resolve_targets,
    push_kind_for_all_running,
)
from zelosmcp.framework.assetstore.sqlite import SQLiteAssetStore


# ── Helpers ───────────────────────────────────────────────────────────


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


def _written_files(root):
    """Return relative paths of all files under *root*."""
    result = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            result.append(os.path.relpath(os.path.join(dirpath, f), root))
    return result


# ── Agent push targets ────────────────────────────────────────────────

class TestAgentPushTargets:
    @pytest.mark.asyncio
    async def test_cursor_target_writes_cursor_skill(self, store, tmp_path):
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
        manager = FakeManager()
        pushed = await push_kind_for_all_running(
            store, manager,
            kind="agent",
            repo_rw_path=str(tmp_path),
        )
        written_paths = _written_files(tmp_path)
        assert any(".cursor/agents" in p for p in written_paths)
        assert not any(".github/agents" in p for p in written_paths)

    @pytest.mark.asyncio
    async def test_vscode_target_writes_github_and_vscode_skills(self, store, tmp_path):
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
        manager = FakeManager()
        pushed = await push_kind_for_all_running(
            store, manager,
            kind="agent",
            repo_rw_path=str(tmp_path),
        )
        written_paths = _written_files(tmp_path)
        assert any(".github/agents" in p for p in written_paths)
        assert not any(".cursor/agents" in p for p in written_paths)

    @pytest.mark.asyncio
    async def test_both_targets_writes_all_three_paths(self, store, tmp_path):
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
        manager = FakeManager()
        pushed = await push_kind_for_all_running(
            store, manager,
            kind="agent",
            repo_rw_path=str(tmp_path),
        )
        written_paths = _written_files(tmp_path)
        assert any(".cursor/agents" in p for p in written_paths)
        assert any(".github/agents" in p for p in written_paths)


# ── Hook push targets ─────────────────────────────────────────────────

class TestHookPushTargets:
    @pytest.mark.asyncio
    async def test_cursor_target_writes_cursor_hooks(self, store, tmp_path):
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
        manager = FakeManager()
        await push_kind_for_all_running(
            store, manager, kind="hook", repo_rw_path=str(tmp_path)
        )
        written_paths = _written_files(tmp_path)
        assert any(".cursor/hooks.json" in p for p in written_paths)
        assert not any(".vscode/hooks.json" in p for p in written_paths)

    @pytest.mark.asyncio
    async def test_vscode_target_writes_vscode_hook_files(self, store, tmp_path):
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
        manager = FakeManager()
        await push_kind_for_all_running(
            store, manager, kind="hook", repo_rw_path=str(tmp_path)
        )
        written_paths = _written_files(tmp_path)
        assert not any(".cursor/hooks.json" in p for p in written_paths)
        assert any(".github/hooks" in p for p in written_paths)
        assert any(".vscode/hooks.json" in p for p in written_paths)

    @pytest.mark.asyncio
    async def test_directories_created_before_write(self, store, tmp_path):
        """Regression: _local_write must create parent directories before writing
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
        manager = FakeManager()
        await push_kind_for_all_running(
            store, manager, kind="hook", repo_rw_path=str(tmp_path)
        )
        # Verify directories were created and files exist on disk.
        assert (tmp_path / ".github" / "hooks").is_dir()
        assert (tmp_path / ".vscode").is_dir()
        written_paths = _written_files(tmp_path)
        assert any(".github/hooks" in p for p in written_paths)
        assert any(".vscode/hooks.json" in p for p in written_paths)

    @pytest.mark.asyncio
    async def test_vscode_hook_file_uses_event_keyed_format(self, store, tmp_path):
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
        manager = FakeManager()
        await push_kind_for_all_running(
            store, manager, kind="hook", repo_rw_path=str(tmp_path)
        )
        # Find the .github/hooks file on disk and verify its format.
        gh_files = [p for p in _written_files(tmp_path) if ".github/hooks" in p]
        assert gh_files, "Expected a .github/hooks file to be written"
        gh_path = tmp_path / gh_files[0]
        data = json.loads(gh_path.read_text(encoding="utf-8"))
        assert "hooks" in data
        assert isinstance(data["hooks"], dict), "VS Code hooks should be event-keyed dict"
        assert "PostToolUse" in data["hooks"]
        entries = data["hooks"]["PostToolUse"]
        assert any(e.get("command") == "prettier --write ." for e in entries)


# ── Remove pushed assets ──────────────────────────────────────────────

from zelosmcp.framework.assetstore.push import remove_pushed_assets


class TestRemovePushedAssets:
    @pytest.mark.asyncio
    async def test_removes_rule_files(self, store, tmp_path):
        """Rule files are deleted; parent .cursor/ and .github/ dirs remain."""
        (tmp_path / ".cursor" / "rules").mkdir(parents=True)
        (tmp_path / ".cursor" / "rules" / "zelosmcp.mdc").write_text("# rule")
        (tmp_path / ".github").mkdir(parents=True)
        (tmp_path / ".github" / "copilot-instructions.md").write_text("# rule")
        (tmp_path / ".vscode").mkdir(parents=True)
        (tmp_path / ".vscode" / "copilot-instructions.md").write_text("# rule")

        removed = await remove_pushed_assets(store, repo_rw_path=str(tmp_path))
        paths = [r.path for r in removed if r.action == "deleted"]
        assert any("zelosmcp.mdc" in p for p in paths)
        assert any(".github/copilot-instructions.md" in p for p in paths)
        assert any(".vscode/copilot-instructions.md" in p for p in paths)
        # Parent directories preserved
        assert (tmp_path / ".cursor").is_dir()
        assert (tmp_path / ".github").is_dir()
        assert (tmp_path / ".vscode").is_dir()

    @pytest.mark.asyncio
    async def test_removes_zelosmcp_json(self, store, tmp_path):
        for d in (".cursor", ".github", ".vscode"):
            (tmp_path / d).mkdir(parents=True, exist_ok=True)
            (tmp_path / d / "zelosmcp.json").write_text("{}")

        removed = await remove_pushed_assets(store, repo_rw_path=str(tmp_path))
        deleted = [r.path for r in removed if r.action == "deleted"]
        assert len([p for p in deleted if "zelosmcp.json" in p]) == 3

    @pytest.mark.asyncio
    async def test_removes_agent_skill_dirs(self, store, tmp_path):
        for d in (".cursor/agents", ".github/agents"):
            (tmp_path / d).mkdir(parents=True)
            (tmp_path / d / "my-agent.md").write_text("# agent")

        removed = await remove_pushed_assets(store, repo_rw_path=str(tmp_path))
        deleted = [r.path for r in removed if r.action == "deleted"]
        assert len([p for p in deleted if "my-agent" in p]) >= 1

    @pytest.mark.asyncio
    async def test_cleans_cursor_hooks(self, store, tmp_path):
        """Only zelosmcp-owned hooks are removed; user hooks survive."""
        (tmp_path / ".cursor").mkdir(parents=True)
        hooks = {
            "hooks": [
                {"name": "lint", "_owner": "zelosmcp", "_key": "lint", "command": "ruff check ."},
                {"name": "user-hook", "command": "echo hi"},
            ]
        }
        (tmp_path / ".cursor" / "hooks.json").write_text(json.dumps(hooks))

        removed = await remove_pushed_assets(store, repo_rw_path=str(tmp_path))
        assert any(r.action == "cleaned" for r in removed)
        result = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())
        assert len(result["hooks"]) == 1
        assert result["hooks"][0]["name"] == "user-hook"

    @pytest.mark.asyncio
    async def test_deletes_cursor_hooks_when_all_zelosmcp(self, store, tmp_path):
        """If all hooks are zelosmcp-owned, the file is deleted entirely."""
        (tmp_path / ".cursor").mkdir(parents=True)
        hooks = {"hooks": [{"name": "lint", "_owner": "zelosmcp", "_key": "lint"}]}
        (tmp_path / ".cursor" / "hooks.json").write_text(json.dumps(hooks))

        removed = await remove_pushed_assets(store, repo_rw_path=str(tmp_path))
        assert any(r.action == "deleted" and "hooks.json" in r.path for r in removed)
        assert not (tmp_path / ".cursor" / "hooks.json").exists()

    @pytest.mark.asyncio
    async def test_cleans_vscode_mcp_json(self, store, tmp_path):
        """The zelosmcp-aggregate entry is removed; user entries survive."""
        (tmp_path / ".vscode").mkdir(parents=True)
        mcp = {"servers": {"zelosmcp-aggregate": {"type": "http"}, "other": {"type": "http"}}}
        (tmp_path / ".vscode" / "mcp.json").write_text(json.dumps(mcp))

        removed = await remove_pushed_assets(store, repo_rw_path=str(tmp_path))
        assert any(r.action == "cleaned" and "mcp.json" in r.path for r in removed)
        result = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
        assert "zelosmcp-aggregate" not in result["servers"]
        assert "other" in result["servers"]

    @pytest.mark.asyncio
    async def test_preserves_non_zelosmcp_files(self, store, tmp_path):
        """Files not managed by zelosmcp are untouched."""
        (tmp_path / ".cursor" / "rules").mkdir(parents=True)
        (tmp_path / ".cursor" / "rules" / "custom.mdc").write_text("# custom")
        (tmp_path / ".github").mkdir(parents=True)
        (tmp_path / ".github" / "CODEOWNERS").write_text("* @team")
        (tmp_path / ".vscode").mkdir(parents=True)
        (tmp_path / ".vscode" / "settings.json").write_text("{}")

        await remove_pushed_assets(store, repo_rw_path=str(tmp_path))
        assert (tmp_path / ".cursor" / "rules" / "custom.mdc").exists()
        assert (tmp_path / ".github" / "CODEOWNERS").exists()
        assert (tmp_path / ".vscode" / "settings.json").exists()

    @pytest.mark.asyncio
    async def test_empty_repo_returns_empty_list(self, store, tmp_path):
        removed = await remove_pushed_assets(store, repo_rw_path=str(tmp_path))
        assert removed == []
