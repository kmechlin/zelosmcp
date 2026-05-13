"""Auth store framework subpackage.

Exposes :class:`AuthStore` (SQLite + Fernet implementation) and related
helpers under the new framework namespace.  The original
:mod:`zelosmcp.auth.store` module is kept as a compatibility re-export
shim so existing callers are unaffected.
"""
from zelosmcp.framework.authstore.sqlite import (
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
