"""Compatibility re-export shim.

The auth store implementation has moved to
:mod:`zelosmcp.framework.authstore.sqlite`.  This module re-exports
the public API so existing imports continue to work without modification.
"""
from zelosmcp.framework.authstore.sqlite import (  # noqa: F401
    AuthStore,
    resolve_db_path,
    resolve_key_path,
    load_or_generate_key,
)

__all__ = [
    "AuthStore",
    "resolve_db_path",
    "resolve_key_path",
    "load_or_generate_key",
]
