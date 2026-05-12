"""Provider that wraps the existing forward-Authorization-verbatim
behaviour as an :class:`AuthProvider`.

Backends that today use ``passthrough: true`` (and rely on Cursor's
own MCP OAuth client to handle the dance) get wrapped in one of
these so the unified provider abstraction covers them. From the
aggregator's POV: a backend with this provider is never gated and
the inbound caller's ``Authorization`` header flows through
unchanged. Identical to pre-PR-1 behaviour.
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


class PassthroughProvider(AuthProvider):
    """No-op provider that preserves legacy passthrough semantics."""

    type = "passthrough"

    def __init__(self, name: str = "passthrough") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def is_ready(self, user_key: str) -> bool:
        # Always ready: gating MUST stay off so the Cursor MCP client
        # handles the OAuth dance directly with the upstream issuer
        # exactly as it did before this module existed.
        return True

    async def mint_token(
        self, user_key: str, audience: str | None = None
    ) -> str | None:
        # ``None`` signals "use whatever the inbound Authorization
        # header carried" — preserves the existing wire-level
        # passthrough behaviour byte-for-byte.
        return None

    async def start_device_flow(self, user_key: str) -> DeviceFlowSession:
        raise AuthProviderError(
            f"provider '{self._name}' is passthrough-only and does not "
            "implement device flow"
        )

    async def poll_device_flow(self, session_id: str) -> DeviceFlowState:
        raise AuthProviderError(
            f"provider '{self._name}' is passthrough-only and does not "
            "implement device flow"
        )

    async def revoke(self, user_key: str) -> None:
        # Nothing stored; nothing to revoke. Idempotent no-op.
        return None

    async def status(self, user_key: str) -> ProviderStatus:
        return ProviderStatus(
            name=self._name,
            type=self.type,
            ready=True,
            identity=None,
            membership_hint=None,
            supports_device_flow=False,
            supports_authorization_code=False,
        )
