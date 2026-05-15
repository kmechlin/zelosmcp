from __future__ import annotations

import asyncio
import logging
import shlex
import time
from contextlib import AsyncExitStack
from typing import Any, Callable

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import Tool

from zelosmcp.compression import (
    compressed_tool_list,
    handle_compressed_call,
    wrapper_tool_names,
)
from zelosmcp.config import CompressSpec, PassthroughPoolSpec
from zelosmcp.response import transform_response
from zelosmcp.savings import measure_call

logger = logging.getLogger("zelosmcp")


def _take_streams(ctx: Any) -> tuple[Any, Any]:
    """Normalize the (read, write[, get_session_id]) tuples used by client transports."""
    if isinstance(ctx, tuple):
        return ctx[0], ctx[1]
    return ctx


class ProxyState:
    """Manages the lifecycle of a single MCP backend and proxies requests to it."""

    def __init__(self, name: str = "mcp") -> None:
        self.name = name
        self.session_manager: StreamableHTTPSessionManager | None = None
        self.client_session: ClientSession | None = None
        self.running = False
        self.backend_info: dict[str, Any] = {}
        self.error: str | None = None
        self._log_subscribers: list[asyncio.Queue[str]] = []
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._startup_error: BaseException | None = None
        # Set on start() when the backend's compress.scope == "global" — the
        # per-backend session manager exposes the same wrapper tools the
        # aggregator does, so /<name>/mcp consumers see compressed schemas
        # instead of the raw backend surface. Otherwise stays None and the
        # session manager runs in passthrough mode.
        self._compress: CompressSpec | None = None
        # Cached full tool list when compression is active. Refreshed on
        # every list_tools call so the wrapper dispatcher resolves names
        # against whatever the backend currently advertises.
        self._compressed_catalog: dict[str, Tool] = {}
        # Optional callable returning the manager-owned savings recorder.
        # ProxyManager wires this in when it constructs the state so the
        # ``call_tool`` handlers can route through ``measure_call``. Stays
        # None for tests / standalone use, in which case measure_call
        # short-circuits to a plain await.
        self._recorder_provider: Callable[[], Any] | None = None
        # OAuth-passthrough mode (transport=http only). When True, no
        # client_session or session_manager is created — /<name>/mcp is
        # routed through manager.proxy_mcp_request instead. Inbound
        # Authorization is forwarded verbatim; 401 + WWW-Authenticate from
        # upstream is propagated so the MCP client (Cursor) handles the
        # OAuth dance directly with the upstream issuer.
        self.is_passthrough: bool = False
        # Backend URL retained for the passthrough dispatcher.
        self.passthrough_url: str | None = None
        # Static fallback bearer — injected only when the inbound request
        # has no Authorization header. None = no fallback.
        self.passthrough_auth_bearer: str | None = None
        # Pool sizing for Phase 2 aggregator integration. Stored here so
        # the lazy pool import (Phase 2A) can read it. None = use defaults.
        self.passthrough_pool_spec: PassthroughPoolSpec | None = None
        # Per-Cursor session pool for the aggregator's tools/list and
        # tools/call fan-out (Phase 2A). Lazily initialised when the pool
        # module is imported; stays None for Phase 1-only deployments.
        self.passthrough_pool: Any = None
        # Cached upstream tool catalog for passthrough backends. Populated
        # by the aggregator's list_tools / call_tool the first time it
        # gets a working session (any inbound user with a valid token
        # warms it for everyone). Subsequent inbound requests reuse the
        # cache so tools/list can render real wrapper-tool catalogs even
        # when the *current* caller has no token. Tool definitions don't
        # vary per user (only the data they return does), so global
        # caching is correct here.
        self.passthrough_catalog: dict[str, Tool] = {}

    def _emit_log(self, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [{self.name}] {message}"
        logger.info("[%s] %s", self.name, message)
        for q in list(self._log_subscribers):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                pass

    def subscribe_logs(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        self._log_subscribers.append(q)
        return q

    def unsubscribe_logs(self, q: asyncio.Queue[str]) -> None:
        try:
            self._log_subscribers.remove(q)
        except ValueError:
            pass

    async def start(
        self,
        transport: str,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        headers: dict[str, str] | None = None,
        compress: CompressSpec | None = None,
        passthrough: bool = False,
        auth_bearer: str | None = None,
        passthrough_pool: PassthroughPoolSpec | None = None,
        response_format: str = "toon",
    ) -> None:
        if self.running:
            raise RuntimeError("Proxy is already running — stop it first")

        self.error = None
        self._ready = asyncio.Event()
        self._startup_error = None
        self._response_format = response_format
        # Only retain compress for scope=global — that's the only case
        # where the per-backend /<name>/mcp endpoint should serve wrappers
        # instead of the raw surface. Aggregator-only and catalog-only
        # scopes are handled by the aggregator alone.
        self._compress = compress if (
            compress is not None and compress.scope == "global"
        ) else None

        # OAuth-passthrough lifecycle is structurally different (no
        # client_session, no StreamableHTTPSessionManager). Fork early so
        # _run_backend stays focused on session-bound transports.
        self.is_passthrough = bool(passthrough)
        self.passthrough_url = url if self.is_passthrough else None
        self.passthrough_auth_bearer = auth_bearer if self.is_passthrough else None
        self.passthrough_pool_spec = passthrough_pool if self.is_passthrough else None

        if self.is_passthrough:
            if transport != "http":
                raise ValueError(
                    "passthrough mode is only valid for transport=http"
                )
            if not url:
                raise ValueError("URL is required for passthrough HTTP transport")
            self._task = asyncio.create_task(
                self._run_passthrough_backend(url=url)
            )
        else:
            self._task = asyncio.create_task(
                self._run_backend(transport, command, args, url, env, cwd, headers)
            )

        await self._ready.wait()

        if self._startup_error is not None:
            raise self._startup_error

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_backend(
        self,
        transport: str,
        command: str | None,
        args: list[str] | None,
        url: str | None,
        env: dict[str, str] | None,
        cwd: str | None,
        headers: dict[str, str] | None,
    ) -> None:
        """Background task owning the full backend lifecycle.

        All async context managers are entered and exited in this single task,
        avoiding anyio cancel-scope cross-task issues.
        """
        self._emit_log(f"Starting backend ({transport})...")

        try:
            async with AsyncExitStack() as stack:
                if transport == "stdio":
                    if not command:
                        raise ValueError("Command is required for stdio transport")

                    if args is None:
                        # Backward-compat: split a joined string command.
                        parts = shlex.split(command)
                        cmd_exe, cmd_args = parts[0], parts[1:]
                        display = command
                    else:
                        cmd_exe, cmd_args = command, list(args)
                        display = " ".join([command, *cmd_args]) if cmd_args else command

                    params = StdioServerParameters(
                        command=cmd_exe,
                        args=cmd_args,
                        env=env,
                        cwd=cwd,
                    )
                    read, write = await stack.enter_async_context(stdio_client(params))
                    self.backend_info = {
                        "transport": "stdio",
                        "command": display,
                    }
                    if cmd_args:
                        self.backend_info["args"] = cmd_args
                    if env:
                        self.backend_info["env"] = env
                    if cwd:
                        self.backend_info["cwd"] = cwd
                    self._emit_log(f"Spawned subprocess: {display}")

                elif transport == "sse":
                    if not url:
                        raise ValueError("URL is required for SSE transport")
                    if headers:
                        cm = sse_client(url, headers=headers)
                    else:
                        cm = sse_client(url)
                    ctx = await stack.enter_async_context(cm)
                    read, write = _take_streams(ctx)
                    self.backend_info = {"transport": "sse", "url": url}
                    if headers:
                        self.backend_info["headers"] = headers
                    self._emit_log(f"Connected to SSE: {url}")

                elif transport == "http":
                    if not url:
                        raise ValueError("URL is required for HTTP transport")
                    if headers:
                        cm = streamablehttp_client(url, headers=headers)
                    else:
                        cm = streamablehttp_client(url)
                    ctx = await stack.enter_async_context(cm)
                    read, write = _take_streams(ctx)
                    self.backend_info = {"transport": "http", "url": url}
                    if headers:
                        self.backend_info["headers"] = headers
                    self._emit_log(f"Connected to HTTP: {url}")

                else:
                    raise ValueError(f"Unknown transport: {transport}")

                self.client_session = await stack.enter_async_context(ClientSession(read, write))
                await self.client_session.initialize()
                self._emit_log("MCP session initialized")

                mcp_server = Server(self.name)
                self._register_handlers(mcp_server)

                self.session_manager = StreamableHTTPSessionManager(
                    app=mcp_server,
                    event_store=None,
                    json_response=True,
                    stateless=True,
                )
                await stack.enter_async_context(self.session_manager.run())
                self.running = True
                self._emit_log(f"Proxy is live")

                self._ready.set()

                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self._emit_log("Stopping proxy...")

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.error = str(exc)
            self._emit_log(f"ERROR: {exc}")
            self._startup_error = exc
            self._ready.set()
        finally:
            self.session_manager = None
            self.client_session = None
            self.running = False
            self.backend_info = {}
            self._compressed_catalog = {}
            self._emit_log("Proxy stopped")

    async def _run_passthrough_backend(self, url: str) -> None:
        """Background task for OAuth-passthrough HTTP backends.

        Unlike :meth:`_run_backend`, this does NOT open an upstream MCP
        session — zelosMCP forwards traffic transparently and the MCP
        client (Cursor) owns the OAuth dance with the upstream issuer.
        The task exists solely so the lifecycle (start/stop, log pump,
        ready event) matches session-bound backends; the real work is
        done by ``manager.proxy_mcp_request`` per inbound request.

        Phase 2A wires :class:`PassthroughSessionPool` here so the
        aggregator can fan out tool calls; for Phase 1 the task is a
        sleep-until-cancelled placeholder.
        """
        self._emit_log(f"Starting passthrough HTTP backend -> {url}")
        try:
            self.backend_info = {
                "transport": "http",
                "url": url,
                "passthrough": True,
            }
            if self.passthrough_auth_bearer:
                # Mirror ServerSpec.to_status — never log or surface the
                # bearer; the redacted marker is enough for status views.
                self.backend_info["auth"] = {"bearer": "***"}
            if self.passthrough_pool_spec is not None:
                self.backend_info["passthroughPool"] = (
                    self.passthrough_pool_spec.to_status()
                )

            # Lazily wire the per-Cursor session pool when the module is
            # available (added in Phase 2A). Failure here must not abort
            # the backend — Phase 1 deployments that only use /<name>/mcp
            # don't need the pool at all, so an ImportError or any other
            # init failure is logged and the backend stays Phase-1 capable.
            try:
                from zelosmcp.passthrough_pool import PassthroughSessionPool

                spec = self.passthrough_pool_spec
                self.passthrough_pool = PassthroughSessionPool(
                    backend_name=self.name,
                    upstream_url=url,
                    max_sessions=(
                        spec.max_sessions
                        if spec is not None
                        else PassthroughPoolSpec().max_sessions
                    ),
                    idle_ttl_seconds=(
                        spec.idle_ttl_seconds
                        if spec is not None
                        else PassthroughPoolSpec().idle_ttl_seconds
                    ),
                    static_bearer=self.passthrough_auth_bearer,
                    log=self._emit_log,
                )
                await self.passthrough_pool.start()
            except ImportError:
                # Phase 2A module not yet present; per-backend mode still
                # works through manager.proxy_mcp_request.
                self.passthrough_pool = None
            except Exception as exc:
                self._emit_log(
                    f"WARN: passthrough pool init failed (aggregator "
                    f"integration disabled for {self.name}): {exc}"
                )
                self.passthrough_pool = None

            self.running = True
            self._emit_log("Passthrough proxy is live")
            self._ready.set()

            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self._emit_log("Stopping passthrough proxy...")

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.error = str(exc)
            self._emit_log(f"ERROR: {exc}")
            self._startup_error = exc
            self._ready.set()
        finally:
            if self.passthrough_pool is not None:
                try:
                    await self.passthrough_pool.close_all()
                except Exception as exc:
                    self._emit_log(f"WARN: pool shutdown error: {exc}")
            self.passthrough_pool = None
            self.running = False
            self.backend_info = {}
            self.is_passthrough = False
            self.passthrough_url = None
            self.passthrough_auth_bearer = None
            self.passthrough_pool_spec = None
            self.passthrough_catalog = {}
            self._emit_log("Passthrough proxy stopped")

    def _register_handlers(self, server: Server) -> None:
        session = self.client_session
        assert session is not None
        compress = self._compress  # captured at handler-registration time

        @server.list_tools()
        async def list_tools() -> list:
            r = await session.list_tools()
            backend_tools = list(r.tools or [])
            if compress is None:
                self._emit_log(f"list_tools -> {len(backend_tools)} tools")
                return backend_tools
            # scope=global: refresh the catalog cache and substitute the
            # wrapper tools. Empty prefix because /<name>/mcp consumers
            # already know the backend by URL.
            self._compressed_catalog = {t.name: t for t in backend_tools}
            wrapped = compressed_tool_list(
                prefix="", tools=backend_tools, level=compress.level
            )
            self._emit_log(f"list_tools -> {len(wrapped)} tools (compressed)")
            return wrapped

        @server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict[str, Any]):
            from mcp.types import CallToolResult

            recorder = (
                self._recorder_provider() if self._recorder_provider else None
            )
            qualified = f"{self.name}__{name}"

            if compress is not None and name in wrapper_tool_names(compress.level):
                self._emit_log(f"call_tool (compressed): {name}")
                return await measure_call(
                    recorder=recorder,
                    backend=self.name,
                    tool=name,
                    qualified=qualified,
                    compressed=True,
                    arguments=arguments,
                    dispatch=lambda: handle_compressed_call(
                        catalog=self._compressed_catalog,
                        op=name,
                        args=arguments,
                        dispatch=session.call_tool,
                        level=compress.level,
                    ),
                )
            self._emit_log(f"call_tool: {name}")

            async def _dispatch() -> CallToolResult:
                r = await session.call_tool(name, arguments)
                content = list(r.content)
                meta = getattr(r, "meta", None)
                meta_dict = (
                    dict(meta) if meta else None
                )
                content, meta_dict = transform_response(
                    content,
                    response_format=self._response_format,
                    meta=meta_dict,
                )
                return CallToolResult(
                    content=content,
                    structuredContent=getattr(
                        r, "structuredContent", None
                    ),
                    isError=bool(r.isError),
                    meta=meta_dict,
                )

            return await measure_call(
                recorder=recorder,
                backend=self.name,
                tool=name,
                qualified=qualified,
                compressed=False,
                arguments=arguments,
                dispatch=_dispatch,
            )

        @server.list_resources()
        async def list_resources() -> list:
            r = await session.list_resources()
            self._emit_log(f"list_resources -> {len(r.resources)} resources")
            return r.resources

        @server.list_resource_templates()
        async def list_resource_templates() -> list:
            r = await session.list_resource_templates()
            self._emit_log(
                f"list_resource_templates -> {len(r.resourceTemplates)} templates"
            )
            return r.resourceTemplates

        @server.read_resource()
        async def read_resource(uri) -> list:
            self._emit_log(f"read_resource: {uri}")
            r = await session.read_resource(uri)
            return r.contents

        @server.list_prompts()
        async def list_prompts() -> list:
            r = await session.list_prompts()
            self._emit_log(f"list_prompts -> {len(r.prompts)} prompts")
            return r.prompts

        @server.get_prompt()
        async def get_prompt(name: str, arguments: dict[str, str] | None = None):
            self._emit_log(f"get_prompt: {name}")
            return await session.get_prompt(name, arguments)
