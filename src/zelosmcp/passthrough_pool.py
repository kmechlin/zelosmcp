"""Per-Cursor session pool for OAuth-passthrough backends.

The :class:`PassthroughSessionPool` lets the aggregator at ``/mcp`` fan
``tools/list`` and ``tools/call`` out to OAuth-protected upstream MCP
servers (GitHub MCP, Atlassian MCP, ...) by maintaining one upstream
:class:`mcp.client.session.ClientSession` per inbound ``Authorization``
header value (keyed by SHA-256 hash). Two Cursor instances with the same
token share a session (correct — they're the same identity); different
tokens get different sessions.

Eviction has two layers:

- **LRU** at ``max_sessions`` — when a fresh session would push the pool
  over its size cap, the least-recently-used entry is closed first.
- **Idle TTL** — a background sweeper closes entries that haven't been
  used for ``idle_ttl_seconds``.

Per-key ``asyncio.Lock`` guards coalesce concurrent first-touch requests
so we never run the upstream OAuth handshake twice for the same token.

When the upstream returns 401 during session initialise (or on any
later request handled here), a :class:`PassthroughChallengeError` is
raised; the aggregator's ASGI middleware surfaces it as a 401 +
``WWW-Authenticate`` response so the MCP client (Cursor) can run its
OAuth dance directly with the upstream issuer.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
import os
import time
from collections import OrderedDict
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger("zelosmcp.passthrough")


# Path to the Debian system CA bundle. When present (i.e. running inside
# the zelosMCP container), httpx is steered at this file so any corporate
# root certs installed via ``update-ca-certificates`` at image-build time
# are honoured. httpx's default uses certifi alone, which doesn't pick
# up corporate roots — that breaks outbound calls through TLS-intercepting
# proxies (Zscaler, Nike egress, etc.).
_SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def _build_mcp_http_client_factory():
    """Return an MCP-SDK-compatible httpx-client factory that trusts the
    system CA bundle (when available).

    The MCP SDK's :class:`streamablehttp_client` accepts a
    ``httpx_client_factory`` whose contract mirrors
    :func:`mcp.shared._httpx_utils.create_mcp_http_client`. We replicate
    its defaults (``follow_redirects=True``, 30s timeout) but layer on
    ``verify=<system bundle>`` so corporate-proxied upstreams (Box,
    Atlassian, etc.) don't fail with ``self-signed certificate in
    certificate chain``.
    """

    verify: Any = (
        _SYSTEM_CA_BUNDLE if os.path.exists(_SYSTEM_CA_BUNDLE) else True
    )

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "follow_redirects": True,
            "timeout": timeout or httpx.Timeout(30.0),
            "verify": verify,
        }
        if headers:
            kwargs["headers"] = headers
        if auth:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    return factory


_HTTP_CLIENT_FACTORY = _build_mcp_http_client_factory()

# Probe timeout — the upstream auth check is on the hot path of every
# pool miss, so we don't wait the full 30s the SDK uses for streamed
# requests. A second-or-two slow upstream still gets through; anything
# slower is degraded enough that falling through to the SDK path
# (which has the longer timeout) is the right move.
_PROBE_TIMEOUT_SECONDS = 5.0


async def _probe_upstream_auth(
    upstream_url: str,
    authorization: str | None,
    *,
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> tuple[int | None, str | None]:
    """Best-effort upstream auth probe.

    Sends one MCP ``initialize`` POST to ``upstream_url`` and inspects
    the response. Returns ``(status_code, www_authenticate)``:

    - ``(401, "Bearer ...")`` — upstream rejected the auth and gave us
      its real challenge header. The pool should raise
      :class:`PassthroughChallengeError` immediately, no SDK roundtrip
      needed.
    - ``(<other status>, None)`` — upstream is reachable and either
      accepted us or returned some non-auth error. Fall through to the
      MCP SDK path so it handles the response normally.
    - ``(None, None)`` — probe failed to reach upstream (DNS, TLS,
      timeout, anything). Fall through to the SDK so the user gets a
      consistent error from the SDK's own transport layer.

    Why probe at all: the SDK runs the actual POST inside an anyio
    task group and swallows ``httpx.HTTPStatusError`` in its
    ``post_writer`` exception handler before our worker ever sees it
    (see ``mcp.client.streamable_http`` for the relevant code). By the
    time control returns to ``_spawn_session_worker``, the upstream
    response (and its ``WWW-Authenticate`` header) is gone. Probing
    with a vanilla httpx client gives us a deterministic capture point.
    """
    headers: dict[str, str] = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if authorization:
        headers["Authorization"] = authorization

    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {
                "name": "zelosmcp-passthrough-probe",
                "version": "0",
            },
        },
    }

    verify: Any = (
        _SYSTEM_CA_BUNDLE if os.path.exists(_SYSTEM_CA_BUNDLE) else True
    )

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            verify=verify,
        ) as client:
            resp = await client.post(upstream_url, json=body, headers=headers)
    except Exception:
        return None, None

    if resp.status_code == 401:
        ww = (
            resp.headers.get("www-authenticate")
            or resp.headers.get("WWW-Authenticate")
        )
        return 401, ww
    return resp.status_code, None

# Carries the inbound HTTP ``Authorization`` header from the ASGI
# dispatcher into the aggregator's MCP handlers. The MCP SDK is
# transport-agnostic and doesn't surface raw HTTP headers, so we use
# a ContextVar — values propagate to ``asyncio.gather``-spawned child
# tasks naturally because asyncio inherits the parent context at Task
# creation.
inbound_authorization: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "zelosmcp_inbound_authorization",
    default=None,
)

# Side-channel that aggregator handlers use to signal an upstream
# OAuth challenge that the ASGI middleware must surface as an HTTP
# 401 + WWW-Authenticate response. The MCP SDK catches exceptions
# raised from handlers and serialises them as JSON-RPC error envelopes
# (HTTP 200), so a plain ``raise`` would never surface the challenge
# at the transport layer.
#
# The ContextVar holds a *list* (mutable container) created by the
# middleware before each request. Both the middleware (parent task)
# and handlers (child tasks created by the SDK's task groups) see
# the same list object — mutations propagate even though child tasks
# get a copy of the ContextVar value at creation time. After
# ``handle_request`` returns, the middleware checks whether the list
# is non-empty; if so, it rewrites the response.
pending_challenge: contextvars.ContextVar[
    "list[PassthroughChallengeError] | None"
] = contextvars.ContextVar(
    "zelosmcp_pending_challenge",
    default=None,
)


def signal_challenge(challenge: "PassthroughChallengeError") -> None:
    """Append a challenge to the per-request signal list, if one is
    bound. No-op when called outside a managed request (e.g. from a
    direct unit test of an aggregator handler).
    """
    box = pending_challenge.get()
    if box is not None:
        box.append(challenge)

# Sentinel used when no inbound Authorization header is present and no
# static fallback is configured. The pool still needs a key so concurrent
# anonymous requests share a session and we don't open one upstream
# connection per request. Anonymous sessions almost always 401 on first
# tool call — that's the desired behaviour: surface the challenge so
# Cursor triggers OAuth.
_ANON_KEY = "anonymous"

# Sweeper cadence. A value smaller than ``idle_ttl_seconds`` means
# evictions land within ~one sweep window of TTL expiry; larger values
# trade timeliness for fewer wakeups. 30s is a reasonable default that
# matches typical idle TTLs of 1800s (60x slack).
_DEFAULT_SWEEP_INTERVAL: float = 30.0


class PassthroughChallengeError(Exception):
    """Raised when an upstream OAuth-protected MCP server returns 401.

    Carries the upstream's ``WWW-Authenticate`` header verbatim (or a
    synthesised one when the real challenge wasn't captured) so the
    middleware in :mod:`zelosmcp.app` can surface it as an HTTP-level
    401 response. That's what makes Cursor's MCP OAuth client trigger
    the browser flow against the upstream issuer rather than zelosMCP.
    """

    def __init__(
        self,
        *,
        backend: str,
        www_authenticate: str,
        status: int = 401,
    ) -> None:
        super().__init__(
            f"upstream backend '{backend}' requires authentication ({status})"
        )
        self.backend = backend
        self.www_authenticate = www_authenticate
        self.status = status


@dataclass
class _PoolEntry:
    """Internal entry tracking one upstream session.

    The session lifecycle is owned by ``worker``: a dedicated
    :class:`asyncio.Task` that enters the ``streamablehttp_client`` +
    :class:`ClientSession` context managers, calls ``initialize()``, and
    then awaits ``shutdown``. The task tears the stack down in its OWN
    task context so anyio's cancel scopes (which are task-bound) never
    cross threads. Setting ``shutdown`` is the documented way to release
    the entry; ``worker`` also shuts down on cancellation.
    """

    session: ClientSession
    worker: asyncio.Task
    shutdown: asyncio.Event
    created_at: float
    last_used: float = field(default_factory=time.monotonic)


def hash_authorization(authorization: str | None) -> str:
    """Stable session-key hash for an Authorization header value.

    Uses SHA-256 (truncated to 32 hex chars — 128 bits of entropy is
    plenty for an in-memory dict key, and shorter strings keep log lines
    legible). Empty / None inputs map to the anonymous sentinel.
    """
    if not authorization:
        return _ANON_KEY
    digest = hashlib.sha256(authorization.encode("utf-8", errors="replace")).hexdigest()
    return digest[:32]


def _synthesize_www_authenticate(upstream_url: str) -> str:
    """Build a Bearer challenge that points clients at the upstream's
    canonical OAuth Protected Resource Metadata document (RFC 9728 §3).

    RFC 9728 §3 specifies that the well-known segment is inserted
    BETWEEN the resource origin and the resource path — i.e. for
    ``https://api.example.com/mcp/`` the metadata URL is
    ``https://api.example.com/.well-known/oauth-protected-resource/mcp/``,
    NOT ``https://api.example.com/.well-known/oauth-protected-resource``
    (that's the root-resource form, which 404s on per-path resources
    like the GitHub MCP server).

    Used when an upstream session creation fails without a captured
    ``WWW-Authenticate`` from the real upstream response — we still
    need to give the client *something* meaningful so its OAuth client
    knows where to go. Real MCP servers should always return the
    header themselves; this is a defensive fallback.
    """
    from urllib.parse import urlparse

    parsed = urlparse(upstream_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    # parsed.path includes the leading "/" when present; if absent
    # (origin-only URL) the well-known doc lives at the root.
    path = parsed.path or ""
    metadata_url = f"{origin}/.well-known/oauth-protected-resource{path}"
    return f'Bearer resource_metadata="{metadata_url}"'


class PassthroughSessionPool:
    """LRU + TTL pool of upstream MCP sessions, keyed by Authorization hash.

    Only used by the aggregator (Phase 2B). Per-backend ``/<name>/mcp``
    requests use :meth:`zelosmcp.manager.ProxyManager.proxy_mcp_request`
    which is a stateless HTTP forwarder — no session pool needed there.
    """

    def __init__(
        self,
        *,
        backend_name: str,
        upstream_url: str,
        max_sessions: int,
        idle_ttl_seconds: int,
        static_bearer: str | None = None,
        log: Callable[[str], None] | None = None,
        sweep_interval: float = _DEFAULT_SWEEP_INTERVAL,
    ) -> None:
        if max_sessions <= 0:
            raise ValueError("max_sessions must be > 0")
        if idle_ttl_seconds <= 0:
            raise ValueError("idle_ttl_seconds must be > 0")

        self.backend_name = backend_name
        self.upstream_url = upstream_url
        self.max_sessions = max_sessions
        self.idle_ttl_seconds = idle_ttl_seconds
        self.static_bearer = static_bearer
        self._log = log or (lambda _msg: None)
        self._sweep_interval = sweep_interval

        # OrderedDict gives us O(1) LRU ordering — move_to_end on use,
        # popitem(last=False) on eviction.
        self._entries: OrderedDict[str, _PoolEntry] = OrderedDict()
        # Per-key locks coalesce concurrent first-touch requests so two
        # parallel `tools/list` calls for the same token only run one
        # upstream OAuth handshake.
        self._key_locks: dict[str, asyncio.Lock] = {}
        # Top-level lock guards the OrderedDict and lock dict structure
        # itself. Held only briefly; the per-key lock owns long-running
        # session-creation waits.
        self._global_lock: asyncio.Lock = asyncio.Lock()

        self._sweeper: asyncio.Task | None = None
        self._closed: bool = False

    async def start(self) -> None:
        """Start the background TTL sweeper. Idempotent."""
        if self._sweeper is not None:
            return
        self._closed = False
        self._sweeper = asyncio.create_task(self._sweep_loop())

    async def close_all(self) -> None:
        """Tear down every session and stop the sweeper. Idempotent."""
        self._closed = True
        sweeper, self._sweeper = self._sweeper, None
        if sweeper is not None:
            sweeper.cancel()
            try:
                await sweeper
            except (asyncio.CancelledError, Exception):
                pass

        async with self._global_lock:
            entries = list(self._entries.items())
            self._entries.clear()
            self._key_locks.clear()

        # Wait for each worker task to exit. Each task owns its own
        # AsyncExitStack and tears it down in its own task context, so we
        # never cross anyio cancel scopes here.
        for key, entry in entries:
            try:
                entry.shutdown.set()
                await entry.worker
            except Exception as exc:
                self._log(f"WARN: pool close ({key[:8]}...): {exc}")

    def stats(self) -> dict[str, Any]:
        """Snapshot for /api/status / debug tooling. Cheap; no locking
        because the OrderedDict reads are atomic for size queries."""
        return {
            "backend": self.backend_name,
            "size": len(self._entries),
            "max": self.max_sessions,
            "idle_ttl_seconds": self.idle_ttl_seconds,
        }

    # ── Core API ───────────────────────────────────────────────────────

    async def get_or_create(
        self, authorization: str | None
    ) -> ClientSession:
        """Return a live :class:`ClientSession` for the given Authorization.

        On a cache hit, refreshes the entry's LRU position and returns the
        cached session. On a miss, opens a new upstream connection +
        session under the per-key lock so concurrent first-touch callers
        share a single OAuth handshake.

        Raises:
            PassthroughChallengeError: if the upstream returns 401 (or
                any auth-related failure) during session creation.
            RuntimeError: if the pool is closed.
        """
        if self._closed:
            raise RuntimeError(
                f"passthrough pool for '{self.backend_name}' is closed"
            )

        key = hash_authorization(authorization or self.static_bearer or None)

        # Fast path — under the global lock just long enough to (a) check
        # for an existing entry and (b) refresh its LRU position.
        async with self._global_lock:
            existing = self._entries.get(key)
            if existing is not None:
                self._entries.move_to_end(key)
                existing.last_used = time.monotonic()
                return existing.session

            # Acquire (or create) the per-key lock so we can release the
            # global lock while the slow path runs.
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock

        async with lock:
            # Re-check under the per-key lock — another waiter may have
            # finished creating the session while we were queued.
            async with self._global_lock:
                existing = self._entries.get(key)
                if existing is not None:
                    self._entries.move_to_end(key)
                    existing.last_used = time.monotonic()
                    return existing.session

            # Slow path: actually create the session. Outside both locks
            # so concurrent get_or_create calls for *different* keys
            # don't serialise on a single OAuth handshake.
            effective_auth = authorization or (
                f"Bearer {self.static_bearer}" if self.static_bearer else None
            )
            entry = await self._spawn_session_worker(effective_auth)

            evicted: list[tuple[str, _PoolEntry]] = []
            async with self._global_lock:
                self._entries[key] = entry
                self._entries.move_to_end(key)
                while len(self._entries) > self.max_sessions:
                    ev_key, ev_entry = self._entries.popitem(last=False)
                    if ev_key == key:
                        # Defensive: never evict the entry we just added
                        # (would leave the caller with a closed session).
                        # Re-insert at the front and stop trimming.
                        self._entries[key] = ev_entry
                        self._entries.move_to_end(key)
                        break
                    evicted.append((ev_key, ev_entry))
                    self._key_locks.pop(ev_key, None)

            # Tell evicted workers to wind down. They tear their own
            # stacks down in their own task contexts, avoiding cross-task
            # cancel-scope errors. We don't await here so a slow upstream
            # close doesn't block the request that triggered the LRU.
            for ev_key, ev_entry in evicted:
                ev_entry.shutdown.set()
                self._log(f"LRU evicted session {ev_key[:8]}...")

            return entry.session

    # ── Internals ──────────────────────────────────────────────────────

    async def _probe_auth(
        self, authorization: str | None
    ) -> tuple[int | None, str | None]:
        """Pool-level wrapper around :func:`_probe_upstream_auth`.

        Exists as a method (not a direct call) so tests can subclass
        the pool or monkey-patch this attribute to skip the network
        probe without touching module globals.
        """
        return await _probe_upstream_auth(
            self.upstream_url, authorization
        )

    async def _spawn_session_worker(
        self, authorization: str | None
    ) -> _PoolEntry:
        """Spawn a worker task that owns one upstream session's lifecycle.

        The worker enters ``streamablehttp_client`` + :class:`ClientSession`
        in its own task, calls ``initialize()``, and then waits on a
        shutdown event before tearing the stack down. This single-task
        ownership is what avoids the ``RuntimeError: Attempted to exit a
        cancel scope that isn't the current task's current cancel scope``
        anyio raises when the SDK's internal task-group is unwound from
        a different task than the one that opened it.

        Returns:
            A :class:`_PoolEntry` whose ``session`` is initialised and
            ready for use from any task.

        Raises:
            PassthroughChallengeError: if the upstream rejects the auth
                during connection or initialise.
        """
        # Probe upstream BEFORE bringing the SDK in. The SDK's anyio
        # task group swallows the underlying ``httpx.HTTPStatusError``
        # from a 401 response — by the time the worker sees the failure
        # the response (with its ``WWW-Authenticate`` header) has been
        # discarded and ``_extract_www_authenticate`` falls through to
        # the synthesised fallback, which doesn't always match what the
        # real upstream would have said. A vanilla httpx probe gives us
        # a deterministic capture of the challenge.
        probe_status, probe_ww = await self._probe_auth(authorization)
        if probe_status == 401:
            raise PassthroughChallengeError(
                backend=self.backend_name,
                www_authenticate=(
                    probe_ww
                    or _synthesize_www_authenticate(self.upstream_url)
                ),
            )

        ready = asyncio.Event()
        shutdown = asyncio.Event()
        result: dict[str, Any] = {}

        async def _worker() -> None:
            try:
                async with AsyncExitStack() as stack:
                    headers: dict[str, str] = {}
                    if authorization:
                        headers["Authorization"] = authorization

                    try:
                        cm = streamablehttp_client(
                            self.upstream_url,
                            headers=headers,
                            httpx_client_factory=_HTTP_CLIENT_FACTORY,
                        )
                        ctx = await stack.enter_async_context(cm)
                    except Exception as exc:
                        result["error"] = exc
                        ready.set()
                        return

                    if isinstance(ctx, tuple):
                        read, write = ctx[0], ctx[1]
                    else:
                        read, write = ctx

                    try:
                        session = await stack.enter_async_context(
                            ClientSession(read, write)
                        )
                        await session.initialize()
                    except Exception as exc:
                        result["error"] = exc
                        ready.set()
                        return

                    result["session"] = session
                    ready.set()

                    try:
                        await shutdown.wait()
                    except asyncio.CancelledError:
                        # Treat cancellation as shutdown — fall through
                        # to AsyncExitStack cleanup, which exits every
                        # context manager in this task's context.
                        pass
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                # Stack-close errors land here. Suppress so a single
                # bad teardown doesn't poison the pool.
                logger.debug(
                    "passthrough worker for %s: stack close failed: %s",
                    self.backend_name,
                    exc,
                )
            finally:
                # The worker task is exiting; if we never signalled
                # ready (e.g. the worker was cancelled mid-init), do
                # so now with an error so the caller doesn't deadlock.
                if not ready.is_set():
                    if "error" not in result:
                        result["error"] = RuntimeError(
                            "passthrough worker exited before signalling ready"
                        )
                    ready.set()

        worker = asyncio.create_task(_worker())
        await ready.wait()

        if "error" in result:
            err = result["error"]
            # Make sure the worker actually finishes shutting down.
            shutdown.set()
            try:
                await worker
            except Exception:
                pass
            ww = self._extract_www_authenticate(err) if isinstance(err, BaseException) else None
            if ww is not None:
                raise PassthroughChallengeError(
                    backend=self.backend_name,
                    www_authenticate=ww,
                )
            # No captured WWW-Authenticate — synthesise one so the MCP
            # client at least knows where to OAuth.
            challenge = PassthroughChallengeError(
                backend=self.backend_name,
                www_authenticate=_synthesize_www_authenticate(self.upstream_url),
            )
            if isinstance(err, BaseException):
                raise challenge from err
            raise challenge

        session = result["session"]
        now = time.monotonic()
        return _PoolEntry(
            session=session,
            worker=worker,
            shutdown=shutdown,
            created_at=now,
            last_used=now,
        )

    @staticmethod
    def _extract_www_authenticate(exc: BaseException) -> str | None:
        """Best-effort extraction of WWW-Authenticate from any SDK error.

        The MCP SDK propagates HTTP failures through anyio task groups,
        so the underlying ``httpx.HTTPStatusError`` (carrying the
        upstream response with its ``WWW-Authenticate`` header) can
        end up in any of:

        - ``exc.response`` directly (httpx-style attachment),
        - ``exc.__cause__`` / ``exc.__context__`` chains (Python-level
          implicit/explicit chaining),
        - ``exc.exceptions`` when wrapped in a
          :class:`BaseExceptionGroup` by ``anyio.create_task_group``.

        We walk all of those defensively so the real challenge survives
        being dragged through the SDK's transport layer.
        """
        seen: set[int] = set()

        def _walk(cur: BaseException | None) -> str | None:
            if cur is None or id(cur) in seen:
                return None
            seen.add(id(cur))
            # Direct ``.response`` attachment (httpx.HTTPStatusError, etc.).
            response = getattr(cur, "response", None)
            if response is not None:
                try:
                    headers = getattr(response, "headers", None)
                    if headers is not None:
                        ww = (
                            headers.get("www-authenticate")
                            or headers.get("WWW-Authenticate")
                        )
                        if ww:
                            return ww
                except Exception:
                    pass
            # ExceptionGroup / BaseExceptionGroup nesting (anyio task
            # groups raise these when child tasks fail).
            for sub in getattr(cur, "exceptions", None) or ():
                ww = _walk(sub)
                if ww:
                    return ww
            # Both implicit (``__context__``) and explicit (``__cause__``)
            # chaining. ``__context__`` matters because the SDK swallows
            # the original httpx error in a ``logger.exception(...)``
            # block before re-raising a generic stream-closed error.
            for nxt in (cur.__cause__, cur.__context__):
                ww = _walk(nxt)
                if ww:
                    return ww
            return None

        return _walk(exc)

    async def _sweep_loop(self) -> None:
        """Background task: evict entries whose ``last_used`` is older
        than ``idle_ttl_seconds``."""
        try:
            while not self._closed:
                await asyncio.sleep(self._sweep_interval)
                if self._closed:
                    return
                await self._sweep_once()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # Sweeper failure must NOT take down the backend; log and
            # let the next start() restart the loop if anyone calls it.
            self._log(f"ERROR: pool sweeper crashed: {exc}")

    async def _sweep_once(self) -> None:
        """Single sweep pass — evict every entry past the idle TTL."""
        now = time.monotonic()
        cutoff = now - self.idle_ttl_seconds
        evicted: list[tuple[str, _PoolEntry]] = []
        async with self._global_lock:
            for key, entry in list(self._entries.items()):
                if entry.last_used <= cutoff:
                    self._entries.pop(key, None)
                    self._key_locks.pop(key, None)
                    evicted.append((key, entry))
        # Signal shutdown to each evicted worker; they tear their own
        # AsyncExitStack down in their own task contexts.
        for key, entry in evicted:
            entry.shutdown.set()
            self._log(f"TTL evicted session {key[:8]}... (idle)")
