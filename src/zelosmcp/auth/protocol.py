"""Protocol + value types every auth provider implements.

The :class:`AuthProvider` Protocol is the single contract the
aggregator, the HTTP auth routes, and the GUI all rely on. Provider
implementations live in sibling modules
(:mod:`zelosmcp.auth.passthrough`, :mod:`zelosmcp.auth.static`, then
``github.py`` / ``okta.py`` in follow-up PRs) and slot into the
:class:`zelosmcp.auth.registry.AuthRegistry` keyed by their declared
``name``.

Per-user state is keyed by ``user_key`` — produced by
:func:`zelosmcp.passthrough_pool.hash_authorization`. For local
single-user deployments the key is the literal string
``"anonymous"``; for multi-tenant remote deployments it's a SHA-256
hash of the inbound ``Authorization`` header. Providers MUST NOT
treat the key value as anything other than an opaque string.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


# ── Exceptions ──────────────────────────────────────────────────────────


class AuthProviderError(Exception):
    """Base class for provider-level failures.

    Distinct from :class:`zelosmcp.passthrough_pool.PassthroughChallengeError`
    which is HTTP-level (Cursor's MCP OAuth client handles those).
    Provider errors are zelosMCP-internal: misconfiguration, store
    corruption, refresh failures, etc. The HTTP layer surfaces them
    as 5xx with a JSON body.
    """


class DeviceFlowError(AuthProviderError):
    """Generic device-flow failure (network error, malformed response,
    upstream rejection)."""


class DeviceFlowExpired(DeviceFlowError):
    """The user_code expired before the user completed authorization.

    Surfaces in the GUI as "Code expired, please try again." Distinct
    from generic :class:`DeviceFlowError` so the UI can render a
    specific re-try affordance.
    """


# ── Value types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeviceFlowSession:
    """One in-progress device-flow handshake.

    Returned by :meth:`AuthProvider.start_device_flow` and consumed by
    the SSE poll endpoint. ``session_id`` is the opaque handle the
    GUI uses to poll for completion; ``user_code`` is what the user
    types into the upstream verification page (or ignores if
    ``verification_uri_complete`` pre-fills it). ``expires_in`` is
    seconds-from-now after which the upstream stops accepting the
    user_code.
    """

    session_id: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: float
    poll_interval: float
    # Browser-flow providers (Authorization Code + PKCE) don't have a
    # user_code. They return an authorization URL instead. Kept on this
    # existing value object so the HTTP route + UI can support both flows
    # without a broader protocol rename from "device" to "auth" yet.
    authorization_url: str | None = None


class DeviceFlowStateKind(str, Enum):
    """Lifecycle state of one device-flow session.

    ``str``-mixin makes the value JSON-serialisable verbatim — useful
    because the SSE stream sends these as plain strings.
    """

    PENDING = "pending"
    COMPLETE = "complete"
    ERROR = "error"
    EXPIRED = "expired"


@dataclass(frozen=True)
class DeviceFlowState:
    """Snapshot of a device-flow session as polled.

    ``identity`` is populated only when ``state`` is
    :attr:`DeviceFlowStateKind.COMPLETE`. ``error_message`` is set
    only when ``state`` is :attr:`DeviceFlowStateKind.ERROR` or
    :attr:`DeviceFlowStateKind.EXPIRED`.
    """

    state: DeviceFlowStateKind
    identity: "ProviderIdentity | None" = None
    error_message: str | None = None


@dataclass(frozen=True)
class ProviderIdentity:
    """Who a stored token belongs to, for display + audit purposes.

    ``username`` is the upstream-provider's canonical handle (GitHub
    login, Okta ``preferred_username``, etc.). ``avatar_url`` is
    optional but lets the Connections card render a proper user
    badge. ``scopes`` lists the scopes the token was actually
    granted (which may differ from what the provider config
    requested). ``expires_at`` is unix timestamp seconds; ``None``
    means "no known expiry" (static bearers, PATs).
    """

    username: str
    avatar_url: str | None = None
    scopes: tuple[str, ...] = ()
    expires_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderStatus:
    """Summary of one provider's per-user state for the Connections UI.

    Returned by :meth:`AuthProvider.status` so the GUI can render the
    full card without fanning out to multiple endpoints. ``ready``
    drives the ``[Connect]`` vs ``[Reconnect]/[Sign out]`` buttons;
    ``identity`` populates the user badge when present;
    ``membership_hint`` is the optional "Membership required: ..."
    sub-line on the card.
    """

    name: str
    type: str
    ready: bool
    identity: ProviderIdentity | None = None
    membership_hint: str | None = None
    supports_device_flow: bool = False
    # Auth-code providers still use the same Connections UI and start/stream
    # endpoints, but return an authorization_url instead of user_code.
    # Keeping this separate lets the GUI label the Connect affordance while
    # preserving backwards compatibility with existing provider cards.
    supports_authorization_code: bool = False


# ── Protocol ────────────────────────────────────────────────────────────


@runtime_checkable
class AuthProvider(Protocol):
    """Surface every auth provider implements.

    Provider instances are constructed once (typically from a parsed
    ``AuthProviderSpec`` in :mod:`zelosmcp.config`) and live for the
    lifetime of the manager. They share the per-user
    :class:`zelosmcp.auth.store.AuthStore` for token storage, but
    aren't required to use it (passthrough / static providers don't).
    """

    @property
    def name(self) -> str:
        """Stable identifier the per-backend ``auth.provider`` field
        references. Letters, digits, underscores; ASCII only. Two
        providers with the same name in one config is a config error
        caught at parse time."""
        ...

    @property
    def type(self) -> str:
        """Provider-type discriminator used by the UI to pick a
        renderer (e.g. ``"github_device_flow"``,
        ``"okta_device_flow"``, ``"passthrough"``, ``"static"``).
        Each provider class hard-codes its own type string."""
        ...

    async def is_ready(self, user_key: str) -> bool:
        """Whether ``mint_token`` will succeed for this user without
        further interaction.

        ``True`` means: there's a valid (non-expired) credential
        usable RIGHT NOW. The aggregator's gating logic uses this to
        decide whether to emit a backend's compressed wrapper tools
        on ``tools/list``.

        Implementations that mint tokens dynamically (passthrough,
        static) always return ``True``; OAuth-style providers
        return ``True`` only after the user has completed the
        device flow at least once and the resulting refresh token
        is still alive.
        """
        ...

    async def mint_token(
        self, user_key: str, audience: str | None = None
    ) -> str | None:
        """Return the ``Authorization`` header VALUE to forward to the
        upstream backend, e.g. ``"Bearer ghp_..."``. Returns the
        full header value including the scheme prefix.

        ``None`` means "no token to inject — let the inbound
        Authorization header fall through unchanged" (passthrough
        behaviour).

        ``audience`` is provider-specific. Token-exchange providers
        use it to mint a downstream-specific token; device-flow
        providers typically ignore it (one token per provider, all
        backends share it).

        MUST NOT raise when ``is_ready`` would have returned True;
        either both succeed or both report "not ready". Refresh
        failures should be transparent if the refresh token is
        still valid.
        """
        ...

    async def start_device_flow(self, user_key: str) -> DeviceFlowSession:
        """Initiate an upstream device flow for the given user.

        Stores the session under the returned ``session_id`` so
        :meth:`poll_device_flow` can find it later. Raises
        :class:`AuthProviderError` for providers that don't support
        device flow (passthrough / static).
        """
        ...

    async def poll_device_flow(self, session_id: str) -> DeviceFlowState:
        """Check the upstream once for completion of an in-progress
        device flow. Called by the SSE stream endpoint at the
        provider-prescribed interval.

        Returns a :class:`DeviceFlowState` with the current state.
        On :attr:`DeviceFlowStateKind.COMPLETE`, the resulting
        access + refresh tokens have already been written to the
        store; subsequent :meth:`mint_token` calls for the same
        ``user_key`` return the new token.
        """
        ...

    async def revoke(self, user_key: str) -> None:
        """Drop the stored token for this user AND notify the upstream
        provider's revocation endpoint (best-effort; network
        failures are logged but don't block local removal).

        After a successful revoke, :meth:`is_ready` returns ``False``
        and the aggregator gates the backend's wrappers again.
        """
        ...

    async def status(self, user_key: str) -> ProviderStatus:
        """Snapshot of this provider's state for ``user_key``,
        suitable for direct rendering in the Connections UI card.
        Should never raise; on internal errors return a
        :class:`ProviderStatus` with ``ready=False`` and
        ``identity=None``."""
        ...
