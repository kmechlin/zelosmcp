"""Pluggable authentication providers for upstream MCP backends.

zelosMCP backends that talk to OAuth-protected upstream MCP servers
(GitHub MCP, Atlassian MCP, etc.) can now opt into a per-user token
broker instead of forwarding the inbound ``Authorization`` header
verbatim.

This package defines the abstract surface every provider implements
plus concrete implementations of the two legacy "providers" that
preserve existing zelosMCP behaviour:

- :class:`PassthroughProvider` — never gates, never mints; forwards
  whatever the inbound caller sent. This is what
  ``passthrough: true`` backends got before this module existed.
- :class:`StaticBearerProvider` — wraps the existing
  per-backend ``auth.bearer`` static token. ``mint_token`` always
  returns the same configured bearer; useful for headless / CI
  scenarios where the OAuth dance can't run.

The two real providers (``GithubOAuthAppProvider``,
``OktaDeviceFlowProvider``) land in follow-up PRs and slot in beside
these shims via :class:`AuthRegistry`.

The encrypted token store backing the real providers lives in
:mod:`zelosmcp.auth.store`; shims don't need it.
"""
from __future__ import annotations

from zelosmcp.auth.factory import (
    ProviderFactory,
    ProviderTypeUnavailable,
    build_provider,
    factory_registered,
    register_factory,
)
# Side-effect import: each provider module registers its factory
# at import time. Keep these imports here so a single ``from
# zelosmcp.auth import ...`` in the manager picks up every
# provider type without needing to enumerate them at the call site.
from zelosmcp.auth.github import GithubOAuthAppProvider  # noqa: F401
from zelosmcp.auth.okta import OktaDeviceFlowProvider  # noqa: F401
from zelosmcp.auth.okta_authorization_code import (  # noqa: F401
    OktaAuthorizationCodeProvider,
)
from zelosmcp.auth.passthrough import PassthroughProvider
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
from zelosmcp.auth.registry import AuthRegistry
from zelosmcp.auth.static import StaticBearerProvider
from zelosmcp.auth.store import (
    AuthStore,
    load_or_generate_key,
    resolve_db_path,
    resolve_key_path,
)

__all__ = [
    "AuthProvider",
    "AuthProviderError",
    "AuthRegistry",
    "AuthStore",
    "DeviceFlowError",
    "DeviceFlowExpired",
    "DeviceFlowSession",
    "DeviceFlowState",
    "DeviceFlowStateKind",
    "GithubOAuthAppProvider",
    "OktaAuthorizationCodeProvider",
    "OktaDeviceFlowProvider",
    "PassthroughProvider",
    "ProviderFactory",
    "ProviderIdentity",
    "ProviderStatus",
    "ProviderTypeUnavailable",
    "StaticBearerProvider",
    "build_provider",
    "factory_registered",
    "load_or_generate_key",
    "register_factory",
    "resolve_db_path",
    "resolve_key_path",
]
