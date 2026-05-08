#!/usr/bin/env python3
"""Compare zelosMCP's aggregator (`/mcp`) against every raw backend
(`/<name>/mcp`) so you can quantify the combined effect of compression
*and* aggregation across the whole stack.

What it does (against a running zelosMCP instance):

1. Discovers all running backends from ``GET /api/status`` (stdlib only —
   no extra deps to bootstrap the discovery step).
2. For each backend, snapshots its raw endpoint at ``/<name>/mcp``:
   ``tools/list``, ``prompts/list``, ``resources/list``.
3. Snapshots the aggregator at ``/mcp`` once, then partitions its tool
   and prompt lists by the ``<backend>__`` name prefix so each backend
   gets compared against its own slice of the aggregator surface.
4. Optionally calls one or more tools on both sides
   (``--call backend.tool[:json-args]``) — the script auto-detects whether
   that backend is compressed (looks for ``<backend>__invoke_tool``) and
   routes through the wrapper transparently.
5. Writes the full payloads to ``--out-dir`` so you can ``diff`` them.
6. Prints:
   - a per-backend table (tools/prompts counts, bytes, tokens, savings)
   - resources totals (aggregator surfaces every backend's resources;
     they're not name-prefixed so we report by URI overlap)
   - per-call parity (with volatile-key redaction so timing noise doesn't
     trip the parity check)
   - a grand totals row.

Token counts use ``tiktoken`` (cl100k_base) when available; otherwise
``ceil(chars/4)`` heuristic. Install the tokenizer extra to opt in:
``pip install -e .[tokenizer]``.

Usage::

    .venv/bin/python scripts/compare_aggregator_mcp.py
    .venv/bin/python scripts/compare_aggregator_mcp.py \
        --backends pincher,filesystem \
        --call pincher.architecture:'{"project":"zelosmcp"}' \
        --call filesystem.list_allowed_directories \
        --out-dir tmp/mcp_compare
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

try:
    import tiktoken  # type: ignore[import-not-found]

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))

    TOKENIZER = "tiktoken/cl100k_base"
except Exception:  # pragma: no cover — graceful fallback
    _ENC = None

    def count_tokens(text: str) -> int:
        return math.ceil(len(text) / 4)

    TOKENIZER = "char/4 heuristic"


# Per-call values that backends inject but that change every invocation.
# Stripped before parity comparison so a "match" verdict means semantic
# equality, not "byte-identical including measured wall-clock latency".
DEFAULT_VOLATILE_KEYS = (
    "latency_ms",
    "staleness_seconds",
    "staleness_human",
)


# ── Data model ──────────────────────────────────────────────────────────


@dataclass
class Listing:
    """One MCP listing operation (tools/list, prompts/list, etc.)."""

    name: str
    items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def json_text(self) -> str:
        return json.dumps(self.items, indent=2, sort_keys=True, default=str)

    @property
    def n_bytes(self) -> int:
        return len(self.json_text.encode("utf-8"))

    @property
    def n_tokens(self) -> int:
        return count_tokens(self.json_text)


@dataclass
class CallSample:
    tool: str
    args: dict[str, Any]
    payload: dict[str, Any]

    @property
    def json_text(self) -> str:
        return json.dumps(self.payload, indent=2, sort_keys=True, default=str)

    @property
    def n_bytes(self) -> int:
        return len(self.json_text.encode("utf-8"))

    @property
    def n_tokens(self) -> int:
        return count_tokens(self.json_text)


@dataclass
class EndpointSnapshot:
    label: str
    url: str
    tools: Listing
    prompts: Listing
    resources: Listing
    calls: dict[str, CallSample] = field(default_factory=dict)


# ── Helpers: model dump, JSON-aware key strip, prefix slicing ───────────


def _model_dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=True, exclude_none=True)
    if hasattr(obj, "dict"):
        return obj.dict(by_alias=True, exclude_none=True)
    if isinstance(obj, list):
        return [_model_dump(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _model_dump(v) for k, v in obj.items()}
    return obj


def _strip_keys(obj: Any, keys: set[str]) -> Any:
    """Drop ``keys`` from any nested dict — including dicts found inside
    JSON-string values (MCP TextContent.text)."""
    if isinstance(obj, dict):
        return {k: _strip_keys(v, keys) for k, v in obj.items() if k not in keys}
    if isinstance(obj, list):
        return [_strip_keys(v, keys) for v in obj]
    if isinstance(obj, str) and obj.startswith(("{", "[")):
        try:
            parsed = json.loads(obj)
        except (json.JSONDecodeError, ValueError):
            return obj
        return json.dumps(_strip_keys(parsed, keys), sort_keys=True)
    return obj


def slice_by_prefix(snap: EndpointSnapshot, backend: str) -> EndpointSnapshot:
    """Return a new snapshot containing only the items belonging to ``backend``.

    Tools and prompts use the ``<backend>__`` name prefix. Resources can't
    be sliced from the aggregator alone because their URIs don't carry an
    origin marker — caller handles resources separately by URI overlap.
    """
    prefix = f"{backend}__"
    tools = [
        it for it in snap.tools.items
        if isinstance(it.get("name"), str) and it["name"].startswith(prefix)
    ]
    prompts = [
        it for it in snap.prompts.items
        if isinstance(it.get("name"), str) and it["name"].startswith(prefix)
    ]
    return EndpointSnapshot(
        label=f"{snap.label}[{backend}]",
        url=snap.url,
        tools=Listing("tools", tools),
        prompts=Listing("prompts", prompts),
        resources=Listing("resources", []),  # filled in later from URI overlap
    )


# ── Backend discovery ───────────────────────────────────────────────────


def discover_backends(base_url: str) -> list[dict[str, Any]]:
    """Return ``[{name, builtin, running, transport}, ...]`` from
    ``GET /api/status``. Stdlib only so this works before the rest of the
    script's imports succeed."""
    url = f"{base_url.rstrip('/')}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Could not reach {url}: {exc}\n"
            f"Is zelosMCP running? Try `make up`."
        ) from exc
    return [s for s in data.get("servers", []) if s.get("running")]


# ── MCP client helpers ──────────────────────────────────────────────────


async def _safe_list(coro_factory, attr: str) -> list[dict[str, Any]]:
    try:
        result = await coro_factory()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "-32601" in msg or "Method not found" in msg:
            return []
        raise
    items = getattr(result, attr, None) or []
    return [_model_dump(it) for it in items]


async def snapshot(
    label: str,
    url: str,
    *,
    calls: list[tuple[str, dict[str, Any]]],
) -> EndpointSnapshot:
    """Connect to ``url``, list everything, run ``calls`` verbatim, and
    return a snapshot. The caller is responsible for translating call
    names between aggregator and raw conventions before passing them in."""
    print(f"  → {label}: {url}", file=sys.stderr)
    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await _safe_list(session.list_tools, "tools")
            prompts = await _safe_list(session.list_prompts, "prompts")
            resources = await _safe_list(session.list_resources, "resources")

            snap = EndpointSnapshot(
                label=label,
                url=url,
                tools=Listing("tools", tools),
                prompts=Listing("prompts", prompts),
                resources=Listing("resources", resources),
            )

            for key, (tool_name, args) in calls:
                try:
                    result = await session.call_tool(tool_name, args)
                    payload = _model_dump(result)
                except Exception as exc:  # noqa: BLE001
                    payload = {"_error": str(exc)}
                snap.calls[key] = CallSample(
                    tool=tool_name, args=args, payload=payload
                )

            return snap


# ── Reporting ───────────────────────────────────────────────────────────


def _fmt_savings(agg: int, raw: int) -> str:
    if raw == 0:
        if agg == 0:
            return "  --"
        return "  +inf"  # aggregator added something the raw didn't have
    pct = (1 - agg / raw) * 100
    sign = "+" if pct >= 0 else "-"
    return f"{sign}{abs(pct):5.1f}%"


def _row(
    backend: str,
    agg: EndpointSnapshot,
    raw: EndpointSnapshot,
    *,
    metric: str,
) -> str:
    """One line of the per-backend table for ``metric`` ∈ {tools, prompts}."""
    agg_listing = getattr(agg, metric)
    raw_listing = getattr(raw, metric)
    return (
        f"  {backend:<14} "
        f"{len(agg_listing.items):>5} {len(raw_listing.items):>5}   "
        f"{agg_listing.n_bytes:>8} {raw_listing.n_bytes:>8}   "
        f"{agg_listing.n_tokens:>7} {raw_listing.n_tokens:>7}   "
        f"{_fmt_savings(agg_listing.n_bytes, raw_listing.n_bytes):>8}"
    )


def _header(metric: str) -> tuple[str, str]:
    title = (
        f"  {'backend':<14} "
        f"{'a#':>5} {'r#':>5}   "
        f"{'a-bytes':>8} {'r-bytes':>8}   "
        f"{'a-toks':>7} {'r-toks':>7}   "
        f"{'savings':>8}"
    )
    rule = f"  {'-' * 14} {'-' * 5} {'-' * 5}   {'-' * 8} {'-' * 8}   {'-' * 7} {'-' * 7}   {'-' * 8}"
    return title, rule


def _resource_overlap(
    agg_resources: list[dict[str, Any]],
    raw_uris: set[str],
) -> list[dict[str, Any]]:
    """Filter aggregator resources down to those whose URI matches the raw
    backend's set. Aggregator passes URIs through unchanged, so this is a
    sound origin attribution."""
    return [r for r in agg_resources if r.get("uri") in raw_uris]


def print_report(
    agg: EndpointSnapshot,
    raws: dict[str, EndpointSnapshot],
) -> None:
    print()
    print("=" * 96)
    print(f"zelosMCP aggregator vs raw backends — tokenizer={TOKENIZER}")
    print("=" * 96)
    print(f"  aggregator  : {agg.url}")
    for name, raw in raws.items():
        print(f"  raw[{name:<10}]: {raw.url}")
    print()

    # ---- TOOLS table ------------------------------------------------------
    title, rule = _header("tools")
    print("  TOOLS  (a=aggregator slice, r=raw backend; savings on bytes)")
    print(title)
    print(rule)

    agg_total_tools = Listing("tools", [])
    raw_total_tools = Listing("tools", [])

    for name, raw in raws.items():
        agg_slice = slice_by_prefix(agg, name)
        print(_row(name, agg_slice, raw, metric="tools"))
        agg_total_tools.items.extend(agg_slice.tools.items)
        raw_total_tools.items.extend(raw.tools.items)

    print(rule)
    total_snap_agg = EndpointSnapshot(
        "TOTAL", agg.url, agg_total_tools,
        Listing("prompts", []), Listing("resources", []),
    )
    total_snap_raw = EndpointSnapshot(
        "TOTAL", "(union)", raw_total_tools,
        Listing("prompts", []), Listing("resources", []),
    )
    print(_row("TOTAL", total_snap_agg, total_snap_raw, metric="tools"))

    # ---- PROMPTS table ----------------------------------------------------
    have_prompts = any(raw.prompts.items for raw in raws.values()) or agg.prompts.items
    if have_prompts:
        print()
        title, rule = _header("prompts")
        print("  PROMPTS")
        print(title)
        print(rule)
        agg_tot_p = Listing("prompts", [])
        raw_tot_p = Listing("prompts", [])
        for name, raw in raws.items():
            agg_slice = slice_by_prefix(agg, name)
            print(_row(name, agg_slice, raw, metric="prompts"))
            agg_tot_p.items.extend(agg_slice.prompts.items)
            raw_tot_p.items.extend(raw.prompts.items)
        print(rule)
        ta = EndpointSnapshot("T", "", Listing("tools", []), agg_tot_p, Listing("resources", []))
        tr = EndpointSnapshot("T", "", Listing("tools", []), raw_tot_p, Listing("resources", []))
        print(_row("TOTAL", ta, tr, metric="prompts"))

    # ---- RESOURCES table (URI overlap, since URIs aren't prefixed) -------
    have_resources = bool(agg.resources.items) or any(
        raw.resources.items for raw in raws.values()
    )
    if have_resources:
        print()
        title, rule = _header("resources")
        print("  RESOURCES  (aggregator slice = URI overlap with raw backend)")
        print(title)
        print(rule)
        agg_tot_r = Listing("resources", [])
        raw_tot_r = Listing("resources", [])
        for name, raw in raws.items():
            raw_uris = {r.get("uri") for r in raw.resources.items if r.get("uri")}
            agg_slice_items = _resource_overlap(agg.resources.items, raw_uris)
            agg_slice = EndpointSnapshot(
                f"agg[{name}]", agg.url,
                Listing("tools", []), Listing("prompts", []),
                Listing("resources", agg_slice_items),
            )
            print(_row(name, agg_slice, raw, metric="resources"))
            agg_tot_r.items.extend(agg_slice_items)
            raw_tot_r.items.extend(raw.resources.items)
        print(rule)
        # Show how many aggregator resources couldn't be attributed to any
        # known raw backend (e.g. resources mounted by a backend we didn't
        # query, or constructed from templates).
        attributed = {r.get("uri") for r in agg_tot_r.items}
        orphans = [
            r for r in agg.resources.items
            if r.get("uri") and r.get("uri") not in attributed
        ]
        if orphans:
            orphan_listing = Listing("resources", orphans)
            print(
                f"  (note: {len(orphans)} aggregator resources, "
                f"{orphan_listing.n_bytes} bytes, "
                f"{orphan_listing.n_tokens} tokens — not attributed to any "
                f"queried raw backend)"
            )
        ta = EndpointSnapshot("T", "", Listing("tools", []), Listing("prompts", []), agg_tot_r)
        tr = EndpointSnapshot("T", "", Listing("tools", []), Listing("prompts", []), raw_tot_r)
        print(_row("TOTAL", ta, tr, metric="resources"))

    # ---- CALL parity ------------------------------------------------------
    if agg.calls:
        print()
        ignored = ", ".join(sorted(DEFAULT_VOLATILE_KEYS))
        print(f"  TOOLS/CALL samples (parity ignores volatile keys: {ignored}):")
        print(
            f"  {'key':<40} {'a-bytes':>8} {'r-bytes':>8}   "
            f"{'a-toks':>7} {'r-toks':>7}   {'parity':>7}"
        )
        print(
            f"  {'-' * 40} {'-' * 8} {'-' * 8}   "
            f"{'-' * 7} {'-' * 7}   {'-' * 7}"
        )
        volatile = set(DEFAULT_VOLATILE_KEYS)
        for key, agg_call in agg.calls.items():
            backend = key.split(".", 1)[0]
            raw_snap = raws.get(backend)
            raw_call = raw_snap.calls.get(key) if raw_snap else None
            if raw_call is None:
                continue
            agg_clean = _strip_keys(agg_call.payload, volatile)
            raw_clean = _strip_keys(raw_call.payload, volatile)
            parity = "match" if agg_clean == raw_clean else "DIFFER"
            print(
                f"  {key:<40} {agg_call.n_bytes:>8} {raw_call.n_bytes:>8}   "
                f"{agg_call.n_tokens:>7} {raw_call.n_tokens:>7}   {parity:>7}"
            )

    print()
    print("Notes:")
    print(
        "  - The aggregator's tools-list is what an MCP client (Cursor, "
        "Copilot) actually sees on its single connection."
    )
    print(
        "  - Backends configured with `compress.level` collapse N tools into "
        "2 generic wrappers (get_tool_schema, invoke_tool); uncompressed "
        "backends just gain the `<name>__` prefix overhead."
    )
    print(
        "  - tools/call payloads should be byte-identical (parity=match) — "
        "compression touches the catalog only, not call results."
    )
    print()


# ── Persistence ─────────────────────────────────────────────────────────


def dump_listing(listing: Listing, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(listing.json_text + "\n")


def dump_snapshot(snap: EndpointSnapshot, out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    dump_listing(snap.tools, out / "tools.json")
    dump_listing(snap.prompts, out / "prompts.json")
    dump_listing(snap.resources, out / "resources.json")
    if snap.calls:
        calls_dir = out / "calls"
        calls_dir.mkdir(parents=True, exist_ok=True)
        for key, sample in snap.calls.items():
            safe = key.replace("/", "_").replace(":", "_")
            (calls_dir / f"{safe}.json").write_text(sample.json_text + "\n")

    summary = {
        "label": snap.label,
        "url": snap.url,
        "tools": {
            "count": len(snap.tools.items),
            "bytes": snap.tools.n_bytes,
            "tokens": snap.tools.n_tokens,
        },
        "prompts": {
            "count": len(snap.prompts.items),
            "bytes": snap.prompts.n_bytes,
            "tokens": snap.prompts.n_tokens,
        },
        "resources": {
            "count": len(snap.resources.items),
            "bytes": snap.resources.n_bytes,
            "tokens": snap.resources.n_tokens,
        },
        "calls": {
            key: {"bytes": s.n_bytes, "tokens": s.n_tokens}
            for key, s in snap.calls.items()
        },
        "tokenizer": TOKENIZER,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    return summary


# ── CLI / orchestration ─────────────────────────────────────────────────


def parse_call(spec: str) -> tuple[str, str, dict[str, Any]]:
    """Parse ``--call`` spec.

    Forms accepted:
      - ``backend.tool``                    -> (backend, tool, {})
      - ``backend.tool:{"k":"v"}``          -> (backend, tool, {"k":"v"})
    """
    head, _, raw_args = spec.partition(":")
    if "." not in head:
        raise SystemExit(
            f"--call {spec!r}: expected `backend.tool[:json-args]` "
            f"(e.g. pincher.architecture)"
        )
    backend, tool = head.split(".", 1)
    raw_args = raw_args.strip()
    if not raw_args:
        return backend, tool, {}
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"--call {spec!r}: argument JSON failed to parse: {exc}"
        ) from exc
    if not isinstance(args, dict):
        raise SystemExit(
            f"--call {spec!r}: arguments must be a JSON object, got "
            f"{type(args).__name__}"
        )
    return backend, tool, args


def split_calls_by_endpoint(
    calls: list[tuple[str, str, dict[str, Any]]],
    agg_tool_names: set[str],
) -> tuple[
    list[tuple[str, tuple[str, dict[str, Any]]]],  # aggregator
    dict[str, list[tuple[str, tuple[str, dict[str, Any]]]]],  # per-backend raw
]:
    """Translate a list of (backend, tool, args) into:

    - aggregator call list keyed by ``"backend.tool"`` — routes through
      ``<backend>__invoke_tool`` if compressed (i.e. that wrapper exists in
      the aggregator's tool list), otherwise direct ``<backend>__<tool>``.
    - per-backend raw call list keyed the same way — raw uses the bare
      ``<tool>`` name.
    """
    agg_calls: list[tuple[str, tuple[str, dict[str, Any]]]] = []
    raw_calls: dict[str, list[tuple[str, tuple[str, dict[str, Any]]]]] = {}
    for backend, tool, args in calls:
        key = f"{backend}.{tool}"
        wrapper = f"{backend}__invoke_tool"
        if wrapper in agg_tool_names:
            agg_calls.append(
                (key, (wrapper, {"tool_name": tool, "tool_input": args}))
            )
        else:
            agg_calls.append((key, (f"{backend}__{tool}", args)))
        raw_calls.setdefault(backend, []).append((key, (tool, args)))
    return agg_calls, raw_calls


async def run(args: argparse.Namespace) -> int:
    base = args.base_url.rstrip("/")
    agg_url = f"{base}/mcp"

    print(f"discovering backends from {base}/api/status …", file=sys.stderr)
    servers = discover_backends(base)
    backend_names = [s["name"] for s in servers]
    if args.backends:
        wanted = {b.strip() for b in args.backends.split(",") if b.strip()}
        unknown = wanted - set(backend_names)
        if unknown:
            raise SystemExit(
                f"Unknown backend(s): {sorted(unknown)}. "
                f"Running: {backend_names}"
            )
        backend_names = [n for n in backend_names if n in wanted]
    if not backend_names:
        raise SystemExit("No running backends to compare.")
    print(f"  → backends: {', '.join(backend_names)}", file=sys.stderr)

    parsed_calls = [parse_call(c) for c in args.call]
    bad = [(b, t) for (b, t, _) in parsed_calls if b not in backend_names]
    if bad:
        raise SystemExit(
            f"--call references backends not in the comparison set: "
            f"{bad}. Running: {backend_names}"
        )

    # Aggregator first so we know which backends are compressed (so we can
    # route raw vs wrapper for sample calls).
    print("snapshotting aggregator …", file=sys.stderr)
    agg = await snapshot("aggregator", agg_url, calls=[])
    agg_tool_names = {
        it.get("name") for it in agg.tools.items if it.get("name")
    }
    agg_calls, raw_calls_by_backend = split_calls_by_endpoint(
        parsed_calls, agg_tool_names
    )

    if agg_calls:
        # Re-snapshot the aggregator now that we have call list (cheap —
        # call_tool is the expensive bit and that's the same either way).
        print("re-snapshotting aggregator with sample calls …", file=sys.stderr)
        agg = await snapshot("aggregator", agg_url, calls=agg_calls)

    print("snapshotting raw backends in parallel …", file=sys.stderr)
    raw_tasks = [
        snapshot(
            name,
            f"{base}/{name}/mcp",
            calls=raw_calls_by_backend.get(name, []),
        )
        for name in backend_names
    ]
    raw_results = await asyncio.gather(*raw_tasks)
    raws = {name: snap for name, snap in zip(backend_names, raw_results)}

    out = Path(args.out_dir)
    summary = {
        "tokenizer": TOKENIZER,
        "aggregator": dump_snapshot(agg, out / "aggregator"),
        "raw": {
            name: dump_snapshot(snap, out / "raw" / name)
            for name, snap in raws.items()
        },
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(f"wrote payloads to {out}/", file=sys.stderr)

    print_report(agg, raws)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="zelosMCP base URL (default %(default)s)",
    )
    parser.add_argument(
        "--out-dir",
        default="./mcp_compare",
        help="Directory to dump JSON payloads (default %(default)s)",
    )
    parser.add_argument(
        "--backends",
        default="",
        help=(
            "Comma-separated subset of backends to compare. "
            "Default: every running backend in /api/status."
        ),
    )
    parser.add_argument(
        "--call",
        action="append",
        default=[],
        metavar="BACKEND.TOOL[:JSON]",
        help=(
            "Run a sample tools/call on both endpoints and compare. "
            'Examples: "pincher.architecture", '
            '"pincher.search:{\\"query\\":\\"foo\\",\\"project\\":\\"zelosmcp\\"}", '
            '"filesystem.list_allowed_directories". Repeatable.'
        ),
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
