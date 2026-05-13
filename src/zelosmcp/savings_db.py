"""Compatibility re-export shim.

The savings store implementation has moved to
:mod:`zelosmcp.framework.savingsstore.sqlite`.  This module re-exports
the public API so existing imports continue to work without modification.
"""
from zelosmcp.framework.savingsstore.sqlite import (  # noqa: F401
    SavingsStore,
    resolve_db_path,
)

__all__ = ["SavingsStore", "resolve_db_path"]
