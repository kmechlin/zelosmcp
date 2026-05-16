"""Response serialization for token-efficient MCP tool output.

Converts JSON and YAML ``TextContent`` blocks into TOON (Token-Optimized
Object Notation) format before returning to the client, saving 40-60%
tokens on structured responses.  Falls back to compact JSON for
structures that TOON can't represent efficiently, and passes non-parseable
text through unchanged.

Configuration is per-backend (``response_format`` in the server spec) with
a global default and an env-var override.  Three modes:

- ``"toon"``  — convert to TOON; compact-JSON fallback on failure (default)
- ``"compact_json"`` — minified JSON only, no TOON
- ``"raw"``   — pass through unchanged (current behaviour)

A session-level gate (``accepts_toon``, default ``True``) lets future
clients opt out via ``initialize`` preferences.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import yaml

try:
    from toon_converter import json_to_toon
except ImportError:  # pragma: no cover — graceful degradation
    json_to_toon = None  # type: ignore[assignment]

from mcp.types import TextContent

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

RESPONSE_FORMATS: frozenset[str] = frozenset({"toon", "compact_json", "raw"})
DEFAULT_RESPONSE_FORMAT: str = "toon"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _try_parse_structured(text: str) -> Any | None:
    """Try to parse *text* as JSON first, then YAML.

    Returns the parsed Python object, or ``None`` if both fail.
    JSON is tried first because it's stricter — ``yaml.safe_load``
    happily parses bare strings and numbers, which we don't want to
    convert.
    """
    # Fast-path: skip obviously non-structured text.
    stripped = text.strip()
    if not stripped:
        return None
    first = stripped[0]
    if first not in ('{', '[', '"', '-', '0', '1', '2', '3', '4',
                     '5', '6', '7', '8', '9', 't', 'f', 'n'):
        # Could still be YAML with a key: value on the first line.
        if ':' not in stripped.split('\n', 1)[0]:
            return None

    # 1) JSON (strict)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass

    # 2) YAML (only if it looks multi-line or has dict/list structure)
    if '\n' in stripped or first in ('-',):
        try:
            obj = yaml.safe_load(text)
            # yaml.safe_load returns str for bare scalars — skip those.
            if isinstance(obj, (dict, list)):
                return obj
        except yaml.YAMLError:
            pass

    return None


def _to_toon(obj: Any) -> str | None:
    """Convert a Python object to TOON format.

    Returns the TOON string, or ``None`` if the library is unavailable
    or the conversion fails.
    """
    if json_to_toon is None:
        return None
    try:
        if isinstance(obj, list):
            return json_to_toon(obj)
        # For dicts, toon_converter expects a list of dicts.
        # Wrap single dicts — the schema line still saves tokens
        # by declaring keys once.
        if isinstance(obj, dict):
            return json_to_toon([obj])
    except Exception:
        log.debug("TOON conversion failed, falling back to compact JSON", exc_info=True)
    return None


def _compact_json(obj: Any) -> str:
    """Minified JSON with no whitespace."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _strip_meta_keys(obj: Any) -> Any:
    """Remove ``_meta`` and ``meta`` keys from a parsed JSON object.

    Works on dicts (top-level removal) and lists-of-dicts (per-element).
    Returns the mutated object (or original if not applicable).
    """
    if isinstance(obj, dict):
        obj.pop("_meta", None)
        obj.pop("meta", None)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                item.pop("_meta", None)
                item.pop("meta", None)
    return obj


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def transform_content_block(
    block: TextContent,
    fmt: str,
    strip_meta: bool = False,
) -> tuple[TextContent, bool]:
    """Transform a single ``TextContent`` block.

    Returns ``(new_block, was_toon_converted)``.
    """
    if fmt == "raw" and not strip_meta:
        return block, False

    text = block.text if hasattr(block, "text") else None
    if text is None:
        return block, False

    obj = _try_parse_structured(text)
    if obj is None:
        # Not parseable — pass through unchanged.
        return block, False

    # Strip _meta / meta envelopes before any serialization.
    if strip_meta:
        obj = _strip_meta_keys(obj)

    if fmt == "raw":
        # Re-serialize only if we stripped something; otherwise pass through.
        if strip_meta:
            return TextContent(type="text", text=_compact_json(obj)), False
        return block, False

    if fmt == "toon":
        toon = _to_toon(obj)
        if toon is not None:
            compact = _compact_json(obj)
            # Only use TOON when it actually saves tokens vs compact JSON.
            if len(toon) <= len(compact):
                return TextContent(type="text", text=toon), True
            # TOON is larger — fall through to compact JSON.
            return TextContent(type="text", text=compact), False
        # TOON failed — fall through to compact JSON.

    # compact_json or toon-fallback
    return TextContent(type="text", text=_compact_json(obj)), False


def transform_response(
    content: list,
    *,
    response_format: str = DEFAULT_RESPONSE_FORMAT,
    accepts_toon: bool = True,
    strip_meta: bool = False,
    meta: dict[str, Any] | None = None,
) -> tuple[list, dict[str, Any] | None]:
    """Transform all ``TextContent`` blocks in a tool-call response.

    Args:
        content: The ``content`` list from a ``CallToolResult``.
        response_format: ``"toon"`` / ``"compact_json"`` / ``"raw"``.
        accepts_toon: Session-level gate; when ``False``, TOON is
            downgraded to ``"compact_json"``.
        strip_meta: Remove ``_meta`` / ``meta`` keys from JSON blocks
            before serialization.
        meta: Existing ``_meta`` dict (or ``None``).  A new dict is
            created when needed.

    Returns:
        ``(new_content, new_meta)`` — the transformed content list and
        updated meta dict (with ``_format: "toon"`` when applicable).
    """
    if response_format == "raw" and not strip_meta:
        return content, meta

    # Session gate: downgrade toon → compact_json when client opts out.
    fmt = response_format
    if fmt == "toon" and not accepts_toon:
        fmt = "compact_json"

    any_toon = False
    new_content: list = []
    for block in content:
        if isinstance(block, TextContent) or (
            hasattr(block, "type") and getattr(block, "type", None) == "text"
        ):
            transformed, was_toon = transform_content_block(block, fmt, strip_meta=strip_meta)
            new_content.append(transformed)
            if was_toon:
                any_toon = True
        else:
            new_content.append(block)

    # Annotate meta when TOON was applied.
    new_meta = dict(meta) if meta else ({} if any_toon else None)
    if any_toon and new_meta is not None:
        new_meta["_format"] = "toon"

    return new_content, new_meta
