"""zelosMCP asset store.

Public API:

- :class:`~row.AssetRow` — the data object for one asset store row.
- :class:`~sqlite.SQLiteAssetStore` — the default SQLite implementation.
- :func:`~sqlite.resolve_db_path` — pick the SQLite path from env / default.
- :mod:`~kinds` — per-kind registry helpers (``register``, ``lookup``,
  ``known``).
- :func:`~seeder.seed_all` — generic seeder driver.

Importing this package automatically registers all built-in asset kinds
(agent, extension, hook, rule) via the ``kinds/`` sub-package.
"""
from zelosmcp.framework.assetstore.row import AssetRow
from zelosmcp.framework.assetstore.sqlite import SQLiteAssetStore, resolve_db_path
from zelosmcp.framework.assetstore.seeder import seed_all

# Trigger built-in kind registrations.  The kinds/__init__.py imports
# each kind module which calls register() on the registry at module level.
import zelosmcp.framework.assetstore.kinds  # noqa: F401  -- triggers all handler registrations

__all__ = [
    "AssetRow",
    "SQLiteAssetStore",
    "resolve_db_path",
    "seed_all",
]
