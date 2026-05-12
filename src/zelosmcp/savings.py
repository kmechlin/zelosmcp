"""Token-savings recording: counter, pincher `_meta` extractor, recorder facade.

Three measurement sources land here:

1. **Compression snapshots** — produced by the aggregator each time
   ``list_tools`` runs. Compares the JSON-serialized full backend tool
   catalog against the compressed-wrapper view that zelosMCP actually returns.
2. **Per-call token accounting** — every ``call_tool`` (raw or compressed)
   contributes input/output token counts plus latency.
3. **Pincher self-reported savings** — pincher already returns BPE-correct
   counts via the ``_meta`` envelope and a ``pincher__stats`` summary.
   We probe both shapes and persist them verbatim alongside.

Counters live in the SQLite store from :mod:`zelosmcp.savings_db`. The
recorder is the only thing instrumented call sites import — it owns lock
contention, error swallowing, and the broadcast hook the SSE endpoint
listens on.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

from zelosmcp.savings_db import SavingsStore

logger = logging.getLogger("zelosmcp.savings")


# Encoding name used for the optional tiktoken backend. cl100k_base is the
# OpenAI gpt-4 / gpt-3.5-turbo BPE; close enough to Anthropic's tokenizer
# for trend-line reporting (within a few percent on typical text).
_TIKTOKEN_ENCODING = "cl100k_base"


class TokenCounter:
    """Lazy tiktoken wrapper with a heuristic fallback.

    ``count_text`` returns the BPE token count when tiktoken is importable;
    otherwise ``len(s) // 4``. ``count_obj`` JSON-serializes first so we
    can score a tool's request/response in one call.
    """

    def __init__(self) -> None:
        self._encoding: Any = None
        self._tried_load = False
        self._using_heuristic = True

    def warmup(self) -> None:
        """Force the BPE merges file to load now (slow first-call). Safe to
        call from the lifespan startup hook so the request hot-path is
        already warm by the time the first tool call lands."""
        self._ensure_encoding()
        if self._encoding is not None:
            try:
                self._encoding.encode("warmup")
            except Exception:
                pass

    @property
    def using_heuristic(self) -> bool:
        return self._using_heuristic

    def _ensure_encoding(self) -> None:
        if self._tried_load:
            return
        self._tried_load = True
        try:
            import tiktoken  # type: ignore[import-not-found]

            self._encoding = tiktoken.get_encoding(_TIKTOKEN_ENCODING)
            self._using_heuristic = False
            logger.info("token counter: using tiktoken/%s", _TIKTOKEN_ENCODING)
        except Exception as exc:
            logger.info(
                "token counter: tiktoken unavailable (%s); using heuristic",
                exc,
            )
            self._encoding = None
            self._using_heuristic = True

    def count_text(self, text: str | None) -> int:
        if not text:
            return 0
        self._ensure_encoding()
        if self._encoding is None:
            return max(1, len(text) // 4)
        try:
            return len(self._encoding.encode(text))
        except Exception:
            return max(1, len(text) // 4)

    def count_obj(self, obj: Any) -> int:
        if obj is None:
            return 0
        if isinstance(obj, str):
            return self.count_text(obj)
        try:
            text = json.dumps(obj, default=str, ensure_ascii=False)
        except Exception:
            text = str(obj)
        return self.count_text(text)


# ── Pincher _meta extractor ─────────────────────────────────────────────


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_PINCHER_META_KEYS_USED = ("tokens_used", "tokensUsed", "input_tokens")
_PINCHER_META_KEYS_SAVED = ("tokens_saved", "tokensSaved", "saved_tokens")
_PINCHER_META_KEYS_COST = ("cost_avoided", "costAvoided", "cost_avoided_usd")


def _pluck(d: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d:
            return d[k]
    return None


def extract_pincher_meta(call_result: Any) -> dict[str, Any] | None:
    """Find pincher's `_meta` envelope on a CallToolResult.

    pincher has been observed to put the envelope in three places: directly
    on the result (``r.meta`` / ``r._meta``), on ``structuredContent`` (as
    a top-level dict key), or on ``content[i].annotations``. We probe each
    in order and return the first that yields any of the recognized keys.
    Returns ``None`` if nothing usable is present.
    """
    candidates: list[Any] = []

    for attr in ("meta", "_meta"):
        v = getattr(call_result, attr, None)
        if v is not None:
            candidates.append(v)

    structured = getattr(call_result, "structuredContent", None)
    if isinstance(structured, dict):
        for attr in ("_meta", "meta"):
            if attr in structured:
                candidates.append(structured[attr])
        # pincher's structuredContent sometimes carries the keys at root.
        candidates.append(structured)

    content = getattr(call_result, "content", None) or []
    for item in content:
        ann = getattr(item, "annotations", None)
        if ann is not None:
            candidates.append(ann)
        meta = getattr(item, "meta", None)
        if meta is not None:
            candidates.append(meta)

    for cand in candidates:
        used = _pluck(cand, _PINCHER_META_KEYS_USED)
        saved = _pluck(cand, _PINCHER_META_KEYS_SAVED)
        cost = _pluck(cand, _PINCHER_META_KEYS_COST)
        if used is None and saved is None and cost is None:
            continue
        return {
            "tokens_used": _coerce_int(used),
            "tokens_saved": _coerce_int(saved),
            "cost_avoided": _coerce_float(cost),
            "raw": cand if isinstance(cand, dict) else None,
        }
    return None


# ── Output rendering helper ─────────────────────────────────────────────


def render_call_output_text(call_result: Any) -> str:
    """Best-effort flatten of a ``CallToolResult`` into a single string for
    token counting. Concatenates every TextContent's ``.text`` then appends
    a JSON dump of ``structuredContent`` when present. Non-text content
    blocks (images, blobs) contribute their type name as a placeholder
    rather than the raw bytes — counting base64 against the LLM's text
    tokenizer would massively over-report."""
    parts: list[str] = []
    for item in getattr(call_result, "content", None) or []:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue
        kind = getattr(item, "type", None) or item.__class__.__name__
        parts.append(f"<{kind}>")
    structured = getattr(call_result, "structuredContent", None)
    if structured is not None:
        try:
            parts.append(json.dumps(structured, default=str, ensure_ascii=False))
        except Exception:
            parts.append(str(structured))
    return "".join(parts)


# ── Recorder ────────────────────────────────────────────────────────────


# Built-in / introspection backends excluded from dashboard totals so the
# dashboard's own queries don't pollute its metrics. ``zelosmcp__*`` is
# the always-on built-in MCP; the recorder still writes events for it
# (useful for debugging) but the public aggregations subtract it.
_EXCLUDE_FROM_TOTALS: tuple[str, ...] = ("zelosmcp",)


# Cap a single tokenization payload before falling back to the heuristic.
# tiktoken on multi-megabyte text is single-threaded Rust that holds the
# GIL; running it inline would block the anyio cancel scope. Anything
# bigger than this gets a fast char-based estimate instead.
_INLINE_TOKEN_LIMIT = 64 * 1024


class SavingsRecorder:
    """Writes savings data through the SQLite store and broadcasts events."""

    def __init__(self, store: SavingsStore, counter: TokenCounter | None = None) -> None:
        self.store = store
        self.counter = counter or TokenCounter()
        self._subscribers: list[asyncio.Queue[str]] = []

    # ── Subscription (SSE) ──────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=128)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _broadcast(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, default=str)
        for q in list(self._subscribers):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                pass

    # ── Recording ───────────────────────────────────────────────────────

    def _count_safely(self, payload_text: str) -> int:
        """Token-count a string, never raising and never blocking forever."""
        if not payload_text:
            return 0
        if len(payload_text) > _INLINE_TOKEN_LIMIT:
            return max(1, len(payload_text) // 4)
        try:
            return self.counter.count_text(payload_text)
        except Exception:
            return max(1, len(payload_text) // 4)

    async def record_compression(
        self,
        *,
        backend: str,
        level: str | None,
        raw_payload: Any,
        compressed_payload: Any,
    ) -> None:
        """Persist a compression snapshot for one backend.

        ``raw_payload`` and ``compressed_payload`` are JSON-serializable
        objects (typically lists of Tool dicts as they'd appear on the
        wire). Token and byte counts are derived from their JSON encoding.
        """
        try:
            raw_json = json.dumps(raw_payload, default=str, ensure_ascii=False)
            comp_json = json.dumps(
                compressed_payload, default=str, ensure_ascii=False
            )
        except Exception as exc:
            logger.debug("record_compression(%s): json encode failed: %s",
                         backend, exc)
            return

        raw_tokens = self._count_safely(raw_json)
        comp_tokens = self._count_safely(comp_json)
        try:
            await self.store.upsert_compression(
                backend=backend,
                level=level,
                raw_tokens=raw_tokens,
                compressed_tokens=comp_tokens,
                raw_bytes=len(raw_json.encode("utf-8")),
                compressed_bytes=len(comp_json.encode("utf-8")),
            )
        except Exception as exc:
            logger.warning("record_compression(%s) write failed: %s",
                           backend, exc)
            return
        self._broadcast({
            "event": "compression",
            "backend": backend,
            "level": level,
            "raw_tokens": raw_tokens,
            "compressed_tokens": comp_tokens,
            "ts": time.time(),
        })

    async def record_call(
        self,
        *,
        backend: str,
        tool: str,
        qualified: str,
        compressed: bool,
        arguments: Any,
        result: Any,
        latency_ms: int,
        error: bool,
    ) -> None:
        in_tokens = self.counter.count_obj(arguments)
        try:
            output_text = render_call_output_text(result)
        except Exception:
            output_text = ""
        out_tokens = self._count_safely(output_text)
        try:
            await self.store.insert_call(
                backend=backend,
                tool=tool,
                qualified=qualified,
                compressed=compressed,
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                latency_ms=latency_ms,
                error=error,
            )
        except Exception as exc:
            logger.warning("record_call(%s) write failed: %s", qualified, exc)
            return

        if backend == "pincher":
            meta = extract_pincher_meta(result)
            if meta is not None:
                try:
                    await self.store.insert_pincher_meta(
                        tool=tool,
                        tokens_used=meta.get("tokens_used"),
                        tokens_saved=meta.get("tokens_saved"),
                        cost_avoided=meta.get("cost_avoided"),
                        raw_meta=meta.get("raw"),
                    )
                except Exception as exc:
                    logger.warning("pincher meta write failed: %s", exc)

        self._broadcast({
            "event": "call",
            "backend": backend,
            "tool": tool,
            "qualified": qualified,
            "compressed": compressed,
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "latency_ms": latency_ms,
            "error": error,
            "ts": time.time(),
        })

    async def record_pincher_stats(self, payload: Any) -> None:
        try:
            await self.store.insert_pincher_stats_snapshot(payload)
        except Exception as exc:
            logger.warning("pincher stats snapshot write failed: %s", exc)
            return
        self._broadcast({"event": "pincher_stats", "ts": time.time()})

    # ── Reads (powering /api/savings) ───────────────────────────────────

    async def snapshot(self) -> dict[str, Any]:
        compression = await self.store.fetch_compression()
        totals = await self.store.fetch_call_totals(
            exclude_backends=_EXCLUDE_FROM_TOTALS
        )
        per_backend = await self.store.fetch_per_backend(
            exclude_backends=_EXCLUDE_FROM_TOTALS
        )
        top_tools = await self.store.fetch_top_tools(
            exclude_backends=_EXCLUDE_FROM_TOTALS
        )
        pincher = await self.store.fetch_pincher_totals()

        compression_saved_tokens = sum(c["saved_tokens"] for c in compression)
        return {
            "generated_at": time.time(),
            "tokenizer": {
                "heuristic": self.counter.using_heuristic,
                "encoding": (
                    None if self.counter.using_heuristic
                    else _TIKTOKEN_ENCODING
                ),
            },
            "compression": compression,
            "compression_saved_tokens_total": compression_saved_tokens,
            "calls": {
                "totals": totals,
                "per_backend": per_backend,
                "top_tools": top_tools,
            },
            "pincher": pincher,
        }


# ── Wrapper helper used by aggregator/proxy instrumentation ─────────────


async def measure_call(
    *,
    recorder: SavingsRecorder | None,
    backend: str,
    tool: str,
    qualified: str,
    compressed: bool,
    arguments: Any,
    dispatch: Callable[[], Awaitable[Any]],
) -> Any:
    """Run ``dispatch()`` and route its result through the recorder.

    Returns the dispatched result unchanged so the call site stays a
    one-line replacement. ``recorder=None`` short-circuits to a plain
    await — handy for paths that must not depend on savings being wired
    up (e.g. tests that don't construct a manager).
    """
    if recorder is None:
        return await dispatch()
    started = time.perf_counter()
    err = False
    result: Any = None
    try:
        result = await dispatch()
        err = bool(getattr(result, "isError", False))
        return result
    except BaseException:
        err = True
        raise
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            await recorder.record_call(
                backend=backend,
                tool=tool,
                qualified=qualified,
                compressed=compressed,
                arguments=arguments,
                result=result,
                latency_ms=latency_ms,
                error=err,
            )
        except Exception as exc:
            logger.debug("savings record_call swallowed: %s", exc)
