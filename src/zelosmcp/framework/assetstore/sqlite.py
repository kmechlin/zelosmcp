"""SQLite implementation of the zelosMCP asset store.

Generic ``(kind, backend, name, target) → body + meta`` storage for
rule sections, extension buttons, Cursor agents/skills, Cursor hooks,
and future asset kinds.  Writes are serialised through a single
:class:`asyncio.Lock`.

Path is configurable via the ``ZELOSMCP_ASSETS_DB`` env var; tests pass
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

from zelosmcp.framework.assetstore.row import AssetRow

logger = logging.getLogger("zelosmcp.assets")


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS assets (
        kind         TEXT NOT NULL,
        backend      TEXT NOT NULL,
        name         TEXT NOT NULL,
        target       TEXT NOT NULL DEFAULT '',
        body         TEXT NOT NULL DEFAULT '',
        meta         TEXT NOT NULL DEFAULT '{}',
        source       TEXT NOT NULL DEFAULT 'seed',
        seed_version INTEGER,
        updated_at   REAL NOT NULL,
        PRIMARY KEY (kind, backend, name, target)
    )
    """,
    "CREATE INDEX IF NOT EXISTS assets_kind    ON assets(kind)",
    "CREATE INDEX IF NOT EXISTS assets_backend ON assets(backend)",
    "CREATE INDEX IF NOT EXISTS assets_kind_backend ON assets(kind, backend)",
]


def resolve_db_path(explicit: str | None = None) -> str:
    """Pick the SQLite path: explicit > ``ZELOSMCP_ASSETS_DB`` > ``~/.zelosmcp/assets.sqlite``.

    Returns ``":memory:"`` unchanged for tests.  Falls back to
    ``":memory:"`` when the home directory can't be created
    (sandboxed / read-only environments) so the proxy still boots —
    assets just won't persist across restarts in that mode.
    """
    candidate = explicit or os.environ.get("ZELOSMCP_ASSETS_DB")
    if candidate:
        return candidate
    home = Path.home() / ".zelosmcp"
    try:
        home.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        logger.warning(
            "assets: cannot create %s (%s); using in-memory store", home, exc
        )
        return ":memory:"
    return str(home / "assets.sqlite")


class SQLiteAssetStore:
    """Async SQLite asset store.  One connection, one writer lock."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def open(self) -> None:
        """Open the connection and ensure the schema exists.  Idempotent."""
        if self._db is not None:
            return
        self._db = await aiosqlite.connect(self.path)
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
        """Close the connection.  Idempotent."""
        db, self._db = self._db, None
        if db is not None:
            try:
                await db.close()
            except Exception as exc:
                logger.warning("assets db close failed: %s", exc)

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLiteAssetStore.open() was never called")
        return self._db

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_asset(row: tuple) -> AssetRow:
        kind, backend, name, target, body, meta_json, source, seed_version, updated_at = row
        try:
            meta = json.loads(meta_json or "{}")
        except (ValueError, TypeError):
            meta = {}
        return AssetRow(
            kind=kind,
            backend=backend,
            name=name,
            target=target or "",
            body=body or "",
            meta=meta,
            source=source or "seed",
            seed_version=seed_version,
            updated_at=updated_at or time.time(),
        )

    # ── Reads ──────────────────────────────────────────────────────────

    async def get(
        self,
        kind: str,
        backend: str,
        name: str,
        target: str = "",
    ) -> AssetRow | None:
        """Return one row by its primary key, or ``None``."""
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                """
                SELECT kind, backend, name, target, body, meta,
                       source, seed_version, updated_at
                FROM assets
                WHERE kind = ? AND backend = ? AND name = ? AND target = ?
                """,
                (kind, backend, name, target),
            )
            row = await cur.fetchone()
            await cur.close()
        return self._row_to_asset(row) if row else None

    async def list(
        self,
        kind: str | None = None,
        backend: str | None = None,
        target: str | None = None,
    ) -> list[AssetRow]:
        """Return all rows matching the supplied filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if backend is not None:
            clauses.append("backend = ?")
            params.append(backend)
        if target is not None:
            clauses.append("target = ?")
            params.append(target)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                f"""
                SELECT kind, backend, name, target, body, meta,
                       source, seed_version, updated_at
                FROM assets {where}
                ORDER BY kind, backend, name, target
                """,
                params,
            )
            rows = await cur.fetchall()
            await cur.close()
        return [self._row_to_asset(r) for r in rows]

    # ── Writes ─────────────────────────────────────────────────────────

    async def upsert(
        self,
        row: AssetRow,
        *,
        only_if_seed_lt: int | None = None,
    ) -> bool:
        """Insert or update a row.

        When ``only_if_seed_lt`` is set, the write is conditional:
        - User-edited rows (``source='user'``) are never overwritten.
        - Seed rows are only overwritten when their ``seed_version`` is
          strictly less than ``only_if_seed_lt`` (so higher-version
          YAML updates win; same-version re-seeds are no-ops).

        Returns ``True`` if the row was written, ``False`` if skipped.
        """
        ts = time.time()
        meta_json = json.dumps(row.meta or {})

        async with self._lock:
            db = self._conn()

            if only_if_seed_lt is not None:
                cur = await db.execute(
                    """
                    SELECT source, seed_version
                    FROM assets
                    WHERE kind = ? AND backend = ? AND name = ? AND target = ?
                    """,
                    (row.kind, row.backend, row.name, row.target),
                )
                existing = await cur.fetchone()
                await cur.close()

                if existing is not None:
                    ex_source, ex_seed_version = existing
                    if ex_source == "user":
                        return False
                    if (
                        ex_source == "seed"
                        and ex_seed_version is not None
                        and ex_seed_version >= only_if_seed_lt
                    ):
                        return False

            await db.execute(
                """
                INSERT INTO assets
                    (kind, backend, name, target, body, meta,
                     source, seed_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, backend, name, target) DO UPDATE SET
                    body         = excluded.body,
                    meta         = excluded.meta,
                    source       = excluded.source,
                    seed_version = excluded.seed_version,
                    updated_at   = excluded.updated_at
                """,
                (
                    row.kind, row.backend, row.name, row.target,
                    row.body, meta_json,
                    row.source, row.seed_version, ts,
                ),
            )
            await db.commit()
        return True

    async def delete(
        self,
        kind: str,
        backend: str,
        name: str,
        target: str = "",
    ) -> bool:
        """Delete a row.  Returns ``True`` if a row was removed."""
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                """
                DELETE FROM assets
                WHERE kind = ? AND backend = ? AND name = ? AND target = ?
                """,
                (kind, backend, name, target),
            )
            removed = cur.rowcount > 0
            await cur.close()
            await db.commit()
        return removed

    # ── Summary ────────────────────────────────────────────────────────

    async def summary(self) -> dict[str, Any]:
        """Return a lightweight stats dict for debug/status endpoints."""
        async with self._lock:
            db = self._conn()
            cur = await db.execute("SELECT COUNT(*) FROM assets")
            (total,) = await cur.fetchone()  # type: ignore[misc]
            await cur.close()
            cur = await db.execute(
                """
                SELECT kind, COUNT(*) FROM assets
                GROUP BY kind ORDER BY kind
                """
            )
            by_kind = {k: n for k, n in await cur.fetchall()}
            await cur.close()
            cur = await db.execute(
                """
                SELECT source, COUNT(*) FROM assets
                GROUP BY source ORDER BY source
                """
            )
            by_source = {s: n for s, n in await cur.fetchall()}
            await cur.close()
        return {
            "total": total,
            "by_kind": by_kind,
            "by_source": by_source,
        }
