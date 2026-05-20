"""Cross-module string / tuple constants.

Centralising values that are referenced from more than one module so they
can't drift. Constants that are private to a single module (e.g. the
reserved-name frozensets in ``config.py``) stay where they are.
"""

from __future__ import annotations

# Aggregator backend / tool name separator. Tool, prompt and elicitation
# names exposed by ``/mcp`` are prefixed ``<backend>{SEP}<original>``.
SEP: str = "__"

# Pincher's `_meta` envelope can land under one of several keys depending
# on the pincher build. ``savings.extract_pincher_meta`` probes each name
# in order and returns the first hit, so the order in these tuples is
# significant (most specific first).
PINCHER_META_KEYS_USED: tuple[str, ...] = (
    "tokens_used",
    "tokensUsed",
    "input_tokens",
)
PINCHER_META_KEYS_SAVED: tuple[str, ...] = (
    "tokens_saved",
    "tokensSaved",
    "saved_tokens",
)
PINCHER_META_KEYS_COST: tuple[str, ...] = (
    "cost_avoided",
    "costAvoided",
    "cost_avoided_usd",
)

__all__ = [
    "SEP",
    "PINCHER_META_KEYS_USED",
    "PINCHER_META_KEYS_SAVED",
    "PINCHER_META_KEYS_COST",
]
