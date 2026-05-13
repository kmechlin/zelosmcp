"""Tests for the rule renderer with and without BackendRuleAssets."""
from __future__ import annotations

import pytest

from zelosmcp.builtin import render_comprehensive_rule
from zelosmcp.framework.assetstore.kinds.rule import BackendRuleAssets


def _catalog(*backends: str) -> dict:
    return {b: {"tools": [{"name": "tool1", "description": "do stuff", "inputSchema": {}}]} for b in backends}


class TestRendererFallback:
    def test_renders_without_rule_assets(self):
        out = render_comprehensive_rule(_catalog("pincher"), access="read-only")
        assert "zelosMCP backend tool catalog" in out
        assert "pincher__tool1" in out

    def test_renders_mandatory_playbook_from_constants(self):
        out = render_comprehensive_rule(
            _catalog("pincher", "filesystem"),
            access="read-only",
            tool_use="priority",
        )
        assert "pincher" in out
        assert "filesystem" in out

    def test_empty_catalog_returns_no_backends_message(self):
        out = render_comprehensive_rule({}, access="read-only")
        assert "No user backends are currently loaded" in out


class TestRendererWithAssets:
    def _make_assets(self, backend: str, playbook: str) -> dict:
        return {
            backend: BackendRuleAssets(
                backend=backend,
                playbook_read_only=playbook,
                playbook_read_write=playbook,
            ),
            "default": BackendRuleAssets(
                backend="default",
                directive_read_only="## Access mode: READ-ONLY\n\nCustom RO directive.\n",
                directive_read_write="## Access mode: READ-WRITE\n\nCustom RW directive.\n",
                directive_tool_use_priority="## Tool-use priority\n\nCustom priority.\n",
                self_check_gate="## Pre-flight check\n\nCustom gate.\n",
            ),
        }

    def test_custom_playbook_used_when_assets_present(self):
        assets = self._make_assets("pincher", "CUSTOM PINCHER PLAYBOOK RO")
        out = render_comprehensive_rule(
            _catalog("pincher", "filesystem"),
            access="read-only",
            tool_use="priority",
            rule_assets=assets,
        )
        assert "CUSTOM PINCHER PLAYBOOK RO" in out

    def test_custom_directive_used_from_default_backend(self):
        assets = self._make_assets("pincher", "PB")
        out = render_comprehensive_rule(
            _catalog("pincher"),
            access="read-only",
            rule_assets=assets,
        )
        assert "Custom RO directive" in out

    def test_tool_instruction_appended_after_tool_entry(self):
        assets = {
            "mybackend": BackendRuleAssets(
                backend="mybackend",
                tool_instructions={"tool1": "Use tool1 for X."},
            ),
            "default": BackendRuleAssets(backend="default"),
        }
        out = render_comprehensive_rule(
            _catalog("mybackend"),
            access="read-only",
            rule_assets=assets,
        )
        assert "Use tool1 for X." in out

    def test_falls_back_gracefully_when_assets_empty(self):
        out = render_comprehensive_rule(
            _catalog("pincher"),
            access="read-only",
            rule_assets={},  # empty but not None
        )
        assert "zelosMCP backend tool catalog" in out
