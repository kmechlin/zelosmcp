from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcp.types import TextContent

from zelosmcp.savings import (
    SavingsRecorder,
    TokenCounter,
    extract_pincher_meta,
    measure_call,
    render_call_output_text,
)
from zelosmcp.savings_db import SavingsStore, resolve_db_path

from tests.conftest import FakeResult, make_pincher_call_result


# ── TokenCounter ────────────────────────────────────────────────────────


def test_token_counter_heuristic_when_tiktoken_missing(monkeypatch):
    """If tiktoken can't be imported, count_text falls back to len/4."""
    counter = TokenCounter()

    real_import = __import__

    def stubbed(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", stubbed)
    counter.warmup()
    assert counter.using_heuristic is True
    assert counter.count_text("a" * 8) == 2
    assert counter.count_text("") == 0
    assert counter.count_obj({"x": 1}) > 0


def test_token_counter_with_tiktoken_when_available():
    """If tiktoken imports successfully, encoding is used and is non-zero."""
    pytest.importorskip("tiktoken")
    counter = TokenCounter()
    counter.warmup()
    n = counter.count_text("hello world")
    assert n > 0
    assert counter.using_heuristic is False


# ── pincher _meta extraction ────────────────────────────────────────────


@pytest.mark.parametrize("location", ["result", "structured", "annotation"])
def test_extract_pincher_meta_three_shapes(location):
    r = make_pincher_call_result(location=location)
    meta = extract_pincher_meta(r)
    assert meta is not None, f"failed at location={location}"
    assert meta["tokens_used"] == 100
    assert meta["tokens_saved"] == 900
    assert meta["cost_avoided"] == pytest.approx(0.0042)


def test_extract_pincher_meta_returns_none_when_absent():
    r = FakeResult(content=[TextContent(type="text", text="hi")],
                   structuredContent=None, isError=False, meta=None)
    assert extract_pincher_meta(r) is None


def test_extract_pincher_meta_handles_camelcase_keys():
    r = FakeResult(
        content=[],
        structuredContent={
            "_meta": {
                "tokensUsed": "55",
                "tokensSaved": 12,
                "costAvoided": "0.001",
            }
        },
        isError=False,
        meta=None,
    )
    meta = extract_pincher_meta(r)
    assert meta == {
        "tokens_used": 55,
        "tokens_saved": 12,
        "cost_avoided": pytest.approx(0.001),
        "raw": {"tokensUsed": "55", "tokensSaved": 12, "costAvoided": "0.001"},
    }


# ── render_call_output_text ─────────────────────────────────────────────


def test_render_call_output_text_text_and_structured():
    r = FakeResult(
        content=[
            TextContent(type="text", text="alpha"),
            TextContent(type="text", text="beta"),
        ],
        structuredContent={"x": 1},
        isError=False,
    )
    out = render_call_output_text(r)
    assert "alpha" in out and "beta" in out
    assert json.dumps({"x": 1}) in out


def test_render_call_output_text_handles_blob_blocks():
    """Non-text content blocks shouldn't get base64'd into the token stream."""
    blob = FakeResult(type="image", data="base64-noise")
    r = FakeResult(content=[blob], structuredContent=None, isError=False)
    out = render_call_output_text(r)
    assert "<image>" in out
    assert "base64-noise" not in out


# ── SavingsStore + Recorder integration ─────────────────────────────────


@pytest.mark.asyncio
async def test_recorder_records_compression_snapshot():
    store = SavingsStore(":memory:")
    await store.open()
    try:
        recorder = SavingsRecorder(store=store, counter=TokenCounter())
        raw = [{"name": "a", "description": "x" * 200}]
        compressed = [{"name": "wrapper"}]
        await recorder.record_compression(
            backend="pincher", level="medium",
            raw_payload=raw, compressed_payload=compressed,
        )
        rows = await store.fetch_compression()
        assert len(rows) == 1
        row = rows[0]
        assert row["backend"] == "pincher"
        assert row["level"] == "medium"
        assert row["raw_tokens"] > row["compressed_tokens"]
        assert row["saved_tokens"] == row["raw_tokens"] - row["compressed_tokens"]
        assert row["saved_pct"] > 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recorder_records_call_event_and_pincher_meta():
    store = SavingsStore(":memory:")
    await store.open()
    try:
        recorder = SavingsRecorder(store=store, counter=TokenCounter())
        result = make_pincher_call_result(text="answer body", tokens_saved=500)
        await recorder.record_call(
            backend="pincher",
            tool="search",
            qualified="pincher__search",
            compressed=False,
            arguments={"query": "open"},
            result=result,
            latency_ms=42,
            error=False,
        )
        totals = await store.fetch_call_totals()
        assert totals["calls"] == 1
        assert totals["compressed_calls"] == 0
        assert totals["input_tokens"] > 0
        assert totals["output_tokens"] > 0
        # Pincher meta recorded as a side effect of pincher call.
        pincher = await store.fetch_pincher_totals()
        assert pincher["calls_with_meta"] == 1
        assert pincher["tokens_saved_total"] == 500
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_non_pincher_call_does_not_record_meta():
    store = SavingsStore(":memory:")
    await store.open()
    try:
        recorder = SavingsRecorder(store=store, counter=TokenCounter())
        result = make_pincher_call_result(location="result")
        await recorder.record_call(
            backend="filesystem",
            tool="read_text_file",
            qualified="filesystem__read_text_file",
            compressed=True,
            arguments={"path": "/x"},
            result=result,
            latency_ms=5,
            error=False,
        )
        pincher = await store.fetch_pincher_totals()
        assert pincher["calls_with_meta"] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_snapshot_excludes_zelosmcp_from_totals():
    store = SavingsStore(":memory:")
    await store.open()
    try:
        recorder = SavingsRecorder(store=store, counter=TokenCounter())
        # One call for a real backend, one for the built-in.
        for backend, tool in [
            ("pincher", "search"),
            ("zelosmcp", "list_loaded_servers"),
        ]:
            await recorder.record_call(
                backend=backend, tool=tool,
                qualified=f"{backend}__{tool}",
                compressed=False,
                arguments={}, result=FakeResult(content=[], structuredContent=None, isError=False),
                latency_ms=1, error=False,
            )
        snap = await recorder.snapshot()
        assert snap["calls"]["totals"]["calls"] == 1
        backends = [b["backend"] for b in snap["calls"]["per_backend"]]
        assert "zelosmcp" not in backends
        assert "pincher" in backends
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_measure_call_records_latency_and_passes_through_result():
    store = SavingsStore(":memory:")
    await store.open()
    try:
        recorder = SavingsRecorder(store=store, counter=TokenCounter())
        sentinel = FakeResult(content=[], structuredContent=None, isError=False)

        async def dispatch():
            await asyncio.sleep(0.01)
            return sentinel

        ret = await measure_call(
            recorder=recorder,
            backend="filesystem",
            tool="read_text_file",
            qualified="filesystem__read_text_file",
            compressed=True,
            arguments={"path": "/etc/hosts"},
            dispatch=dispatch,
        )
        assert ret is sentinel
        rows = await store.fetch_per_backend()
        assert any(r["backend"] == "filesystem" for r in rows)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_measure_call_records_error_when_dispatch_raises():
    store = SavingsStore(":memory:")
    await store.open()
    try:
        recorder = SavingsRecorder(store=store, counter=TokenCounter())

        async def dispatch():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await measure_call(
                recorder=recorder,
                backend="docker",
                tool="list_containers",
                qualified="docker__list_containers",
                compressed=False,
                arguments={},
                dispatch=dispatch,
            )
        totals = await store.fetch_call_totals()
        assert totals["calls"] == 1
        assert totals["errors"] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_measure_call_with_no_recorder_is_passthrough():
    """Calls outside a manager (tests, raw scripts) must still work."""
    sentinel = object()

    async def dispatch():
        return sentinel

    out = await measure_call(
        recorder=None,
        backend="x", tool="y", qualified="x__y", compressed=False,
        arguments={}, dispatch=dispatch,
    )
    assert out is sentinel


# ── /api/savings via SavingsRecorder.snapshot() ─────────────────────────


@pytest.mark.asyncio
async def test_snapshot_shape_and_top_tools_ordering():
    store = SavingsStore(":memory:")
    await store.open()
    try:
        recorder = SavingsRecorder(store=store, counter=TokenCounter())
        # Heavy-output call.
        heavy = FakeResult(
            content=[TextContent(type="text", text="x" * 4000)],
            structuredContent=None, isError=False,
        )
        light = FakeResult(
            content=[TextContent(type="text", text="ok")],
            structuredContent=None, isError=False,
        )
        for _ in range(3):
            await recorder.record_call(
                backend="pincher", tool="search",
                qualified="pincher__search", compressed=False,
                arguments={"q": "x"}, result=heavy,
                latency_ms=10, error=False,
            )
        for _ in range(10):
            await recorder.record_call(
                backend="filesystem", tool="get_file_info",
                qualified="filesystem__get_file_info", compressed=True,
                arguments={"path": "/x"}, result=light,
                latency_ms=2, error=False,
            )

        snap = await recorder.snapshot()
        assert "compression" in snap
        assert "calls" in snap
        assert "pincher" in snap

        top_tools = snap["calls"]["top_tools"]
        assert top_tools, "expected at least one top tool"
        # Heavy-output calls dominate token count even with fewer calls.
        assert top_tools[0]["qualified"] == "pincher__search"
    finally:
        await store.close()


# ── DB path resolution ─────────────────────────────────────────────────


def test_resolve_db_path_uses_env(tmp_path, monkeypatch):
    target = str(tmp_path / "custom.sqlite")
    monkeypatch.setenv("ZELOSMCP_SAVINGS_DB", target)
    assert resolve_db_path() == target


def test_resolve_db_path_explicit_wins(tmp_path, monkeypatch):
    target = str(tmp_path / "explicit.sqlite")
    monkeypatch.setenv("ZELOSMCP_SAVINGS_DB", str(tmp_path / "env.sqlite"))
    assert resolve_db_path(target) == target


def test_resolve_db_path_passes_memory_through():
    assert resolve_db_path(":memory:") == ":memory:"
