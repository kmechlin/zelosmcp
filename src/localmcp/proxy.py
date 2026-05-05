from __future__ import annotations

import asyncio
import logging
import shlex
import time
from contextlib import AsyncExitStack
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

logger = logging.getLogger("localmcp")


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
    ) -> None:
        if self.running:
            raise RuntimeError("Proxy is already running — stop it first")

        self.error = None
        self._ready = asyncio.Event()
        self._startup_error = None

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
            self._emit_log("Proxy stopped")

    def _register_handlers(self, server: Server) -> None:
        session = self.client_session
        assert session is not None

        @server.list_tools()
        async def list_tools() -> list:
            r = await session.list_tools()
            return r.tools

        @server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict[str, Any]):
            self._emit_log(f"call_tool: {name}")
            r = await session.call_tool(name, arguments)
            # Pass through both content and structuredContent unchanged. The
            # MCP SDK's lowlevel server validates: if the advertised tool has
            # an outputSchema and the response's structuredContent is None,
            # it replaces the response with a validation error. Returning
            # only `r.content` would drop the backend's structuredContent
            # (when set) and trip that validation for any tool with a
            # declared outputSchema (e.g. filesystem, anything
            # using FastMCP/SDK >=1.13 with auto-generated schemas).
            from mcp.types import CallToolResult

            return CallToolResult(
                content=r.content,
                structuredContent=getattr(r, "structuredContent", None),
                isError=bool(r.isError),
            )

        @server.list_resources()
        async def list_resources() -> list:
            r = await session.list_resources()
            return r.resources

        @server.list_resource_templates()
        async def list_resource_templates() -> list:
            r = await session.list_resource_templates()
            return r.resourceTemplates

        @server.read_resource()
        async def read_resource(uri) -> list:
            self._emit_log(f"read_resource: {uri}")
            r = await session.read_resource(uri)
            return r.contents

        @server.list_prompts()
        async def list_prompts() -> list:
            r = await session.list_prompts()
            return r.prompts

        @server.get_prompt()
        async def get_prompt(name: str, arguments: dict[str, str] | None = None):
            self._emit_log(f"get_prompt: {name}")
            return await session.get_prompt(name, arguments)
