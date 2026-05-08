"""PR 4 tests for the GitHub OAuth App device-flow provider.

Mocks ``httpx.AsyncClient`` against the four GitHub endpoints the
provider talks to:

- POST ``github.com/login/device/code`` (start)
- POST ``github.com/login/oauth/access_token`` (poll + refresh)
- GET ``api.github.com/user`` (identity fetch on completion)
- DELETE ``api.github.com/applications/{client_id}/token`` (revoke)

End-to-end against the real github.com endpoints lives elsewhere
(out of CI by default; see ``docs/oauth-passthrough.md``).
"""
from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet

from zelosmcp.auth import AuthStore, GithubOAuthAppProvider
from zelosmcp.auth.protocol import (
    AuthProviderError,
    DeviceFlowError,
    DeviceFlowSession,
    DeviceFlowStateKind,
    ProviderIdentity,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
async def store():
    s = AuthStore(":memory:", Fernet(Fernet.generate_key()))
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _make_provider(store: AuthStore, **overrides):
    defaults = dict(
        name="gh",
        client_id="Iv1.test_client_id",
        scopes=("repo", "read:org"),
        membership_hint=None,
        store=store,
    )
    defaults.update(overrides)
    return GithubOAuthAppProvider(**defaults)


def _mock_httpx(handler):
    """Patch ``httpx.AsyncClient`` with a transport that routes every
    request through ``handler(request) -> httpx.Response``. Simpler
    than spinning up a fake ASGI app."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    return patch("zelosmcp.auth.github.AsyncClient", side_effect=factory)


def _route(request, routes):
    """Tiny dispatcher: dict of (method, url) -> Response builder."""
    key = (request.method, str(request.url))
    if key in routes:
        return routes[key](request)
    raise AssertionError(f"unexpected request {request.method} {request.url}")


# ── start_device_flow ───────────────────────────────────────────────────


class TestStartDeviceFlow:
    @pytest.mark.asyncio
    async def test_happy_path_persists_session(self, store: AuthStore):
        provider = _make_provider(store)

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "device_code": "dc_secret",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "verification_uri_complete":
                        "https://github.com/login/device?user_code=ABCD-1234",
                    "expires_in": 900,
                    "interval": 5,
                },
            )

        with _mock_httpx(handler):
            session = await provider.start_device_flow("anonymous")
        assert isinstance(session, DeviceFlowSession)
        assert session.user_code == "ABCD-1234"
        assert session.verification_uri_complete is not None
        # Session should be persisted so the SSE poll endpoint can
        # find it.
        row = await store.get_device_session(session.session_id)
        assert row is not None
        assert row["state"] == "pending"
        assert row["user_key"] == "anonymous"

    @pytest.mark.asyncio
    async def test_upstream_500_raises_device_flow_error(
        self, store: AuthStore
    ):
        provider = _make_provider(store)

        def handler(request):
            return httpx.Response(500, text="upstream broken")

        with _mock_httpx(handler):
            with pytest.raises(DeviceFlowError):
                await provider.start_device_flow("anonymous")

    @pytest.mark.asyncio
    async def test_missing_device_code_raises(self, store: AuthStore):
        provider = _make_provider(store)

        def handler(request):
            # GitHub never returns this shape, but defend against it.
            return httpx.Response(200, json={"user_code": "ABC"})

        with _mock_httpx(handler):
            with pytest.raises(DeviceFlowError, match="missing"):
                await provider.start_device_flow("anonymous")


# ── poll_device_flow ────────────────────────────────────────────────────


class TestPollDeviceFlow:
    @pytest.mark.asyncio
    async def test_pending_state(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_device_session(
            session_id="s1",
            user_key="anonymous",
            provider="gh",
            device_code="dc",
            poll_interval=5.0,
            expires_at=time.time() + 900,
        )

        def handler(request):
            return httpx.Response(
                200, json={"error": "authorization_pending"},
            )

        with _mock_httpx(handler):
            state = await provider.poll_device_flow("s1")
        assert state.state is DeviceFlowStateKind.PENDING

    @pytest.mark.asyncio
    async def test_slow_down_treated_as_pending(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_device_session(
            session_id="s1", user_key="anonymous", provider="gh",
            device_code="dc", poll_interval=5.0,
            expires_at=time.time() + 900,
        )

        def handler(request):
            return httpx.Response(200, json={"error": "slow_down"})

        with _mock_httpx(handler):
            state = await provider.poll_device_flow("s1")
        assert state.state is DeviceFlowStateKind.PENDING

    @pytest.mark.asyncio
    async def test_complete_persists_token_and_identity(
        self, store: AuthStore,
    ):
        provider = _make_provider(store)
        await store.put_device_session(
            session_id="s1", user_key="anonymous", provider="gh",
            device_code="dc", poll_interval=5.0,
            expires_at=time.time() + 900,
        )

        token_resp_json = {
            "access_token": "gho_secret_access",
            "refresh_token": "ghr_secret_refresh",
            "expires_in": 28800,
            "scope": "repo read:org",
            "token_type": "bearer",
        }
        user_resp_json = {
            "login": "kmechl",
            "avatar_url": "https://avatar/kmechl.png",
        }

        def handler(request):
            url = str(request.url)
            if "login/oauth/access_token" in url:
                return httpx.Response(200, json=token_resp_json)
            if "/user" in url:
                # Verify the bearer was sent on the identity fetch.
                assert request.headers["authorization"] == "Bearer gho_secret_access"
                return httpx.Response(200, json=user_resp_json)
            return httpx.Response(404)

        with _mock_httpx(handler):
            state = await provider.poll_device_flow("s1")

        assert state.state is DeviceFlowStateKind.COMPLETE
        assert state.identity is not None
        assert state.identity.username == "kmechl"
        assert state.identity.avatar_url == "https://avatar/kmechl.png"

        # Token should now be retrievable via the store.
        token = await store.get_token(
            user_key="anonymous", provider="gh", audience=None,
        )
        assert token is not None
        assert token["access_token"] == "gho_secret_access"
        assert token["refresh_token"] == "ghr_secret_refresh"
        assert token["identity_username"] == "kmechl"
        assert token["scopes"] == ("repo", "read:org")

    @pytest.mark.asyncio
    async def test_expired_token_marks_session_expired(
        self, store: AuthStore,
    ):
        provider = _make_provider(store)
        await store.put_device_session(
            session_id="s1", user_key="anonymous", provider="gh",
            device_code="dc", poll_interval=5.0,
            expires_at=time.time() + 900,
        )

        def handler(request):
            return httpx.Response(200, json={"error": "expired_token"})

        with _mock_httpx(handler):
            state = await provider.poll_device_flow("s1")
        assert state.state is DeviceFlowStateKind.EXPIRED
        # Subsequent polls should return cached terminal state.
        with _mock_httpx(lambda r: httpx.Response(500)):
            again = await provider.poll_device_flow("s1")
        assert again.state is DeviceFlowStateKind.EXPIRED

    @pytest.mark.asyncio
    async def test_access_denied_marks_session_error(
        self, store: AuthStore,
    ):
        provider = _make_provider(store)
        await store.put_device_session(
            session_id="s1", user_key="anonymous", provider="gh",
            device_code="dc", poll_interval=5.0,
            expires_at=time.time() + 900,
        )

        def handler(request):
            return httpx.Response(200, json={"error": "access_denied"})

        with _mock_httpx(handler):
            state = await provider.poll_device_flow("s1")
        assert state.state is DeviceFlowStateKind.ERROR
        assert "denied" in (state.error_message or "")

    @pytest.mark.asyncio
    async def test_unknown_session_returns_error(self, store: AuthStore):
        provider = _make_provider(store)
        state = await provider.poll_device_flow("nope")
        assert state.state is DeviceFlowStateKind.ERROR
        assert "unknown" in (state.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_complete_state_cached_skips_upstream(
        self, store: AuthStore,
    ):
        # Once a session is complete, further polls return the cached
        # terminal state without hitting GitHub.
        provider = _make_provider(store)
        await store.put_device_session(
            session_id="s1", user_key="anonymous", provider="gh",
            device_code="dc", poll_interval=5.0,
            expires_at=time.time() + 900,
        )
        import json as _json
        await store.update_device_session(
            session_id="s1",
            state="complete",
            identity_json=_json.dumps({
                "username": "kmechl",
                "avatar_url": None,
                "scopes": ["repo"],
            }),
        )

        def handler(request):
            raise AssertionError("should not call upstream on cached complete")

        with _mock_httpx(handler):
            state = await provider.poll_device_flow("s1")
        assert state.state is DeviceFlowStateKind.COMPLETE
        assert state.identity is not None
        assert state.identity.username == "kmechl"


# ── is_ready / mint_token ───────────────────────────────────────────────


class TestIsReadyAndMintToken:
    @pytest.mark.asyncio
    async def test_unauthenticated_user_not_ready(self, store: AuthStore):
        provider = _make_provider(store)
        assert await provider.is_ready("anonymous") is False
        assert await provider.mint_token("anonymous") is None

    @pytest.mark.asyncio
    async def test_fresh_token_ready_and_mints(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="gh", audience=None,
            access_token="gho_xxx", refresh_token=None,
            expires_at=time.time() + 3600,
            scopes=("repo",),
            identity_username="kmechl",
            identity_avatar_url=None,
        )
        assert await provider.is_ready("anonymous") is True
        assert await provider.mint_token("anonymous") == "Bearer gho_xxx"

    @pytest.mark.asyncio
    async def test_expired_token_no_refresh_not_ready(
        self, store: AuthStore,
    ):
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="gh", audience=None,
            access_token="gho_old", refresh_token=None,
            expires_at=time.time() - 10,
            scopes=(),
            identity_username=None,
            identity_avatar_url=None,
        )
        assert await provider.is_ready("anonymous") is False
        assert await provider.mint_token("anonymous") is None

    @pytest.mark.asyncio
    async def test_expired_token_with_refresh_succeeds(
        self, store: AuthStore,
    ):
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="gh", audience=None,
            access_token="gho_old", refresh_token="ghr_xxx",
            expires_at=time.time() - 10,
            scopes=("repo",),
            identity_username="kmechl",
            identity_avatar_url=None,
        )

        def handler(request):
            return httpx.Response(200, json={
                "access_token": "gho_new",
                "refresh_token": "ghr_rotated",
                "expires_in": 28800,
                "scope": "repo",
            })

        with _mock_httpx(handler):
            assert await provider.is_ready("anonymous") is True

        # Mint should return the new token (refreshed).
        with _mock_httpx(handler):
            assert await provider.mint_token("anonymous") == "Bearer gho_new"

        # Store should now hold the rotated refresh token.
        token = await store.get_token(
            user_key="anonymous", provider="gh", audience=None,
        )
        assert token["refresh_token"] == "ghr_rotated"

    @pytest.mark.asyncio
    async def test_refresh_failure_mints_none(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="gh", audience=None,
            access_token="gho_old", refresh_token="ghr_revoked",
            expires_at=time.time() - 10,
            scopes=(),
            identity_username=None,
            identity_avatar_url=None,
        )

        def handler(request):
            return httpx.Response(400, json={"error": "bad_refresh_token"})

        with _mock_httpx(handler):
            assert await provider.is_ready("anonymous") is False
        with _mock_httpx(handler):
            assert await provider.mint_token("anonymous") is None


# ── revoke ──────────────────────────────────────────────────────────────


class TestRevoke:
    @pytest.mark.asyncio
    async def test_revoke_removes_token_locally(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="gh", audience=None,
            access_token="gho_xxx", refresh_token=None,
            expires_at=time.time() + 3600,
            scopes=(), identity_username=None, identity_avatar_url=None,
        )

        def handler(request):
            # GitHub revocation needs Basic auth with client_secret;
            # public OAuth Apps don't have one, so 401 is expected
            # and treated as success (local removal still happens).
            return httpx.Response(401)

        with _mock_httpx(handler):
            await provider.revoke("anonymous")

        assert await store.get_token(
            user_key="anonymous", provider="gh", audience=None,
        ) is None

    @pytest.mark.asyncio
    async def test_revoke_idempotent_when_nothing_stored(
        self, store: AuthStore,
    ):
        provider = _make_provider(store)
        # Should not call upstream since there's no token to revoke.
        with _mock_httpx(lambda r: httpx.Response(500)):
            await provider.revoke("anonymous")  # No-op, no error.


# ── status ──────────────────────────────────────────────────────────────


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_unauthenticated(self, store: AuthStore):
        provider = _make_provider(
            store, membership_hint="Nike.uee.maria"
        )
        status = await provider.status("anonymous")
        assert status.ready is False
        assert status.identity is None
        assert status.membership_hint == "Nike.uee.maria"
        assert status.supports_device_flow is True

    @pytest.mark.asyncio
    async def test_status_authenticated(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="gh", audience=None,
            access_token="gho_xxx", refresh_token="ghr_xxx",
            expires_at=time.time() + 3600,
            scopes=("repo", "read:org"),
            identity_username="kmechl",
            identity_avatar_url="https://avatar/kmechl.png",
        )
        status = await provider.status("anonymous")
        assert status.ready is True
        assert status.identity is not None
        assert status.identity.username == "kmechl"
        assert status.identity.avatar_url == "https://avatar/kmechl.png"

    @pytest.mark.asyncio
    async def test_status_expired_token_with_refresh_still_ready(
        self, store: AuthStore,
    ):
        # Status is computed without consulting the upstream — if a
        # refresh token exists, we report ready (the actual refresh
        # happens lazily on mint_token).
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="gh", audience=None,
            access_token="gho_old", refresh_token="ghr_xxx",
            expires_at=time.time() - 10,
            scopes=(), identity_username="kmechl",
            identity_avatar_url=None,
        )
        status = await provider.status("anonymous")
        assert status.ready is True


# ── Constructor validation ──────────────────────────────────────────────


class TestConstructor:
    def test_empty_client_id_rejected(self, store: AuthStore):
        with pytest.raises(ValueError, match="non-empty client_id"):
            GithubOAuthAppProvider(
                name="gh", client_id="", store=store,
            )

    def test_missing_store_rejected(self):
        with pytest.raises(ValueError, match="AuthStore"):
            GithubOAuthAppProvider(
                name="gh", client_id="Iv1.x", store=None,  # type: ignore[arg-type]
            )

    def test_membership_hint_propagates(self, store: AuthStore):
        provider = _make_provider(
            store, membership_hint="Nike.uee.maria"
        )
        # Reflected in status payload (already covered above) and the
        # underlying attribute is settable via constructor.
        assert provider._membership_hint == "Nike.uee.maria"
