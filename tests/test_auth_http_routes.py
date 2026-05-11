"""PR 4 tests for the five new auth HTTP routes in :mod:`zelosmcp.app`.

Routes covered:

- ``GET /api/auth/providers`` — list with per-user status.
- ``POST /api/auth/{provider}/start`` — initiate device flow.
- ``GET /api/auth/{provider}/stream?session=<id>`` — SSE stream.
- ``GET /api/auth/{provider}/identity`` — current user identity.
- ``POST /api/auth/{provider}/revoke`` — sign out.

Tests stub the GitHub upstream via ``httpx.MockTransport`` patched
into the provider module so we exercise the real route handler end-
to-end without a network roundtrip.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest

from zelosmcp.app import create_app
from zelosmcp.manager import ProxyManager


def _fresh():
    manager = ProxyManager(mandatory_config_path="")
    app = create_app(manager)
    return app, manager


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@asynccontextmanager
async def _lifespan(app):
    queue: asyncio.Queue = asyncio.Queue()
    sent: list = []

    async def receive():
        return await queue.get()

    async def send(msg):
        sent.append(msg)

    task = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    await queue.put({"type": "lifespan.startup"})
    for _ in range(100):
        if any(m.get("type") == "lifespan.startup.complete" for m in sent):
            break
        await asyncio.sleep(0.02)
    else:
        raise RuntimeError("lifespan startup did not complete in 2s")
    try:
        yield
    finally:
        await queue.put({"type": "lifespan.shutdown"})
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()


def _patch_github_httpx(handler):
    """Patch the httpx.AsyncClient used inside the github provider so
    tests can inject a deterministic upstream."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    return patch(
        "zelosmcp.auth.github.AsyncClient", side_effect=factory,
    )


async def _bootstrap(manager: ProxyManager) -> None:
    """Open the in-memory auth store + register a github provider so
    routes have something to dispatch to."""
    monkey_db = ":memory:"
    await manager.start_auth_store(db_path=monkey_db)
    await manager.start_auth_providers({
        "providers": {
            "gh": {
                "type": "github_device_flow",
                "client_id": "Iv1.test",
            }
        }
    })


# ── GET /api/auth/providers ────────────────────────────────────────────


class TestListProviders:
    @pytest.mark.asyncio
    async def test_empty_registry(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/api/auth/providers")
        assert r.status_code == 200
        assert r.json() == {"providers": []}

    @pytest.mark.asyncio
    async def test_lists_registered_provider_unauthenticated(self):
        app, manager = _fresh()
        await _bootstrap(manager)
        try:
            async with _client(app) as c:
                r = await c.get("/api/auth/providers")
            assert r.status_code == 200
            providers = r.json()["providers"]
            assert len(providers) == 1
            entry = providers[0]
            assert entry["name"] == "gh"
            assert entry["type"] == "github_device_flow"
            assert entry["ready"] is False
            assert entry["identity"] is None
            assert entry["supports_device_flow"] is True
            assert entry["supports_authorization_code"] is False
        finally:
            await manager.stop_auth_store()


# ── POST /api/auth/{provider}/start ────────────────────────────────────


class TestStartDeviceFlow:
    @pytest.mark.asyncio
    async def test_unknown_provider_returns_404(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/api/auth/missing/start")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_start_returns_user_code(self):
        app, manager = _fresh()
        await _bootstrap(manager)

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

        try:
            with _patch_github_httpx(handler):
                async with _client(app) as c:
                    r = await c.post("/api/auth/gh/start")
            assert r.status_code == 200
            body = r.json()
            assert body["user_code"] == "ABCD-1234"
            assert body["session_id"]
            assert (
                body["verification_uri_complete"]
                == "https://github.com/login/device?user_code=ABCD-1234"
            )
        finally:
            await manager.stop_auth_store()

    @pytest.mark.asyncio
    async def test_upstream_failure_returns_502(self):
        app, manager = _fresh()
        await _bootstrap(manager)

        def handler(request):
            return httpx.Response(500)

        try:
            with _patch_github_httpx(handler):
                async with _client(app) as c:
                    r = await c.post("/api/auth/gh/start")
            assert r.status_code == 502
        finally:
            await manager.stop_auth_store()


# ── GET /api/auth/{provider}/identity ──────────────────────────────────


class TestIdentity:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_null_identity(self):
        app, manager = _fresh()
        await _bootstrap(manager)
        try:
            async with _client(app) as c:
                r = await c.get("/api/auth/gh/identity")
            assert r.status_code == 200
            assert r.json() == {"ready": False, "identity": None}
        finally:
            await manager.stop_auth_store()

    @pytest.mark.asyncio
    async def test_authenticated_returns_user_badge(self):
        app, manager = _fresh()
        await _bootstrap(manager)
        # Seed a token directly so we don't need to drive the device
        # flow end-to-end for this test.
        await manager.auth_store.put_token(
            user_key="anonymous", provider="gh", audience=None,
            access_token="gho_xxx", refresh_token=None,
            expires_at=time.time() + 3600,
            scopes=("repo",),
            identity_username="kmechl",
            identity_avatar_url="https://avatar/kmechl.png",
        )
        try:
            async with _client(app) as c:
                r = await c.get("/api/auth/gh/identity")
            assert r.status_code == 200
            body = r.json()
            assert body["ready"] is True
            assert body["identity"]["username"] == "kmechl"
            assert body["identity"]["scopes"] == ["repo"]
        finally:
            await manager.stop_auth_store()

    @pytest.mark.asyncio
    async def test_unknown_provider_404(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/api/auth/missing/identity")
        assert r.status_code == 404


# ── POST /api/auth/{provider}/revoke ───────────────────────────────────


class TestRevoke:
    @pytest.mark.asyncio
    async def test_revoke_drops_local_token(self):
        app, manager = _fresh()
        await _bootstrap(manager)
        await manager.auth_store.put_token(
            user_key="anonymous", provider="gh", audience=None,
            access_token="gho_xxx", refresh_token=None,
            expires_at=time.time() + 3600,
            scopes=(), identity_username=None,
            identity_avatar_url=None,
        )

        def handler(request):
            # Mock the upstream revocation endpoint as 401 (the
            # public-client expected response) so the local removal
            # is still the source of truth.
            return httpx.Response(401)

        try:
            with _patch_github_httpx(handler):
                async with _client(app) as c:
                    r = await c.post("/api/auth/gh/revoke")
            assert r.status_code == 200
            assert r.json() == {"ok": True}
            # Token should be gone.
            assert await manager.auth_store.get_token(
                user_key="anonymous", provider="gh", audience=None,
            ) is None
        finally:
            await manager.stop_auth_store()

    @pytest.mark.asyncio
    async def test_revoke_unknown_provider_404(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post("/api/auth/missing/revoke")
        assert r.status_code == 404


# ── GET /api/auth/{provider}/stream (SSE) ──────────────────────────────


class TestStreamSSE:
    @pytest.mark.asyncio
    async def test_stream_emits_complete_state(self):
        # Pre-seed a session in the store, then have the upstream
        # respond as if the user has already authorized. The stream
        # should immediately emit a "complete" frame and end.
        app, manager = _fresh()
        await _bootstrap(manager)
        provider = manager.auth_registry.get("gh")

        def start_handler(request):
            return httpx.Response(
                200,
                json={
                    "device_code": "dc_secret",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "verification_uri_complete":
                        "https://github.com/login/device?user_code=ABCD-1234",
                    "expires_in": 900,
                    "interval": 1,
                },
            )

        try:
            with _patch_github_httpx(start_handler):
                session = await provider.start_device_flow("anonymous")

            user_resp = {
                "login": "kmechl",
                "avatar_url": None,
            }
            token_resp = {
                "access_token": "gho_secret",
                "refresh_token": "ghr_secret",
                "expires_in": 28800,
                "scope": "repo",
            }

            def stream_handler(request):
                url = str(request.url)
                if "login/oauth/access_token" in url:
                    return httpx.Response(200, json=token_resp)
                if "/user" in url:
                    return httpx.Response(200, json=user_resp)
                return httpx.Response(404)

            with _patch_github_httpx(stream_handler):
                async with _client(app) as c:
                    async with c.stream(
                        "GET",
                        f"/api/auth/gh/stream?session={session.session_id}",
                    ) as r:
                        assert r.status_code == 200
                        frames: list[dict] = []
                        async for line in r.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            frames.append(json.loads(line[6:]))
                            if frames[-1]["state"] == "complete":
                                break
            assert any(f["state"] == "complete" for f in frames)
            complete = next(f for f in frames if f["state"] == "complete")
            assert complete["identity"]["username"] == "kmechl"
        finally:
            await manager.stop_auth_store()

    @pytest.mark.asyncio
    async def test_stream_missing_session_param_400(self):
        app, manager = _fresh()
        await _bootstrap(manager)
        try:
            async with _client(app) as c:
                r = await c.get("/api/auth/gh/stream")
            assert r.status_code == 400
        finally:
            await manager.stop_auth_store()

    @pytest.mark.asyncio
    async def test_stream_unknown_provider_404(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/api/auth/missing/stream?session=x")
        assert r.status_code == 404


# ── GET /api/auth/{provider}/callback (auth-code providers) ────────────


class TestAuthCodeCallback:
    @pytest.mark.asyncio
    async def test_callback_completes_okta_authorization_code_flow(self):
        app, manager = _fresh()
        await manager.start_auth_store(db_path=":memory:")
        await manager.start_auth_providers({
            "providers": {
                "okta": {
                    "type": "okta_authorization_code",
                    "issuer": "https://nike.okta.com/oauth2/default",
                    "client_id": "0oa.test",
                    "redirect_uri": "http://localhost:8000/api/auth/okta/callback",
                }
            }
        })
        provider = manager.auth_registry.get("okta")
        session = await provider.start_device_flow("anonymous")

        def handler(request):
            url = str(request.url)
            if url.endswith("/v1/token"):
                return httpx.Response(200, json={
                    "access_token": "okta_access",
                    "refresh_token": "okta_refresh",
                    "expires_in": 3600,
                    "scope": "openid profile email",
                })
            if url.endswith("/v1/userinfo"):
                return httpx.Response(200, json={
                    "preferred_username": "kmechl@nike.com",
                    "picture": None,
                })
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        try:
            with patch(
                "zelosmcp.auth.okta_authorization_code.AsyncClient",
                side_effect=factory,
            ):
                async with _client(app) as c:
                    r = await c.get(
                        f"/api/auth/okta/callback?code=abc&state={session.session_id}"
                    )
            assert r.status_code == 200
            assert "Authorization complete" in r.text
            identity = await provider.status("anonymous")
            assert identity.ready is True
            assert identity.identity.username == "kmechl@nike.com"
        finally:
            await manager.stop_auth_store()

    @pytest.mark.asyncio
    async def test_callback_unknown_provider_404(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/api/auth/missing/callback?code=x&state=y")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_callback_unsupported_provider_400(self):
        app, manager = _fresh()
        await manager.start_auth_store(db_path=":memory:")
        await manager.start_auth_providers({
            "providers": {
                "gh": {
                    "type": "github_device_flow",
                    "client_id": "Iv1.test",
                }
            }
        })
        try:
            async with _client(app) as c:
                r = await c.get("/api/auth/gh/callback?code=x&state=y")
            assert r.status_code == 400
        finally:
            await manager.stop_auth_store()
