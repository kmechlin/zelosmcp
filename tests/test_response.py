"""Tests for zelosmcp.response — TOON/JSON/YAML response serialization."""

from __future__ import annotations

import json

from mcp.types import TextContent

from zelosmcp.response import (
    DEFAULT_RESPONSE_FORMAT,
    RESPONSE_FORMATS,
    _compact_json,
    _to_toon,
    _try_parse_structured,
    transform_content_block,
    transform_response,
)


# ---------------------------------------------------------------------------
# _try_parse_structured
# ---------------------------------------------------------------------------


class TestTryParseStructured:
    def test_json_object(self):
        assert _try_parse_structured('{"a": 1}') == {"a": 1}

    def test_json_array(self):
        assert _try_parse_structured('[1, 2, 3]') == [1, 2, 3]

    def test_yaml_dict(self):
        text = "name: Alice\nage: 30\n"
        result = _try_parse_structured(text)
        assert result == {"name": "Alice", "age": 30}

    def test_yaml_list(self):
        text = "- one\n- two\n- three\n"
        result = _try_parse_structured(text)
        assert result == ["one", "two", "three"]

    def test_plain_text_returns_none(self):
        assert _try_parse_structured("Hello world") is None

    def test_empty_returns_none(self):
        assert _try_parse_structured("") is None
        assert _try_parse_structured("   ") is None

    def test_markdown_returns_none(self):
        assert _try_parse_structured("# Title\n\nSome text.") is None

    def test_json_preferred_over_yaml(self):
        """JSON is valid YAML — ensure JSON path wins."""
        text = '{"key": "value"}'
        result = _try_parse_structured(text)
        assert result == {"key": "value"}

    def test_yaml_scalar_returns_none(self):
        """yaml.safe_load('hello') returns 'hello' — skip scalars."""
        assert _try_parse_structured("hello") is None


# ---------------------------------------------------------------------------
# _compact_json
# ---------------------------------------------------------------------------


class TestCompactJson:
    def test_no_whitespace(self):
        obj = {"a": 1, "b": [2, 3]}
        result = _compact_json(obj)
        assert " " not in result
        assert "\n" not in result
        assert json.loads(result) == obj

    def test_unicode_preserved(self):
        obj = {"emoji": "🎉"}
        result = _compact_json(obj)
        assert "🎉" in result


# ---------------------------------------------------------------------------
# _to_toon
# ---------------------------------------------------------------------------


class TestToToon:
    def test_list_of_dicts(self):
        data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
        result = _to_toon(data)
        assert result is not None
        assert "@schema:" in result
        assert "Alice" in result
        assert "Bob" in result

    def test_single_dict(self):
        data = {"id": 1, "name": "Alice"}
        result = _to_toon(data)
        assert result is not None
        assert "@schema:" in result

    def test_returns_none_without_library(self, monkeypatch):
        import zelosmcp.response as mod

        monkeypatch.setattr(mod, "json_to_toon", None)
        assert _to_toon([{"a": 1}]) is None


# ---------------------------------------------------------------------------
# transform_content_block
# ---------------------------------------------------------------------------


class TestTransformContentBlock:
    def test_raw_passthrough(self):
        block = TextContent(type="text", text='{"a": 1}')
        result, was_toon = transform_content_block(block, "raw")
        assert result.text == '{"a": 1}'
        assert was_toon is False

    def test_toon_conversion(self):
        data = [{"id": 1, "name": "Alice"}]
        block = TextContent(type="text", text=json.dumps(data))
        result, was_toon = transform_content_block(block, "toon")
        assert was_toon is True
        assert "@schema:" in result.text

    def test_compact_json(self):
        block = TextContent(
            type="text",
            text=json.dumps({"a": 1, "b": 2}, indent=2),
        )
        result, was_toon = transform_content_block(block, "compact_json")
        assert was_toon is False
        assert result.text == '{"a":1,"b":2}'

    def test_non_parseable_passthrough(self):
        block = TextContent(type="text", text="Hello world")
        result, was_toon = transform_content_block(block, "toon")
        assert result.text == "Hello world"
        assert was_toon is False

    def test_yaml_to_toon(self):
        yaml_text = "- id: 1\n  name: Alice\n- id: 2\n  name: Bob\n"
        block = TextContent(type="text", text=yaml_text)
        result, was_toon = transform_content_block(block, "toon")
        assert was_toon is True
        assert "@schema:" in result.text

    def test_yaml_to_compact_json(self):
        yaml_text = "name: Alice\nage: 30\n"
        block = TextContent(type="text", text=yaml_text)
        result, was_toon = transform_content_block(block, "compact_json")
        assert was_toon is False
        parsed = json.loads(result.text)
        assert parsed == {"name": "Alice", "age": 30}


# ---------------------------------------------------------------------------
# transform_response
# ---------------------------------------------------------------------------


class TestTransformResponse:
    def test_raw_noop(self):
        content = [TextContent(type="text", text='{"a": 1}')]
        new_content, meta = transform_response(
            content, response_format="raw"
        )
        assert new_content[0].text == '{"a": 1}'
        assert meta is None

    def test_toon_sets_format_meta(self):
        data = [{"id": 1, "name": "Alice"}]
        content = [TextContent(type="text", text=json.dumps(data))]
        new_content, meta = transform_response(
            content, response_format="toon"
        )
        assert meta is not None
        assert meta.get("_format") == "toon"
        assert "@schema:" in new_content[0].text

    def test_compact_json_no_format_meta(self):
        content = [
            TextContent(
                type="text",
                text=json.dumps({"a": 1}, indent=2),
            )
        ]
        new_content, meta = transform_response(
            content, response_format="compact_json"
        )
        assert new_content[0].text == '{"a":1}'
        # No _format meta for compact_json
        assert meta is None

    def test_session_gate_downgrades_toon(self):
        data = [{"id": 1, "name": "Alice"}]
        content = [TextContent(type="text", text=json.dumps(data))]
        new_content, meta = transform_response(
            content,
            response_format="toon",
            accepts_toon=False,
        )
        # Should downgrade to compact_json
        parsed = json.loads(new_content[0].text)
        assert parsed == data
        assert meta is None  # No toon format marker

    def test_preserves_existing_meta(self):
        data = [{"id": 1, "name": "Alice"}]
        content = [TextContent(type="text", text=json.dumps(data))]
        existing_meta = {"some_key": "some_value"}
        new_content, meta = transform_response(
            content,
            response_format="toon",
            meta=existing_meta,
        )
        assert meta["some_key"] == "some_value"
        assert meta["_format"] == "toon"

    def test_mixed_content_types(self):
        """Non-text blocks pass through unchanged."""
        from mcp.types import ImageContent

        text_block = TextContent(
            type="text",
            text=json.dumps([{"a": 1}]),
        )
        img_block = ImageContent(
            type="image",
            data="base64data",
            mimeType="image/png",
        )
        new_content, meta = transform_response(
            [text_block, img_block],
            response_format="toon",
        )
        assert len(new_content) == 2
        assert "@schema:" in new_content[0].text
        assert new_content[1] == img_block

    def test_multiple_text_blocks(self):
        content = [
            TextContent(type="text", text=json.dumps({"a": 1})),
            TextContent(type="text", text="plain text"),
            TextContent(
                type="text",
                text=json.dumps([{"x": 1}]),
            ),
        ]
        new_content, meta = transform_response(
            content, response_format="toon"
        )
        # First block: dict → TOON
        assert "@schema:" in new_content[0].text
        # Second block: unchanged
        assert new_content[1].text == "plain text"
        # Third block: list → TOON
        assert "@schema:" in new_content[2].text


# ---------------------------------------------------------------------------
# Config constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_format(self):
        assert DEFAULT_RESPONSE_FORMAT == "toon"

    def test_valid_formats(self):
        assert RESPONSE_FORMATS == {"toon", "compact_json", "raw"}
