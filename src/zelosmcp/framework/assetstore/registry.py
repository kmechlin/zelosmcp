"""Per-kind asset registry.

Formerly ``kinds.py``.  Renamed to ``registry.py`` to avoid a naming
conflict with the ``kinds/`` sub-package that houses the kind handler
modules.

An :class:`AssetKind` describes one family of assets (rules, extensions,
agents, hooks).  Kinds register themselves at import time via
:func:`register`; the new unified seeder calls each kind's
``parse_section`` function for the matching top-level key in a
per-backend YAML file.

Adding a new kind requires:

1. Create ``framework/assetstore/kinds/<name>.py`` with a module-level
   ``register(...)`` call at the bottom.  Import from
   ``zelosmcp.framework.assetstore.registry`` for ``AssetKind``,
   ``register``, etc.
2. Import the new module from
   ``framework/assetstore/kinds/__init__.py`` so the registration runs
   on package import.

No other code needs to change — the driver, HTTP routes, and UI loop
over :func:`known()` dynamically.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from zelosmcp.framework.assetstore.row import AssetRow

logger = logging.getLogger("zelosmcp.assets")


# Type aliases.
ParseSectionFn = Callable[
    [dict, str, int],   # section_data, backend, seed_version
    list[AssetRow],
]
ValidatorFn = Callable[[AssetRow], None]  # raises on invalid
RenderProjectFn = Callable[
    [AssetRow, "RepoCtx"],
    list["ProjectFile"],
]


@dataclass
class ProjectFile:
    """One file to be written into a project by the push writer."""

    rel_path: str
    body: str
    mode: str = "overwrite"  # "overwrite" | "merge"


@dataclass
class RepoCtx:
    """Minimal context passed to ``render_for_project`` calls."""

    name: str
    ro_path: str
    rw_path: str
    extra: dict[str, Any] = field(default_factory=dict)


# Forward-reference for type hints inside this module.
AssetStoreProtocol = Any  # resolved properly by callers via the protocol module


@dataclass
class AssetKind:
    """Descriptor for one family of zelosMCP assets.

    Attributes
    ----------
    id:
        Stable kind identifier used in asset rows (``"rule"``,
        ``"extension"``, ``"agent"``, ``"hook"``).
    section_key:
        Top-level key in the unified per-backend YAML document that
        contains this kind's data (``"rules"``, ``"extensions"``,
        ``"agents"``, ``"hooks"``).
    label:
        Human-readable label for the GUI (``"Rules"``, ``"Extensions"``,
        etc.).
    description:
        One-sentence description shown in the assets index.
    parse_section:
        Callable that converts the already-loaded YAML section dict into a
        list of :class:`AssetRow` objects.  The seeder calls this once per
        backend YAML file for each registered kind.
    validate:
        Optional callable that validates a row before upsert; raises on
        invalid data.
    render_for_project:
        Optional callable that translates one :class:`AssetRow` plus a
        :class:`RepoCtx` into a list of :class:`ProjectFile` objects to
        be written into the repo.  ``None`` if the kind is not pushable.
    stub_body:
        Body template used when inserting an empty placeholder row for a
        backend that has no YAML file.  ``None`` means no stub is
        generated for this kind.
    """

    id: str
    section_key: str
    label: str = ""
    description: str = ""
    parse_section: ParseSectionFn | None = None
    validate: ValidatorFn | None = None
    render_for_project: RenderProjectFn | None = None
    stub_body: str | None = None


_REGISTRY: dict[str, AssetKind] = {}


def register(kind: AssetKind) -> None:
    """Register a kind.  Raises :class:`ValueError` on duplicate id."""
    if kind.id in _REGISTRY:
        raise ValueError(f"AssetKind '{kind.id}' is already registered")
    _REGISTRY[kind.id] = kind
    logger.debug("asset kind registered: %s", kind.id)


def lookup(kind_id: str) -> AssetKind | None:
    """Return the :class:`AssetKind` with the given id, or ``None``."""
    return _REGISTRY.get(kind_id)


def known() -> list[AssetKind]:
    """Return all registered kinds in registration order."""
    return list(_REGISTRY.values())
