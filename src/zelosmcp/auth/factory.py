"""Construct :class:`AuthProvider` instances from parsed
:class:`zelosmcp.config.AuthProviderSpec` records.

The factory split exists so config parsing can happen before the
encrypted token store is opened (parser is sync; store needs an
async ``open()`` and a Fernet key). The manager calls
:func:`build_provider` after both prerequisites are ready.

Adding a new provider type means: register a new factory here AND
extend :data:`zelosmcp.config.AUTH_PROVIDER_TYPES`. PR 1 covers
``passthrough`` and ``static``; PR 4 will add ``github_device_flow``;
PR 6 will add ``okta_device_flow``.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from zelosmcp.auth.passthrough import PassthroughProvider
from zelosmcp.auth.protocol import AuthProvider
from zelosmcp.auth.static import StaticBearerProvider

if TYPE_CHECKING:
    from zelosmcp.auth.store import AuthStore
    from zelosmcp.config import AuthProviderSpec


# Provider factory signature: takes the parsed spec + (optional) store
# and returns a constructed AuthProvider. Store is None when called
# before the auth store is initialised (early-validation path); real
# providers that need persistence raise ``ProviderTypeUnavailable``
# in that case so the caller knows to retry post-store-init.
ProviderFactory = Callable[
    ["AuthProviderSpec", "AuthStore | None"], AuthProvider
]


class ProviderTypeUnavailable(Exception):
    """Raised by a factory that requires the auth store but was called
    before the store was opened. The manager catches this and defers
    construction to the post-store-init phase."""


def _passthrough_factory(
    spec: "AuthProviderSpec", store: "AuthStore | None"
) -> AuthProvider:
    return PassthroughProvider(name=spec.name)


def _static_factory(
    spec: "AuthProviderSpec", store: "AuthStore | None"
) -> AuthProvider:
    if not spec.bearer:
        raise ValueError(
            f"static provider '{spec.name}' requires a bearer; this should "
            "have been caught at parse time"
        )
    return StaticBearerProvider(name=spec.name, bearer=spec.bearer)


_FACTORIES: dict[str, ProviderFactory] = {
    "passthrough": _passthrough_factory,
    "static": _static_factory,
    # github_device_flow registered by zelosmcp.auth.github (PR 4).
    # okta_device_flow registered by zelosmcp.auth.okta (PR 6).
}


def register_factory(type_name: str, factory: ProviderFactory) -> None:
    """Add a provider factory for ``type_name``. Called by provider
    modules at import time so the registry is populated before the
    manager loads the config. Raises ``ValueError`` on duplicate
    registration so the import-order bugs that produce silent overrides
    surface loudly."""
    if type_name in _FACTORIES:
        raise ValueError(
            f"provider factory for '{type_name}' is already registered"
        )
    _FACTORIES[type_name] = factory


def build_provider(
    spec: "AuthProviderSpec", store: "AuthStore | None" = None
) -> AuthProvider:
    """Construct an :class:`AuthProvider` from a parsed spec.

    Raises :class:`ProviderTypeUnavailable` when the requested type's
    factory hasn't been registered yet — used by the early-validation
    path (config parse) so we don't fail-hard on unimplemented types
    while still surfacing the real error at runtime.
    """
    factory = _FACTORIES.get(spec.type)
    if factory is None:
        raise ProviderTypeUnavailable(
            f"no factory registered for provider type '{spec.type}' "
            "(missing import? install order issue?)"
        )
    return factory(spec, store)


def factory_registered(type_name: str) -> bool:
    """Whether ``type_name`` currently has a factory. Used by the
    manager's startup logging to flag types that are configured but
    can't be instantiated yet."""
    return type_name in _FACTORIES
