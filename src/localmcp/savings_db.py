"""Persistent token-savings store backed by SQLite.

The dashboard's three measurement sources (compression snapshots, per-call
events, pincher `_meta` and `pincher__stats` snapshots) all land here.
Reads and writes are serialized through a single asyncio lock so we never
fight aiosqlite's own per-connection locking, and so aggregation queries
see a consistent view even while the call hot-path is writing.

Path is configurable via the ``LOCALMCP_SAVINGS_DB`` env var; tests pass
``":memory:"`` to keep the schema in-process and forget it on shutdown.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger("localmcp.savings")


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS compression_snapshot (
        backend TEXT PRIMARY KEY,
        level TEXT,
        raw_tokens INTEGER,
        compressed_tokens INTEGER,
        raw_bytes INTEGER,
        compressed_bytes INTEGER,
        captured_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS call_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        backend TEXT,
        tool TEXT,
        qualified TEXT,
        compressed INTEGER,
        input_tokens INTEGER,
        output_tokens INTEGER,
        latency_ms INTEGER,
        error INTEGER,
        ts REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS call_events_backend_ts ON call_events(backend, ts)",
    "CREATE INDEX IF NOT EXISTS call_events_qualified ON call_events(qualified)",
    """
    CREATE TABLE IF NOT EXISTS pincher_meta (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tool TEXT,
        tokens_used INTEGER,
        tokens_saved INTEGER,
        cost_avoided REAL,
        raw_meta TEXT,
        ts REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS pincher_meta_ts ON pincher_meta(ts)",
    """
    CREATE TABLE IF NOT EXISTS pincher_stats_snapshot (
        captured_at REAL PRIMARY KEY,
        payload TEXT
    )
    """,
]


def resolve_db_path(explicit: str | None = None) -> str:
    """Pick the SQLite path: explicit > env var > ``~/.localmcp/savings.sqlite``.

    Returns the literal string ``":memory:"`` unchanged for tests. Falls
    back to ``":memory:"`` when the home directory can't be created
    (sandboxed environments, read-only filesystems) so the proxy still
    boots — counters just don't survive restarts in that mode.
    """
    candidate = explicit or os.environ.get("LOCALMCP_SAVINGS_DB")
    if candidate:
        return candidate
    home = Path.home() / ".localmcp"
    try:
        home.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        logger.warning(
            "savings: cannot create %s (%s); using in-memory store", home, exc
        )
        return ":memory:"
    return str(home / "savings.sqlite")


class SavingsStore:
    """Async SQLite store. One connection, one writer lock."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._db is not None:
            return
        self._db = await aiosqlite.connect(self.path)
        # WAL gives readers a non-blocking view while the call hot-path
        # writes; harmless for ":memory:" (ignored).
        if self.path != ":memory:":
            try:
                await self._db.execute("PRAGMA journal_mode=WAL")
            except Exception:
                pass
        await self._db.execute("PRAGMA foreign_keys=ON")
        for stmt in _SCHEMA:
            await self._db.execute(stmt)
        await self._db.commit()

    async def close(self) -> None:
        db, self._db = self._db, None
        if db is not None:
            try:
                await db.close()
            except Exception as exc:
                logger.warning("savings db close failed: %s", exc)

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SavingsStore.open() was never called")
        return self._db

    # ── Writers ─────────────────────────────────────────────────────────

    async def upsert_compression(
        self,
        *,
        backend: str,
        level: str | None,
        raw_tokens: int,
        compressed_tokens: int,
        raw_bytes: int,
        compressed_bytes: int,
    ) -> None:
        ts = time.time()
        async with self._lock:
            db = self._conn()
            await db.execute(
                """
                INSERT INTO compression_snapshot
                    (backend, level, raw_tokens, compressed_tokens,
                     raw_bytes, compressed_bytes, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(backend) DO UPDATE SET
                    level = excluded.level,
                    raw_tokens = excluded.raw_tokens,
                    compressed_tokens = excluded.compressed_tokens,
                    raw_bytes = excluded.raw_bytes,
                    compressed_bytes = excluded.compressed_bytes,
                    captured_at = excluded.captured_at
                """,
                (backend, level, raw_tokens, compressed_tokens,
                 raw_bytes, compressed_bytes, ts),
            )
            await db.commit()

    async def insert_call(
        self,
        *,
        backend: str,
        tool: str,
        qualified: str,
        compressed: bool,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        error: bool,
    ) -> None:
        ts = time.time()
        async with self._lock:
            db = self._conn()
            await db.execute(
                """
                INSERT INTO call_events
                    (backend, tool, qualified, compressed,
                     input_tokens, output_tokens, latency_ms, error, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (backend, tool, qualified, 1 if compressed else 0,
                 input_tokens, output_tokens, latency_ms,
                 1 if error else 0, ts),
            )
            await db.commit()

    async def insert_pincher_meta(
        self,
        *,
        tool: str,
        tokens_used: int | None,
        tokens_saved: int | None,
        cost_avoided: float | None,
        raw_meta: dict[str, Any] | None,
    ) -> None:
        ts = time.time()
        async with self._lock:
            db = self._conn()
            await db.execute(
                """
                INSERT INTO pincher_meta
                    (tool, tokens_used, tokens_saved,
                     cost_avoided, raw_meta, ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tool, tokens_used, tokens_saved, cost_avoided,
                 json.dumps(raw_meta) if raw_meta is not None else None, ts),
            )
            await db.commit()

    async def insert_pincher_stats_snapshot(self, payload: Any) -> None:
        ts = time.time()
        async with self._lock:
            db = self._conn()
            await db.execute(
                """
                INSERT OR REPLACE INTO pincher_stats_snapshot
                    (captured_at, payload)
                VALUES (?, ?)
                """,
                (ts, json.dumps(payload, default=str)),
            )
            await db.commit()

    # ── Readers ─────────────────────────────────────────────────────────

    async def fetch_compression(self) -> list[dict[str, Any]]:
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                """
                SELECT backend, level, raw_tokens, compressed_tokens,
                       raw_bytes, compressed_bytes, captured_at
                FROM compression_snapshot
                ORDER BY backend
                """
            )
            rows = await cur.fetchall()
            await cur.close()
        out: list[dict[str, Any]] = []
        for backend, level, raw_t, comp_t, raw_b, comp_b, ts in rows:
            saved_t = max(0, (raw_t or 0) - (comp_t or 0))
            saved_pct = (saved_t / raw_t * 100.0) if raw_t else 0.0
            out.append({
                "backend": backend,
                "level": level,
                "raw_tokens": raw_t or 0,
                "compressed_tokens": comp_t or 0,
                "raw_bytes": raw_b or 0,
                "compressed_bytes": comp_b or 0,
                "saved_tokens": saved_t,
                "saved_pct": round(saved_pct, 2),
                "captured_at": ts,
            })
        return out

    async def fetch_call_totals(
        self, *, exclude_backends: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        placeholders = ",".join("?" * len(exclude_backends)) or "''"
        excl_clause = (
            f"WHERE backend NOT IN ({placeholders})" if exclude_backends else ""
        )
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                f"""
                SELECT
                    COUNT(*) AS calls,
                    COALESCE(SUM(input_tokens), 0),
                    COALESCE(SUM(output_tokens), 0),
                    COALESCE(SUM(compressed), 0),
                    COALESCE(SUM(error), 0)
                FROM call_events {excl_clause}
                """,
                exclude_backends,
            )
            row = await cur.fetchone()
            await cur.close()
        calls, in_t, out_t, compressed, errors = row or (0, 0, 0, 0, 0)
        compressed_pct = (compressed / calls * 100.0) if calls else 0.0
        return {
            "calls": calls,
            "input_tokens": in_t,
            "output_tokens": out_t,
            "compressed_calls": compressed,
            "compressed_pct": round(compressed_pct, 2),
            "errors": errors,
        }

    async def fetch_per_backend(
        self, *, exclude_backends: tuple[str, ...] = ()
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("?" * len(exclude_backends)) or "''"
        excl_clause = (
            f"WHERE backend NOT IN ({placeholders})" if exclude_backends else ""
        )
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                f"""
                SELECT backend,
                       COUNT(*) AS calls,
                       COALESCE(SUM(compressed), 0),
                       COALESCE(SUM(input_tokens), 0),
                       COALESCE(SUM(output_tokens), 0),
                       AVG(latency_ms),
                       COALESCE(SUM(error), 0)
                FROM call_events {excl_clause}
                GROUP BY backend
                ORDER BY calls DESC
                """,
                exclude_backends,
            )
            rows = await cur.fetchall()
            await cur.close()
        out: list[dict[str, Any]] = []
        for backend, calls, compressed, in_t, out_t, avg_lat, errors in rows:
            out.append({
                "backend": backend,
                "calls": calls,
                "compressed_calls": compressed,
                "compressed_pct": round((compressed / calls * 100.0)
                                        if calls else 0.0, 2),
                "input_tokens": in_t,
                "output_tokens": out_t,
                "avg_latency_ms": round(avg_lat or 0.0, 2),
                "errors": errors,
            })
        return out

    async def fetch_top_tools(
        self,
        *,
        limit: int = 10,
        exclude_backends: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("?" * len(exclude_backends)) or "''"
        excl_clause = (
            f"WHERE backend NOT IN ({placeholders})" if exclude_backends else ""
        )
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                f"""
                SELECT qualified, backend, tool,
                       COUNT(*) AS calls,
                       COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                       AVG(latency_ms)
                FROM call_events {excl_clause}
                GROUP BY qualified
                ORDER BY tokens DESC
                LIMIT ?
                """,
                (*exclude_backends, limit),
            )
            rows = await cur.fetchall()
            await cur.close()
        return [
            {
                "qualified": q,
                "backend": b,
                "tool": t,
                "calls": calls,
                "tokens": tokens,
                "avg_latency_ms": round(avg_lat or 0.0, 2),
            }
            for q, b, t, calls, tokens, avg_lat in rows
        ]

    async def fetch_pincher_totals(self) -> dict[str, Any]:
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                """
                SELECT COUNT(*),
                       COALESCE(SUM(tokens_used), 0),
                       COALESCE(SUM(tokens_saved), 0),
                       COALESCE(SUM(cost_avoided), 0.0)
                FROM pincher_meta
                """
            )
            row = await cur.fetchone()
            await cur.close()
            cur = await db.execute(
                """
                SELECT captured_at, payload
                FROM pincher_stats_snapshot
                ORDER BY captured_at DESC
                LIMIT 1
                """
            )
            stats_row = await cur.fetchone()
            await cur.close()
        n, used, saved, cost = row or (0, 0, 0, 0.0)
        latest_stats: Any = None
        if stats_row:
            try:
                latest_stats = json.loads(stats_row[1])
            except (TypeError, ValueError):
                latest_stats = stats_row[1]
        return {
            "calls_with_meta": n,
            "tokens_used_total": used,
            "tokens_saved_total": saved,
            "cost_avoided_usd_total": round(cost, 6),
            "latest_stats": latest_stats,
            "latest_stats_at": stats_row[0] if stats_row else None,
        }
