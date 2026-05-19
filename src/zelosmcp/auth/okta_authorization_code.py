"""Okta Authorization Code + PKCE provider.

This provider is the fallback for Okta tenants that expose the standard
``Authorization Code`` + ``Refresh Token`` grants for Native apps but do NOT
enable the Device Authorization grant. It uses the same zelosMCP Connections UI
contract as device-flow providers:

1. ``POST /api/auth/<provider>/start`` calls ``start_device_flow`` (legacy
   method name) and returns an ``authorization_url``.
2. The browser opens that URL and Okta redirects back to
   ``/api/auth/<provider>/callback``.
3. The callback exchanges the code using PKCE, stores the tokens, marks the
   auth session complete, and returns a small "you may close this tab" page.
4. The existing SSE stream observes the complete state and updates the modal.

Despite the method names, no device code is involved. The encrypted
``device_sessions.device_code_enc`` column stores a JSON blob with the PKCE
``code_verifier`` and redirect URI so we can keep the schema stable.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from httpx import AsyncClient

from zelosmcp.auth.factory import register_factory
from zelosmcp.auth.protocol import (
    AuthProvider,
    DeviceFlowError,
    DeviceFlowSession,
    DeviceFlowState,
    DeviceFlowStateKind,
    ProviderIdentity,
    ProviderStatus,
)
from zelosmcp.auth.store import AuthStore

logger = logging.getLogger("zelosmcp.auth.okta_authorization_code")

_DEFAULT_TIMEOUT_SECONDS = 15.0
_TOKEN_REFRESH_LEEWAY_SECONDS = 300
_SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def _httpx_verify() -> Any:
    return _SYSTEM_CA_BUNDLE if os.path.exists(_SYSTEM_CA_BUNDLE) else True


def _short_session_id() -> str:
    return secrets.token_urlsafe(16)


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _new_code_verifier() -> str:
    # RFC 7636: 43-128 chars from unreserved URI charset. token_urlsafe(64)
    # yields ~86 chars and is fine for Okta.
    return secrets.token_urlsafe(64)


def _code_challenge(verifier: str) -> str:
    return _base64url(hashlib.sha256(verifier.encode("ascii")).digest())


class OktaAuthorizationCodeProvider(AuthProvider):
    """Okta Native-app Authorization Code + PKCE provider."""

    type = "okta_authorization_code"

    def __init__(
        self,
        *,
        name: str,
        issuer: str,
        client_id: str,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        scopes: tuple[str, ...] = ("openid", "profile", "email"),
        membership_hint: str | None = None,
        store: AuthStore,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not issuer:
            raise ValueError(
                f"OktaAuthorizationCodeProvider '{name}' requires a non-empty issuer"
            )
        if not client_id:
            raise ValueError(
                f"OktaAuthorizationCodeProvider '{name}' requires a non-empty client_id"
            )
        if store is None:
            raise ValueError(
                f"OktaAuthorizationCodeProvider '{name}' requires an AuthStore "
                "(call manager.start_auth_store() first)"
            )
        self._name = name
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = (
            redirect_uri
            or f"http://localhost:8000/api/auth/{name}/callback"
        )
        scopes_set = set(scopes)
        scopes_set.add("openid")
        self._scopes = tuple(sorted(scopes_set))
        self._membership_hint = membership_hint
        self._store = store
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return self._name

    @property
    def authorize_url(self) -> str:
        return f"{self._issuer}/v1/authorize"

    @property
    def token_url(self) -> str:
        return f"{self._issuer}/v1/token"

    @property
    def userinfo_url(self) -> str:
        return f"{self._issuer}/v1/userinfo"

    @property
    def revoke_url(self) -> str:
        return f"{self._issuer}/v1/revoke"

    async def is_ready(self, user_key: str) -> bool:
        token = await self._store.get_token(
            user_key=user_key, provider=self._name, audience=None
        )
        if token is None:
            return False
        if not self._token_expired(token):
            return True
        return await self._try_refresh(user_key, token) is not None

    async def mint_token(
        self, user_key: str, audience: str | None = None
    ) -> str | None:
        token = await self._store.get_token(
            user_key=user_key, provider=self._name, audience=None
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
        session_id = _short_session_id()
        code_verifier = _new_code_verifier()
        secret_json = json.dumps({
            "code_verifier": code_verifier,
            "redirect_uri": self._redirect_uri,
        })
        expires_in = 600.0
        await self._store.put_device_session(
            session_id=session_id,
            user_key=user_key,
            provider=self._name,
            device_code=secret_json,
            poll_interval=1.0,
            expires_at=time.time() + expires_in,
        )
        query = {
            "client_id": self._client_id,
            "response_type": "code",
            "scope": " ".join(self._scopes),
            "redirect_uri": self._redirect_uri,
            "state": session_id,
            "code_challenge": _code_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        authorization_url = f"{self.authorize_url}?{urlencode(query)}"
        return DeviceFlowSession(
            session_id=session_id,
            user_code="",
            verification_uri=authorization_url,
            verification_uri_complete=None,
            expires_in=expires_in,
            poll_interval=1.0,
            authorization_url=authorization_url,
        )

    async def poll_device_flow(self, session_id: str) -> DeviceFlowState:
        session = await self._store.get_device_session(session_id)
        if session is None:
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR,
                error_message=f"unknown session_id '{session_id}'",
            )
        state = session["state"]
        if state == "complete":
            return DeviceFlowState(
                state=DeviceFlowStateKind.COMPLETE,
                identity=self._identity_from_session(session),
            )
        if state == "expired":
            return DeviceFlowState(
                state=DeviceFlowStateKind.EXPIRED,
                error_message="authorization session expired",
            )
        if state == "error":
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR,
                error_message=session.get("error_message"),
            )
        return DeviceFlowState(state=DeviceFlowStateKind.PENDING)

    async def handle_callback(
        self,
        *,
        code: str | None,
        state: str | None,
        error: str | None = None,
        error_description: str | None = None,
    ) -> DeviceFlowState:
        if not state:
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR,
                error_message="missing state",
            )
        session = await self._store.get_device_session(state)
        if session is None:
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR,
                error_message="unknown or expired state",
            )
        if error:
            msg = error_description or error
            await self._store.update_device_session(
                session_id=state, state="error", error_message=msg
            )
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR, error_message=msg
            )
        if not code:
            await self._store.update_device_session(
                session_id=state, state="error", error_message="missing code"
            )
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR, error_message="missing code"
            )
        secret = session.get("device_code")
        try:
            payload = json.loads(secret or "{}")
            verifier = payload["code_verifier"]
            redirect_uri = payload["redirect_uri"]
        except Exception as exc:  # noqa: BLE001 - corrupt session
            msg = f"invalid authorization session: {exc}"
            await self._store.update_device_session(
                session_id=state, state="error", error_message=msg
            )
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR, error_message=msg
            )

        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                token_data = {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                }
                auth = None
                if self._client_secret:
                    auth = (self._client_id, self._client_secret)
                else:
                    token_data["client_id"] = self._client_id
                resp = await client.post(
                    self.token_url,
                    data=token_data,
                    auth=auth,
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                token_payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            detail = ""
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    body = response.json()
                    err = body.get("error")
                    desc = body.get("error_description")
                    if err or desc:
                        detail = f" ({err}: {desc})"
                except ValueError:
                    text = response.text.strip()
                    if text:
                        detail = f" ({text[:500]})"
            msg = f"token exchange failed: {exc}{detail}"
            await self._store.update_device_session(
                session_id=state, state="error", error_message=msg
            )
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR, error_message=msg
            )

        access_token = token_payload.get("access_token")
        if not access_token:
            msg = token_payload.get("error_description") or token_payload.get(
                "error", "missing access_token"
            )
            await self._store.update_device_session(
                session_id=state, state="error", error_message=msg
            )
            return DeviceFlowState(
                state=DeviceFlowStateKind.ERROR, error_message=msg
            )

        identity = await self._fetch_identity(access_token)
        scopes_granted = (
            tuple(token_payload.get("scope", "").split())
            if token_payload.get("scope") else self._scopes
        )
        expires_in = token_payload.get("expires_in")
        expires_at = time.time() + float(expires_in) if expires_in else None
        refresh_token = token_payload.get("refresh_token")
        await self._store.put_token(
            user_key=session["user_key"],
            provider=self._name,
            audience=None,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scopes=scopes_granted,
            identity_username=identity.username if identity else None,
            identity_avatar_url=identity.avatar_url if identity else None,
        )
        await self._store.update_device_session(
            session_id=state,
            state="complete",
            identity_json=json.dumps({
                "username": identity.username if identity else None,
                "avatar_url": identity.avatar_url if identity else None,
                "scopes": list(scopes_granted),
            }),
        )
        return DeviceFlowState(
            state=DeviceFlowStateKind.COMPLETE, identity=identity
        )

    async def revoke(self, user_key: str) -> None:
        token = await self._store.get_token(
            user_key=user_key, provider=self._name, audience=None
        )
        await self._store.delete_token(
            user_key=user_key, provider=self._name, audience=None
        )
        if token is None:
            return
        try:
            async with AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=_httpx_verify(),
            ) as client:
                for tok_kind in ("access_token", "refresh_token"):
                    tok = token.get(tok_kind)
                    if not tok:
                        continue
                    await client.post(
                        self.revoke_url,
                        data={
                            "client_id": self._client_id,
                            "token": tok,
                            "token_type_hint": tok_kind,
                        },
                        headers={"Accept": "application/json"},
                    )
        except httpx.HTTPError as exc:
            logger.info("okta auth-code revoke best-effort failed: %s", exc)

    async def status(self, user_key: str) -> ProviderStatus:
        token = await self._store.get_token(
            user_key=user_key, provider=self._name, audience=None
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
            supports_device_flow=False,
            supports_authorization_code=True,
        )

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
            logger.info("okta auth-code refresh failed: %s", exc)
            return None
        new_access = payload.get("access_token")
        if not new_access:
            return None
        new_refresh = payload.get("refresh_token") or refresh_token
        expires_in = payload.get("expires_in")
        expires_at = time.time() + float(expires_in) if expires_in else None
        scopes = (
            tuple(payload.get("scope", "").split())
            if payload.get("scope") else token.get("scopes", ())
        )
        await self._store.put_token(
            user_key=user_key,
            provider=self._name,
            audience=None,
            access_token=new_access,
            refresh_token=new_refresh,
            expires_at=expires_at,
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
            logger.info("okta auth-code /userinfo fetch failed: %s", exc)
            return None
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
        try:
            data = json.loads(blob)
        except ValueError:
            return None
        return ProviderIdentity(
            username=str(data.get("username") or ""),
            avatar_url=data.get("avatar_url"),
            scopes=tuple(data.get("scopes", ())),
        )


def _factory(spec, store):
    from zelosmcp.auth.factory import ProviderTypeUnavailable

    if store is None:
        raise ProviderTypeUnavailable(
            f"okta provider '{spec.name}' requires the auth store; "
            "call manager.start_auth_store() before start_auth_providers()"
        )
    return OktaAuthorizationCodeProvider(
        name=spec.name,
        issuer=spec.issuer or "",
        client_id=spec.client_id or "",
        client_secret=spec.client_secret,
        redirect_uri=spec.redirect_uri,
        scopes=tuple(spec.scopes) if spec.scopes else
            ("openid", "profile", "email"),
        membership_hint=spec.membership_hint,
        store=store,
    )


register_factory("okta_authorization_code", _factory)
