"""Provider that wraps the existing static ``auth.bearer`` config as
an :class:`AuthProvider`.

Backends that today set ``auth: { bearer: "${SOME_PAT}" }`` get
wrapped in a synthetic StaticBearerProvider auto-registered by the
config loader. From the aggregator's POV: the backend is always
"ready" (we have a token), and ``mint_token`` returns the same
configured bearer for every user.

This is a single-token-per-backend model — the bearer doesn't vary
per Cursor session, so multi-tenant deployments using static bearers
share one identity across all callers. That's the same trade-off
the legacy ``auth.bearer`` field already made; we preserve it
verbatim.
"""
from __future__ import annotations

from zelosmcp.auth.protocol import (
    AuthProvider,
    AuthProviderError,
    DeviceFlowSession,
    DeviceFlowState,
    ProviderIdentity,
    ProviderStatus,
)


class StaticBearerProvider(AuthProvider):
    """Always-ready provider that returns a single configured bearer.

    The ``bearer`` parameter is the raw token (no ``"Bearer "``
    prefix); :meth:`mint_token` adds the prefix on the way out so
    the wire format matches what the upstream expects.
    """

    type = "static"

    def __init__(
        self,
        name: str,
        bearer: str,
        *,
        identity_label: str | None = None,
    ) -> None:
        if not bearer:
            raise ValueError(
                f"StaticBearerProvider '{name}' requires a non-empty bearer"
            )
        self._name = name
        self._bearer = bearer
        # Free-form display label for the Connections UI when present
        # (e.g. "Static PAT (CI fallback)"). Not used for auth.
        self._identity_label = identity_label

    @property
    def name(self) -> str:
        return self._name

    async def is_ready(self, user_key: str) -> bool:
        # We have a configured token; always ready. The aggregator's
        # gating treats this as "wrappers visible from cold start"
        # which matches today's auth.bearer behaviour.
        return True

    async def mint_token(
        self, user_key: str, audience: str | None = None
    ) -> str | None:
        return f"Bearer {self._bearer}"

    async def start_device_flow(self, user_key: str) -> DeviceFlowSession:
        raise AuthProviderError(
            f"provider '{self._name}' is a static bearer and does not "
            "implement device flow; rotate the underlying secret instead"
        )

    async def poll_device_flow(self, session_id: str) -> DeviceFlowState:
        raise AuthProviderError(
            f"provider '{self._name}' is a static bearer and does not "
            "implement device flow"
        )

    async def revoke(self, user_key: str) -> None:
        # The bearer lives in process / env, not in the auth store.
        # Removing it would require reconfiguring the backend; this
        # provider doesn't model that.
        raise AuthProviderError(
            f"provider '{self._name}' is a static bearer; revocation "
            "requires rotating the configured token, not a runtime call"
        )

    async def status(self, user_key: str) -> ProviderStatus:
        identity: ProviderIdentity | None = None
        if self._identity_label:
            identity = ProviderIdentity(username=self._identity_label)
        return ProviderStatus(
            name=self._name,
            type=self.type,
            ready=True,
            identity=identity,
            membership_hint=None,
            supports_device_flow=False,
            supports_authorization_code=False,
        )
