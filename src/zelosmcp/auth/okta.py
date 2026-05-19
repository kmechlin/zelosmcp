"""Okta OAuth provider implementing OAuth 2.0 Device Authorization
Grant (RFC 8628) against an Okta Authorization Server.

Wire-protocol structure mirrors :mod:`zelosmcp.auth.github` (same
public-client device flow, same store schema, same revocation
pattern). Differences:

- Endpoints derive from the configured ``issuer`` URL — Okta apps
  live at ``<issuer>/v1/device/authorize``, ``<issuer>/v1/token``,
  ``<issuer>/v1/userinfo``, ``<issuer>/v1/revoke``.
- Identity comes from the ``/v1/userinfo`` endpoint (OIDC), with
  ``preferred_username`` / ``name`` / ``picture`` mapping cleanly
  onto :class:`ProviderIdentity`.
- Revocation uses POST (RFC 7009) with ``token`` + ``client_id``
  in the body. Public clients are accepted (no Basic auth needed).
- The optional ``membership_hint`` field (e.g. ``Nike.uee.maria``)
  is surfaced via :class:`ProviderStatus` so the Connections UI
  can warn users about authorized-group requirements BEFORE they
  hit Okta's consent screen and get rejected.

PIVOT NOTE: if Nike's Okta tenant doesn't enable the Device
Authorization grant for our app, swap this provider's ``type`` to
``okta_pkce_loopback`` (a future module). Same store, same UX, same
``membership_hint`` plumbing — only the wire-level handshake
differs.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any

import httpx
# Module-local rebinding so tests can patch the AsyncClient used here
# without affecting the global httpx (mirrors the pattern in
# :mod:`zelosmcp.auth.github`).
from httpx import AsyncClient

from zelosmcp.auth.factory import register_factory
from zelosmcp.auth.protocol import (
    AuthProvider,
    AuthProviderError,
    DeviceFlowError,
    DeviceFlowSession,
    DeviceFlowState,
    DeviceFlowStateKind,
    ProviderIdentity,
    ProviderStatus,
)
from zelosmcp.auth.store import AuthStore

logger = logging.getLogger("zelosmcp.auth.okta")

_DEFAULT_TIMEOUT_SECONDS = 15.0
_TOKEN_REFRESH_LEEWAY_SECONDS = 300

_SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def _httpx_verify() -> Any:
    return _SYSTEM_CA_BUNDLE if os.path.exists(_SYSTEM_CA_BUNDLE) else True


def _short_session_id() -> str:
    return secrets.token_urlsafe(16)


class OktaDeviceFlowProvider(AuthProvider):
    """Okta-issued user OAuth tokens via the device authorization grant."""

    type = "okta_device_flow"

    def __init__(
        self,
        *,
        name: str,
        issuer: str,
        client_id: str,
        scopes: tuple[str, ...] = ("openid", "profile", "email"),
        membership_hint: str | None = None,
        store: AuthStore,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not issuer:
            raise ValueError(
                f"OktaDeviceFlowProvider '{name}' requires a non-empty issuer"
            )
        if not client_id:
            raise ValueError(
                f"OktaDeviceFlowProvider '{name}' requires a non-empty client_id"
            )
        if store is None:
            raise ValueError(
                f"OktaDeviceFlowProvider '{name}' requires an AuthStore "
                "(call manager.start_auth_store() first)"
            )
        self._name = name
        # Strip trailing slash so URL composition is uniform regardless
        # of whether the user wrote "https://nike.okta.com/oauth2/default"
        # or "https://nike.okta.com/oauth2/default/".
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        # Always include "openid" — Okta requires it for any OIDC token,
        # which we need to pull identity info from /userinfo.
        scopes_set = set(scopes)
        scopes_set.add("openid")
        self._scopes = tuple(sorted(scopes_set))
        self._membership_hint = membership_hint
        self._store = store
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return self._name

    # Endpoint URL accessors — kept as properties so a subclass /
    # tenant-specific override can swap them without rewriting the
    # request methods.
    @property
    def device_code_url(self) -> str:
        return f"{self._issuer}/v1/device/authorize"

    @property
    def token_url(self) -> str:
        return f"{self._issuer}/v1/token"

    @property
    def userinfo_url(self) -> str:
        return f"{self._issuer}/v1/userinfo"

    @property
    def revoke_url(self) -> str:
        return f"{self._issuer}/v1/revoke"

    @property
    def verification_uri_default(self) -> str:
        # Okta returns this in the device/authorize response; this is
        # the fallback when the response omits it.
        return f"{self._issuer}/v1/device"

    # ── AuthProvider surface ────────────────────────────────────────────

    async def is_ready(self, user_key: str) -> bool:
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
        # ``audience`` is reserved for the future Okta token-exchange
        # provider (RFC 8693). The pure device-flow provider mints a
        # single user token regardless of which downstream calls it —
        # the upstream resource server is expected to accept the
        # token by issuer + scope, not by audience.
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
        body = {
            "client_id": self._client_id,
            "scope": " ".join(self._scopes),
        }
        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                resp = await client.post(
                    self.device_code_url,
                    data=body,
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DeviceFlowError(
                f"okta device flow start failed: {exc}"
            ) from exc

        device_code = payload.get("device_code")
        user_code = payload.get("user_code")
        verification_uri = (
            payload.get("verification_uri") or self.verification_uri_default
        )
        verification_uri_complete = payload.get("verification_uri_complete")
        expires_in = float(payload.get("expires_in", 600))
        interval = float(payload.get("interval", 5))

        if not device_code or not user_code:
            raise DeviceFlowError(
                "okta device flow start: missing device_code or user_code"
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
        session = await self._store.get_device_session(session_id)
        if session is None:
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR,
                error_message=f"unknown session_id '{session_id}'",
            )

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
                    self.token_url,
                    data={
                        "client_id": self._client_id,
                        "device_code": device_code,
                        "grant_type":
                            "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                )
                # Okta returns non-200 with JSON-RPC-shaped error
                # bodies for the standard pending/slow-down/expired
                # cases. Don't treat 400 as fatal until we've
                # inspected the body.
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

        # Pending / slow-down / errors per RFC 8628 §3.5. Okta wraps
        # the error in OAuth-error JSON with HTTP 400.
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
        token = await self._store.get_token(
            user_key=user_key, provider=self._name, audience=None,
        )
        await self._store.delete_token(
            user_key=user_key, provider=self._name, audience=None,
        )
        if token is None:
            return
        # Best-effort upstream revocation per RFC 7009. Public clients
        # send token + client_id in the body, no Basic auth needed.
        # Try the access token first, then the refresh token (Okta
        # supports revoking either; revoking the refresh token also
        # invalidates derived access tokens).
        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                for tok_kind in ("access_token", "refresh_token"):
                    tok_value = token.get(tok_kind)
                    if not tok_value:
                        continue
                    try:
                        resp = await client.post(
                            self.revoke_url,
                            data={
                                "client_id": self._client_id,
                                "token": tok_value,
                                "token_type_hint": tok_kind,
                            },
                            headers={"Accept": "application/json"},
                        )
                        if resp.status_code not in (200, 204):
                            logger.info(
                                "okta revoke (%s) returned %s",
                                tok_kind, resp.status_code,
                            )
                    except httpx.HTTPError as exc:
                        logger.info(
                            "okta revoke (%s) network error: %s",
                            tok_kind, exc,
                        )
        except httpx.HTTPError as exc:
            logger.info("okta revoke client setup failed: %s", exc)

    async def status(self, user_key: str) -> ProviderStatus:
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
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return None
        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                resp = await client.post(
                    self.token_url,
                    data={
                        "client_id": self._client_id,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "scope": " ".join(self._scopes),
                    },
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("okta refresh failed for %s: %s", self._name, exc)
            return None

        new_access = payload.get("access_token")
        if not new_access:
            logger.info(
                "okta refresh: no access_token in response (%s)",
                payload.get("error", "unknown"),
            )
            return None
        # Okta's default policy ROTATES refresh tokens — the response
        # carries a new one and the old one is invalidated. Always
        # store the new one if present; fall back to the old one only
        # for tenants with rotation disabled.
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
        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                resp = await client.get(
                    self.userinfo_url,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {access_token}",
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("okta /userinfo fetch failed: %s", exc)
            return None
        # Prefer ``preferred_username`` (the Okta login), fall back to
        # ``email`` then ``name`` so we always render something useful
        # in the GUI badge.
        username = (
            payload.get("preferred_username")
            or payload.get("email")
            or payload.get("name")
            or ""
        )
        return ProviderIdentity(
            username=str(username),
            avatar_url=payload.get("picture"),
            scopes=self._scopes,
        )

    @staticmethod
    def _identity_from_session(
        session: dict[str, Any],
    ) -> ProviderIdentity | None:
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
    """Factory adapter from :class:`AuthProviderSpec`."""
    from zelosmcp.auth.factory import ProviderTypeUnavailable

    if store is None:
        raise ProviderTypeUnavailable(
            f"okta provider '{spec.name}' requires the auth store; "
            "call manager.start_auth_store() before start_auth_providers()"
        )
    return OktaDeviceFlowProvider(
        name=spec.name,
        issuer=spec.issuer or "",
        client_id=spec.client_id or "",
        scopes=tuple(spec.scopes) if spec.scopes else
            ("openid", "profile", "email"),
        membership_hint=spec.membership_hint,
        store=store,
    )


register_factory("okta_device_flow", _factory)
