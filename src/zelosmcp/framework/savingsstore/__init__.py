"""Savings store framework subpackage.

Exposes :class:`SavingsStore` (SQLite implementation) and
:func:`resolve_db_path` under the new framework namespace.
The original :mod:`zelosmcp.savings_db` module is kept as a
compatibility re-export shim so existing callers are unaffected.
"""
from zelosmcp.framework.savingsstore.sqlite import SavingsStore, resolve_db_path

__all__ = ["SavingsStore", "resolve_db_path"]
