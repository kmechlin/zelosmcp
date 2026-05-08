"""Unit tests for PR 1 of the auth-provider framework.

Covers:

- :class:`zelosmcp.auth.store.AuthStore` — encrypted token CRUD,
  device-session lifecycle, key derivation paths.
- :class:`zelosmcp.auth.passthrough.PassthroughProvider` — preserves
  legacy passthrough semantics (always-ready, mint-None).
- :class:`zelosmcp.auth.static.StaticBearerProvider` — preserves
  legacy ``auth.bearer`` semantics (always-ready, mint-the-bearer).
- :class:`zelosmcp.auth.registry.AuthRegistry` — name-based lookup
  + atomic replace.

Real-network / real-OAuth coverage lands with the GitHub and Okta
providers in later PRs.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from zelosmcp.auth import (
    AuthProvider,
    AuthProviderError,
    AuthRegistry,
    AuthStore,
    DeviceFlowSession,
    DeviceFlowState,
    DeviceFlowStateKind,
    PassthroughProvider,
    ProviderIdentity,
    ProviderStatus,
    StaticBearerProvider,
    load_or_generate_key,
    resolve_db_path,
    resolve_key_path,
)


# ── Path resolution ─────────────────────────────────────────────────────


class TestResolveDbPath:
    def test_explicit_wins(self):
        assert resolve_db_path("/tmp/explicit.sqlite") == "/tmp/explicit.sqlite"

    def test_env_var_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("ZELOSMCP_AUTH_DB", "/tmp/from_env.sqlite")
        assert resolve_db_path() == "/tmp/from_env.sqlite"

    def test_explicit_beats_env(self, monkeypatch):
        monkeypatch.setenv("ZELOSMCP_AUTH_DB", "/tmp/from_env.sqlite")
        assert resolve_db_path("/tmp/explicit.sqlite") == "/tmp/explicit.sqlite"

    def test_default_falls_back_to_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ZELOSMCP_AUTH_DB", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Path.home() honours $HOME on POSIX; Windows uses USERPROFILE.
        path = resolve_db_path()
        assert path.endswith("auth.sqlite")

    def test_in_memory_passthrough(self):
        assert resolve_db_path(":memory:") == ":memory:"


class TestResolveKeyPath:
    def test_explicit_wins(self):
        assert resolve_key_path("/tmp/explicit.key") == Path("/tmp/explicit.key")

    def test_env_var_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("ZELOSMCP_AUTH_KEY_FILE", "/tmp/from_env.key")
        assert resolve_key_path() == Path("/tmp/from_env.key")

    def test_default_uses_home_zelosmcp(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ZELOSMCP_AUTH_KEY_FILE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        path = resolve_key_path()
        assert path.name == "auth.key"
        assert path.parent.name == ".zelosmcp"


class TestLoadOrGenerateKey:
    def test_generates_when_missing(self, tmp_path):
        key_path = tmp_path / "auth.key"
        assert not key_path.exists()
        key = load_or_generate_key(key_path)
        assert key_path.exists()
        # Generated key must be a valid Fernet key.
        Fernet(key)
        # Same call returns the same bytes, doesn't regenerate.
        again = load_or_generate_key(key_path)
        assert again == key

    def test_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "auth.key"
        load_or_generate_key(nested)
        assert nested.exists()

    def test_uses_existing_key(self, tmp_path):
        key_path = tmp_path / "auth.key"
        canned = Fernet.generate_key()
        key_path.write_bytes(canned)
        loaded = load_or_generate_key(key_path)
        assert loaded == canned

    def test_strips_trailing_whitespace(self, tmp_path):
        key_path = tmp_path / "auth.key"
        canned = Fernet.generate_key()
        key_path.write_bytes(canned + b"\n   ")
        loaded = load_or_generate_key(key_path)
        assert loaded == canned

    def test_empty_file_raises(self, tmp_path):
        key_path = tmp_path / "auth.key"
        key_path.write_bytes(b"   \n")
        with pytest.raises(RuntimeError, match="empty"):
            load_or_generate_key(key_path)

    def test_chmod_600_when_writable(self, tmp_path):
        # Skip the perm assertion on platforms where chmod is a no-op
        # (Windows). On POSIX the new file MUST be 0600.
        key_path = tmp_path / "auth.key"
        load_or_generate_key(key_path)
        if os.name != "nt":
            mode = key_path.stat().st_mode & 0o777
            assert mode == 0o600


# ── Store fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def fernet_key() -> Fernet:
    return Fernet(Fernet.generate_key())


@pytest.fixture
async def store(fernet_key: Fernet) -> AuthStore:
    """In-memory AuthStore, opened and ready for use."""
    s = AuthStore(":memory:", fernet_key)
    await s.open()
    try:
        yield s
    finally:
        await s.close()


# ── Token CRUD ──────────────────────────────────────────────────────────


class TestAuthStoreTokens:
    @pytest.mark.asyncio
    async def test_put_then_get_roundtrip(self, store: AuthStore):
        await store.put_token(
            user_key="anonymous",
            provider="github_oauth_app",
            audience=None,
            access_token="gho_secret_access",
            refresh_token="ghr_secret_refresh",
            expires_at=time.time() + 3600,
            scopes=("repo", "read:org"),
            identity_username="kmechl",
            identity_avatar_url="https://avatar/kmechl.png",
        )
        row = await store.get_token(
            user_key="anonymous",
            provider="github_oauth_app",
            audience=None,
        )
        assert row is not None
        assert row["access_token"] == "gho_secret_access"
        assert row["refresh_token"] == "ghr_secret_refresh"
        assert row["scopes"] == ("repo", "read:org")
        assert row["identity_username"] == "kmechl"
        assert row["identity_avatar_url"] == "https://avatar/kmechl.png"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store: AuthStore):
        row = await store.get_token(
            user_key="anonymous", provider="github_oauth_app", audience=None,
        )
        assert row is None

    @pytest.mark.asyncio
    async def test_audience_distinguishes_rows(self, store: AuthStore):
        for aud, secret in (("api://atlas", "tok-atlas"), ("api://art", "tok-art")):
            await store.put_token(
                user_key="anonymous",
                provider="nike_okta",
                audience=aud,
                access_token=secret,
                refresh_token=None,
                expires_at=None,
                scopes=None,
                identity_username=None,
                identity_avatar_url=None,
            )
        row_a = await store.get_token(
            user_key="anonymous", provider="nike_okta", audience="api://atlas",
        )
        row_b = await store.get_token(
            user_key="anonymous", provider="nike_okta", audience="api://art",
        )
        assert row_a is not None and row_a["access_token"] == "tok-atlas"
        assert row_b is not None and row_b["access_token"] == "tok-art"

    @pytest.mark.asyncio
    async def test_user_key_distinguishes_rows(self, store: AuthStore):
        # Multi-tenant scenario: two Cursor sessions share a backend
        # but each has their own token.
        for user, secret in (("hash_a", "tok-a"), ("hash_b", "tok-b")):
            await store.put_token(
                user_key=user,
                provider="github_oauth_app",
                audience=None,
                access_token=secret,
                refresh_token=None,
                expires_at=None,
                scopes=None,
                identity_username=None,
                identity_avatar_url=None,
            )
        a = await store.get_token(
            user_key="hash_a", provider="github_oauth_app", audience=None,
        )
        b = await store.get_token(
            user_key="hash_b", provider="github_oauth_app", audience=None,
        )
        assert a["access_token"] == "tok-a"
        assert b["access_token"] == "tok-b"

    @pytest.mark.asyncio
    async def test_put_replaces_existing(self, store: AuthStore):
        common = dict(
            user_key="anonymous",
            provider="github_oauth_app",
            audience=None,
            refresh_token=None,
            expires_at=None,
            scopes=None,
            identity_username=None,
            identity_avatar_url=None,
        )
        await store.put_token(access_token="first", **common)
        await store.put_token(access_token="second", **common)
        row = await store.get_token(
            user_key="anonymous", provider="github_oauth_app", audience=None,
        )
        assert row["access_token"] == "second"

    @pytest.mark.asyncio
    async def test_delete_removes_row(self, store: AuthStore):
        await store.put_token(
            user_key="anonymous",
            provider="github_oauth_app",
            audience=None,
            access_token="tok",
            refresh_token=None,
            expires_at=None,
            scopes=None,
            identity_username=None,
            identity_avatar_url=None,
        )
        removed = await store.delete_token(
            user_key="anonymous", provider="github_oauth_app", audience=None,
        )
        assert removed is True
        row = await store.get_token(
            user_key="anonymous", provider="github_oauth_app", audience=None,
        )
        assert row is None

    @pytest.mark.asyncio
    async def test_delete_missing_returns_false(self, store: AuthStore):
        removed = await store.delete_token(
            user_key="anonymous", provider="github_oauth_app", audience=None,
        )
        assert removed is False

    @pytest.mark.asyncio
    async def test_delete_provider_tokens(self, store: AuthStore):
        for user in ("a", "b", "c"):
            await store.put_token(
                user_key=user,
                provider="github_oauth_app",
                audience=None,
                access_token="tok",
                refresh_token=None,
                expires_at=None,
                scopes=None,
                identity_username=None,
                identity_avatar_url=None,
            )
        # Different provider — should NOT be deleted.
        await store.put_token(
            user_key="a",
            provider="nike_okta",
            audience=None,
            access_token="tok",
            refresh_token=None,
            expires_at=None,
            scopes=None,
            identity_username=None,
            identity_avatar_url=None,
        )
        removed = await store.delete_provider_tokens("github_oauth_app")
        assert removed == 3
        row = await store.get_token(
            user_key="a", provider="nike_okta", audience=None,
        )
        assert row is not None  # Untouched.

    @pytest.mark.asyncio
    async def test_undecryptable_blob_returns_none(
        self, fernet_key: Fernet
    ):
        # Write with one key, read with a different key — the row
        # should surface as None (not raise) so callers treat it
        # like a missing token and prompt re-auth.
        store_a = AuthStore(":memory:", fernet_key)
        await store_a.open()
        try:
            await store_a.put_token(
                user_key="anonymous",
                provider="github_oauth_app",
                audience=None,
                access_token="tok",
                refresh_token=None,
                expires_at=None,
                scopes=None,
                identity_username=None,
                identity_avatar_url=None,
            )
            # Hot-swap the Fernet key on the store object so subsequent
            # reads use the wrong key. Same DB connection, different
            # decryption identity.
            store_a._fernet = Fernet(Fernet.generate_key())
            row = await store_a.get_token(
                user_key="anonymous",
                provider="github_oauth_app",
                audience=None,
            )
            assert row is None
        finally:
            await store_a.close()


# ── Device-session CRUD ─────────────────────────────────────────────────


class TestAuthStoreDeviceSessions:
    @pytest.mark.asyncio
    async def test_put_then_get_roundtrip(self, store: AuthStore):
        await store.put_device_session(
            session_id="sess-abc",
            user_key="anonymous",
            provider="github_oauth_app",
            device_code="dc_secret_long_polling_token",
            poll_interval=5.0,
            expires_at=time.time() + 900,
        )
        row = await store.get_device_session("sess-abc")
        assert row is not None
        assert row["device_code"] == "dc_secret_long_polling_token"
        assert row["state"] == "pending"
        assert row["provider"] == "github_oauth_app"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store: AuthStore):
        assert await store.get_device_session("nope") is None

    @pytest.mark.asyncio
    async def test_state_transition(self, store: AuthStore):
        await store.put_device_session(
            session_id="sess-1",
            user_key="anonymous",
            provider="github_oauth_app",
            device_code="dc",
            poll_interval=5.0,
            expires_at=time.time() + 900,
        )
        identity_blob = json.dumps({"username": "kmechl"})
        await store.update_device_session(
            session_id="sess-1",
            state="complete",
            identity_json=identity_blob,
        )
        row = await store.get_device_session("sess-1")
        assert row["state"] == "complete"
        assert row["identity_json"] == identity_blob

    @pytest.mark.asyncio
    async def test_expired_state_surfaces_for_pending_past_expiry(
        self, store: AuthStore
    ):
        await store.put_device_session(
            session_id="sess-old",
            user_key="anonymous",
            provider="github_oauth_app",
            device_code="dc",
            poll_interval=5.0,
            expires_at=time.time() - 1.0,  # already expired
        )
        row = await store.get_device_session("sess-old")
        assert row["state"] == "expired"

    @pytest.mark.asyncio
    async def test_explicit_complete_state_survives_past_expiry(
        self, store: AuthStore
    ):
        # Once we've recorded a completion, the row stays "complete"
        # even past the device_code's expiry — the upstream token is
        # what matters from then on.
        await store.put_device_session(
            session_id="sess-done",
            user_key="anonymous",
            provider="github_oauth_app",
            device_code="dc",
            poll_interval=5.0,
            expires_at=time.time() - 1.0,
        )
        await store.update_device_session(
            session_id="sess-done",
            state="complete",
            identity_json="{}",
        )
        row = await store.get_device_session("sess-done")
        assert row["state"] == "complete"

    @pytest.mark.asyncio
    async def test_delete_session(self, store: AuthStore):
        await store.put_device_session(
            session_id="sess-del",
            user_key="anonymous",
            provider="github_oauth_app",
            device_code="dc",
            poll_interval=5.0,
            expires_at=time.time() + 900,
        )
        await store.delete_device_session("sess-del")
        assert await store.get_device_session("sess-del") is None

    @pytest.mark.asyncio
    async def test_prune_expired(self, store: AuthStore):
        for sid, exp in (
            ("old1", time.time() - 1.0),
            ("old2", time.time() - 5.0),
            ("fresh", time.time() + 900),
        ):
            await store.put_device_session(
                session_id=sid,
                user_key="anonymous",
                provider="github_oauth_app",
                device_code="dc",
                poll_interval=5.0,
                expires_at=exp,
            )
        removed = await store.prune_expired_device_sessions()
        assert removed == 2
        assert await store.get_device_session("fresh") is not None
        assert await store.get_device_session("old1") is None


class TestAuthStoreLifecycle:
    @pytest.mark.asyncio
    async def test_open_idempotent(self, fernet_key: Fernet):
        s = AuthStore(":memory:", fernet_key)
        await s.open()
        await s.open()  # Must not raise.
        await s.close()

    @pytest.mark.asyncio
    async def test_close_idempotent(self, fernet_key: Fernet):
        s = AuthStore(":memory:", fernet_key)
        await s.open()
        await s.close()
        await s.close()  # Must not raise.

    @pytest.mark.asyncio
    async def test_use_before_open_raises(self, fernet_key: Fernet):
        s = AuthStore(":memory:", fernet_key)
        with pytest.raises(RuntimeError, match="open"):
            await s.put_token(
                user_key="anonymous",
                provider="github_oauth_app",
                audience=None,
                access_token="tok",
                refresh_token=None,
                expires_at=None,
                scopes=None,
                identity_username=None,
                identity_avatar_url=None,
            )

    def test_open_with_key_file_factory(self, tmp_path, monkeypatch):
        # Convenience constructor wires together resolve_* + Fernet.
        monkeypatch.setenv("ZELOSMCP_AUTH_DB", ":memory:")
        key_path = tmp_path / "auth.key"
        store = AuthStore.open_with_key_file(key_path=key_path)
        assert key_path.exists()
        assert store.path == ":memory:"


# ── Provider: PassthroughProvider ───────────────────────────────────────


class TestPassthroughProvider:
    def test_implements_protocol(self):
        provider = PassthroughProvider()
        assert isinstance(provider, AuthProvider)

    def test_default_name(self):
        assert PassthroughProvider().name == "passthrough"

    def test_custom_name(self):
        assert PassthroughProvider("github_legacy").name == "github_legacy"

    def test_type_is_passthrough(self):
        assert PassthroughProvider().type == "passthrough"

    @pytest.mark.asyncio
    async def test_is_ready_always_true(self):
        provider = PassthroughProvider()
        assert await provider.is_ready("anonymous") is True
        assert await provider.is_ready("any-user-key") is True

    @pytest.mark.asyncio
    async def test_mint_token_returns_none(self):
        # None means "use whatever the inbound Authorization said" —
        # preserves existing wire-level passthrough behaviour.
        provider = PassthroughProvider()
        assert await provider.mint_token("anonymous") is None
        assert await provider.mint_token("anonymous", audience="api://x") is None

    @pytest.mark.asyncio
    async def test_device_flow_methods_raise(self):
        provider = PassthroughProvider()
        with pytest.raises(AuthProviderError):
            await provider.start_device_flow("anonymous")
        with pytest.raises(AuthProviderError):
            await provider.poll_device_flow("any-session-id")

    @pytest.mark.asyncio
    async def test_revoke_is_noop(self):
        # No-op so callers can uniformly call revoke without
        # branching on provider type.
        await PassthroughProvider().revoke("anonymous")  # Must not raise.

    @pytest.mark.asyncio
    async def test_status_reflects_passthrough_semantics(self):
        status = await PassthroughProvider().status("anonymous")
        assert status.ready is True
        assert status.identity is None
        assert status.supports_device_flow is False
        assert status.type == "passthrough"


# ── Provider: StaticBearerProvider ──────────────────────────────────────


class TestStaticBearerProvider:
    def test_implements_protocol(self):
        provider = StaticBearerProvider("ci_pat", bearer="ghp_xxx")
        assert isinstance(provider, AuthProvider)

    def test_empty_bearer_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            StaticBearerProvider("ci_pat", bearer="")

    def test_type_is_static(self):
        provider = StaticBearerProvider("ci_pat", bearer="ghp_xxx")
        assert provider.type == "static"

    @pytest.mark.asyncio
    async def test_is_ready_always_true(self):
        provider = StaticBearerProvider("ci_pat", bearer="ghp_xxx")
        assert await provider.is_ready("anonymous") is True

    @pytest.mark.asyncio
    async def test_mint_token_returns_full_header_value(self):
        # The wire format wants "Bearer <token>"; provider adds the
        # prefix so callers can drop the result straight into the
        # Authorization header.
        provider = StaticBearerProvider("ci_pat", bearer="ghp_xxx")
        assert await provider.mint_token("anonymous") == "Bearer ghp_xxx"

    @pytest.mark.asyncio
    async def test_mint_token_ignores_user_key(self):
        # Static = single identity for all callers (matches legacy
        # auth.bearer semantics).
        provider = StaticBearerProvider("ci_pat", bearer="ghp_xxx")
        a = await provider.mint_token("user-a")
        b = await provider.mint_token("user-b")
        assert a == b == "Bearer ghp_xxx"

    @pytest.mark.asyncio
    async def test_device_flow_methods_raise(self):
        provider = StaticBearerProvider("ci_pat", bearer="ghp_xxx")
        with pytest.raises(AuthProviderError):
            await provider.start_device_flow("anonymous")
        with pytest.raises(AuthProviderError):
            await provider.poll_device_flow("any-session-id")

    @pytest.mark.asyncio
    async def test_revoke_raises(self):
        # Static bearers can't be revoked at runtime; the user has to
        # rotate the underlying secret in the env. We surface this
        # rather than silently no-op so the GUI can render a clear
        # error if a Sign-out button is wired up incorrectly.
        provider = StaticBearerProvider("ci_pat", bearer="ghp_xxx")
        with pytest.raises(AuthProviderError):
            await provider.revoke("anonymous")

    @pytest.mark.asyncio
    async def test_status_with_label(self):
        provider = StaticBearerProvider(
            "ci_pat", bearer="ghp_xxx", identity_label="Static PAT (CI)"
        )
        status = await provider.status("anonymous")
        assert status.ready is True
        assert status.identity is not None
        assert status.identity.username == "Static PAT (CI)"
        assert status.type == "static"

    @pytest.mark.asyncio
    async def test_status_without_label(self):
        provider = StaticBearerProvider("ci_pat", bearer="ghp_xxx")
        status = await provider.status("anonymous")
        assert status.identity is None


# ── Registry ────────────────────────────────────────────────────────────


class TestAuthRegistry:
    def test_empty_registry_lookup(self):
        registry = AuthRegistry()
        assert registry.get("missing") is None
        assert registry.get_for_backend("any", "missing") is None
        assert len(registry) == 0
        assert "missing" not in registry

    def test_register_and_lookup(self):
        registry = AuthRegistry()
        provider = PassthroughProvider("legacy")
        registry.register(provider)
        assert registry.get("legacy") is provider
        assert "legacy" in registry
        assert len(registry) == 1

    def test_duplicate_register_raises(self):
        registry = AuthRegistry()
        registry.register(PassthroughProvider("dup"))
        with pytest.raises(ValueError, match="already contains"):
            registry.register(PassthroughProvider("dup"))

    def test_unregister_returns_removed(self):
        registry = AuthRegistry()
        provider = PassthroughProvider("temp")
        registry.register(provider)
        removed = registry.unregister("temp")
        assert removed is provider
        assert registry.get("temp") is None

    def test_unregister_missing_returns_none(self):
        assert AuthRegistry().unregister("missing") is None

    def test_replace_all_atomic_swap(self):
        registry = AuthRegistry()
        registry.register(PassthroughProvider("old"))
        new = [
            PassthroughProvider("new1"),
            StaticBearerProvider("new2", bearer="ghp_xxx"),
        ]
        registry.replace_all(new)
        assert registry.get("old") is None
        assert registry.get("new1") is not None
        assert registry.get("new2") is not None

    def test_replace_all_rejects_duplicates(self):
        registry = AuthRegistry()
        registry.register(PassthroughProvider("keep"))
        bad = [PassthroughProvider("dup"), PassthroughProvider("dup")]
        with pytest.raises(ValueError, match="duplicate"):
            registry.replace_all(bad)
        # On failure the old set survives unchanged.
        assert registry.get("keep") is not None

    def test_get_for_backend_with_none_provider(self):
        # Backends without auth.provider configured should resolve to
        # None — the aggregator interprets that as "no gating".
        registry = AuthRegistry()
        registry.register(PassthroughProvider("legacy"))
        assert registry.get_for_backend("github", None) is None
        assert registry.get_for_backend("github", "") is None

    def test_get_for_backend_with_named_provider(self):
        registry = AuthRegistry()
        provider = PassthroughProvider("github_oauth_app")
        registry.register(provider)
        resolved = registry.get_for_backend("github", "github_oauth_app")
        assert resolved is provider

    def test_names_returns_sorted(self):
        registry = AuthRegistry()
        registry.register(PassthroughProvider("zeta"))
        registry.register(PassthroughProvider("alpha"))
        registry.register(PassthroughProvider("mu"))
        assert registry.names() == ("alpha", "mu", "zeta")

    def test_values_iterates_in_sorted_order(self):
        registry = AuthRegistry()
        registry.register(PassthroughProvider("c"))
        registry.register(PassthroughProvider("a"))
        registry.register(PassthroughProvider("b"))
        names = [p.name for p in registry.values()]
        assert names == ["a", "b", "c"]


# ── Cross-cutting: provider + store interplay still works ──────────────


class TestProviderStoreIntegration:
    """The shim providers in PR 1 don't use the store, but a smoke
    test confirms the package imports cleanly together and the
    store/registry combo is composable for future providers."""

    @pytest.mark.asyncio
    async def test_passthrough_in_registry_with_store(
        self, store: AuthStore
    ):
        registry = AuthRegistry()
        registry.register(PassthroughProvider("passthrough_legacy"))
        provider = registry.get("passthrough_legacy")
        # Provider doesn't touch the store, but both can coexist.
        assert await provider.is_ready("anonymous") is True
        assert await provider.mint_token("anonymous") is None
        # Store still functional alongside.
        await store.put_token(
            user_key="anonymous",
            provider="future_provider",
            audience=None,
            access_token="placeholder",
            refresh_token=None,
            expires_at=None,
            scopes=None,
            identity_username=None,
            identity_avatar_url=None,
        )
        row = await store.get_token(
            user_key="anonymous", provider="future_provider", audience=None,
        )
        assert row is not None
