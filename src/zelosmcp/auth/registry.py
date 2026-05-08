"""Per-process registry of constructed :class:`AuthProvider` instances.

The :class:`zelosmcp.manager.ProxyManager` owns one registry,
populated when the providers config (``configs/auth-providers.json``)
loads at startup or via ``POST /api/auth/providers/config``. Backends
look up their provider by name via
:meth:`AuthRegistry.get_for_backend`; the aggregator iterates over
:meth:`AuthRegistry.values` for the Connections UI listing.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator

from zelosmcp.auth.protocol import AuthProvider


class AuthRegistry:
    """Name-keyed container of provider instances.

    Mutation API (``register`` / ``unregister`` / ``replace_all``) is
    intentionally narrow — provider instances are meant to be
    constructed in the config-loading path and only swapped wholesale
    when the providers file is reloaded. Per-key removal exists for
    the future-tense GUI "delete provider" affordance but isn't wired
    up yet.

    Not thread-safe in the strict sense; callers from async code
    should hold an asyncio.Lock when racing against ``replace_all``.
    The current single-call-site usage (manager startup +
    config-replace endpoint) doesn't race so we keep the
    implementation lock-free for clarity.
    """

    def __init__(self) -> None:
        self._providers: dict[str, AuthProvider] = {}

    def register(self, provider: AuthProvider) -> None:
        """Add a provider; raises ``ValueError`` on duplicate name so
        config bugs surface immediately rather than silently
        clobbering an earlier entry.
        """
        name = provider.name
        if name in self._providers:
            raise ValueError(
                f"AuthRegistry already contains a provider named '{name}'"
            )
        self._providers[name] = provider

    def unregister(self, name: str) -> AuthProvider | None:
        """Drop a provider by name; returns the removed instance or
        ``None`` if the name wasn't registered."""
        return self._providers.pop(name, None)

    def replace_all(self, providers: Iterable[AuthProvider]) -> None:
        """Atomic swap — used by ``POST /api/auth/providers/config``.

        Builds the new dict first so a duplicate-name in the
        replacement set raises before the old set is dropped.
        """
        replacement: dict[str, AuthProvider] = {}
        for provider in providers:
            name = provider.name
            if name in replacement:
                raise ValueError(
                    f"duplicate provider name in replacement set: '{name}'"
                )
            replacement[name] = provider
        self._providers = replacement

    def get(self, name: str) -> AuthProvider | None:
        """Return the provider registered under ``name``, or ``None``.

        Used by the aggregator's gating logic and the per-backend
        ``mint_token`` lookup. Callers handle the ``None`` case
        (typically: skip the backend or fall back to passthrough).
        """
        return self._providers.get(name)

    def get_for_backend(
        self, backend_name: str, configured_provider: str | None
    ) -> AuthProvider | None:
        """Resolve the provider a backend should use.

        ``configured_provider`` is whatever the backend's
        ``auth.provider`` field said (``None`` for legacy backends
        with no auth config). Returns ``None`` for either case
        (legacy backend OR config error) — the caller decides how
        strict to be. The aggregator treats ``None`` as "no gating,
        forward verbatim"; the manager validates references
        eagerly at config load so production runs never hit the
        config-error path here.
        """
        if not configured_provider:
            return None
        return self._providers.get(configured_provider)

    def names(self) -> tuple[str, ...]:
        """Sorted tuple of registered provider names. Stable iteration
        order is useful for status payloads and UI rendering."""
        return tuple(sorted(self._providers))

    def values(self) -> Iterator[AuthProvider]:
        """Iterate registered providers in name-sorted order."""
        for name in sorted(self._providers):
            yield self._providers[name]

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._providers
