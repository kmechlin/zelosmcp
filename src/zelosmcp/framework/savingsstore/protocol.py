"""Protocol (interface) for the savings / token-metrics store.

Callers that only need the public read/write surface should depend on
this protocol rather than the concrete SQLite class so that alternative
implementations (Postgres, in-memory for CI, etc.) can be swapped in.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SavingsStoreProtocol(Protocol):
    """Async read/write surface for the token-savings store."""

    async def open(self) -> None: ...
    async def close(self) -> None: ...

    async def upsert_compression(
        self,
        *,
        backend: str,
        level: str | None,
        raw_tokens: int,
        compressed_tokens: int,
        raw_bytes: int,
        compressed_bytes: int,
    ) -> None: ...

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
    ) -> None: ...

    async def insert_pincher_meta(
        self,
        *,
        tool: str,
        tokens_used: int | None,
        tokens_saved: int | None,
        cost_avoided: float | None,
        raw_meta: dict[str, Any] | None,
    ) -> None: ...

    async def insert_pincher_stats_snapshot(self, payload: Any) -> None: ...

    async def fetch_compression(self) -> list[dict[str, Any]]: ...
    async def fetch_call_totals(
        self, *, exclude_backends: tuple[str, ...] = ()
    ) -> dict[str, Any]: ...
    async def fetch_per_backend(
        self, *, exclude_backends: tuple[str, ...] = ()
    ) -> list[dict[str, Any]]: ...
    async def fetch_top_tools(
        self, *, limit: int = 10, exclude_backends: tuple[str, ...] = ()
    ) -> list[dict[str, Any]]: ...
    async def fetch_pincher_totals(self) -> dict[str, Any]: ...
