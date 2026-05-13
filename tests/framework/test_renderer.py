"""Tests for the rule renderer with and without BackendRuleAssets."""
from __future__ import annotations

import pytest

from zelosmcp.builtin import render_comprehensive_rule
from zelosmcp.framework.assetstore.kinds.rule import BackendRuleAssets


def _catalog(*backends: str) -> dict:
    return {b: {"tools": [{"name": "tool1", "description": "do stuff", "inputSchema": {}}]} for b in backends}


def _catalog_with_tools(backend: str, tools: list[dict]) -> dict:
    return {backend: {"tools": tools}}


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
            # Global directives are keyed under "zelosmcp" (the builtin backend
            # name) — that's what load_backend_rule_assets / _pick look up.
            "zelosmcp": BackendRuleAssets(
                backend="zelosmcp",
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


# ── Compressed-backend rendering ─────────────────────────────────────────────


class TestCompressedBackendRendering:
    """Renderer emits wrapper trio + underlying-tools sub-list when a backend
    is listed in ``compressed_backends``."""

    _COMPRESS_MEDIUM = {"level": "medium", "scope": "aggregator"}
    _COMPRESS_HIGH = {"level": "high", "scope": "aggregator"}
    _COMPRESS_MAX = {"level": "max", "scope": "global"}

    def test_wrapper_trio_appears_for_compressed_backend(self):
        out = render_comprehensive_rule(
            _catalog("pincher"),
            compressed_backends={"pincher": self._COMPRESS_MEDIUM},
        )
        assert "pincher__get_tool_schema" in out
        assert "pincher__search_tools" in out
        assert "pincher__invoke_tool" in out

    def test_underlying_tools_sub_header_emitted(self):
        out = render_comprehensive_rule(
            _catalog("pincher"),
            compressed_backends={"pincher": self._COMPRESS_MEDIUM},
        )
        assert "Underlying tools" in out
        assert "pincher__invoke_tool" in out

    def test_underlying_tool_listed_without_qualified_prefix(self):
        """Underlying tools should NOT carry `<backend>__` since they
        cannot be called directly at /mcp when compressed."""
        out = render_comprehensive_rule(
            _catalog("pincher"),
            compressed_backends={"pincher": self._COMPRESS_MEDIUM},
        )
        # Underlying tools section: `tool1` appears as bare name
        assert "- `tool1`" in out
        # But NOT as a qualified top-level tool in this section
        # (the wrapper trio uses qualified names; underlying sub-list doesn't)
        assert "pincher__tool1" not in out

    def test_compressed_rules_block_emitted_once(self):
        out = render_comprehensive_rule(
            _catalog("pincher", "filesystem"),
            compressed_backends={
                "pincher": self._COMPRESS_MEDIUM,
                "filesystem": self._COMPRESS_HIGH,
            },
            tool_use="priority",
        )
        assert "Compressed backends" in out
        # The global section header (## Compressed backends) appears exactly
        # once regardless of how many compressed backends are present.
        assert out.count("## Compressed backends") == 1

    def test_compressed_rules_block_absent_when_no_compressed_backends(self):
        out = render_comprehensive_rule(
            _catalog("pincher"),
            tool_use="priority",
        )
        assert "Compressed backends" not in out

    def test_compressed_rules_block_absent_when_none(self):
        out = render_comprehensive_rule(
            _catalog("pincher"),
            tool_use="priority",
            compressed_backends=None,
        )
        assert "Compressed backends" not in out

    def test_level_max_emits_list_tools_instead_of_trio(self):
        out = render_comprehensive_rule(
            _catalog("pincher"),
            compressed_backends={"pincher": self._COMPRESS_MAX},
            tool_use="available",
        )
        # level=max: the one wrapper is list_tools
        assert "pincher__list_tools" in out
        # get_tool_schema and search_tools are trio-only (not max)
        assert "pincher__get_tool_schema" not in out
        assert "pincher__search_tools" not in out
        # invoke_tool still appears as the sub-section noun but is NOT
        # listed as a wire-level callable bullet entry at level=max
        assert "- `pincher__invoke_tool`" not in out

    def test_uncompressed_backend_keeps_qualified_tools(self):
        """Backends NOT in compressed_backends still get the normal
        `<backend>__<tool>` treatment."""
        out = render_comprehensive_rule(
            _catalog("mybackend"),
            compressed_backends={"other": self._COMPRESS_MEDIUM},
        )
        assert "mybackend__tool1" in out
        assert "Underlying tools" not in out

    def test_mixed_compressed_and_uncompressed(self):
        catalog = {
            "pincher": {"tools": [{"name": "architecture", "description": "arch"}]},
            "plain": {"tools": [{"name": "do_thing", "description": "plain"}]},
        }
        out = render_comprehensive_rule(
            catalog,
            compressed_backends={"pincher": self._COMPRESS_MEDIUM},
        )
        # pincher: wrapper trio shown, underlying sub-list
        assert "pincher__get_tool_schema" in out
        assert "pincher__invoke_tool" in out
        assert "- `architecture`" in out
        # plain: qualified tools shown normally
        assert "plain__do_thing" in out

    def test_compressed_rules_block_uses_store_body_when_available(self):
        """When rule_assets supplies a compressed_rules_* section it
        should override the hardcoded fallback constant."""
        assets = {
            "zelosmcp": BackendRuleAssets(
                backend="zelosmcp",
                compressed_rules_read_only="CUSTOM COMPRESSED RULES BLOCK\n",
            ),
        }
        out = render_comprehensive_rule(
            _catalog("pincher"),
            tool_use="priority",
            compressed_backends={"pincher": self._COMPRESS_MEDIUM},
            rule_assets=assets,
        )
        assert "CUSTOM COMPRESSED RULES BLOCK" in out

    def test_compressed_mandatory_playbook_preferred_when_available(self):
        """When both playbook_compressed_* and playbook_* are set,
        the compressed variant is preferred for backends in
        ``compressed_backends``."""
        assets = {
            "pincher": BackendRuleAssets(
                backend="pincher",
                playbook_read_only="UNCOMPRESSED PINCHER PB",
                playbook_compressed_read_only="COMPRESSED PINCHER PB",
            ),
            "zelosmcp": BackendRuleAssets(backend="zelosmcp"),
        }
        out = render_comprehensive_rule(
            _catalog("pincher", "filesystem"),
            access="read-only",
            tool_use="priority",
            rule_assets=assets,
            compressed_backends={"pincher": self._COMPRESS_MEDIUM},
        )
        assert "COMPRESSED PINCHER PB" in out
        assert "UNCOMPRESSED PINCHER PB" not in out

    def test_falls_back_to_playbook_when_compressed_variant_empty(self):
        """When playbook_compressed_* is empty/missing but playbook_*
        is set, the standard playbook is used as fallback."""
        assets = {
            "pincher": BackendRuleAssets(
                backend="pincher",
                playbook_read_only="FALLBACK PINCHER PB",
                playbook_compressed_read_only="",
            ),
            "zelosmcp": BackendRuleAssets(backend="zelosmcp"),
        }
        out = render_comprehensive_rule(
            _catalog("pincher", "filesystem"),
            access="read-only",
            tool_use="priority",
            rule_assets=assets,
            compressed_backends={"pincher": self._COMPRESS_MEDIUM},
        )
        assert "FALLBACK PINCHER PB" in out

    def test_tool_instruction_attached_to_underlying_tool(self):
        """Per-tool instructions from the asset store should still appear
        in the underlying tools sub-list for compressed backends."""
        assets = {
            "mybackend": BackendRuleAssets(
                backend="mybackend",
                tool_instructions={"tool1": "Use tool1 for X."},
            ),
        }
        out = render_comprehensive_rule(
            _catalog("mybackend"),
            compressed_backends={"mybackend": self._COMPRESS_MEDIUM},
            rule_assets=assets,
        )
        assert "Use tool1 for X." in out

    def test_compressed_block_absent_when_tool_use_available(self):
        """``tool_use=available`` must not emit the compressed-rules block
        (it has no prioritization section at all)."""
        out = render_comprehensive_rule(
            _catalog("pincher"),
            tool_use="available",
            compressed_backends={"pincher": self._COMPRESS_MEDIUM},
        )
        assert "Compressed backends" not in out


class TestCompressedPlaybookFallbacks:
    """``render_comprehensive_rule`` uses the hardcoded compressed-playbook
    constants (not None) when rule_assets is absent."""

    def test_pincher_compressed_ro_uses_invoke_tool_framing(self):
        out = render_comprehensive_rule(
            {"pincher": {"tools": [{"name": "architecture", "description": "arch"}]},
             "filesystem": {"tools": []}},
            access="read-only",
            tool_use="priority",
            compressed_backends={"pincher": {"level": "medium", "scope": "aggregator"}},
        )
        assert "invoke_tool" in out
        # The compressed mandatory playbook should reference invoke_tool
        assert 'tool_name="architecture"' in out or "pincher__invoke_tool" in out

    def test_filesystem_compressed_rw_uses_invoke_tool_framing(self):
        out = render_comprehensive_rule(
            {"filesystem": {"tools": [{"name": "read_text_file", "description": "read"}]}},
            access="read-write",
            tool_use="priority",
            compressed_backends={"filesystem": {"level": "medium", "scope": "aggregator"}},
        )
        assert "filesystem__invoke_tool" in out
