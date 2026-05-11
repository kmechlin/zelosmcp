"""Tests for Okta Authorization Code + PKCE provider."""
from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet

from zelosmcp.auth import AuthStore, OktaAuthorizationCodeProvider
from zelosmcp.auth.protocol import DeviceFlowStateKind


_ISSUER = "https://nike.okta.com/oauth2/default"


@pytest.fixture
async def store():
    s = AuthStore(":memory:", Fernet(Fernet.generate_key()))
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _provider(store: AuthStore, **kwargs):
    defaults = dict(
        name="nike_okta",
        issuer=_ISSUER,
        client_id="0oa.test",
        redirect_uri="http://localhost:8000/api/auth/nike_okta/callback",
        scopes=("openid", "profile", "email"),
        membership_hint="Nike.uee.maria",
        store=store,
    )
    defaults.update(kwargs)
    return OktaAuthorizationCodeProvider(**defaults)


def _mock_httpx(handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    return patch(
        "zelosmcp.auth.okta_authorization_code.AsyncClient",
        side_effect=factory,
    )


class TestStart:
    @pytest.mark.asyncio
    async def test_start_returns_authorization_url_with_pkce(self, store):
        provider = _provider(store)
        session = await provider.start_device_flow("anonymous")
        assert session.authorization_url
        assert session.user_code == ""
        parsed = urlparse(session.authorization_url)
        assert parsed.scheme == "https"
        assert parsed.path.endswith("/v1/authorize")
        q = parse_qs(parsed.query)
        assert q["client_id"] == ["0oa.test"]
        assert q["response_type"] == ["code"]
        assert q["redirect_uri"] == [
            "http://localhost:8000/api/auth/nike_okta/callback"
        ]
        assert q["state"] == [session.session_id]
        assert q["code_challenge_method"] == ["S256"]
        assert q.get("code_challenge")

        stored = await store.get_device_session(session.session_id)
        secret = json.loads(stored["device_code"])
        assert secret["code_verifier"]
        assert secret["redirect_uri"] == provider._redirect_uri

    @pytest.mark.asyncio
    async def test_default_redirect_uri(self, store):
        provider = _provider(store, redirect_uri=None)
        assert (
            provider._redirect_uri
            == "http://localhost:8000/api/auth/nike_okta/callback"
        )


class TestCallback:
    @pytest.mark.asyncio
    async def test_callback_exchanges_code_and_stores_token(self, store):
        provider = _provider(store)
        session = await provider.start_device_flow("anonymous")

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == provider.token_url:
                form = parse_qs(request.content.decode())
                assert form["grant_type"] == ["authorization_code"]
                assert form["code"] == ["auth_code"]
                assert form["client_id"] == ["0oa.test"]
                assert form["redirect_uri"] == [provider._redirect_uri]
                assert form.get("code_verifier")
                return httpx.Response(
                    200,
                    json={
                        "access_token": "okta_access",
                        "refresh_token": "okta_refresh",
                        "expires_in": 3600,
                        "scope": "openid profile email",
                    },
                )
            if str(request.url) == provider.userinfo_url:
                assert request.headers["authorization"] == "Bearer okta_access"
                return httpx.Response(
                    200,
                    json={
                        "preferred_username": "kmechl@nike.com",
                        "picture": "https://avatar",
                    },
                )
            return httpx.Response(404)

        with _mock_httpx(handler):
            state = await provider.handle_callback(
                code="auth_code", state=session.session_id
            )

        assert state.state is DeviceFlowStateKind.COMPLETE
        token = await store.get_token(
            user_key="anonymous", provider="nike_okta", audience=None
        )
        assert token["access_token"] == "okta_access"
        assert token["refresh_token"] == "okta_refresh"
        assert token["identity_username"] == "kmechl@nike.com"

        poll = await provider.poll_device_flow(session.session_id)
        assert poll.state is DeviceFlowStateKind.COMPLETE
        assert poll.identity.username == "kmechl@nike.com"

    @pytest.mark.asyncio
    async def test_callback_error_marks_session_error(self, store):
        provider = _provider(store)
        session = await provider.start_device_flow("anonymous")
        state = await provider.handle_callback(
            code=None,
            state=session.session_id,
            error="access_denied",
            error_description="Denied by policy",
        )
        assert state.state is DeviceFlowStateKind.ERROR
        assert "Denied" in state.error_message

    @pytest.mark.asyncio
    async def test_missing_state_returns_error(self, store):
        provider = _provider(store)
        state = await provider.handle_callback(code="x", state=None)
        assert state.state is DeviceFlowStateKind.ERROR


class TestTokenUse:
    @pytest.mark.asyncio
    async def test_mint_token_after_callback(self, store):
        provider = _provider(store)
        await store.put_token(
            user_key="anonymous",
            provider="nike_okta",
            audience=None,
            access_token="okta_access",
            refresh_token=None,
            expires_at=time.time() + 3600,
            scopes=("openid",),
            identity_username="kmechl@nike.com",
            identity_avatar_url=None,
        )
        assert await provider.is_ready("anonymous") is True
        assert await provider.mint_token("anonymous") == "Bearer okta_access"

    @pytest.mark.asyncio
    async def test_status_reports_authorization_code_support(self, store):
        provider = _provider(store)
        status = await provider.status("anonymous")
        assert status.supports_device_flow is False
        assert status.supports_authorization_code is True
        assert status.membership_hint == "Nike.uee.maria"
