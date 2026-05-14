"""Tests for per-kind _render_for_project target dispatch.

Verifies that agent / hook / rule kinds produce the correct ProjectFile set
for cursor-only, vscode-only, and both-targets configurations.
"""
from __future__ import annotations

import json

import pytest

from zelosmcp.framework.assetstore.kinds.agent import _render_for_project as agent_render, _slug
from zelosmcp.framework.assetstore.kinds.hook import _render_for_project as hook_render
from zelosmcp.framework.assetstore.kinds.rule import _render_for_project as rule_render
from zelosmcp.framework.assetstore.registry import RepoCtx
from zelosmcp.framework.assetstore.row import AssetRow

_CTX = RepoCtx(name="testrepo", ro_path="/ro/testrepo", rw_path="/rw/testrepo")


def _agent_row(name="my_agent", targets=None, description="An agent"):
    meta = {
        "name": name,
        "description": description,
        "targets": targets if targets is not None else ["cursor", "vscode"],
        "push": {},
    }
    return AssetRow(kind="agent", backend="test", name=name, target="cursor",
                    body="# Agent body\nDo stuff.", meta=meta,
                    source="seed", seed_version=1)


def _hook_row(name="my_hook", targets=None, cursor_event="afterFileEdit", vscode_event="PostToolUse"):
    meta = {
        "name": name,
        "event": cursor_event,
        "command": "ruff check .",
        "targets": targets if targets is not None else ["cursor", "vscode"],
        "cursor_event": cursor_event,
        "vscode_event": vscode_event,
    }
    body = json.dumps({"name": name, "event": cursor_event, "command": "ruff check .",
                       "_owner": "zelosmcp", "_key": name}, indent=2)
    return AssetRow(kind="hook", backend="test", name=name, target="cursor",
                    body=body, meta=meta, source="seed", seed_version=1)


def _rule_row(target=""):
    return AssetRow(kind="rule", backend="test", name="playbook", target=target,
                    body="# Rule body", meta={}, source="seed", seed_version=1)


# ── Agent ──────────────────────────────────────────────────────────────

class TestAgentRenderTargets:
    def test_cursor_only(self):
        row = _agent_row(targets=["cursor"])
        files = agent_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert ".cursor/skills/my_agent/SKILL.md" in paths
        assert all(".github" not in p and ".vscode" not in p for p in paths)

    def test_vscode_only(self):
        row = _agent_row(targets=["vscode"])
        files = agent_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert ".cursor/skills/my_agent/SKILL.md" not in paths
        slug = _slug("my_agent")
        assert f".github/skills/{slug}/SKILL.md" in paths
        assert f".vscode/skills/{slug}/SKILL.md" in paths

    def test_both_targets(self):
        row = _agent_row(targets=["cursor", "vscode"])
        files = agent_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert ".cursor/skills/my_agent/SKILL.md" in paths
        slug = _slug("my_agent")
        assert f".github/skills/{slug}/SKILL.md" in paths
        assert f".vscode/skills/{slug}/SKILL.md" in paths
        assert len(paths) == 3

    def test_vscode_body_has_frontmatter(self):
        row = _agent_row(targets=["vscode"])
        files = agent_render(row, _CTX)
        vscode_file = next(f for f in files if ".github" in f.rel_path)
        assert "---\nname:" in vscode_file.body
        assert "description:" in vscode_file.body
        assert "# Agent body" in vscode_file.body

    def test_cursor_body_no_frontmatter(self):
        row = _agent_row(targets=["cursor"])
        files = agent_render(row, _CTX)
        cursor_file = files[0]
        assert "---\nname:" not in cursor_file.body
        assert "# Agent body" in cursor_file.body

    def test_slug_normalises_name(self):
        assert _slug("My Agent Name") == "my-agent-name"
        assert _slug("my_agent_123") == "my-agent-123"
        assert _slug("A" * 100) == "a" * 64

    def test_default_targets_both(self):
        row = _agent_row(targets=None)
        files = agent_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert len(paths) == 3

    def test_push_cursor_override_respected(self):
        meta = {
            "name": "custom",
            "description": "",
            "targets": ["cursor"],
            "push": {"cursor": ".cursor/skills/override/SKILL.md"},
        }
        row = AssetRow(kind="agent", backend="t", name="custom", target="cursor",
                       body="body", meta=meta, source="seed", seed_version=1)
        files = agent_render(row, _CTX)
        assert files[0].rel_path == ".cursor/skills/override/SKILL.md"


# ── Hook ───────────────────────────────────────────────────────────────

class TestHookRenderTargets:
    def test_cursor_only(self):
        row = _hook_row(targets=["cursor"])
        files = hook_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert ".cursor/hooks.json" in paths
        assert len(paths) == 1

    def test_vscode_only(self):
        row = _hook_row(targets=["vscode"])
        files = hook_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert ".cursor/hooks.json" not in paths
        assert ".github/hooks/zelosmcp.json" in paths
        assert ".vscode/hooks.json" in paths

    def test_both_targets(self):
        row = _hook_row(targets=["cursor", "vscode"])
        files = hook_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert ".cursor/hooks.json" in paths
        assert ".github/hooks/zelosmcp.json" in paths
        assert ".vscode/hooks.json" in paths
        assert len(paths) == 3

    def test_cursor_body_uses_cursor_event(self):
        row = _hook_row(targets=["cursor"], cursor_event="afterFileEdit")
        files = hook_render(row, _CTX)
        cursor_file = next(f for f in files if f.rel_path == ".cursor/hooks.json")
        data = json.loads(cursor_file.body)
        assert data["event"] == "afterFileEdit"

    def test_vscode_body_uses_vscode_event(self):
        row = _hook_row(targets=["vscode"], vscode_event="PreToolUse")
        files = hook_render(row, _CTX)
        gh_file = next(f for f in files if ".github" in f.rel_path)
        data = json.loads(gh_file.body)
        assert data["event"] == "PreToolUse"

    def test_cursor_body_has_cursor_format(self):
        row = _hook_row(targets=["cursor"])
        files = hook_render(row, _CTX)
        cursor_file = next(f for f in files if f.rel_path == ".cursor/hooks.json")
        data = json.loads(cursor_file.body)
        # Cursor format: flat dict with name, event, command, _owner, _key
        assert "name" in data
        assert "event" in data
        assert "_owner" in data

    def test_vscode_body_has_vscode_format(self):
        row = _hook_row(targets=["vscode"])
        files = hook_render(row, _CTX)
        gh_file = next(f for f in files if ".github" in f.rel_path)
        data = json.loads(gh_file.body)
        # VS Code body: flat dict with event and command (will be merged into event-keyed map)
        assert data.get("event") is not None
        assert data.get("command") is not None
        assert "_owner" in data

    def test_all_hook_files_mode_merge(self):
        row = _hook_row(targets=["cursor", "vscode"])
        files = hook_render(row, _CTX)
        assert all(f.mode == "merge" for f in files)

    def test_default_targets_is_both(self):
        row = _hook_row(targets=None)
        files = hook_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert len(paths) == 3


# ── Rule (per-row push) ────────────────────────────────────────────────

class TestRuleRenderTargets:
    def test_cursor_target(self):
        row = _rule_row(target="cursor")
        files = rule_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert ".cursor/rules/zelosmcp.mdc" in paths
        assert len(paths) == 1

    def test_vscode_target_emits_both_paths(self):
        row = _rule_row(target="vscode")
        files = rule_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert ".github/copilot-instructions.md" in paths
        assert ".vscode/copilot-instructions.md" in paths
        assert len(paths) == 2

    def test_empty_target_emits_all_three(self):
        row = _rule_row(target="")
        files = rule_render(row, _CTX)
        paths = [f.rel_path for f in files]
        assert ".cursor/rules/zelosmcp.mdc" in paths
        assert ".github/copilot-instructions.md" in paths
        assert ".vscode/copilot-instructions.md" in paths
        assert len(paths) == 3

    def test_all_rule_files_mode_overwrite(self):
        row = _rule_row(target="")
        files = rule_render(row, _CTX)
        assert all(f.mode == "overwrite" for f in files)
