"""Coordinates many ProxyState instances behind one HTTP surface.

The manager is what `app.py` actually talks to. It owns a per-name registry of
:class:`localmcp.proxy.ProxyState` objects, tracks which one is the primary
(mirrored at ``/mcp`` instead of just ``/<name>/mcp``), and aggregates log
subscriptions across all servers.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from starlette.responses import JSONResponse

from localmcp.aggregator import Aggregator
from localmcp.builtin import NAME as BUILTIN_NAME, BuiltinServer
from localmcp.config import ServerSpec, parse_config
from localmcp.proxy import ProxyState


# Default lookup order for the mandatory MCP set. First existing path wins.
# - /app/configs is the root Dockerfile's runtime path.
# - /opt/localmcp/configs is the cert-aware Dockerfile's runtime path.
# - The repo-relative path lets developers run uvicorn directly from the
#   working tree (tests can use ProxyManager(mandatory_config_path="") to
#   skip mandatory entirely).
_MANDATORY_PATH_CANDIDATES: tuple[str, ...] = (
    "/app/configs/mandatory-localmcp.json",
    "/opt/localmcp/configs/mandatory-localmcp.json",
    str(Path(__file__).resolve().parent.parent.parent / "configs" / "mandatory-localmcp.json"),
)


# Hop-by-hop headers per RFC 7230 §6.1 plus a few headers httpx manages
# itself (Host gets rewritten to the upstream's authority; Content-Length
# is recomputed from the forwarded body). Stripped on both request and
# response sides of the proxy.
_HOP_BY_HOP: frozenset[str] = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
})


_log = logging.getLogger("localmcp.manager")


class ProxyManager:
    """Lifecycle owner for many ProxyStates plus the /mcp aggregator."""

    def __init__(self, mandatory_config_path: str | None = None) -> None:
        # ``self.servers`` mixes user-configured ProxyStates with the
        # always-on BuiltinServer (under the reserved key "localmcp").
        # The builtin is ProxyState-shaped, so the dispatcher and
        # aggregator iterate over it transparently. Lifecycle methods
        # (start_all/stop_all/start_one/stop_one) explicitly skip it.
        self.servers: dict[str, Any] = {}
        self._specs: dict[str, ServerSpec] = {}
        self._primary: str | None = None
        self._log_subscribers: list[asyncio.Queue[str]] = []
        # Ring buffer of every line ever broadcast, capped to keep memory
        # bounded. New SSE subscribers replay this snapshot before
        # entering live-tail so the activity panel reflects the full
        # session history (including startup banners that fired before
        # the browser connected).
        self._log_history: collections.deque[str] = collections.deque(maxlen=2000)
        self._log_pumps: dict[str, asyncio.Task] = {}
        self.aggregator = Aggregator(self)
        # The BuiltinServer is ProxyState-shaped and pre-seeded so the
        # dispatcher can route /localmcp/mcp from the very first request.
        # Its log pump is attached lazily from `start_builtin()` because
        # `_attach_log_pump` requires a running event loop.
        self.builtin = BuiltinServer(self)
        self.servers[BUILTIN_NAME] = self.builtin
        # Shared HTTP client used by the reverse-proxy dispatcher to forward
        # requests to backend HTTP sidecars. Lifecycle is owned by app.py's
        # lifespan hook (start_http_client / stop_http_client) so connection
        # pooling survives across config reloads. Tests can replace the
        # client by setting this attribute directly.
        self._http_client: httpx.AsyncClient | None = None
        # Mandatory MCP set merged into every start_all() payload before
        # parsing. ``None`` (default) auto-discovers from the runtime paths
        # in _MANDATORY_PATH_CANDIDATES; ``""`` disables mandatory merging
        # entirely (used by tests to avoid spawning real subprocesses);
        # any other value is the literal path to the mandatory JSON.
        self._mandatory_config_path = mandatory_config_path
        # Cached parsed mandatory ``mcpServers`` dict (populated lazily on
        # first read by _read_mandatory_servers; reused across start_all()
        # invocations within a single ProxyManager lifetime).
        self._mandatory_cache: dict[str, Any] | None = None
        self._mandatory_cache_loaded = False

    @property
    def primary(self) -> str | None:
        return self._primary

    def primary_state(self) -> ProxyState | None:
        if self._primary is None:
            return None
        return self.servers.get(self._primary)

    def get(self, name: str) -> ProxyState | None:
        return self.servers.get(name)

    def names(self) -> list[str]:
        return list(self.servers.keys())

    async def start_all(self, raw_config: Any) -> dict[str, Any]:
        """Replace the current set of servers with whatever ``raw_config`` defines.

        Stops anything currently running, parses the config (after merging
        the mandatory MCP set on top of the user payload — user wins on
        same-name collisions), concurrently starts each backend, then brings
        up the aggregator at ``/mcp``. Returns a per-server result map.
        """
        await self.stop_all()

        merged = self._merge_mandatory(raw_config)
        specs, primary = parse_config(merged)
        self._specs = {s.name: s for s in specs}

        if primary is not None:
            self._broadcast(
                "[aggregator] primaryMCP is deprecated and ignored — "
                "/mcp now aggregates all servers"
            )
        self._primary = None

        results: dict[str, Any] = {}
        coros = []
        for spec in specs:
            if spec.name == BUILTIN_NAME:
                # parse_config already rejects this via RESERVED_NAMES, but
                # belt-and-braces in case a future code path bypasses it.
                continue
            state = ProxyState(name=spec.name)
            self.servers[spec.name] = state
            self._attach_log_pump(state)
            coros.append(self._start_one_spec(state, spec))

        outcomes = await asyncio.gather(*coros, return_exceptions=True)
        for spec, outcome in zip(specs, outcomes):
            if isinstance(outcome, BaseException):
                results[spec.name] = {"ok": False, "error": str(outcome)}
            else:
                results[spec.name] = {"ok": True}

        if any(s.running for s in self.servers.values()):
            try:
                await self.aggregator.start()
            except Exception as exc:
                self._broadcast(f"[aggregator] failed to start: {exc}")

        return {
            "primary": None,
            "servers": results,
        }

    def _merge_mandatory(self, raw_config: Any) -> Any:
        """Merge the mandatory MCP set into ``raw_config``.

        Returns a new dict (the input is left unmodified). Mandatory
        backends fill in any names absent from the user's payload; if the
        user's payload defines an entry with the same name, the user's
        entry wins so they can override args/env/etc.

        Returns the input unchanged when:
        - ``mandatory_config_path`` is set to the empty string (test mode).
        - The mandatory file doesn't exist or fails to parse.
        - ``raw_config`` isn't a dict (parse_config will raise downstream).
        """
        mandatory = self._read_mandatory_servers()
        if not mandatory or not isinstance(raw_config, dict):
            return raw_config

        merged = dict(raw_config)
        user_servers = merged.get("mcpServers")
        user_servers = dict(user_servers) if isinstance(user_servers, dict) else {}

        injected: list[str] = []
        for name, entry in mandatory.items():
            if name not in user_servers:
                user_servers[name] = entry
                injected.append(name)

        merged["mcpServers"] = user_servers
        if injected:
            self._broadcast(
                f"[manager] merged mandatory backends: {', '.join(sorted(injected))}"
            )
        return merged

    def _read_mandatory_servers(self) -> dict[str, Any] | None:
        """Read and cache the mandatory file's ``mcpServers`` dict.

        Returns ``None`` when mandatory is disabled, the file is missing,
        or the file can't be parsed. Subsequent calls return the cached
        result (positive or None) without re-reading the file.
        """
        if self._mandatory_cache_loaded:
            return self._mandatory_cache

        self._mandatory_cache_loaded = True
        path = self._resolve_mandatory_path()
        if path is None:
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except FileNotFoundError:
            _log.info("mandatory config not found at %s; skipping merge", path)
            return None
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("mandatory config %s failed to load: %s", path, exc)
            return None

        servers = payload.get("mcpServers") if isinstance(payload, dict) else None
        if not isinstance(servers, dict):
            _log.warning(
                "mandatory config %s missing 'mcpServers' object; skipping merge",
                path,
            )
            return None

        self._mandatory_cache = servers
        return servers

    def _resolve_mandatory_path(self) -> str | None:
        """Pick the actual mandatory-file path to read.

        Honours the constructor's ``mandatory_config_path`` argument:
        - ``""`` => disabled (test mode).
        - non-empty string => use that path verbatim.
        - ``None`` (default) => first existing path in
          _MANDATORY_PATH_CANDIDATES, or ``None`` if none exist.
        """
        explicit = self._mandatory_config_path
        if explicit == "":
            return None
        if isinstance(explicit, str) and explicit:
            return explicit
        for candidate in _MANDATORY_PATH_CANDIDATES:
            if os.path.isfile(candidate):
                return candidate
        return None

    async def stop_all(self) -> None:
        """Stop every user-configured backend AND the aggregator. The
        always-on builtin (`localmcp`) is intentionally preserved so its
        tools remain available across config reloads."""
        await self.aggregator.stop()
        # Snapshot of stoppable (non-builtin) backends.
        to_stop = [
            (name, state)
            for name, state in self.servers.items()
            if name != BUILTIN_NAME
        ]
        if to_stop:
            await asyncio.gather(
                *(state.stop() for _, state in to_stop),
                return_exceptions=True,
            )
        # Cancel only the log pumps for the backends we just stopped.
        for name, _ in to_stop:
            task = self._log_pumps.pop(name, None)
            if task is not None:
                task.cancel()
        for name, _ in to_stop:
            self.servers.pop(name, None)
        self._specs.clear()
        self._primary = None

    async def start_one(self, name: str) -> None:
        if name == BUILTIN_NAME:
            raise KeyError(
                f"'{BUILTIN_NAME}' is the always-on builtin and cannot be "
                "started/stopped"
            )
        spec = self._specs.get(name)
        if spec is None:
            raise KeyError(f"No server named '{name}'")
        state = self.servers.get(name)
        if state is None:
            state = ProxyState(name=name)
            self.servers[name] = state
            self._attach_log_pump(state)
        if state.running:
            raise RuntimeError(f"Server '{name}' is already running")
        await self._start_one_spec(state, spec)

    async def stop_one(self, name: str) -> None:
        if name == BUILTIN_NAME:
            raise KeyError(
                f"'{BUILTIN_NAME}' is the always-on builtin and cannot be "
                "started/stopped"
            )
        state = self.servers.get(name)
        if state is None:
            raise KeyError(f"No server named '{name}'")
        await state.stop()

    async def start_builtin(self) -> None:
        """Bring up the always-on builtin MCP. Called once from the
        Starlette lifespan hook in :func:`localmcp.app.create_app` before
        any HTTP request arrives. Idempotent."""
        if self.builtin.running:
            return
        await self.builtin.start()
        # Now that we have a running event loop, hook the builtin's log
        # stream into the manager's broadcast set so its activity shows
        # up in `/api/logs` like every other backend.
        if BUILTIN_NAME not in self._log_pumps:
            self._attach_log_pump(self.builtin)
        # The aggregator depends on at least one running backend;
        # making sure it's live as soon as the builtin is up means
        # /mcp can serve `localmcp__*` tools even when no user
        # backend has been configured yet.
        if not self.aggregator.running:
            try:
                await self.aggregator.start()
            except Exception as exc:
                self._broadcast(f"[aggregator] failed to start: {exc}")

    async def stop_builtin(self) -> None:
        """Tear down the builtin. Called from the lifespan shutdown hook."""
        await self.aggregator.stop()
        task = self._log_pumps.pop(BUILTIN_NAME, None)
        if task is not None:
            task.cancel()
        await self.builtin.stop()

    async def start_http_client(self) -> None:
        """Initialise the shared httpx.AsyncClient used by the reverse-proxy
        dispatcher. Called once from the Starlette lifespan startup hook.
        Idempotent."""
        if self._http_client is not None:
            return
        # Modest connect timeout so a stopped backend fails fast; longer
        # read window so streaming responses (dashboard HTML, slow query
        # endpoints) don't get cut off. follow_redirects stays off — we
        # forward the upstream's redirect response verbatim instead.
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
            follow_redirects=False,
        )

    async def stop_http_client(self) -> None:
        """Close the shared httpx.AsyncClient. Called from the lifespan
        shutdown hook. Idempotent."""
        client, self._http_client = self._http_client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception as exc:
                _log.warning("reverse-proxy: client close failed: %s", exc)

    def find_reverse_proxy(
        self, path: str
    ) -> tuple[ServerSpec, Any] | None:
        """Locate the backend whose reverseProxy.mount best matches ``path``.

        Returns ``(spec, state)`` where ``state`` may be ``None`` if the
        backend is configured but not currently running. Returns ``None``
        when no mount matches. Longest-prefix wins so nested mounts like
        ``/foo`` and ``/foo/bar`` would both resolve correctly (though
        ``parse_config`` rejects overlap today, the longest-match rule
        keeps this future-proof).
        """
        best: tuple[ServerSpec, Any] | None = None
        best_len = -1
        for name, spec in self._specs.items():
            if spec.reverse_proxy is None:
                continue
            mount = spec.reverse_proxy.mount
            # Segment-aware: '/foo' must match '/foo' or '/foo/...' but
            # never '/foobar'. Append '/' before testing.
            if path == mount or path.startswith(mount + "/"):
                if len(mount) > best_len:
                    best_len = len(mount)
                    best = (spec, self.servers.get(name))
        return best

    async def proxy_request(
        self,
        spec: ServerSpec,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        """Forward an ASGI HTTP request to ``spec.reverse_proxy.upstream``.

        Reads the request body in full (non-streaming v1 — pincher's payloads
        are tiny and this keeps the implementation simple). Streams the
        response body back to the caller via ``Response.aiter_raw``.
        """
        rp = spec.reverse_proxy
        assert rp is not None, "proxy_request called without a reverseProxy"
        client = self._http_client
        if client is None:
            resp = JSONResponse(
                {"error": "reverse-proxy client not initialised"},
                status_code=503,
            )
            await resp(scope, receive, send)
            return

        # Build the forwarded URL. When stripPrefix is true we drop the mount
        # before forwarding (so '/foo/v1/x' -> '<upstream>/v1/x'); otherwise
        # the path is forwarded verbatim ('/foo/v1/x' -> '<upstream>/foo/v1/x'),
        # which lets upstreams like pincher honour X-Forwarded-Prefix
        # themselves.
        request_path = scope.get("path", "/")
        if rp.strip_prefix and request_path.startswith(rp.mount):
            forwarded_path = request_path[len(rp.mount) :] or "/"
        else:
            forwarded_path = request_path

        raw_query = scope.get("query_string", b"") or b""
        upstream_url = httpx.URL(rp.upstream + forwarded_path)
        if raw_query:
            upstream_url = upstream_url.copy_with(query=raw_query)

        # Read the full request body. Bounded by the client; non-streaming
        # by design (see docstring).
        body_chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"") or b""
            if chunk:
                body_chunks.append(chunk)
            more = message.get("more_body", False)
        body = b"".join(body_chunks)

        # Build forwarded headers: drop hop-by-hop, then layer X-Forwarded-*,
        # user-configured headers, and bearer auth in that order. Each later
        # layer wins so users can override the canonical X-Forwarded-* set
        # if they need to.
        headers: list[tuple[str, str]] = []
        for raw_name, raw_value in scope.get("headers", []):
            hname = raw_name.decode("latin-1")
            if hname.lower() in _HOP_BY_HOP:
                continue
            headers.append((hname, raw_value.decode("latin-1")))

        client_host = ""
        client_info = scope.get("client")
        if isinstance(client_info, (list, tuple)) and client_info:
            client_host = str(client_info[0])

        scheme = scope.get("scheme", "http")
        host_header = ""
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name == b"host":
                host_header = raw_value.decode("latin-1")
                break

        forwarded: dict[str, str] = {
            "X-Forwarded-Proto": scheme,
            "X-Forwarded-Prefix": rp.mount,
        }
        if host_header:
            forwarded["X-Forwarded-Host"] = host_header
        if client_host:
            # Append onto any existing X-Forwarded-For chain so transparent
            # proxies in front of LocalMCP keep their origin information.
            existing_xff = next(
                (v for k, v in headers if k.lower() == "x-forwarded-for"),
                None,
            )
            forwarded["X-Forwarded-For"] = (
                f"{existing_xff}, {client_host}" if existing_xff else client_host
            )
            # Drop the existing X-Forwarded-For so the merged value wins.
            headers = [
                (k, v) for k, v in headers if k.lower() != "x-forwarded-for"
            ]

        # User-configured headers take precedence over the canonical
        # forwarded set so admins can override (e.g. set a fixed
        # X-Forwarded-Host for clients with a known external hostname).
        for k, v in rp.headers.items():
            forwarded[k] = v

        # Drop any forwarded-key duplicates from the original header list,
        # then append the canonical forwarded set.
        forwarded_lower = {k.lower() for k in forwarded}
        headers = [(k, v) for k, v in headers if k.lower() not in forwarded_lower]
        headers.extend(forwarded.items())

        # Inject bearer token only when the caller hasn't supplied their own
        # Authorization header. Lets clients with their own credentials pass
        # through unchanged.
        if rp.auth_bearer:
            has_auth = any(k.lower() == "authorization" for k, _ in headers)
            if not has_auth:
                headers.append(("Authorization", f"Bearer {rp.auth_bearer}"))

        method = scope.get("method", "GET")
        try:
            req = client.build_request(
                method,
                upstream_url,
                headers=headers,
                content=body if body else None,
            )
            # Non-streaming v1: read the full upstream body before relaying.
            # Pincher's payloads (dashboard HTML, JSON tool responses) are
            # small enough that this keeps the implementation simple. If we
            # later proxy SSE / large downloads, switch to client.send(stream=True)
            # and aiter_bytes().
            upstream_resp = await client.send(req)
        except httpx.RequestError as exc:
            resp = JSONResponse(
                {
                    "error": "reverse-proxy upstream unreachable",
                    "backend": spec.name,
                    "upstream": rp.upstream,
                    "detail": str(exc),
                },
                status_code=502,
            )
            await resp(scope, receive, send)
            return

        response_headers: list[tuple[bytes, bytes]] = []
        for raw_name, raw_value in upstream_resp.headers.raw:
            name_lower = raw_name.decode("latin-1").lower()
            if name_lower in _HOP_BY_HOP:
                continue
            # httpx auto-decompresses the upstream's body (gzip / br /
            # deflate) when we read .content, so a forwarded
            # Content-Encoding header would mismatch the bytes we send.
            # The browser would try to decompress already-plain HTML and
            # render an empty page. Drop the encoding header — at the
            # current size of these responses (dashboard ~50KB, tool
            # JSONs much smaller) the lack of wire compression is
            # negligible.
            if name_lower == "content-encoding":
                continue
            response_headers.append((raw_name, raw_value))

        await send({
            "type": "http.response.start",
            "status": upstream_resp.status_code,
            "headers": response_headers,
        })
        await send({
            "type": "http.response.body",
            "body": upstream_resp.content,
            "more_body": False,
        })

    def status(self) -> dict[str, Any]:
        servers = []
        for name, state in self.servers.items():
            spec = self._specs.get(name)
            entry: dict[str, Any] = {
                "name": name,
                "running": state.running,
                "error": state.error,
                "primary": name == self._primary,
                # `builtin: true` lets the UI render the always-on row
                # differently (no Stop button, etc.).
                "builtin": name == BUILTIN_NAME,
            }
            if spec is not None:
                entry["transport"] = spec.transport
                entry["spec"] = spec.to_status()
            elif state.backend_info:
                entry["transport"] = state.backend_info.get("transport")
                entry["spec"] = dict(state.backend_info)
            servers.append(entry)
        # `running` reflects whether any USER backend is up. The builtin
        # is always up by design, so it's excluded from this aggregate
        # so the UI badge / curl probes still mean what they used to.
        return {
            "primary": self._primary,
            "servers": servers,
            "running": any(
                s.running for n, s in self.servers.items() if n != BUILTIN_NAME
            ),
        }

    def subscribe_logs(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=512)
        self._log_subscribers.append(q)
        return q

    def subscribe_logs_with_history(
        self,
    ) -> tuple[list[str], asyncio.Queue[str]]:
        """Snapshot the buffered history and atomically register a new
        subscriber queue.

        Both operations are synchronous, so they run without any
        ``await`` interleaving with ``_broadcast`` (the only writer of
        history and queues). That means new lines emitted after this
        call go to the queue, lines emitted before went to the
        snapshot, and there is no window for duplicates or drops.
        Callers should drain the snapshot first, then pull from the
        queue.
        """
        snapshot = list(self._log_history)
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=512)
        self._log_subscribers.append(q)
        return snapshot, q

    def unsubscribe_logs(self, q: asyncio.Queue[str]) -> None:
        try:
            self._log_subscribers.remove(q)
        except ValueError:
            pass

    def _broadcast(self, line: str) -> None:
        self._log_history.append(line)
        for q in list(self._log_subscribers):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                pass

    def _attach_log_pump(self, state: ProxyState) -> None:
        """Forward a child state's logs into the manager's subscriber set."""
        source = state.subscribe_logs()

        async def pump() -> None:
            try:
                while True:
                    line = await source.get()
                    self._broadcast(line)
            except asyncio.CancelledError:
                pass
            finally:
                state.unsubscribe_logs(source)

        task = asyncio.create_task(pump())
        self._log_pumps[state.name] = task

    async def _start_one_spec(self, state: ProxyState, spec: ServerSpec) -> None:
        await state.start(
            transport=spec.transport,
            command=spec.command,
            args=spec.args if spec.transport == "stdio" else None,
            url=spec.url,
            env=spec.env,
            cwd=spec.cwd,
            headers=spec.headers,
            compress=spec.compress,
        )
