"""Unit tests for localmcp.manager.ProxyManager."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from localmcp.manager import ProxyManager
from tests.conftest import (
    fake_stdio_client,
    fake_sse_client,
    fake_http_client,
    make_mock_session,
)


def _patches():
    mock_session = make_mock_session()

    @asynccontextmanager
    async def patched_client_session(read, write):
        yield mock_session

    @asynccontextmanager
    async def patched_run(self):
        yield

    return [
        patch("localmcp.proxy.stdio_client", side_effect=fake_stdio_client),
        patch("localmcp.proxy.sse_client", side_effect=fake_sse_client),
        patch("localmcp.proxy.streamablehttp_client", side_effect=fake_http_client),
        patch("localmcp.proxy.ClientSession", side_effect=patched_client_session),
        patch("localmcp.proxy.StreamableHTTPSessionManager.run", patched_run),
    ]


_CONFIG = {
    "mcpServers": {
        "alpha": {"command": "echo", "args": ["a"]},
        "beta":  {"type": "sse", "url": "http://x/sse"},
        "gamma": {"type": "streamable-http", "url": "http://x/mcp"},
    },
}


class TestStartAll:
    @pytest.mark.asyncio
    async def test_starts_every_server(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            result = await m.start_all(_CONFIG)
            assert result["primary"] is None
            assert set(result["servers"].keys()) == {"alpha", "beta", "gamma"}
            assert all(r["ok"] for r in result["servers"].values())
            assert m.primary is None
            assert m.aggregator.running is True
            for name in ("alpha", "beta", "gamma"):
                assert m.get(name).running is True
            await m.stop_all()
            assert m.aggregator.running is False

    @pytest.mark.asyncio
    async def test_primarymcp_is_deprecated_but_accepted(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            q = m.subscribe_logs()
            await m.start_all({
                "primaryMCP": "alpha",
                "mcpServers": {"alpha": {"command": "echo"}},
            })
            assert m.primary is None
            seen = []
            try:
                while True:
                    seen.append(q.get_nowait())
            except asyncio.QueueEmpty:
                pass
            assert any("primaryMCP is deprecated" in line for line in seen)
            await m.stop_all()
            m.unsubscribe_logs(q)

    @pytest.mark.asyncio
    async def test_replaces_existing_servers(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            await m.start_all(_CONFIG)
            await m.start_all({"mcpServers": {"only": {"command": "echo"}}})
            # `localmcp` is the always-on builtin; it lives alongside any
            # user-configured backends and survives start_all/stop_all.
            user_names = [n for n in m.names() if n != "localmcp"]
            assert user_names == ["only"]
            assert m.primary is None
            await m.stop_all()

    @pytest.mark.asyncio
    async def test_failure_in_one_does_not_stop_others(self):
        good = make_mock_session()

        @asynccontextmanager
        async def patched_client_session(read, write):
            yield good

        @asynccontextmanager
        async def patched_run(self):
            yield

        @asynccontextmanager
        async def failing_sse(url, *a, **kw):
            raise ConnectionError("sse boom")
            yield  # pragma: no cover

        with (
            patch("localmcp.proxy.stdio_client", side_effect=fake_stdio_client),
            patch("localmcp.proxy.sse_client", side_effect=failing_sse),
            patch("localmcp.proxy.ClientSession", side_effect=patched_client_session),
            patch("localmcp.proxy.StreamableHTTPSessionManager.run", patched_run),
        ):
            m = ProxyManager()
            result = await m.start_all({
                "mcpServers": {
                    "ok":  {"command": "echo"},
                    "bad": {"type": "sse", "url": "http://x/sse"},
                }
            })
            assert result["servers"]["ok"]["ok"] is True
            assert result["servers"]["bad"]["ok"] is False
            assert "boom" in result["servers"]["bad"]["error"]
            assert m.get("ok").running is True
            assert m.get("bad").running is False
            await m.stop_all()


class TestPerServer:
    @pytest.mark.asyncio
    async def test_stop_one_then_start_one(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            await m.start_all(_CONFIG)
            await m.stop_one("beta")
            assert m.get("beta").running is False
            await m.start_one("beta")
            assert m.get("beta").running is True
            await m.stop_all()

    @pytest.mark.asyncio
    async def test_unknown_name_raises(self):
        m = ProxyManager()
        with pytest.raises(KeyError):
            await m.start_one("ghost")
        with pytest.raises(KeyError):
            await m.stop_one("ghost")

    @pytest.mark.asyncio
    async def test_start_one_when_already_running_raises(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            await m.start_all(_CONFIG)
            with pytest.raises(RuntimeError, match="already running"):
                await m.start_one("alpha")
            await m.stop_all()


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_when_empty(self):
        m = ProxyManager()
        s = m.status()
        # `localmcp` is the always-on builtin; until its lifespan-managed
        # `start_builtin()` has run, `state.running` is still False, but the
        # row exists so the UI can render the slot.
        assert s["primary"] is None
        assert s["running"] is False
        assert [row["name"] for row in s["servers"]] == ["localmcp"]
        assert s["servers"][0]["builtin"] is True
        assert s["servers"][0]["running"] is False

    @pytest.mark.asyncio
    async def test_status_running(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            await m.start_all(_CONFIG)
            s = m.status()
            assert s["primary"] is None
            assert s["running"] is True
            by_name = {srv["name"]: srv for srv in s["servers"]}
            for name in ("alpha", "beta", "gamma"):
                assert by_name[name]["primary"] is False
            assert by_name["alpha"]["transport"] == "stdio"
            assert by_name["beta"]["transport"] == "sse"
            assert by_name["gamma"]["transport"] == "http"
            await m.stop_all()


class TestLogAggregation:
    @pytest.mark.asyncio
    async def test_logs_from_children_are_broadcast(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            q = m.subscribe_logs()
            await m.start_all({"mcpServers": {"alpha": {"command": "echo"}}})

            # Drain a few messages, looking for the [alpha] tag.
            seen = []
            try:
                while True:
                    msg = await asyncio.wait_for(q.get(), timeout=0.1)
                    seen.append(msg)
            except asyncio.TimeoutError:
                pass
            assert any("[alpha]" in s for s in seen)
            await m.stop_all()
            m.unsubscribe_logs(q)

    def test_unsubscribe_unknown_is_noop(self):
        m = ProxyManager()
        q = asyncio.Queue()
        m.unsubscribe_logs(q)


class TestReverseProxy:
    """Lookup behavior of ProxyManager.find_reverse_proxy.

    The httpx client and proxy_request integration is exercised via the
    end-to-end tests in test_app_integration.py. This block only covers
    the path-matching / longest-prefix logic.
    """

    @pytest.mark.asyncio
    async def test_find_returns_running_state(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            await m.start_all({
                "mcpServers": {
                    "alpha": {
                        "command": "echo",
                        "reverseProxy": {
                            "mount": "/alpha",
                            "upstream": "http://127.0.0.1:9000",
                        },
                    },
                },
            })
            match = m.find_reverse_proxy("/alpha/v1/health")
            assert match is not None
            spec, state = match
            assert spec.name == "alpha"
            assert state is m.get("alpha")
            assert state.running is True
            await m.stop_all()

    @pytest.mark.asyncio
    async def test_find_returns_none_for_no_match(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            await m.start_all({
                "mcpServers": {
                    "alpha": {
                        "command": "echo",
                        "reverseProxy": {
                            "mount": "/alpha",
                            "upstream": "http://127.0.0.1:9000",
                        },
                    },
                },
            })
            assert m.find_reverse_proxy("/beta/v1/health") is None
            assert m.find_reverse_proxy("/api/status") is None
            await m.stop_all()

    @pytest.mark.asyncio
    async def test_find_skips_backends_without_proxy(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            await m.start_all({
                "mcpServers": {
                    "alpha": {"command": "echo"},
                },
            })
            assert m.find_reverse_proxy("/alpha/x") is None
            await m.stop_all()

    @pytest.mark.asyncio
    async def test_segment_aware_no_false_positive(self):
        """``/foo`` must not match ``/foobar/...``."""
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            await m.start_all({
                "mcpServers": {
                    "foo": {
                        "command": "echo",
                        "reverseProxy": {
                            "mount": "/foo",
                            "upstream": "http://127.0.0.1:9000",
                        },
                    },
                },
            })
            assert m.find_reverse_proxy("/foobar/v1") is None
            assert m.find_reverse_proxy("/foo") is not None
            assert m.find_reverse_proxy("/foo/anything") is not None
            await m.stop_all()

    @pytest.mark.asyncio
    async def test_status_includes_reverse_proxy(self):
        with _patches()[0], _patches()[1], _patches()[2], _patches()[3], _patches()[4]:
            m = ProxyManager()
            await m.start_all({
                "mcpServers": {
                    "alpha": {
                        "command": "echo",
                        "reverseProxy": {
                            "mount": "/alpha",
                            "upstream": "http://127.0.0.1:9000",
                        },
                    },
                },
            })
            status = m.status()
            row = next(s for s in status["servers"] if s["name"] == "alpha")
            assert row["spec"]["reverseProxy"] == {
                "mount": "/alpha",
                "upstream": "http://127.0.0.1:9000",
            }
            await m.stop_all()

    @pytest.mark.asyncio
    async def test_http_client_lifecycle_idempotent(self):
        m = ProxyManager()
        # Idempotent: calling start twice doesn't reinitialise.
        await m.start_http_client()
        first = m._http_client
        await m.start_http_client()
        assert m._http_client is first
        # Idempotent: calling stop twice is fine.
        await m.stop_http_client()
        assert m._http_client is None
        await m.stop_http_client()
        assert m._http_client is None
