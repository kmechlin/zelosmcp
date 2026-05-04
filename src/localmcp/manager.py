"""Coordinates many ProxyState instances behind one HTTP surface.

The manager is what `app.py` actually talks to. It owns a per-name registry of
:class:`localmcp.proxy.ProxyState` objects, tracks which one is the primary
(mirrored at ``/mcp`` instead of just ``/<name>/mcp``), and aggregates log
subscriptions across all servers.
"""
from __future__ import annotations

import asyncio
from typing import Any

from localmcp.aggregator import Aggregator
from localmcp.builtin import NAME as BUILTIN_NAME, BuiltinServer
from localmcp.config import ServerSpec, parse_config
from localmcp.proxy import ProxyState


class ProxyManager:
    """Lifecycle owner for many ProxyStates plus the /mcp aggregator."""

    def __init__(self) -> None:
        # ``self.servers`` mixes user-configured ProxyStates with the
        # always-on BuiltinServer (under the reserved key "localmcp").
        # The builtin is ProxyState-shaped, so the dispatcher and
        # aggregator iterate over it transparently. Lifecycle methods
        # (start_all/stop_all/start_one/stop_one) explicitly skip it.
        self.servers: dict[str, Any] = {}
        self._specs: dict[str, ServerSpec] = {}
        self._primary: str | None = None
        self._log_subscribers: list[asyncio.Queue[str]] = []
        self._log_pumps: dict[str, asyncio.Task] = {}
        self.aggregator = Aggregator(self)
        # The BuiltinServer is ProxyState-shaped and pre-seeded so the
        # dispatcher can route /localmcp/mcp from the very first request.
        # Its log pump is attached lazily from `start_builtin()` because
        # `_attach_log_pump` requires a running event loop.
        self.builtin = BuiltinServer(self)
        self.servers[BUILTIN_NAME] = self.builtin

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

        Stops anything currently running, parses the config, concurrently starts
        each backend, then brings up the aggregator at ``/mcp``. Returns a
        per-server result map.
        """
        await self.stop_all()

        specs, primary = parse_config(raw_config)
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

    def unsubscribe_logs(self, q: asyncio.Queue[str]) -> None:
        try:
            self._log_subscribers.remove(q)
        except ValueError:
            pass

    def _broadcast(self, line: str) -> None:
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
        )
