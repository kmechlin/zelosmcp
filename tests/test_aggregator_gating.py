"""PR 3 tests: aggregator gating of passthrough wrappers based on
:class:`AuthProvider.is_ready`.

Three scenarios to cover (per the plan):

1. Backend has no ``auth.provider`` configured → wrappers always
   visible (legacy passthrough behaviour, unchanged).
2. Backend has ``auth.provider`` set + provider says ``is_ready =
   True`` → wrappers visible.
3. Backend has ``auth.provider`` set + provider says ``is_ready =
   False`` → wrappers HIDDEN. The backend is invisible in
   ``tools/list`` from Cursor's POV until the user authenticates
   in the GUI.

A defensive fourth scenario: provider's ``is_ready`` raises →
treat as not-ready, hide wrappers, log the error. We don't want a
buggy provider to leak wrappers to Cursor and fail later.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mcp.types import Tool

from zelosmcp.aggregator import Aggregator
from zelosmcp.auth import PassthroughProvider
from zelosmcp.config import CompressSpec, ServerSpec
from zelosmcp.manager import ProxyManager
from zelosmcp.proxy import ProxyState


# Local copies of the test helpers from test_aggregator_unit so we
# don't introduce a cross-file dependency. Slightly different shape
# (we need passthrough-mode states, not session-bound ones).


def _tool(name: str) -> Tool:
    return Tool(
        name=name,
        description=f"Tool {name}",
        inputSchema={"type": "object", "properties": {}},
    )


def _passthrough_state(
    name: str, *, cached_tools: list[Tool] | None = None
) -> ProxyState:
    """Construct a ProxyState wired into passthrough mode with a
    pre-warmed catalog so the aggregator's tools/list path sees the
    backend as a candidate for wrapper emission."""
    state = ProxyState(name=name)
    state.running = True
    state.is_passthrough = True
    # Sentinel-truthy passthrough_pool — the aggregator's
    # `_passthrough_states()` only checks for non-None.
    state.passthrough_pool = object()
    state.passthrough_catalog = {
        t.name: t for t in (cached_tools or [_tool("search"), _tool("read")])
    }
    return state


def _spec(
    name: str,
    *,
    auth_provider: str | None = None,
    compress: CompressSpec | None = None,
) -> ServerSpec:
    return ServerSpec(
        name=name,
        transport="http",
        url=f"https://{name}.example.com/mcp",
        passthrough=True,
        auth_provider=auth_provider,
        compress=compress or CompressSpec(level="medium", scope="aggregator"),
    )


def _find_handler(server, name_substring: str):
    """Pull the registered handler whose request-class name contains
    ``name_substring`` (the SDK name-mangles handler names so we can't
    look up by attribute)."""
    for cls, handler in server.request_handlers.items():
        if name_substring in cls.__name__:
            return handler
    raise KeyError(f"no handler matching {name_substring!r}")


def _register(agg: Aggregator):
    """Register handlers on a fresh Server so we can invoke list_tools
    without the ASGI lifespan dance."""
    from mcp.server.lowlevel.server import Server

    server = Server("test")
    agg._register_handlers(server)
    return server


def _setup(
    *,
    auth_provider_name: str | None,
    provider_instance=None,
    cached_tools: list[Tool] | None = None,
):
    """Build a manager + aggregator with one passthrough backend.

    ``auth_provider_name`` is the value to put in the backend spec's
    ``auth_provider`` field (None = legacy unconfigured). When set,
    ``provider_instance`` is registered in the auth registry under
    that same name so cross-resolution works.
    """
    manager = ProxyManager(mandatory_config_path="")
    state = _passthrough_state("github", cached_tools=cached_tools)
    manager.servers["github"] = state
    manager._specs["github"] = _spec(
        "github", auth_provider=auth_provider_name,
    )
    if auth_provider_name and provider_instance is not None:
        manager.auth_registry.register(provider_instance)
    agg = Aggregator(manager)
    server = _register(agg)
    return manager, agg, server, state


# ── Scenario 1: no provider configured → no gating ─────────────────────


class TestNoProviderConfigured:
    @pytest.mark.asyncio
    async def test_wrappers_visible_when_no_provider(self):
        # Legacy passthrough mode: no auth.provider in the spec, no
        # entry in the auth registry. Behaviour preserved from before
        # PR 3 — wrappers always emitted.
        _, _, server, _ = _setup(auth_provider_name=None)
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = sorted(t.name for t in result.root.tools)
        assert names == ["github__get_tool_schema", "github__invoke_tool"]


# ── Scenario 2: provider configured + ready → wrappers visible ─────────


class TestProviderReady:
    @pytest.mark.asyncio
    async def test_wrappers_visible_when_provider_ready(self):
        # PassthroughProvider always returns is_ready=True. With this
        # provider attached, gating becomes a no-op so the wrappers
        # show up.
        _, _, server, _ = _setup(
            auth_provider_name="legacy_passthrough",
            provider_instance=PassthroughProvider("legacy_passthrough"),
        )
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = sorted(t.name for t in result.root.tools)
        assert names == ["github__get_tool_schema", "github__invoke_tool"]


# ── Scenario 3: provider configured + not-ready → wrappers hidden ──────


class TestProviderNotReady:
    @pytest.mark.asyncio
    async def test_wrappers_hidden_when_provider_not_ready(self):
        not_ready = PassthroughProvider("github_oauth_app")
        # Override is_ready to return False — simulates "user has not
        # completed the device flow yet".
        not_ready.is_ready = AsyncMock(return_value=False)

        _, agg, server, _ = _setup(
            auth_provider_name="github_oauth_app",
            provider_instance=not_ready,
        )
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = [t.name for t in result.root.tools]
        # Backend is GATED — no entry for it in tools/list at all.
        # Cursor sees no github__* tools until the user authenticates.
        assert "github__invoke_tool" not in names
        assert "github__get_tool_schema" not in names

    @pytest.mark.asyncio
    async def test_other_backends_unaffected_by_one_gated_backend(self):
        # Multi-backend scenario: one gated, one always-visible. The
        # gated one drops out, the other one stays.
        manager = ProxyManager(mandatory_config_path="")

        gated_state = _passthrough_state("github")
        manager.servers["github"] = gated_state
        manager._specs["github"] = _spec(
            "github", auth_provider="github_oauth_app",
        )
        not_ready = PassthroughProvider("github_oauth_app")
        not_ready.is_ready = AsyncMock(return_value=False)
        manager.auth_registry.register(not_ready)

        legacy_state = _passthrough_state("legacy")
        manager.servers["legacy"] = legacy_state
        manager._specs["legacy"] = _spec("legacy", auth_provider=None)

        agg = Aggregator(manager)
        server = _register(agg)

        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = sorted(t.name for t in result.root.tools)
        # Only the legacy backend's wrappers survive; github is gated.
        assert names == ["legacy__get_tool_schema", "legacy__invoke_tool"]


# ── Defensive: provider raises → treat as not-ready ────────────────────


class TestProviderRaises:
    @pytest.mark.asyncio
    async def test_wrappers_hidden_when_provider_is_ready_raises(self):
        # A bug in the provider implementation (e.g. corrupted store,
        # network blip during JWT verification) shouldn't leak
        # wrappers to Cursor — fail closed.
        broken = PassthroughProvider("broken_provider")
        broken.is_ready = AsyncMock(side_effect=RuntimeError("simulated"))

        _, _, server, _ = _setup(
            auth_provider_name="broken_provider",
            provider_instance=broken,
        )
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = [t.name for t in result.root.tools]
        assert "github__invoke_tool" not in names
        assert "github__get_tool_schema" not in names


# ── Provider in spec but not in registry → treat as not-configured ─────


class TestProviderRegistryMiss:
    @pytest.mark.asyncio
    async def test_unknown_provider_treated_as_no_gating(self):
        # If the spec references a provider that isn't in the
        # registry (e.g. providers config swap dropped it), we let
        # the wrappers through rather than hiding them. The spec
        # validator should have caught this at config-load time;
        # this is the defensive fallback for runtime drift. The
        # alternative (silently gate) would surprise users with
        # disappearing tools.
        _, _, server, _ = _setup(
            auth_provider_name="missing_from_registry",
            provider_instance=None,  # NOT registered.
        )
        handler = _find_handler(server, "ListToolsRequest")
        result = await handler(None)
        names = sorted(t.name for t in result.root.tools)
        assert names == ["github__get_tool_schema", "github__invoke_tool"]
