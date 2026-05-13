"""``AssetRow`` — the canonical data object for one asset store entry."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AssetRow:
    """One row in the asset store.

    Parameters
    ----------
    kind:
        Asset kind id — ``"rule"``, ``"extension"``, ``"agent"``,
        ``"hook"``.
    backend:
        MCP backend the asset is associated with, e.g. ``"pincher"``,
        ``"filesystem"``.  ``"default"`` for backend-agnostic content.
    name:
        Asset name within the ``(kind, backend)`` bucket, e.g.
        ``"playbook_read_only"``, ``"index_project"``.
    target:
        IDE target discriminator — ``""`` (both), ``"cursor"``,
        ``"vscode"``.
    body:
        The markdown / JSON / text body of the asset.
    meta:
        Kind-specific structured fields as a plain dict — stored as
        JSON.  Extensions use it for ``tool``, ``args_template``,
        ``targets``, etc.; hooks for ``event``; agents for
        ``push_path``.  Defaults to an empty dict.
    source:
        ``"seed"`` — written by the built-in YAML seeder.
        ``"user"`` — written via the GUI or API.  The seeder never
        overwrites ``"user"`` rows.
    seed_version:
        Integer version from the YAML seed file.  ``None`` for user
        rows.  Used by the conditional upsert logic to avoid
        downgrading seed content.
    updated_at:
        Unix timestamp of the last write.  Set automatically by the
        store on every upsert.
    """

    kind: str
    backend: str
    name: str
    target: str = ""
    body: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    source: str = "seed"
    seed_version: int | None = None
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation for API responses."""
        return {
            "kind": self.kind,
            "backend": self.backend,
            "name": self.name,
            "target": self.target,
            "body": self.body,
            "meta": self.meta,
            "source": self.source,
            "seed_version": self.seed_version,
            "updated_at": self.updated_at,
        }
