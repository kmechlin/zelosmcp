"""SQLite implementation of the savings / token-metrics store.

Moved from :mod:`zelosmcp.savings_db` to the new Bifrost-style
:mod:`zelosmcp.framework.savingsstore` namespace.  The original module
is kept as a compatibility re-export shim.

Path is configurable via the ``ZELOSMCP_SAVINGS_DB`` env var; tests pass
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

logger = logging.getLogger("zelosmcp.savings")


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
    (
        "CREATE INDEX IF NOT EXISTS call_events_backend_ts "
        "ON call_events(backend, ts)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS call_events_qualified "
        "ON call_events(qualified)"
    ),
    """
    CREATE TABLE IF NOT EXISTS proxy_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        ts REAL NOT NULL,
        method TEXT NOT NULL,
        backend TEXT,
        tool TEXT,
        qualified TEXT,
        compressed INTEGER,
        input_tokens INTEGER,
        output_tokens INTEGER,
        raw_output_tokens INTEGER,
        raw_output_bytes INTEGER,
        transform_type TEXT,
        latency_ms INTEGER,
        error INTEGER,
        error_message TEXT,
        meta TEXT,
        input_text TEXT,
        upstream_text TEXT,
        returned_text TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS proxy_events_ts ON proxy_events(ts)",
    (
        "CREATE INDEX IF NOT EXISTS proxy_events_backend_ts "
        "ON proxy_events(backend, ts)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS proxy_events_method_ts "
        "ON proxy_events(method, ts)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS proxy_events_qualified "
        "ON proxy_events(qualified)"
    ),
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
    """Pick the SQLite path.

    Order: explicit > ``$ZELOSMCP_SAVINGS_DB`` > ``<state-dir>/savings.sqlite``,
    where ``<state-dir>`` follows the suite container contract (see
    ``zelosmcp.framework.state_dir``).

    Returns ``":memory:"`` unchanged for tests; falls back to ``":memory:"``
    when the state directory can't be created (sandboxed / read-only) so the
    proxy still boots.
    """
    from zelosmcp.framework.state_dir import resolve_state_dir

    candidate = explicit or os.environ.get("ZELOSMCP_SAVINGS_DB")
    if candidate:
        return candidate
    state = resolve_state_dir()
    try:
        state.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        logger.warning(
            "savings: cannot create %s (%s); using in-memory store", state, exc
        )
        return ":memory:"
    return str(state / "savings.sqlite")


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

    async def insert_event(
        self,
        *,
        event_id: str,
        method: str,
        backend: str | None,
        tool: str | None,
        qualified: str | None,
        compressed: bool,
        input_tokens: int | None,
        output_tokens: int | None,
        raw_output_tokens: int | None,
        raw_output_bytes: int | None,
        transform_type: str | None,
        latency_ms: int | None,
        error: bool,
        error_message: str | None,
        meta: dict[str, Any] | None,
        input_text: str | None = None,
        upstream_text: str | None = None,
        returned_text: str | None = None,
    ) -> None:
        ts = time.time()
        async with self._lock:
            db = self._conn()
            await db.execute(
                """
                INSERT INTO proxy_events
                    (event_id, ts, method, backend, tool, qualified,
                     compressed, input_tokens, output_tokens,
                     raw_output_tokens, raw_output_bytes, transform_type,
                     latency_ms, error, error_message, meta,
                     input_text, upstream_text, returned_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?)
                """,
                (
                    event_id,
                    ts,
                    method,
                    backend,
                    tool,
                    qualified,
                    1 if compressed else 0,
                    input_tokens,
                    output_tokens,
                    raw_output_tokens,
                    raw_output_bytes,
                    transform_type,
                    latency_ms,
                    1 if error else 0,
                    error_message,
                    json.dumps(meta) if meta is not None else None,
                    input_text,
                    upstream_text,
                    returned_text,
                ),
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

    async def prune_before(self, cutoff_ts: float) -> int:
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                "DELETE FROM proxy_events WHERE ts < ?",
                (cutoff_ts,),
            )
            await db.commit()
            deleted = max(cur.rowcount or 0, 0)
            await cur.close()
        return deleted

    async def query_events(
        self,
        *,
        backend: str | None = None,
        method: str | None = None,
        tool: str | None = None,
        errors_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if backend:
            clauses.append("backend = ?")
            params.append(backend)
        if method:
            clauses.append("method = ?")
            params.append(method)
        if tool:
            clauses.append("(tool LIKE ? OR qualified LIKE ?)")
            like = f"%{tool}%"
            params.extend([like, like])
        if errors_only:
            clauses.append("error = 1")
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                f"SELECT COUNT(*) FROM proxy_events {where_clause}",
                tuple(params),
            )
            total_row = await cur.fetchone()
            await cur.close()

            cur = await db.execute(
                f"""
                SELECT event_id, ts, method, backend, tool, qualified,
                       compressed, input_tokens, output_tokens,
                       raw_output_tokens, raw_output_bytes, transform_type,
                       latency_ms, error, error_message, meta,
                       input_text, upstream_text, returned_text
                FROM proxy_events {where_clause}
                ORDER BY ts DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            )
            rows = await cur.fetchall()
            await cur.close()

        events: list[dict[str, Any]] = []
        for row in rows:
            (
                event_id,
                ts,
                event_method,
                row_backend,
                row_tool,
                qualified,
                compressed,
                input_tokens,
                output_tokens,
                raw_output_tokens,
                raw_output_bytes,
                transform_type,
                latency_ms,
                error,
                error_message,
                meta,
                input_text,
                upstream_text,
                returned_text,
            ) = row
            try:
                decoded_meta = json.loads(meta) if meta is not None else None
            except (TypeError, ValueError):
                decoded_meta = meta
            events.append({
                "event_id": event_id,
                "ts": ts,
                "method": event_method,
                "backend": row_backend,
                "tool": row_tool,
                "qualified": qualified,
                "compressed": bool(compressed),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "raw_output_tokens": raw_output_tokens,
                "raw_output_bytes": raw_output_bytes,
                "transform_type": transform_type,
                "latency_ms": latency_ms,
                "error": bool(error),
                "error_message": error_message,
                "meta": decoded_meta,
                "input_text": input_text,
                "upstream_text": upstream_text,
                "returned_text": returned_text,
            })
        return {
            "events": events,
            "total": (total_row[0] if total_row else 0),
        }

    async def summarize_events(
        self,
        *,
        backend: str | None = None,
        top_n: int = 20,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if backend:
            clauses.append("backend = ?")
            params.append(backend)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        tool_clauses = [*clauses, "qualified IS NOT NULL"]
        tool_where_clause = f"WHERE {' AND '.join(tool_clauses)}"

        async with self._lock:
            db = self._conn()

            cur = await db.execute(
                f"""
                SELECT
                    COUNT(*) AS events,
                    COALESCE(
                        SUM(CASE WHEN method = 'tools/call' THEN 1 ELSE 0 END),
                        0
                    ) AS calls,
                    COALESCE(SUM(error), 0) AS errors,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(raw_output_tokens), 0) AS raw_output_tokens,
                    COALESCE(SUM(raw_output_bytes), 0) AS raw_output_bytes,
                    COALESCE(SUM(compressed), 0) AS compressed_events,
                    AVG(latency_ms) AS avg_latency_ms,
                    MIN(ts) AS oldest_event_at,
                    MAX(ts) AS latest_event_at,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN raw_output_tokens IS NOT NULL
                                     AND output_tokens IS NOT NULL
                                     AND raw_output_tokens > output_tokens
                                THEN raw_output_tokens - output_tokens
                                ELSE 0
                            END
                        ),
                        0
                    ) AS transform_saved_tokens
                FROM proxy_events {where_clause}
                """,
                tuple(params),
            )
            totals_row = await cur.fetchone()
            await cur.close()

            cur = await db.execute(
                f"""
                SELECT backend,
                       COUNT(*) AS events,
                       COALESCE(
                           SUM(CASE WHEN method = 'tools/call' THEN 1 ELSE 0 END),
                           0
                       ) AS calls,
                       COALESCE(SUM(error), 0) AS errors,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(raw_output_tokens), 0) AS raw_output_tokens,
                       COALESCE(SUM(compressed), 0) AS compressed_events,
                       AVG(latency_ms) AS avg_latency_ms,
                       COALESCE(
                           SUM(
                               CASE
                                   WHEN raw_output_tokens IS NOT NULL
                                        AND output_tokens IS NOT NULL
                                        AND raw_output_tokens > output_tokens
                                   THEN raw_output_tokens - output_tokens
                                   ELSE 0
                               END
                           ),
                           0
                       ) AS transform_saved_tokens
                FROM proxy_events {where_clause}
                GROUP BY backend
                ORDER BY events DESC, backend ASC
                """,
                tuple(params),
            )
            backend_rows = await cur.fetchall()
            await cur.close()

            cur = await db.execute(
                f"""
                SELECT method,
                       COUNT(*) AS events,
                       COALESCE(SUM(error), 0) AS errors,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(raw_output_tokens), 0) AS raw_output_tokens,
                       COALESCE(SUM(compressed), 0) AS compressed_events,
                       AVG(latency_ms) AS avg_latency_ms,
                       COALESCE(
                           SUM(
                               CASE
                                   WHEN raw_output_tokens IS NOT NULL
                                        AND output_tokens IS NOT NULL
                                        AND raw_output_tokens > output_tokens
                                   THEN raw_output_tokens - output_tokens
                                   ELSE 0
                               END
                           ),
                           0
                       ) AS transform_saved_tokens
                FROM proxy_events {where_clause}
                GROUP BY method
                ORDER BY events DESC, method ASC
                """,
                tuple(params),
            )
            method_rows = await cur.fetchall()
            await cur.close()

            cur = await db.execute(
                f"""
                SELECT qualified,
                       backend,
                       tool,
                       COUNT(*) AS events,
                       COALESCE(SUM(error), 0) AS errors,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(raw_output_tokens), 0) AS raw_output_tokens,
                       AVG(latency_ms) AS avg_latency_ms,
                       COALESCE(
                           SUM(
                               CASE
                                   WHEN raw_output_tokens IS NOT NULL
                                        AND output_tokens IS NOT NULL
                                        AND raw_output_tokens > output_tokens
                                   THEN raw_output_tokens - output_tokens
                                   ELSE 0
                               END
                           ),
                           0
                       ) AS transform_saved_tokens,
                       COALESCE(
                           SUM(
                               COALESCE(input_tokens, 0)
                               + COALESCE(raw_output_tokens, output_tokens, 0)
                           ),
                           0
                       ) AS token_volume
                FROM proxy_events
                  {tool_where_clause}
                GROUP BY qualified, backend, tool
                ORDER BY token_volume DESC, events DESC, qualified ASC
                LIMIT ?
                """,
                (*params, top_n),
            )
            tool_rows = await cur.fetchall()
            await cur.close()

            cur = await db.execute(
                f"""
                SELECT transform_type,
                       COUNT(*) AS events,
                       COALESCE(SUM(raw_output_tokens), 0) AS raw_output_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(
                           SUM(
                               CASE
                                   WHEN raw_output_tokens IS NOT NULL
                                        AND output_tokens IS NOT NULL
                                        AND raw_output_tokens > output_tokens
                                   THEN raw_output_tokens - output_tokens
                                   ELSE 0
                               END
                           ),
                           0
                       ) AS transform_saved_tokens
                FROM proxy_events {where_clause}
                GROUP BY transform_type
                ORDER BY events DESC
                """,
                tuple(params),
            )
            transform_rows = await cur.fetchall()
            await cur.close()

        (
            total_events,
            total_calls,
            total_errors,
            total_input_tokens,
            total_output_tokens,
            total_raw_output_tokens,
            total_raw_output_bytes,
            total_compressed_events,
            avg_latency_ms,
            oldest_event_at,
            latest_event_at,
            transform_saved_tokens,
        ) = totals_row or (0, 0, 0, 0, 0, 0, 0, 0, None, None, None, 0)

        transform_saved_pct = (
            transform_saved_tokens / total_raw_output_tokens * 100.0
            if total_raw_output_tokens else 0.0
        )

        return {
            "generated_at": time.time(),
            "backend_filter": backend,
            "totals": {
                "events": total_events,
                "calls": total_calls,
                "errors": total_errors,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "raw_output_tokens": total_raw_output_tokens,
                "raw_output_bytes": total_raw_output_bytes,
                "compressed_events": total_compressed_events,
                "avg_latency_ms": round(avg_latency_ms or 0.0, 2),
                "oldest_event_at": oldest_event_at,
                "latest_event_at": latest_event_at,
                "transform_saved_tokens": transform_saved_tokens,
                "transform_saved_pct": round(transform_saved_pct, 2),
            },
            "per_backend": [
                {
                    "backend": row_backend,
                    "events": events,
                    "calls": calls,
                    "errors": errors,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "raw_output_tokens": raw_output_tokens,
                    "compressed_events": compressed_events,
                    "avg_latency_ms": round(avg_latency or 0.0, 2),
                    "transform_saved_tokens": saved_tokens,
                    "transform_saved_pct": round(
                        saved_tokens / raw_output_tokens * 100.0
                        if raw_output_tokens else 0.0,
                        2,
                    ),
                }
                for (
                    row_backend,
                    events,
                    calls,
                    errors,
                    input_tokens,
                    output_tokens,
                    raw_output_tokens,
                    compressed_events,
                    avg_latency,
                    saved_tokens,
                ) in backend_rows
            ],
            "per_method": [
                {
                    "method": row_method,
                    "events": events,
                    "errors": errors,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "raw_output_tokens": raw_output_tokens,
                    "compressed_events": compressed_events,
                    "avg_latency_ms": round(avg_latency or 0.0, 2),
                    "transform_saved_tokens": saved_tokens,
                    "transform_saved_pct": round(
                        saved_tokens / raw_output_tokens * 100.0
                        if raw_output_tokens else 0.0,
                        2,
                    ),
                }
                for (
                    row_method,
                    events,
                    errors,
                    input_tokens,
                    output_tokens,
                    raw_output_tokens,
                    compressed_events,
                    avg_latency,
                    saved_tokens,
                ) in method_rows
            ],
            "top_tools": [
                {
                    "qualified": qualified,
                    "backend": row_backend,
                    "tool": row_tool,
                    "events": events,
                    "errors": errors,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "raw_output_tokens": raw_output_tokens,
                    "avg_latency_ms": round(avg_latency or 0.0, 2),
                    "transform_saved_tokens": saved_tokens,
                    "token_volume": token_volume,
                }
                for (
                    qualified,
                    row_backend,
                    row_tool,
                    events,
                    errors,
                    input_tokens,
                    output_tokens,
                    raw_output_tokens,
                    avg_latency,
                    saved_tokens,
                    token_volume,
                ) in tool_rows
            ],
            "transform_types": [
                {
                    "transform_type": transform_type,
                    "events": events,
                    "raw_output_tokens": raw_output_tokens,
                    "output_tokens": output_tokens,
                    "transform_saved_tokens": saved_tokens,
                }
                for (
                    transform_type,
                    events,
                    raw_output_tokens,
                    output_tokens,
                    saved_tokens,
                ) in transform_rows
            ],
        }

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
            f"WHERE backend NOT IN ({placeholders})"
            if exclude_backends else ""
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
            f"WHERE backend NOT IN ({placeholders})"
            if exclude_backends else ""
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
            f"WHERE backend NOT IN ({placeholders})"
            if exclude_backends else ""
        )
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                f"""
                SELECT qualified, backend, tool,
                       COUNT(*) AS calls,
                       COALESCE(
                           SUM(input_tokens + output_tokens),
                           0
                       ) AS tokens,
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
