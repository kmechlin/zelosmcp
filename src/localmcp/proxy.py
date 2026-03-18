from __future__ import annotations

import asyncio
import logging
import shlex
import time
from contextlib import AsyncExitStack
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

logger = logging.getLogger("localmcp")


class ProxyState:
    """Manages the lifecycle of a single MCP backend and proxies requests to it."""

    def __init__(self) -> None:
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
        line = f"[{ts}] {message}"
        logger.info(message)
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
        url: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        if self.running:
            raise RuntimeError("Proxy is already running — stop it first")

        self.error = None
        self._ready = asyncio.Event()
        self._startup_error = None

        self._task = asyncio.create_task(
            self._run_backend(transport, command, url, env)
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

    async def _run_backend(self, transport: str, command: str | None, url: str | None, env: dict[str, str] | None) -> None:
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
                    parts = shlex.split(command)
                    params = StdioServerParameters(
                        command=parts[0], args=parts[1:], env=env,
                    )
                    read, write = await stack.enter_async_context(stdio_client(params))
                    self.backend_info = {"transport": "stdio", "command": command}
                    if env:
                        self.backend_info["env"] = env
                    self._emit_log(f"Spawned subprocess: {command}")

                elif transport == "sse":
                    if not url:
                        raise ValueError("URL is required for SSE transport")
                    from mcp.client.sse import sse_client

                    read, write = await stack.enter_async_context(sse_client(url))
                    self.backend_info = {"transport": "sse", "url": url}
                    self._emit_log(f"Connected to SSE: {url}")

                elif transport == "http":
                    if not url:
                        raise ValueError("URL is required for HTTP transport")
                    from mcp.client.streamable_http import streamable_http_client

                    read, write = await stack.enter_async_context(streamable_http_client(url))
                    self.backend_info = {"transport": "http", "url": url}
                    self._emit_log(f"Connected to HTTP: {url}")

                else:
                    raise ValueError(f"Unknown transport: {transport}")

                self.client_session = await stack.enter_async_context(ClientSession(read, write))
                await self.client_session.initialize()
                self._emit_log("MCP session initialized")

                mcp_server = Server("localmcp")
                self._register_handlers(mcp_server)

                self.session_manager = StreamableHTTPSessionManager(
                    app=mcp_server,
                    event_store=None,
                    json_response=True,
                    stateless=True,
                )
                await stack.enter_async_context(self.session_manager.run())
                self.running = True
                self._emit_log("Proxy is live at /mcp")

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
        async def call_tool(name: str, arguments: dict[str, Any]) -> list:
            self._emit_log(f"call_tool: {name}")
            r = await session.call_tool(name, arguments)
            if r.isError:
                from mcp.types import CallToolResult

                return CallToolResult(content=r.content, isError=True)
            return r.content

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
