"""Protocol (interface) for the zelosMCP asset store.

Assets are configuration artefacts associated with an MCP backend —
rule playbooks, UI extension buttons, Cursor agent/skill definitions,
Cursor hook entries, and future kinds.

Every asset row is addressed by a four-part key::

    (kind, backend, name, target)

``kind``
    Asset kind id — ``"rule"``, ``"extension"``, ``"agent"``,
    ``"hook"``.  Callers can query across all kinds with ``kind=None``.
``backend``
    The MCP backend the asset is associated with (e.g. ``"pincher"``,
    ``"filesystem"``).  ``"default"`` is the catch-all for content not
    tied to a specific backend.
``name``
    Asset-specific name within the ``(kind, backend)`` bucket — e.g.
    ``"playbook_read_only"`` for rules, ``"index_project"`` for
    extensions.
``target``
    Optional IDE target discriminator — ``""`` (both), ``"cursor"``, or
    ``"vscode"``.  Most kinds use ``""`` which means the asset applies
    to all targets.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from zelosmcp.framework.assetstore.row import AssetRow


@runtime_checkable
class AssetStoreProtocol(Protocol):
    """Async read/write surface for the asset store.

    Implementations must be safe to call from multiple asyncio tasks
    concurrently.  Writes MUST be serialised (e.g. through an
    :class:`asyncio.Lock`) so reads always see a consistent view.
    """

    async def open(self) -> None:
        """Open the underlying store and ensure the schema exists.
        Idempotent — calling ``open()`` on an already-open store is a
        no-op."""
        ...

    async def close(self) -> None:
        """Close the underlying store.  Idempotent."""
        ...

    async def get(
        self,
        kind: str,
        backend: str,
        name: str,
        target: str = "",
    ) -> AssetRow | None:
        """Return one asset row, or ``None`` if it does not exist."""
        ...

    async def list(
        self,
        kind: str | None = None,
        backend: str | None = None,
        target: str | None = None,
    ) -> list[AssetRow]:
        """Return all asset rows matching the given filters.

        Passing ``None`` for any argument widens the filter to all
        values of that column.
        """
        ...

    async def upsert(
        self,
        row: AssetRow,
        *,
        only_if_seed_lt: int | None = None,
    ) -> bool:
        """Insert or update an asset row.

        Parameters
        ----------
        row:
            The asset to write.
        only_if_seed_lt:
            When set, the upsert is conditional: if an existing row has
            ``source='seed'`` and ``seed_version >= only_if_seed_lt``,
            the write is skipped and ``False`` is returned.  Rows with
            ``source='user'`` are **never** overwritten by this method.
            Use ``only_if_seed_lt=row.seed_version`` when seeding from
            YAML files to ensure version-gated, non-destructive seeding.

        Returns
        -------
        ``True`` if the row was written, ``False`` if it was skipped.
        """
        ...

    async def delete(
        self,
        kind: str,
        backend: str,
        name: str,
        target: str = "",
    ) -> bool:
        """Delete an asset row.

        Returns ``True`` if a row was removed, ``False`` if it did not
        exist.
        """
        ...

    async def summary(self) -> dict[str, Any]:
        """Return a lightweight stats dict suitable for debug/status
        endpoints: total row count, count per kind, source breakdown."""
        ...
