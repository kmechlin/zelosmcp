"""Integration tests for the manager's auth-providers wiring.

Exercises the cross-cutting paths the per-module unit tests can't
cover on their own:

- :meth:`ProxyManager.start_auth_providers` parses + builds + swaps
  the registry atomically.
- :meth:`ProxyManager.start_all` cross-validates server references
  against currently-loaded providers and refuses to start when
  references dangle.
- ``GET`` and ``POST /api/auth/providers/config`` HTTP routes round-trip
  through the manager and surface the redacted spec view.
- Lifespan auto-load reads ``ZELOSMCP_AUTH_PROVIDERS_FILE`` (or the
  default path) and populates the registry before the first request.

Tests use the in-memory auth store so nothing touches the on-disk
``~/.zelosmcp/auth.sqlite``.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from zelosmcp.app import create_app
from zelosmcp.config import ConfigError
from zelosmcp.manager import ProxyManager


def _fresh():
    manager = ProxyManager(mandatory_config_path="")
    app = create_app(manager)
    return app, manager


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@asynccontextmanager
async def _lifespan(app):
    """Drive Starlette's ASGI lifespan protocol manually.

    Mirrors the helper in :mod:`tests.test_app_integration` —
    httpx's ASGITransport doesn't run lifespan events on its own,
    so any test that needs the lifespan to fire (e.g. autoload of
    auth providers, savings store init) wraps the request block
    in this context manager.
    """
    queue: asyncio.Queue = asyncio.Queue()
    sent: list = []

    async def receive():
        return await queue.get()

    async def send(msg):
        sent.append(msg)

    task = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    await queue.put({"type": "lifespan.startup"})
    for _ in range(100):
        if any(m.get("type") == "lifespan.startup.complete" for m in sent):
            break
        await asyncio.sleep(0.02)
    else:
        raise RuntimeError("lifespan startup did not complete in 2s")
    try:
        yield
    finally:
        await queue.put({"type": "lifespan.shutdown"})
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()


# ── start_auth_providers ────────────────────────────────────────────────


class TestManagerStartAuthProviders:
    @pytest.mark.asyncio
    async def test_loads_legacy_provider_types(self):
        manager = ProxyManager(mandatory_config_path="")
        result = await manager.start_auth_providers({
            "providers": {
                "passthrough_legacy": {"type": "passthrough"},
                "ci_pat": {"type": "static", "bearer": "ghp_xxx"},
            }
        })
        assert result["providers"]["passthrough_legacy"] == "ready"
        assert result["providers"]["ci_pat"] == "ready"
        assert "passthrough_legacy" in manager.auth_registry
        assert "ci_pat" in manager.auth_registry

    @pytest.mark.asyncio
    async def test_provider_without_open_store_marked_unavailable(self):
        # The github factory needs the encrypted auth store. When the
        # manager loads providers BEFORE start_auth_store has run,
        # the factory raises ProviderTypeUnavailable and the provider
        # surfaces as "unavailable" rather than crashing the load.
        manager = ProxyManager(mandatory_config_path="")
        result = await manager.start_auth_providers({
            "providers": {
                "gh": {
                    "type": "github_device_flow",
                    "client_id": "Iv1.x",
                }
            }
        })
        assert result["providers"]["gh"] == "unavailable"
        assert "gh" not in manager.auth_registry

    async def test_provider_with_open_store_loads_successfully(self):
        # With the auth store open, the github provider constructs
        # cleanly and ends up in the registry.
        manager = ProxyManager(mandatory_config_path="")
        await manager.start_auth_store(db_path=":memory:")
        try:
            result = await manager.start_auth_providers({
                "providers": {
                    "gh": {
                        "type": "github_device_flow",
                        "client_id": "Iv1.x",
                    }
                }
            })
            assert result["providers"]["gh"] == "ready"
            assert "gh" in manager.auth_registry
        finally:
            await manager.stop_auth_store()

    @pytest.mark.asyncio
    async def test_swap_replaces_old_set(self):
        manager = ProxyManager(mandatory_config_path="")
        await manager.start_auth_providers({
            "providers": {
                "old": {"type": "passthrough"}
            }
        })
        assert "old" in manager.auth_registry

        await manager.start_auth_providers({
            "providers": {
                "new": {"type": "passthrough"}
            }
        })
        assert "old" not in manager.auth_registry
        assert "new" in manager.auth_registry

    @pytest.mark.asyncio
    async def test_invalid_config_leaves_registry_intact(self):
        manager = ProxyManager(mandatory_config_path="")
        await manager.start_auth_providers({
            "providers": {
                "keep": {"type": "passthrough"}
            }
        })
        with pytest.raises(ConfigError):
            await manager.start_auth_providers({
                "providers": {
                    "bad": {"type": "made_up"}
                }
            })
        # The successfully-loaded set should survive a failed swap.
        assert "keep" in manager.auth_registry

    @pytest.mark.asyncio
    async def test_dropping_referenced_provider_raises(self):
        # Servers reference provider X; new providers config drops X.
        # Manager should refuse the swap so a live deployment can't
        # silently lose the provider its backends depend on.
        manager = ProxyManager(mandatory_config_path="")
        await manager.start_auth_providers({
            "providers": {
                "gh_provider": {"type": "passthrough"}
            }
        })
        # Manually register a server spec referencing it (we don't
        # need to actually start the backend for this test).
        from zelosmcp.config import ServerSpec
        manager._specs = {
            "github": ServerSpec(
                name="github",
                transport="http",
                url="https://x/mcp",
                passthrough=True,
                auth_provider="gh_provider",
            ),
        }

        with pytest.raises(ConfigError, match="gh_provider"):
            await manager.start_auth_providers({
                "providers": {
                    "different_provider": {"type": "passthrough"}
                }
            })

    @pytest.mark.asyncio
    async def test_current_config_redacts_secrets(self):
        manager = ProxyManager(mandatory_config_path="")
        await manager.start_auth_providers({
            "providers": {
                "ci_pat": {"type": "static", "bearer": "ghp_secret"}
            }
        })
        snapshot = manager.current_auth_providers_config()
        assert snapshot["providers"]["ci_pat"]["bearer"] == "***"

    @pytest.mark.asyncio
    async def test_current_config_unredacted_for_tests(self):
        manager = ProxyManager(mandatory_config_path="")
        await manager.start_auth_providers({
            "providers": {
                "ci_pat": {"type": "static", "bearer": "ghp_secret"}
            }
        })
        snapshot = manager.current_auth_providers_config(redacted=False)
        assert snapshot["providers"]["ci_pat"]["bearer"] == "ghp_secret"


# ── start_all cross-validation ──────────────────────────────────────────


class TestStartAllValidatesProviderReferences:
    @pytest.mark.asyncio
    async def test_dangling_provider_in_server_config_raises(self):
        # Server references a provider that's not in the registry —
        # start_all parses the config, calls validate_provider_references,
        # which raises ConfigError before any backend starts.
        manager = ProxyManager(mandatory_config_path="")
        with pytest.raises(ConfigError, match="missing_provider"):
            await manager.start_all({
                "mcpServers": {
                    "github": {
                        "type": "streamable-http",
                        "url": "https://api.githubcopilot.com/mcp/",
                        "passthrough": True,
                        "auth": {"provider": "missing_provider"},
                    }
                }
            })


# ── HTTP routes ─────────────────────────────────────────────────────────


class TestAuthProvidersConfigHTTP:
    @pytest.mark.asyncio
    async def test_get_empty_initially(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.get("/api/auth/providers/config")
        assert r.status_code == 200
        assert r.json() == {"providers": {}}

    @pytest.mark.asyncio
    async def test_post_then_get_roundtrip(self):
        app, manager = _fresh()
        payload = {
            "providers": {
                "passthrough_legacy": {"type": "passthrough"},
                "ci_pat": {"type": "static", "bearer": "ghp_secret"},
            }
        }
        async with _client(app) as c:
            r = await c.post(
                "/api/auth/providers/config", json=payload,
            )
            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is True
            assert body["providers"]["passthrough_legacy"] == "ready"

            r = await c.get("/api/auth/providers/config")
        assert r.status_code == 200
        snapshot = r.json()
        assert "passthrough_legacy" in snapshot["providers"]
        assert snapshot["providers"]["ci_pat"]["bearer"] == "***"

    @pytest.mark.asyncio
    async def test_post_invalid_json_returns_400(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post(
                "/api/auth/providers/config",
                content="{not json",
                headers={"content-type": "application/json"},
            )
        assert r.status_code == 400
        assert r.json()["ok"] is False

    @pytest.mark.asyncio
    async def test_post_invalid_schema_returns_400(self):
        app, _ = _fresh()
        async with _client(app) as c:
            r = await c.post(
                "/api/auth/providers/config",
                json={"providers": {"x": {"type": "made_up_type"}}},
            )
        assert r.status_code == 400
        body = r.json()
        assert body["ok"] is False
        assert "must be one of" in body["error"]


# ── Auto-load at startup ────────────────────────────────────────────────


class TestAutoloadAuthProviders:
    @pytest.mark.asyncio
    async def test_loads_from_env_var_path(self, tmp_path, monkeypatch):
        providers_file = tmp_path / "providers.json"
        providers_file.write_text(json.dumps({
            "providers": {
                "boot_load": {"type": "passthrough"}
            }
        }))
        monkeypatch.setenv(
            "ZELOSMCP_AUTH_PROVIDERS_FILE", str(providers_file)
        )

        app, manager = _fresh()
        async with _lifespan(app):
            # Lifespan startup completes before yield; registry is
            # populated by the time we make any request.
            async with _client(app) as c:
                r = await c.get("/api/status")
            assert r.status_code == 200
            assert "boot_load" in manager.auth_registry

    @pytest.mark.asyncio
    async def test_missing_file_is_fine(self, tmp_path, monkeypatch):
        # Pointing at a non-existent file logs a warning but doesn't
        # fail the lifespan — empty providers config is a valid
        # legacy-passthrough-only deployment.
        monkeypatch.setenv(
            "ZELOSMCP_AUTH_PROVIDERS_FILE",
            str(tmp_path / "does-not-exist.json"),
        )
        app, manager = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                r = await c.get("/api/status")
            assert r.status_code == 200
            assert len(manager.auth_registry) == 0

    @pytest.mark.asyncio
    async def test_malformed_file_does_not_crash(
        self, tmp_path, monkeypatch
    ):
        bad = tmp_path / "providers.json"
        bad.write_text("{ this is not json")
        monkeypatch.setenv("ZELOSMCP_AUTH_PROVIDERS_FILE", str(bad))

        app, manager = _fresh()
        async with _lifespan(app):
            async with _client(app) as c:
                r = await c.get("/api/status")
            assert r.status_code == 200
            assert len(manager.auth_registry) == 0
