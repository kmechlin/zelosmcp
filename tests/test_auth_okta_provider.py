"""PR 6 tests for the Okta device-flow provider.

Mocks ``httpx.AsyncClient`` against the four Okta endpoints the
provider talks to. Mirrors the structure of
``tests/test_auth_github_provider.py`` since the wire protocol is
the same RFC 8628 device flow with provider-specific endpoint URLs.

Real Okta tenant coverage requires a registered Native app + the
Device Authorization grant enabled by an admin (see plan PR 6
PIVOT NOTE).
"""
from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet

from zelosmcp.auth import AuthStore, OktaDeviceFlowProvider
from zelosmcp.auth.protocol import (
    DeviceFlowError,
    DeviceFlowSession,
    DeviceFlowStateKind,
)


_ISSUER = "https://nike.okta.com/oauth2/default"


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
        name="okta",
        issuer=_ISSUER,
        client_id="0oa.test",
        scopes=("openid", "profile", "email"),
        membership_hint=None,
        store=store,
    )
    defaults.update(overrides)
    return OktaDeviceFlowProvider(**defaults)


def _mock_httpx(handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    return patch(
        "zelosmcp.auth.okta.AsyncClient", side_effect=factory,
    )


# ── Constructor ────────────────────────────────────────────────────────


class TestConstructor:
    def test_empty_issuer_rejected(self, store: AuthStore):
        with pytest.raises(ValueError, match="non-empty issuer"):
            OktaDeviceFlowProvider(
                name="okta", issuer="", client_id="0oa.x", store=store,
            )

    def test_empty_client_id_rejected(self, store: AuthStore):
        with pytest.raises(ValueError, match="non-empty client_id"):
            OktaDeviceFlowProvider(
                name="okta", issuer=_ISSUER, client_id="", store=store,
            )

    def test_missing_store_rejected(self):
        with pytest.raises(ValueError, match="AuthStore"):
            OktaDeviceFlowProvider(
                name="okta",
                issuer=_ISSUER,
                client_id="0oa.x",
                store=None,  # type: ignore[arg-type]
            )

    def test_issuer_trailing_slash_normalised(self, store: AuthStore):
        provider = _make_provider(store, issuer=_ISSUER + "/")
        assert provider.token_url == _ISSUER + "/v1/token"
        assert provider.device_code_url == _ISSUER + "/v1/device/authorize"

    def test_openid_scope_always_included(self, store: AuthStore):
        # Even if the config didn't include "openid", the provider
        # adds it because OIDC needs it for /userinfo to return.
        provider = _make_provider(store, scopes=("profile",))
        assert "openid" in provider._scopes

    def test_membership_hint_propagates(self, store: AuthStore):
        provider = _make_provider(store, membership_hint="Nike.uee.maria")
        assert provider._membership_hint == "Nike.uee.maria"


# ── start_device_flow ───────────────────────────────────────────────────


class TestStartDeviceFlow:
    @pytest.mark.asyncio
    async def test_happy_path_persists_session(self, store: AuthStore):
        provider = _make_provider(store)

        def handler(request):
            assert str(request.url) == provider.device_code_url
            return httpx.Response(
                200,
                json={
                    "device_code": "dc_secret",
                    "user_code": "WXYZ-9999",
                    "verification_uri": "https://nike.okta.com/v1/device",
                    "verification_uri_complete":
                        "https://nike.okta.com/v1/device?user_code=WXYZ-9999",
                    "expires_in": 600,
                    "interval": 5,
                },
            )

        with _mock_httpx(handler):
            session = await provider.start_device_flow("anonymous")
        assert isinstance(session, DeviceFlowSession)
        assert session.user_code == "WXYZ-9999"
        assert "WXYZ-9999" in (session.verification_uri_complete or "")
        row = await store.get_device_session(session.session_id)
        assert row is not None
        assert row["state"] == "pending"

    @pytest.mark.asyncio
    async def test_upstream_error_raises(self, store: AuthStore):
        provider = _make_provider(store)

        def handler(request):
            return httpx.Response(500)

        with _mock_httpx(handler):
            with pytest.raises(DeviceFlowError):
                await provider.start_device_flow("anonymous")


# ── poll_device_flow ────────────────────────────────────────────────────


class TestPollDeviceFlow:
    @pytest.mark.asyncio
    async def test_pending_state(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_device_session(
            session_id="s1", user_key="anonymous", provider="okta",
            device_code="dc", poll_interval=5.0,
            expires_at=time.time() + 600,
        )

        def handler(request):
            return httpx.Response(
                400, json={"error": "authorization_pending"},
            )

        with _mock_httpx(handler):
            state = await provider.poll_device_flow("s1")
        assert state.state is DeviceFlowStateKind.PENDING

    @pytest.mark.asyncio
    async def test_complete_persists_token_and_identity(
        self, store: AuthStore,
    ):
        provider = _make_provider(store)
        await store.put_device_session(
            session_id="s1", user_key="anonymous", provider="okta",
            device_code="dc", poll_interval=5.0,
            expires_at=time.time() + 600,
        )

        token_resp = {
            "access_token": "okta_secret_access",
            "refresh_token": "okta_secret_refresh",
            "id_token": "ignored.jwt.payload",
            "expires_in": 3600,
            "scope": "openid profile email",
            "token_type": "Bearer",
        }
        userinfo_resp = {
            "sub": "00uABC",
            "preferred_username": "kmechl@nike.com",
            "name": "Kelly Mechlin",
            "email": "kmechl@nike.com",
            "picture": "https://avatar/kmechl.png",
        }

        def handler(request):
            url = str(request.url)
            if url == provider.token_url:
                return httpx.Response(200, json=token_resp)
            if url == provider.userinfo_url:
                assert (
                    request.headers["authorization"]
                    == "Bearer okta_secret_access"
                )
                return httpx.Response(200, json=userinfo_resp)
            return httpx.Response(404)

        with _mock_httpx(handler):
            state = await provider.poll_device_flow("s1")
        assert state.state is DeviceFlowStateKind.COMPLETE
        assert state.identity is not None
        assert state.identity.username == "kmechl@nike.com"
        assert state.identity.avatar_url == "https://avatar/kmechl.png"

        token = await store.get_token(
            user_key="anonymous", provider="okta", audience=None,
        )
        assert token is not None
        assert token["access_token"] == "okta_secret_access"
        assert token["refresh_token"] == "okta_secret_refresh"
        assert "openid" in token["scopes"]

    @pytest.mark.asyncio
    async def test_expired_token_marks_expired(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_device_session(
            session_id="s1", user_key="anonymous", provider="okta",
            device_code="dc", poll_interval=5.0,
            expires_at=time.time() + 600,
        )

        def handler(request):
            return httpx.Response(400, json={"error": "expired_token"})

        with _mock_httpx(handler):
            state = await provider.poll_device_flow("s1")
        assert state.state is DeviceFlowStateKind.EXPIRED

    @pytest.mark.asyncio
    async def test_access_denied_marks_error(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_device_session(
            session_id="s1", user_key="anonymous", provider="okta",
            device_code="dc", poll_interval=5.0,
            expires_at=time.time() + 600,
        )

        def handler(request):
            return httpx.Response(400, json={"error": "access_denied"})

        with _mock_httpx(handler):
            state = await provider.poll_device_flow("s1")
        assert state.state is DeviceFlowStateKind.ERROR


# ── Refresh + mint_token ────────────────────────────────────────────────


class TestRefreshAndMint:
    @pytest.mark.asyncio
    async def test_fresh_token_minted(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="okta", audience=None,
            access_token="okta_xxx", refresh_token=None,
            expires_at=time.time() + 3600,
            scopes=(), identity_username=None,
            identity_avatar_url=None,
        )
        assert await provider.mint_token("anonymous") == "Bearer okta_xxx"

    @pytest.mark.asyncio
    async def test_expired_token_with_refresh_succeeds(
        self, store: AuthStore,
    ):
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="okta", audience=None,
            access_token="okta_old", refresh_token="okta_rt",
            expires_at=time.time() - 10,
            scopes=("openid",),
            identity_username="kmechl@nike.com",
            identity_avatar_url=None,
        )

        def handler(request):
            return httpx.Response(200, json={
                "access_token": "okta_new",
                "refresh_token": "okta_rt_rotated",
                "expires_in": 3600,
                "scope": "openid profile email",
                "token_type": "Bearer",
            })

        with _mock_httpx(handler):
            assert await provider.mint_token("anonymous") == "Bearer okta_new"

        # Refresh-token rotation: new token should be in the store.
        token = await store.get_token(
            user_key="anonymous", provider="okta", audience=None,
        )
        assert token["refresh_token"] == "okta_rt_rotated"

    @pytest.mark.asyncio
    async def test_audience_argument_ignored(self, store: AuthStore):
        # The pure device-flow Okta provider mints one token per
        # user; ``audience`` is reserved for a future token-exchange
        # provider. Calling with an audience should still succeed
        # (return the same token) — no audience-specific lookup.
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="okta", audience=None,
            access_token="okta_xxx", refresh_token=None,
            expires_at=time.time() + 3600,
            scopes=(), identity_username=None,
            identity_avatar_url=None,
        )
        result = await provider.mint_token(
            "anonymous", audience="api://atlassian-mcp",
        )
        assert result == "Bearer okta_xxx"


# ── Revoke ──────────────────────────────────────────────────────────────


class TestRevoke:
    @pytest.mark.asyncio
    async def test_revoke_removes_token_locally(self, store: AuthStore):
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="okta", audience=None,
            access_token="okta_xxx", refresh_token="okta_rt",
            expires_at=time.time() + 3600,
            scopes=(), identity_username=None,
            identity_avatar_url=None,
        )

        # Track which tokens are revoked upstream.
        revoked: list[str] = []

        def handler(request):
            assert str(request.url) == provider.revoke_url
            data = dict(item.split("=") for item in
                        request.content.decode().split("&"))
            revoked.append(data["token"])
            return httpx.Response(200)

        with _mock_httpx(handler):
            await provider.revoke("anonymous")

        assert await store.get_token(
            user_key="anonymous", provider="okta", audience=None,
        ) is None
        assert "okta_xxx" in revoked
        assert "okta_rt" in revoked


# ── Status ──────────────────────────────────────────────────────────────


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_with_membership_hint_unauthenticated(
        self, store: AuthStore,
    ):
        provider = _make_provider(
            store, membership_hint="Nike.uee.maria",
        )
        status = await provider.status("anonymous")
        assert status.ready is False
        assert status.membership_hint == "Nike.uee.maria"
        assert status.supports_device_flow is True
        assert status.type == "okta_device_flow"

    @pytest.mark.asyncio
    async def test_status_authenticated_includes_identity(
        self, store: AuthStore,
    ):
        provider = _make_provider(store)
        await store.put_token(
            user_key="anonymous", provider="okta", audience=None,
            access_token="okta_xxx", refresh_token=None,
            expires_at=time.time() + 3600,
            scopes=("openid", "profile", "email"),
            identity_username="kmechl@nike.com",
            identity_avatar_url="https://avatar/kmechl.png",
        )
        status = await provider.status("anonymous")
        assert status.ready is True
        assert status.identity is not None
        assert status.identity.username == "kmechl@nike.com"


# ── Endpoint URL composition ───────────────────────────────────────────


class TestEndpointUrls:
    def test_default_endpoints(self, store: AuthStore):
        provider = _make_provider(store)
        assert provider.device_code_url == f"{_ISSUER}/v1/device/authorize"
        assert provider.token_url == f"{_ISSUER}/v1/token"
        assert provider.userinfo_url == f"{_ISSUER}/v1/userinfo"
        assert provider.revoke_url == f"{_ISSUER}/v1/revoke"
