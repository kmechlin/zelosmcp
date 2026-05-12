"""GitHub OAuth App provider implementing device authorization grant
(RFC 8628) against ``github.com/login/device``.

Per GitHub's own MCP install guide for Cursor (see
``docs/oauth-passthrough.md``), Cursor is currently a PAT-only host
for ``api.githubcopilot.com/mcp/`` because Cursor isn't registered
as a GitHub OAuth App. This provider sidesteps that by hosting the
device flow inside zelosMCP itself: the user clicks Connect in the
zelosMCP GUI, completes a one-click browser handoff, and zelosMCP
stores the resulting ``gho_*`` user-OAuth token in the encrypted
auth store. From then on, the aggregator forwards that token to
``api.githubcopilot.com/mcp/`` on the user's behalf — Cursor never
sees the OAuth dance.

Tokens are user-OAuth access tokens (``gho_*``), not GitHub App
installation tokens (``ghs_*``). Per-user identity is preserved so
Copilot's per-seat licensing works.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
import uuid
from typing import Any

import httpx
# Module-local rebinding of ``AsyncClient`` so tests can patch ONLY
# this provider's outbound HTTP without touching every other httpx
# consumer in the process. Patching ``zelosmcp.auth.github.httpx.
# AsyncClient`` would inadvertently affect the test's own ASGI
# transport client (since ``zelosmcp.auth.github.httpx`` IS the
# global httpx module). Patching ``zelosmcp.auth.github.AsyncClient``
# only swaps the rebound name here.
from httpx import AsyncClient

from zelosmcp.auth.factory import register_factory
from zelosmcp.auth.protocol import (
    AuthProvider,
    AuthProviderError,
    DeviceFlowError,
    DeviceFlowExpired,
    DeviceFlowSession,
    DeviceFlowState,
    DeviceFlowStateKind,
    ProviderIdentity,
    ProviderStatus,
)
from zelosmcp.auth.store import AuthStore

logger = logging.getLogger("zelosmcp.auth.github")

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_REVOKE_URL_TEMPLATE = "https://api.github.com/applications/{client_id}/token"
_USER_URL = "https://api.github.com/user"
_VERIFICATION_URI = "https://github.com/login/device"

_DEFAULT_TIMEOUT_SECONDS = 15.0

# Refresh tokens slightly before expiry so a token mint right as the
# old one expires doesn't briefly fail. GitHub access tokens are
# typically 8h; a 5-minute leeway is comfortably under that.
_TOKEN_REFRESH_LEEWAY_SECONDS = 300


# Mirrors the system-CA trick from passthrough_pool — corp-proxied
# upstreams need it; default httpx falls back to certifi-only.
_SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def _httpx_verify() -> Any:
    return _SYSTEM_CA_BUNDLE if os.path.exists(_SYSTEM_CA_BUNDLE) else True


def _short_session_id() -> str:
    """Opaque session id for the device-flow handoff; stored in
    ``device_sessions.session_id`` and round-trips through the SSE
    poll endpoint. URL-safe + 22 chars of entropy is plenty."""
    return secrets.token_urlsafe(16)


class GithubOAuthAppProvider(AuthProvider):
    """OAuth App + device-flow provider for ``api.githubcopilot.com/mcp/``.

    Constructor takes the parsed :class:`AuthProviderSpec` (for
    ``client_id`` + ``scopes`` + ``membership_hint``) and the open
    :class:`AuthStore` (for per-user token persistence).
    """

    type = "github_device_flow"

    def __init__(
        self,
        *,
        name: str,
        client_id: str,
        scopes: tuple[str, ...] = (),
        membership_hint: str | None = None,
        store: AuthStore,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not client_id:
            raise ValueError(
                f"GithubOAuthAppProvider '{name}' requires a non-empty client_id"
            )
        if store is None:
            raise ValueError(
                f"GithubOAuthAppProvider '{name}' requires an AuthStore "
                "(call manager.start_auth_store() first)"
            )
        self._name = name
        self._client_id = client_id
        self._scopes = tuple(scopes)
        self._membership_hint = membership_hint
        self._store = store
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return self._name

    # ── AuthProvider surface ────────────────────────────────────────────

    async def is_ready(self, user_key: str) -> bool:
        """``True`` iff the user has a stored token and (if expired)
        we can refresh it without user interaction."""
        token = await self._store.get_token(
            user_key=user_key, provider=self._name, audience=None,
        )
        if token is None:
            return False
        if not self._token_expired(token):
            return True
        return await self._try_refresh(user_key, token) is not None

    async def mint_token(
        self, user_key: str, audience: str | None = None
    ) -> str | None:
        """Return ``"Bearer <access_token>"`` for the given user.
        Returns ``None`` (i.e. "no token available, fall through to
        passthrough behaviour") rather than raising so a transient
        store / refresh failure doesn't crash the request — the
        upstream's own 401 will surface to the user instead."""
        token = await self._store.get_token(
            user_key=user_key, provider=self._name, audience=None,
        )
        if token is None:
            return None
        if self._token_expired(token):
            refreshed = await self._try_refresh(user_key, token)
            if refreshed is None:
                return None
            access = refreshed
        else:
            access = token["access_token"]
        return f"Bearer {access}"

    async def start_device_flow(self, user_key: str) -> DeviceFlowSession:
        """Initiate a device-flow handshake and persist the device
        session so the SSE poll endpoint can find it later (even
        across process restarts).
        """
        body = {"client_id": self._client_id}
        if self._scopes:
            body["scope"] = " ".join(self._scopes)
        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                resp = await client.post(
                    _DEVICE_CODE_URL,
                    data=body,
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DeviceFlowError(
                f"github device flow start failed: {exc}"
            ) from exc

        device_code = payload.get("device_code")
        user_code = payload.get("user_code")
        verification_uri = payload.get("verification_uri") or _VERIFICATION_URI
        verification_uri_complete = payload.get("verification_uri_complete")
        expires_in = float(payload.get("expires_in", 900))
        interval = float(payload.get("interval", 5))

        if not device_code or not user_code:
            raise DeviceFlowError(
                "github device flow start: missing device_code or user_code"
            )

        session_id = _short_session_id()
        await self._store.put_device_session(
            session_id=session_id,
            user_key=user_key,
            provider=self._name,
            device_code=device_code,
            poll_interval=interval,
            expires_at=time.time() + expires_in,
        )
        return DeviceFlowSession(
            session_id=session_id,
            user_code=user_code,
            verification_uri=verification_uri,
            verification_uri_complete=verification_uri_complete,
            expires_in=expires_in,
            poll_interval=interval,
        )

    async def poll_device_flow(self, session_id: str) -> DeviceFlowState:
        """One poll cycle against GitHub's token endpoint. Returns
        the new state; on completion, the access + refresh tokens
        have already been written to the store."""
        session = await self._store.get_device_session(session_id)
        if session is None:
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR,
                error_message=f"unknown session_id '{session_id}'",
            )

        # Stored state may have already terminalised — return cached.
        cached_state = session["state"]
        if cached_state == "complete":
            identity = self._identity_from_session(session)
            return DeviceFlowState(
                state=DeviceFlowStateKind.COMPLETE, identity=identity
            )
        if cached_state == "expired":
            return DeviceFlowState(
                state=DeviceFlowStateKind.EXPIRED,
                error_message="device code expired before user authorized",
            )
        if cached_state == "error":
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR,
                error_message=session.get("error_message"),
            )

        device_code = session["device_code"]
        if not device_code:
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR,
                error_message="device session has no decryptable device_code",
            )

        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "client_id": self._client_id,
                        "device_code": device_code,
                        "grant_type":
                            "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            await self._store.update_device_session(
                session_id=session_id,
                state="error",
                error_message=str(exc),
            )
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR, error_message=str(exc),
            )

        access_token = payload.get("access_token")
        if access_token:
            # Successful completion. Persist token + identity, mark
            # the session complete so subsequent polls return
            # cached terminal state.
            identity = await self._fetch_identity(access_token)
            scopes_granted = (
                tuple(payload.get("scope", "").split())
                if payload.get("scope") else self._scopes
            )
            expires_in = payload.get("expires_in")
            expires_at = (
                time.time() + float(expires_in) if expires_in else None
            )
            refresh_token = payload.get("refresh_token")

            await self._store.put_token(
                user_key=session["user_key"],
                provider=self._name,
                audience=None,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                scopes=scopes_granted,
                identity_username=identity.username if identity else None,
                identity_avatar_url=(
                    identity.avatar_url if identity else None
                ),
            )
            import json as _json
            await self._store.update_device_session(
                session_id=session_id,
                state="complete",
                identity_json=_json.dumps({
                    "username": identity.username if identity else None,
                    "avatar_url": (
                        identity.avatar_url if identity else None
                    ),
                    "scopes": list(scopes_granted),
                }),
            )
            return DeviceFlowState(
                state=DeviceFlowStateKind.COMPLETE, identity=identity
            )

        # Pending / slow-down / errors per RFC 8628 §3.5.
        error = payload.get("error", "unknown_error")
        if error == "authorization_pending":
            return DeviceFlowState(state=DeviceFlowStateKind.PENDING)
        if error == "slow_down":
            return DeviceFlowState(state=DeviceFlowStateKind.PENDING)
        if error == "expired_token":
            await self._store.update_device_session(
                session_id=session_id,
                state="expired",
                error_message="device code expired",
            )
            return DeviceFlowState(
                state=DeviceFlowStateKind.EXPIRED,
                error_message="device code expired before user authorized",
            )
        if error == "access_denied":
            await self._store.update_device_session(
                session_id=session_id,
                state="error",
                error_message="user denied authorization",
            )
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR,
                error_message="user denied authorization",
            )

        msg = payload.get("error_description") or error
        await self._store.update_device_session(
            session_id=session_id, state="error", error_message=msg,
        )
        return DeviceFlowState(
            state=DeviceFlowStateKind.ERROR, error_message=msg,
        )

    async def revoke(self, user_key: str) -> None:
        """Drop the local token AND best-effort revoke at GitHub.

        Local removal is unconditional so the GUI's Sign-out button
        works even when GitHub's revocation endpoint is unreachable.
        """
        token = await self._store.get_token(
            user_key=user_key, provider=self._name, audience=None,
        )
        await self._store.delete_token(
            user_key=user_key, provider=self._name, audience=None,
        )
        if token is None or token.get("access_token") is None:
            return
        url = _REVOKE_URL_TEMPLATE.format(client_id=self._client_id)
        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                # NOTE: GitHub's revocation endpoint requires Basic auth
                # with client_id + client_secret. Public OAuth Apps
                # using device flow don't have a client_secret, so this
                # endpoint isn't usable here — the upstream rejects
                # with 401. Local token removal is what makes Sign-out
                # work; the user can also revoke via
                # https://github.com/settings/applications. We attempt
                # the call anyway in case future GitHub versions
                # support PKCE-style public-client revocation.
                # ``client.delete`` doesn't accept a body; use the
                # generic ``request`` method to attach the JSON.
                resp = await client.request(
                    "DELETE",
                    url,
                    json={"access_token": token["access_token"]},
                )
                if resp.status_code not in (204, 404, 401):
                    logger.info(
                        "github revoke returned unexpected status %s",
                        resp.status_code,
                    )
        except httpx.HTTPError as exc:
            logger.info("github revoke best-effort failed: %s", exc)

    async def status(self, user_key: str) -> ProviderStatus:
        """Provider status for the Connections card: ready flag,
        identity badge, membership hint."""
        token = await self._store.get_token(
            user_key=user_key, provider=self._name, audience=None,
        )
        identity: ProviderIdentity | None = None
        ready = False
        if token is not None:
            ready = not self._token_expired(token) or bool(
                token.get("refresh_token")
            )
            if token.get("identity_username"):
                identity = ProviderIdentity(
                    username=token["identity_username"],
                    avatar_url=token.get("identity_avatar_url"),
                    scopes=token.get("scopes", ()),
                    expires_at=token.get("expires_at"),
                )
        return ProviderStatus(
            name=self._name,
            type=self.type,
            ready=ready,
            identity=identity,
            membership_hint=self._membership_hint,
            supports_device_flow=True,
            supports_authorization_code=False,
        )

    # ── Internals ───────────────────────────────────────────────────────

    @staticmethod
    def _token_expired(token: dict[str, Any]) -> bool:
        expires_at = token.get("expires_at")
        if expires_at is None:
            return False
        return time.time() >= expires_at - _TOKEN_REFRESH_LEEWAY_SECONDS

    async def _try_refresh(
        self, user_key: str, token: dict[str, Any]
    ) -> str | None:
        """Refresh the access token using the stored refresh_token.

        Returns the new access token on success, ``None`` if no
        refresh token exists or the upstream rejects the refresh
        (typically: refresh token revoked / expired). On None the
        caller treats the user as "not ready" and prompts re-auth.
        """
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return None
        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "client_id": self._client_id,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("github refresh failed for %s: %s", self._name, exc)
            return None

        new_access = payload.get("access_token")
        if not new_access:
            logger.info(
                "github refresh: no access_token in response (%s)",
                payload.get("error", "unknown"),
            )
            return None

        new_refresh = payload.get("refresh_token") or refresh_token
        expires_in = payload.get("expires_in")
        new_expires_at = (
            time.time() + float(expires_in) if expires_in else None
        )
        scopes = (
            tuple(payload.get("scope", "").split())
            if payload.get("scope")
            else token.get("scopes", ())
        )
        await self._store.put_token(
            user_key=user_key,
            provider=self._name,
            audience=None,
            access_token=new_access,
            refresh_token=new_refresh,
            expires_at=new_expires_at,
            scopes=scopes,
            identity_username=token.get("identity_username"),
            identity_avatar_url=token.get("identity_avatar_url"),
        )
        return new_access

    async def _fetch_identity(
        self, access_token: str
    ) -> ProviderIdentity | None:
        """GET /user once at completion to populate the avatar / login
        for the GUI badge. Failure is non-fatal — we still complete the
        flow with whatever the token grants; the GUI just shows
        "Connected" without a name."""
        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                resp = await client.get(
                    _USER_URL,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {access_token}",
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("github /user fetch failed: %s", exc)
            return None
        return ProviderIdentity(
            username=str(payload.get("login") or ""),
            avatar_url=payload.get("avatar_url"),
            scopes=self._scopes,
        )

    @staticmethod
    def _identity_from_session(
        session: dict[str, Any],
    ) -> ProviderIdentity | None:
        """Reconstruct a ProviderIdentity from a stored complete-state
        device session row's ``identity_json`` blob."""
        blob = session.get("identity_json")
        if not blob:
            return None
        import json as _json
        try:
            data = _json.loads(blob)
        except ValueError:
            return None
        return ProviderIdentity(
            username=str(data.get("username") or ""),
            avatar_url=data.get("avatar_url"),
            scopes=tuple(data.get("scopes", ())),
        )


def _factory(spec, store):
    """Factory adapter from :class:`AuthProviderSpec` to provider
    instance — what :func:`zelosmcp.auth.factory.build_provider`
    invokes."""
    from zelosmcp.auth.factory import ProviderTypeUnavailable

    if store is None:
        raise ProviderTypeUnavailable(
            f"github provider '{spec.name}' requires the auth store; "
            "call manager.start_auth_store() before start_auth_providers()"
        )
    return GithubOAuthAppProvider(
        name=spec.name,
        client_id=spec.client_id or "",
        scopes=tuple(spec.scopes),
        membership_hint=spec.membership_hint,
        store=store,
    )


# Auto-register at import time so configurations referencing
# ``github_device_flow`` resolve once this module is imported.
register_factory("github_device_flow", _factory)
