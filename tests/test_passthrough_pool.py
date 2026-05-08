"""Unit tests for :class:`PassthroughSessionPool`.

Stubs the upstream connection (``streamablehttp_client`` +
``ClientSession``) so eviction, coalescing, and challenge-propagation
behaviour can be exercised without a real network. Real-network coverage
lives in :mod:`tests.test_oauth_passthrough_integration`.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zelosmcp.passthrough_pool import (
    PassthroughChallengeError,
    PassthroughSessionPool,
    _synthesize_www_authenticate,
    hash_authorization,
)


# ── Fake upstream ───────────────────────────────────────────────────────


class _FakeSession:
    """Stand-in for :class:`mcp.client.session.ClientSession`."""

    _counter = 0

    def __init__(self, label: str = "session") -> None:
        type(self)._counter += 1
        self.id = self._counter
        self.label = label
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def aclose(self) -> None:
        self.closed = True


def _patch_streams(*, raise_on_init: bool = False, raise_on_connect: bool = False):
    """Patches that produce a fresh ``_FakeSession`` per session.

    ``raise_on_init=True`` makes the session's ``initialize()`` call
    raise an exception with a fake httpx response carrying a
    WWW-Authenticate header — used to verify challenge extraction.

    ``raise_on_connect=True`` makes the streamablehttp_client itself
    raise — the pool must still synthesise a challenge.

    Always patches the upstream auth probe to a no-op (``(None,
    None)``) so the SDK fakes are reached without a real network
    roundtrip. Tests that exercise the probe path patch
    ``_probe_upstream_auth`` themselves.
    """

    async def fake_probe(_url, _auth, **_kwargs):
        return None, None

    @asynccontextmanager
    async def fake_streamablehttp_client(url: str, *, headers=None, **_kwargs):
        if raise_on_connect:
            response = MagicMock()
            response.headers = {
                "WWW-Authenticate": (
                    'Bearer resource_metadata='
                    '"https://api.example.com/.well-known/oauth-protected-resource"'
                ),
            }
            err = RuntimeError("upstream auth required")
            err.response = response  # type: ignore[attr-defined]
            raise err
        read = MagicMock()
        write = MagicMock()
        get_session_id = MagicMock()
        yield read, write, get_session_id

    @asynccontextmanager
    async def fake_client_session(read, write):
        s = _FakeSession()
        if raise_on_init:
            response = MagicMock()
            response.headers = {
                "WWW-Authenticate": "Bearer error=\"invalid_token\"",
            }
            err = RuntimeError("init failed: 401")
            err.response = response  # type: ignore[attr-defined]
            s.initialize = AsyncMock(side_effect=err)
        yield s

    return [
        patch(
            "zelosmcp.passthrough_pool._probe_upstream_auth",
            side_effect=fake_probe,
        ),
        patch(
            "zelosmcp.passthrough_pool.streamablehttp_client",
            side_effect=fake_streamablehttp_client,
        ),
        patch(
            "zelosmcp.passthrough_pool.ClientSession",
            side_effect=fake_client_session,
        ),
    ]


# ── Hash helper ─────────────────────────────────────────────────────────


class TestHashAuthorization:
    def test_none_maps_to_anonymous(self):
        assert hash_authorization(None) == "anonymous"

    def test_empty_maps_to_anonymous(self):
        assert hash_authorization("") == "anonymous"

    def test_same_token_produces_same_hash(self):
        assert hash_authorization("Bearer xyz") == hash_authorization("Bearer xyz")

    def test_different_tokens_produce_different_hashes(self):
        assert hash_authorization("Bearer a") != hash_authorization("Bearer b")

    def test_hash_length(self):
        # 32 hex chars => 128 bits — plenty for a dict key.
        assert len(hash_authorization("Bearer x")) == 32


# ── Pool: basic lifecycle ───────────────────────────────────────────────


class TestPoolLifecycle:
    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=4,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        await pool.start()
        first = pool._sweeper
        await pool.start()
        # Second call is a no-op; same task object.
        assert pool._sweeper is first
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_close_all_closes_active_sessions(self):
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=4,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        for p in _patch_streams():
            p.start()
        try:
            await pool.start()
            session_a = await pool.get_or_create("Bearer A")
            session_b = await pool.get_or_create("Bearer B")
            await pool.close_all()
            # The pool drops references; we can't assert .closed
            # on the FakeSession because it doesn't intercept the
            # AsyncExitStack close path. But we CAN assert that the
            # pool is empty.
            assert len(pool._entries) == 0
            assert pool._sweeper is None
        finally:
            for p in _patch_streams():
                p.stop()

    @pytest.mark.asyncio
    async def test_close_all_idempotent(self):
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=2,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        await pool.close_all()
        await pool.close_all()  # Must not raise.

    @pytest.mark.asyncio
    async def test_get_or_create_after_close_raises(self):
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=2,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        await pool.start()
        await pool.close_all()
        with pytest.raises(RuntimeError, match="closed"):
            await pool.get_or_create("Bearer x")

    @pytest.mark.asyncio
    async def test_init_validates_positive_caps(self):
        with pytest.raises(ValueError, match="max_sessions"):
            PassthroughSessionPool(
                backend_name="x",
                upstream_url="http://x",
                max_sessions=0,
                idle_ttl_seconds=10,
            )
        with pytest.raises(ValueError, match="idle_ttl_seconds"):
            PassthroughSessionPool(
                backend_name="x",
                upstream_url="http://x",
                max_sessions=1,
                idle_ttl_seconds=0,
            )


# ── Caching, LRU, TTL ───────────────────────────────────────────────────


class TestPoolCaching:
    @pytest.mark.asyncio
    async def test_same_token_reuses_session(self):
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=4,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        for p in _patch_streams():
            p.start()
        try:
            s1 = await pool.get_or_create("Bearer same")
            s2 = await pool.get_or_create("Bearer same")
            assert s1 is s2
            assert len(pool._entries) == 1
        finally:
            await pool.close_all()
            for p in _patch_streams():
                p.stop()

    @pytest.mark.asyncio
    async def test_different_tokens_get_different_sessions(self):
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=4,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        for p in _patch_streams():
            p.start()
        try:
            s1 = await pool.get_or_create("Bearer A")
            s2 = await pool.get_or_create("Bearer B")
            assert s1 is not s2
            assert len(pool._entries) == 2
        finally:
            await pool.close_all()
            for p in _patch_streams():
                p.stop()

    @pytest.mark.asyncio
    async def test_lru_eviction(self):
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=2,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        evicted = []
        pool._log = lambda m: evicted.append(m) if "evicted" in m else None
        for p in _patch_streams():
            p.start()
        try:
            await pool.get_or_create("Bearer A")
            await pool.get_or_create("Bearer B")
            assert len(pool._entries) == 2
            # Access A again to make it MRU; B becomes LRU.
            await pool.get_or_create("Bearer A")
            await pool.get_or_create("Bearer C")
            # B should have been evicted.
            assert len(pool._entries) == 2
            # Hash check: B's key shouldn't be in the dict anymore.
            key_a = hash_authorization("Bearer A")
            key_b = hash_authorization("Bearer B")
            key_c = hash_authorization("Bearer C")
            assert key_a in pool._entries
            assert key_c in pool._entries
            assert key_b not in pool._entries
            assert any("evicted" in m for m in evicted)
        finally:
            await pool.close_all()
            for p in _patch_streams():
                p.stop()

    @pytest.mark.asyncio
    async def test_ttl_eviction(self):
        # Tiny TTL + tiny sweep interval so we exercise the sweeper
        # without slowing the test suite. The asyncio.sleep below is
        # the smallest reliable amount that lets one sweep cycle run.
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=4,
            idle_ttl_seconds=1,
            sweep_interval=0.05,
        )
        for p in _patch_streams():
            p.start()
        try:
            await pool.start()
            await pool.get_or_create("Bearer A")
            assert len(pool._entries) == 1
            # Wait a bit longer than idle_ttl_seconds + one sweep
            # interval. Two sweep cycles is enough to age out the
            # entry without blowing the test runtime.
            await asyncio.sleep(1.2)
            assert len(pool._entries) == 0
        finally:
            await pool.close_all()
            for p in _patch_streams():
                p.stop()

    @pytest.mark.asyncio
    async def test_lru_self_evict_guard(self):
        """If the cap is 1 and a fresh insert tries to evict itself
        (degenerate edge case), the entry must survive."""
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=1,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        for p in _patch_streams():
            p.start()
        try:
            s1 = await pool.get_or_create("Bearer A")
            s2 = await pool.get_or_create("Bearer B")
            # B should have evicted A; the pool must still contain B.
            assert len(pool._entries) == 1
            assert s1 is not s2
        finally:
            await pool.close_all()
            for p in _patch_streams():
                p.stop()


# ── Coalescing ─────────────────────────────────────────────────────────


class TestPoolCoalescing:
    @pytest.mark.asyncio
    async def test_concurrent_first_touch_creates_one_session(self):
        """Two parallel get_or_create calls for the same key must share
        a single underlying session — not two parallel OAuth handshakes."""
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=4,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )

        # Slow ``initialize`` so we can verify two concurrent waiters
        # observe coalescing behaviour rather than racing past it.
        slow_event = asyncio.Event()

        @asynccontextmanager
        async def fake_streamablehttp_client(url: str, *, headers=None, **_kwargs):
            yield MagicMock(), MagicMock(), MagicMock()

        @asynccontextmanager
        async def slow_client_session(read, write):
            s = _FakeSession()
            original_init = s.initialize

            async def slow_init():
                await slow_event.wait()
                return await original_init()

            s.initialize = slow_init
            yield s

        async def fake_probe(_url, _auth, **_kwargs):
            return None, None

        with patch(
            "zelosmcp.passthrough_pool._probe_upstream_auth",
            side_effect=fake_probe,
        ), patch(
            "zelosmcp.passthrough_pool.streamablehttp_client",
            side_effect=fake_streamablehttp_client,
        ), patch(
            "zelosmcp.passthrough_pool.ClientSession",
            side_effect=slow_client_session,
        ):
            t1 = asyncio.create_task(pool.get_or_create("Bearer A"))
            t2 = asyncio.create_task(pool.get_or_create("Bearer A"))
            # Give both tasks a chance to enter the per-key lock.
            await asyncio.sleep(0)
            # Release the slow init.
            slow_event.set()
            s1, s2 = await asyncio.gather(t1, t2)
            assert s1 is s2
            assert len(pool._entries) == 1
        await pool.close_all()


# ── Challenge propagation ──────────────────────────────────────────────


class TestPoolChallenges:
    @pytest.mark.asyncio
    async def test_init_failure_extracts_www_authenticate(self):
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=2,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        for p in _patch_streams(raise_on_init=True):
            p.start()
        try:
            with pytest.raises(PassthroughChallengeError) as ei:
                await pool.get_or_create("Bearer bad-token")
            assert ei.value.backend == "github"
            assert "invalid_token" in ei.value.www_authenticate
            # Failed sessions are NOT cached — next attempt should retry.
            assert len(pool._entries) == 0
        finally:
            await pool.close_all()
            for p in _patch_streams(raise_on_init=True):
                p.stop()

    @pytest.mark.asyncio
    async def test_connect_failure_synthesizes_challenge(self):
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=2,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        for p in _patch_streams(raise_on_connect=True):
            p.start()
        try:
            with pytest.raises(PassthroughChallengeError) as ei:
                await pool.get_or_create("Bearer x")
            # The fake httpx response carried a real WWW-Authenticate
            # so we should propagate that instead of synthesising.
            assert "resource_metadata" in ei.value.www_authenticate
            assert "api.example.com" in ei.value.www_authenticate
        finally:
            await pool.close_all()
            for p in _patch_streams(raise_on_connect=True):
                p.stop()

    @pytest.mark.asyncio
    async def test_unrecognized_failure_synthesizes_challenge(self):
        """When the upstream error has no WWW-Authenticate, we fall back
        to a synthetic challenge pointing at the canonical metadata
        URL so the client still has somewhere to go."""
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=2,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )

        async def fake_probe(_url, _auth, **_kwargs):
            return None, None

        @asynccontextmanager
        async def fake_streamablehttp_client(url: str, *, headers=None, **_kwargs):
            raise RuntimeError("connection reset")

        with patch(
            "zelosmcp.passthrough_pool._probe_upstream_auth",
            side_effect=fake_probe,
        ), patch(
            "zelosmcp.passthrough_pool.streamablehttp_client",
            side_effect=fake_streamablehttp_client,
        ):
            with pytest.raises(PassthroughChallengeError) as ei:
                await pool.get_or_create("Bearer x")
            assert "resource_metadata" in ei.value.www_authenticate
            assert "api.example.com" in ei.value.www_authenticate
            assert "/.well-known/oauth-protected-resource" in ei.value.www_authenticate
            # RFC 9728 §3: the well-known segment is inserted between
            # the origin and the resource path, so the metadata URL
            # MUST end with the resource path (here ``/mcp``) — not
            # the bare well-known doc which 404s on per-path resources.
            assert ei.value.www_authenticate.rstrip('"').endswith("/mcp")
        await pool.close_all()


# ── Probe (pre-SDK upstream auth check) ────────────────────────────────


class TestProbe:
    """The probe runs BEFORE ``streamablehttp_client`` so a 401 from
    the upstream surfaces with its real ``WWW-Authenticate`` header
    intact — the SDK's anyio task group otherwise swallows the
    underlying ``httpx.HTTPStatusError`` and we lose the challenge."""

    @pytest.mark.asyncio
    async def test_probe_401_raises_with_captured_header(self):
        """Probe sees 401 + WWW-Authenticate → raise immediately,
        skipping the SDK roundtrip."""
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=2,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )

        captured_ww = (
            'Bearer error="invalid_request", '
            'resource_metadata="https://api.example.com/'
            '.well-known/oauth-protected-resource/mcp/"'
        )

        async def fake_probe(_url, _auth, **_kwargs):
            return 401, captured_ww

        # If the probe short-circuits correctly, the SDK fakes are
        # never reached. We don't patch them — the test would crash
        # if anything tried to open a real session.
        with patch(
            "zelosmcp.passthrough_pool._probe_upstream_auth",
            side_effect=fake_probe,
        ):
            with pytest.raises(PassthroughChallengeError) as ei:
                await pool.get_or_create(None)
        assert ei.value.backend == "github"
        assert ei.value.www_authenticate == captured_ww
        assert len(pool._entries) == 0
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_probe_401_without_header_falls_back_to_synthesizer(self):
        """If the upstream is misconfigured and returns 401 with no
        ``WWW-Authenticate`` header, we still raise — but with a
        synthesised challenge so the client has somewhere to go."""
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=2,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )

        async def fake_probe(_url, _auth, **_kwargs):
            return 401, None

        with patch(
            "zelosmcp.passthrough_pool._probe_upstream_auth",
            side_effect=fake_probe,
        ):
            with pytest.raises(PassthroughChallengeError) as ei:
                await pool.get_or_create(None)
        ww = ei.value.www_authenticate
        assert "resource_metadata" in ww
        assert "api.example.com" in ww
        # Synthesised URL is RFC 9728 §3 path-aware — must include
        # the resource path so per-path resources resolve.
        assert "/.well-known/oauth-protected-resource/mcp" in ww
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_probe_failure_falls_through_to_sdk(self):
        """Probe returning ``(None, None)`` (network error, timeout,
        anything) must NOT short-circuit — the SDK path runs as
        before so we don't regress on transient probe failures."""
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=2,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        for p in _patch_streams():
            p.start()
        try:
            session = await pool.get_or_create("Bearer good-token")
            assert session is not None
            assert len(pool._entries) == 1
        finally:
            await pool.close_all()
            for p in _patch_streams():
                p.stop()

    @pytest.mark.asyncio
    async def test_probe_non_401_falls_through_to_sdk(self):
        """Probe seeing a non-401 (200, 500, etc.) lets the SDK
        proceed — the response was reachable but isn't an auth
        challenge for us to short-circuit on."""
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=2,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )

        async def fake_probe(_url, _auth, **_kwargs):
            return 200, None

        @asynccontextmanager
        async def fake_streamablehttp_client(url, *, headers=None, **_kwargs):
            yield MagicMock(), MagicMock(), MagicMock()

        @asynccontextmanager
        async def fake_client_session(read, write):
            yield _FakeSession()

        with patch(
            "zelosmcp.passthrough_pool._probe_upstream_auth",
            side_effect=fake_probe,
        ), patch(
            "zelosmcp.passthrough_pool.streamablehttp_client",
            side_effect=fake_streamablehttp_client,
        ), patch(
            "zelosmcp.passthrough_pool.ClientSession",
            side_effect=fake_client_session,
        ):
            session = await pool.get_or_create("Bearer good-token")
        assert session is not None
        assert len(pool._entries) == 1
        await pool.close_all()


# ── Synthesizer URL (RFC 9728 §3 path-awareness) ───────────────────────


class TestSynthesizer:
    def test_synthesizer_inserts_resource_path(self):
        """Per RFC 9728 §3 the well-known segment goes BETWEEN origin
        and resource path. ``/mcp/`` resource → metadata URL must
        include the trailing ``/mcp/``."""
        ww = _synthesize_www_authenticate("https://api.example.com/mcp/")
        assert (
            'resource_metadata="https://api.example.com'
            '/.well-known/oauth-protected-resource/mcp/"'
        ) in ww

    def test_synthesizer_root_resource(self):
        """A URL with no path segment should produce the bare
        well-known doc URL."""
        ww = _synthesize_www_authenticate("https://api.example.com")
        assert (
            'resource_metadata="https://api.example.com'
            '/.well-known/oauth-protected-resource"'
        ) in ww

    def test_synthesizer_handles_trailing_slash_only(self):
        ww = _synthesize_www_authenticate("https://api.example.com/")
        # parsed.path == "/" — well-known + path = "/.well-known/.../."
        assert "https://api.example.com/.well-known/oauth-protected-resource/" in ww


# ── _extract_www_authenticate (defensive walker) ───────────────────────


class TestExtractWWWAuthenticate:
    """Mid-session 401s come back through the SDK's anyio task groups
    wrapped in ``BaseExceptionGroup``. The walker must descend into
    ``.exceptions`` and walk both ``__cause__`` and ``__context__``
    so we never lose the upstream challenge."""

    def _fake_response(self, ww: str):
        response = MagicMock()
        response.headers = {"www-authenticate": ww}
        err = RuntimeError("upstream 401")
        err.response = response  # type: ignore[attr-defined]
        return err

    def test_extracts_from_direct_response(self):
        err = self._fake_response('Bearer error="invalid_token"')
        ww = PassthroughSessionPool._extract_www_authenticate(err)
        assert ww == 'Bearer error="invalid_token"'

    def test_walks_cause_chain(self):
        inner = self._fake_response('Bearer error="cause"')
        try:
            raise RuntimeError("outer") from inner
        except RuntimeError as outer:
            ww = PassthroughSessionPool._extract_www_authenticate(outer)
        assert ww == 'Bearer error="cause"'

    def test_walks_context_chain(self):
        inner = self._fake_response('Bearer error="context"')
        try:
            try:
                raise inner
            except RuntimeError:
                raise RuntimeError("outer")
        except RuntimeError as outer:
            ww = PassthroughSessionPool._extract_www_authenticate(outer)
        assert ww == 'Bearer error="context"'

    def test_walks_exception_group(self):
        inner = self._fake_response('Bearer error="grouped"')
        # BaseExceptionGroup is the anyio task-group wrapper.
        group = BaseExceptionGroup("transport", [inner])
        ww = PassthroughSessionPool._extract_www_authenticate(group)
        assert ww == 'Bearer error="grouped"'

    def test_returns_none_when_no_response_anywhere(self):
        err = RuntimeError("plain error")
        ww = PassthroughSessionPool._extract_www_authenticate(err)
        assert ww is None


# ── Stats ──────────────────────────────────────────────────────────────


class TestPoolStats:
    @pytest.mark.asyncio
    async def test_stats_reflect_size(self):
        pool = PassthroughSessionPool(
            backend_name="github",
            upstream_url="https://api.example.com/mcp",
            max_sessions=4,
            idle_ttl_seconds=60,
            sweep_interval=10.0,
        )
        for p in _patch_streams():
            p.start()
        try:
            assert pool.stats() == {
                "backend": "github",
                "size": 0,
                "max": 4,
                "idle_ttl_seconds": 60,
            }
            await pool.get_or_create("Bearer a")
            await pool.get_or_create("Bearer b")
            stats = pool.stats()
            assert stats["size"] == 2
            assert stats["max"] == 4
        finally:
            await pool.close_all()
            for p in _patch_streams():
                p.stop()
