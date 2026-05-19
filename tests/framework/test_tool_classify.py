"""Unit tests for the shared tool classifier."""
from __future__ import annotations

import pytest

from zelosmcp.framework.assetstore.tool_classify import classify_tool, format_args


class TestClassifyTool:
    def _tool(self, name: str, **annotations) -> dict:
        t = {"name": name}
        if annotations:
            t["annotations"] = annotations
        return t

    def test_readonly_hint_wins(self):
        assert classify_tool(self._tool("delete_thing", readOnlyHint=True)) == "readonly"

    def test_destructive_hint_wins_over_readonly(self):
        t = {"name": "thing", "annotations": {"destructiveHint": True, "readOnlyHint": True}}
        assert classify_tool(t) == "destructive"

    def test_create_prefix_is_mutates(self):
        assert classify_tool(self._tool("create_pod")) == "mutates"

    def test_delete_prefix_is_mutates(self):
        assert classify_tool(self._tool("delete_pod")) == "mutates"

    def test_list_prefix_defaults_to_question_mark(self):
        # list_ is NOT in the mutating prefixes; falls through to "?"
        assert classify_tool(self._tool("list_pods")) == "?"

    def test_unknown_tool_is_question_mark(self):
        assert classify_tool(self._tool("frobnicate_resource")) == "?"

    def test_empty_annotations_falls_through(self):
        t = {"name": "start_server", "annotations": {}}
        assert classify_tool(t) == "mutates"


class TestFormatArgs:
    def test_no_schema(self):
        assert format_args(None) == "()"

    def test_empty_object(self):
        assert format_args({}) == "()"

    def test_object_no_properties(self):
        result = format_args({"type": "object"})
        assert result == "(...)"

    def test_required_and_optional(self):
        schema = {
            "type": "object",
            "properties": {"a": {}, "b": {}, "c": {}},
            "required": ["a", "c"],
        }
        result = format_args(schema)
        assert "a" in result
        assert "b?" in result
        assert "c" in result
        assert result.startswith("(")
        assert result.endswith(")")

    def test_required_first(self):
        schema = {
            "type": "object",
            "properties": {"opt": {}, "req": {}},
            "required": ["req"],
        }
        result = format_args(schema)
        # required 'req' must appear before optional 'opt?'
        assert result.index("req") < result.index("opt?")
