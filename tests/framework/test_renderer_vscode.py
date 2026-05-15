"""Tests for render_comprehensive_rule VS Code (copilot-instructions) format parity.

Verifies that the ``fmt=copilot-instructions`` output:
  - omits the YAML frontmatter block
  - contains the same backend catalog sections as the Cursor output
  - is accepted by the VS Code copilot body (plain markdown, no fenced frontmatter)
"""
from __future__ import annotations

import pytest

from zelosmcp.builtin import render_comprehensive_rule
from zelosmcp.framework.assetstore.kinds.rule import BackendRuleAssets


def _catalog(*backends: str) -> dict:
    return {
        b: {"tools": [{"name": "tool1", "description": "do stuff", "inputSchema": {}}]}
        for b in backends
    }


def _make_assets(backend: str) -> dict:
    return {
        backend: BackendRuleAssets(
            backend=backend,
        ),
        "zelosmcp": BackendRuleAssets(
            backend="zelosmcp",
            directive_read_only="## Access mode: READ-ONLY\n\nRO directive.\n",
            directive_read_write="## Access mode: READ-WRITE\n\nRW directive.\n",
        ),
    }


class TestCopilotInstructionsFormat:
    def test_no_yaml_frontmatter(self):
        out = render_comprehensive_rule(_catalog("pincher"), fmt="copilot-instructions")
        assert not out.startswith("---")
        assert "alwaysApply" not in out

    def test_cursor_mdc_has_frontmatter(self):
        out = render_comprehensive_rule(_catalog("pincher"), fmt="cursor-mdc")
        assert out.startswith("---")
        assert "alwaysApply" in out

    def test_catalog_entries_same_in_both_formats(self):
        catalog = _catalog("pincher", "filesystem")
        cursor_out = render_comprehensive_rule(catalog, fmt="cursor-mdc", access="read-only")
        vscode_out = render_comprehensive_rule(catalog, fmt="copilot-instructions", access="read-only")
        # Both should list the same backend tools.
        assert "pincher__tool1" in cursor_out
        assert "pincher__tool1" in vscode_out
        assert "filesystem__tool1" in cursor_out
        assert "filesystem__tool1" in vscode_out

    def test_directives_same_content(self):
        catalog = _catalog("pincher")
        assets = _make_assets("pincher")
        cursor_out = render_comprehensive_rule(
            catalog, fmt="cursor-mdc", access="read-only", rule_assets=assets
        )
        vscode_out = render_comprehensive_rule(
            catalog, fmt="copilot-instructions", access="read-only", rule_assets=assets
        )
        # Both should include the custom directives.
        assert "RO directive" in cursor_out
        assert "RO directive" in vscode_out

    def test_tool_use_priority_affects_both_formats(self):
        catalog = _catalog("pincher")
        cursor_prio = render_comprehensive_rule(catalog, fmt="cursor-mdc", tool_use="priority")
        vscode_prio = render_comprehensive_rule(catalog, fmt="copilot-instructions", tool_use="priority")
        cursor_avail = render_comprehensive_rule(catalog, fmt="cursor-mdc", tool_use="available")
        vscode_avail = render_comprehensive_rule(catalog, fmt="copilot-instructions", tool_use="available")
        # Priority adds a "Tool-use priority" / "Pre-flight check" section.
        assert "Tool-use priority" in cursor_prio or "Pre-flight" in cursor_prio
        assert "Tool-use priority" in vscode_prio or "Pre-flight" in vscode_prio
        # Available does not.
        assert "Tool-use priority" not in cursor_avail
        assert "Tool-use priority" not in vscode_avail

    def test_read_write_directive_in_both_formats(self):
        catalog = _catalog("pincher")
        assets = _make_assets("pincher")
        cursor_rw = render_comprehensive_rule(
            catalog, fmt="cursor-mdc", access="read-write", rule_assets=assets
        )
        vscode_rw = render_comprehensive_rule(
            catalog, fmt="copilot-instructions", access="read-write", rule_assets=assets
        )
        assert "RW directive" in cursor_rw
        assert "RW directive" in vscode_rw

    def test_empty_catalog_message_in_both_formats(self):
        cursor_out = render_comprehensive_rule({}, fmt="cursor-mdc")
        vscode_out = render_comprehensive_rule({}, fmt="copilot-instructions")
        assert "No user backends are currently loaded" in cursor_out
        assert "No user backends are currently loaded" in vscode_out
