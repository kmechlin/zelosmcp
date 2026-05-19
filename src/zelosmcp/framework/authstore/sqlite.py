"""SQLite + Fernet implementation of the auth token store.

Moved from :mod:`zelosmcp.auth.store` to the new Bifrost-style
:mod:`zelosmcp.framework.authstore` namespace.  The original module
is kept as a compatibility re-export shim.

Tokens are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).
The key lives at ``~/.zelosmcp/auth.key`` (chmod 600), generated on
first run if missing.  In Kubernetes that key is mounted from a Secret
rather than auto-generated; otherwise a Pod restart would lose every
token.  Device sessions track in-progress OAuth flows so the SSE poll
endpoint can survive a process restart mid-flow.
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
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("zelosmcp.auth.store")


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS tokens (
        user_key TEXT NOT NULL,
        provider TEXT NOT NULL,
        audience TEXT NOT NULL,
        access_token_enc BLOB NOT NULL,
        refresh_token_enc BLOB,
        expires_at REAL,
        scopes TEXT,
        identity_username TEXT,
        identity_avatar_url TEXT,
        updated_at REAL NOT NULL,
        PRIMARY KEY (user_key, provider, audience)
    )
    """,
    "CREATE INDEX IF NOT EXISTS tokens_provider ON tokens(provider)",
    """
    CREATE TABLE IF NOT EXISTS device_sessions (
        session_id TEXT PRIMARY KEY,
        user_key TEXT NOT NULL,
        provider TEXT NOT NULL,
        device_code_enc BLOB NOT NULL,
        poll_interval REAL,
        expires_at REAL,
        state TEXT NOT NULL,
        identity_json TEXT,
        error_message TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS device_sessions_user ON device_sessions(user_key, provider)",
    "CREATE INDEX IF NOT EXISTS device_sessions_expires ON device_sessions(expires_at)",
]


# SQLite primary keys treat NULL columns as distinct, so we normalize a
# missing audience to this sentinel for storage. Reads invert it.
_NULL_AUDIENCE = ""


def resolve_db_path(explicit: str | None = None) -> str:
    """Pick the SQLite path: explicit > env var > ``~/.zelosmcp/auth.sqlite``.

    Returns ``":memory:"`` unchanged for tests. Falls back to
    ``":memory:"`` when the home directory can't be created
    (sandboxed environments, read-only filesystems) so the proxy
    still boots — auth state just doesn't survive restarts in that
    mode and the user has to re-auth on every restart.
    """
    candidate = explicit or os.environ.get("ZELOSMCP_AUTH_DB")
    if candidate:
        return candidate
    home = Path.home() / ".zelosmcp"
    try:
        home.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        logger.warning(
            "auth: cannot create %s (%s); using in-memory store", home, exc
        )
        return ":memory:"
    return str(home / "auth.sqlite")


def resolve_key_path(explicit: str | None = None) -> Path:
    """Pick the Fernet key path: explicit > env var > ``~/.zelosmcp/auth.key``.

    Always returns a :class:`Path` even when the parent directory
    doesn't exist; :func:`load_or_generate_key` handles the
    ``mkdir`` + permissions setup before writing.
    """
    candidate = explicit or os.environ.get("ZELOSMCP_AUTH_KEY_FILE")
    if candidate:
        return Path(candidate)
    return Path.home() / ".zelosmcp" / "auth.key"


def load_or_generate_key(path: Path) -> bytes:
    """Read the Fernet key from ``path``, generating one on first run.

    Generated keys are written with mode 0600 to prevent other users
    on the host from reading them. Existing files keep their
    permissions (we don't chmod files we didn't create — paranoia
    against trampling deliberate overrides).

    For Kubernetes deployments the key MUST be supplied from a
    Secret mount and not auto-generated; otherwise a Pod restart
    would leave existing encrypted rows un-decryptable. The auto-
    generation path here is for local single-user dev convenience.
    """
    if path.exists():
        data = path.read_bytes().strip()
        if not data:
            raise RuntimeError(
                f"auth key file {path} is empty; delete it to regenerate"
            )
        return data

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except (OSError, PermissionError) as exc:
        # Best effort — Windows + some FUSE mounts ignore chmod. We
        # log but don't fail because the alternative is leaving the
        # store unusable on those platforms.
        logger.warning(
            "auth: cannot chmod 600 on %s (%s); key is on disk with default perms",
            path, exc,
        )
    return key


class AuthStore:
    """Async encrypted SQLite store for tokens + device-flow sessions.

    Single aiosqlite connection per process, write-serialised through
    one :class:`asyncio.Lock` so we never fight aiosqlite's per-
    connection locking and so reads see a consistent view even
    while the auth-route hot-path is writing.

    Encryption is Fernet (cryptography library); each value gets
    its own ciphertext. Identity columns (username, avatar_url)
    are stored in plaintext because the GUI needs to render them
    and they're already user-public information at the upstream
    provider — encrypting them would only encrypt-blob the cards.
    """

    def __init__(self, path: str, fernet: Fernet) -> None:
        self.path = path
        self._fernet = fernet
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def open_with_key_file(
        cls,
        path: str | None = None,
        key_path: Path | str | None = None,
    ) -> AuthStore:
        """Convenience constructor: resolve both the DB and key
        locations from env / defaults and load (or generate) the
        key. Caller still must ``await store.open()`` before
        first use."""
        resolved_db = resolve_db_path(path)
        resolved_key = (
            Path(key_path) if key_path is not None
            else resolve_key_path()
        )
        key_bytes = load_or_generate_key(resolved_key)
        return cls(resolved_db, Fernet(key_bytes))

    async def open(self) -> None:
        """Open the connection and create the schema. Idempotent."""
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
        """Close the connection. Idempotent."""
        db, self._db = self._db, None
        if db is not None:
            try:
                await db.close()
            except Exception as exc:
                logger.warning("auth db close failed: %s", exc)

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("AuthStore.open() was never called")
        return self._db

    def _encrypt(self, plaintext: str | None) -> bytes | None:
        if plaintext is None:
            return None
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def _decrypt(self, ciphertext: bytes | memoryview | None) -> str | None:
        if ciphertext is None:
            return None
        try:
            return self._fernet.decrypt(bytes(ciphertext)).decode("utf-8")
        except InvalidToken:
            # Key rotation lost the old encrypted blobs; surface as
            # None rather than raising so the caller treats this
            # like a missing token and prompts re-auth.
            logger.warning(
                "auth store: undecryptable blob (key changed?); treating as missing"
            )
            return None

    # ── Tokens ──────────────────────────────────────────────────────────

    async def put_token(
        self,
        *,
        user_key: str,
        provider: str,
        audience: str | None,
        access_token: str,
        refresh_token: str | None,
        expires_at: float | None,
        scopes: tuple[str, ...] | None,
        identity_username: str | None,
        identity_avatar_url: str | None,
    ) -> None:
        """Insert-or-replace one stored token. Identity columns and
        scope list are plaintext for direct rendering."""
        scopes_json = json.dumps(list(scopes)) if scopes else None
        access_enc = self._encrypt(access_token)
        refresh_enc = self._encrypt(refresh_token)
        ts = time.time()
        async with self._lock:
            db = self._conn()
            await db.execute(
                """
                INSERT INTO tokens (
                    user_key, provider, audience,
                    access_token_enc, refresh_token_enc,
                    expires_at, scopes,
                    identity_username, identity_avatar_url,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_key, provider, audience) DO UPDATE SET
                    access_token_enc = excluded.access_token_enc,
                    refresh_token_enc = excluded.refresh_token_enc,
                    expires_at = excluded.expires_at,
                    scopes = excluded.scopes,
                    identity_username = excluded.identity_username,
                    identity_avatar_url = excluded.identity_avatar_url,
                    updated_at = excluded.updated_at
                """,
                (
                    user_key, provider, audience or _NULL_AUDIENCE,
                    access_enc, refresh_enc,
                    expires_at, scopes_json,
                    identity_username, identity_avatar_url,
                    ts,
                ),
            )
            await db.commit()

    async def get_token(
        self,
        *,
        user_key: str,
        provider: str,
        audience: str | None,
    ) -> dict[str, Any] | None:
        """Return the full row dict for a stored token, with
        access/refresh tokens decrypted to strings. Missing rows
        return ``None``; rows whose blob fails to decrypt also
        return ``None`` (treated as "needs re-auth")."""
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                """
                SELECT access_token_enc, refresh_token_enc, expires_at,
                       scopes, identity_username, identity_avatar_url,
                       updated_at
                FROM tokens
                WHERE user_key = ? AND provider = ? AND audience = ?
                """,
                (user_key, provider, audience or _NULL_AUDIENCE),
            )
            row = await cur.fetchone()
            await cur.close()
        if row is None:
            return None
        access_enc, refresh_enc, expires_at, scopes_json, username, avatar, updated = row
        access_token = self._decrypt(access_enc)
        if access_token is None:
            return None
        refresh_token = self._decrypt(refresh_enc)
        scopes: tuple[str, ...] = ()
        if scopes_json:
            try:
                parsed = json.loads(scopes_json)
                if isinstance(parsed, list):
                    scopes = tuple(str(s) for s in parsed)
            except (ValueError, TypeError):
                scopes = ()
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "scopes": scopes,
            "identity_username": username,
            "identity_avatar_url": avatar,
            "updated_at": updated,
        }

    async def delete_token(
        self,
        *,
        user_key: str,
        provider: str,
        audience: str | None,
    ) -> bool:
        """Delete one stored token; returns ``True`` if a row was
        actually removed (so the caller can decide whether to call
        the upstream revocation endpoint)."""
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                """
                DELETE FROM tokens
                WHERE user_key = ? AND provider = ? AND audience = ?
                """,
                (user_key, provider, audience or _NULL_AUDIENCE),
            )
            removed = cur.rowcount > 0
            await cur.close()
            await db.commit()
        return removed

    async def delete_provider_tokens(self, provider: str) -> int:
        """Drop every token for one provider. Used when a provider is
        removed from the config — we don't want to leave orphaned
        encrypted blobs around for a name that no longer resolves.
        Returns the number of rows removed (best-effort logging)."""
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                "DELETE FROM tokens WHERE provider = ?", (provider,)
            )
            removed = cur.rowcount
            await cur.close()
            await db.commit()
        return removed

    # ── Device sessions ─────────────────────────────────────────────────

    async def put_device_session(
        self,
        *,
        session_id: str,
        user_key: str,
        provider: str,
        device_code: str,
        poll_interval: float,
        expires_at: float,
    ) -> None:
        """Record a freshly-started device flow. ``device_code`` is
        the upstream's polling secret; encrypted at rest because
        anyone with it can complete the flow on behalf of the user
        between start and expiry."""
        ts = time.time()
        device_code_enc = self._encrypt(device_code)
        async with self._lock:
            db = self._conn()
            await db.execute(
                """
                INSERT OR REPLACE INTO device_sessions (
                    session_id, user_key, provider, device_code_enc,
                    poll_interval, expires_at, state,
                    identity_json, error_message,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, user_key, provider, device_code_enc,
                    poll_interval, expires_at, "pending",
                    None, None,
                    ts, ts,
                ),
            )
            await db.commit()

    async def get_device_session(
        self, session_id: str
    ) -> dict[str, Any] | None:
        """Return the device-session row with the device_code
        decrypted. ``None`` if the session never existed; rows past
        ``expires_at`` are returned with state forcibly set to
        ``"expired"`` so callers don't accidentally keep polling."""
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                """
                SELECT user_key, provider, device_code_enc, poll_interval,
                       expires_at, state, identity_json, error_message,
                       created_at, updated_at
                FROM device_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            )
            row = await cur.fetchone()
            await cur.close()
        if row is None:
            return None
        (
            user_key, provider, code_enc, poll_interval,
            expires_at, state, identity_json, error_message,
            created, updated,
        ) = row
        device_code = self._decrypt(code_enc)
        # Surface upstream-expiry as a hard signal so handlers don't
        # waste a roundtrip polling a code the AS already invalidated.
        effective_state = state
        if state == "pending" and expires_at and time.time() > expires_at:
            effective_state = "expired"
        return {
            "session_id": session_id,
            "user_key": user_key,
            "provider": provider,
            "device_code": device_code,
            "poll_interval": poll_interval,
            "expires_at": expires_at,
            "state": effective_state,
            "identity_json": identity_json,
            "error_message": error_message,
            "created_at": created,
            "updated_at": updated,
        }

    async def update_device_session(
        self,
        *,
        session_id: str,
        state: str,
        identity_json: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Transition a device session to a new state. Caller passes
        the JSON-serialised identity blob and / or an error message
        as appropriate. ``state`` values are the string variants of
        :class:`zelosmcp.auth.protocol.DeviceFlowStateKind`."""
        ts = time.time()
        async with self._lock:
            db = self._conn()
            await db.execute(
                """
                UPDATE device_sessions
                SET state = ?, identity_json = ?, error_message = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (state, identity_json, error_message, ts, session_id),
            )
            await db.commit()

    async def delete_device_session(self, session_id: str) -> None:
        """Drop a device session by id; idempotent — no-op when the
        id never existed."""
        async with self._lock:
            db = self._conn()
            await db.execute(
                "DELETE FROM device_sessions WHERE session_id = ?",
                (session_id,),
            )
            await db.commit()

    async def prune_expired_device_sessions(self) -> int:
        """Drop all device sessions that have aged past their
        expiry. Returns the count removed. Safe to call from a
        periodic background task; the auth-store hot path also
        treats expired rows as "expired" without needing pruning,
        so this is purely housekeeping."""
        cutoff = time.time()
        async with self._lock:
            db = self._conn()
            cur = await db.execute(
                "DELETE FROM device_sessions WHERE expires_at < ?",
                (cutoff,),
            )
            removed = cur.rowcount
            await cur.close()
            await db.commit()
        return removed
