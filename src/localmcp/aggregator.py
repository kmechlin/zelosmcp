"""Single MCP endpoint that fans tools, prompts, and resources across every running backend.

Tools and prompts are surfaced under the qualified name ``<server>__<original>``
so the same logical name can exist on multiple backends without collision;
``call_tool`` / ``get_prompt`` split the prefix back off and forward to the
matching backend's :class:`mcp.client.session.ClientSession`.

Resources keep their original URIs (no prefixing). The aggregator tracks
``URI -> backend`` as a side effect of ``list_resources`` calls and uses that
table to route ``read_resource`` back to the originating backend, with a
fan-out fallback for URIs that were never explicitly listed (typically reads
of templated URIs).
"""
from __future__ import annotations

import asyncio
import base64
import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import McpError
from mcp.types import METHOD_NOT_FOUND

if TYPE_CHECKING:
    from localmcp.manager import ProxyManager
    from localmcp.proxy import ProxyState

logger = logging.getLogger("localmcp")

SEP = "__"


class Aggregator:
    """Lifecycle owner of the aggregating MCP Server mounted at ``/mcp``."""

    def __init__(self, manager: "ProxyManager") -> None:
        self.manager = manager
        self.session_manager: StreamableHTTPSessionManager | None = None
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._startup_error: BaseException | None = None
        self._resource_origin: dict[str, str] = {}

    @property
    def running(self) -> bool:
        return self.session_manager is not None

    async def start(self) -> None:
        if self.running:
            return
        self._ready = asyncio.Event()
        self._startup_error = None
        self._task = asyncio.create_task(self._run())
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

    def _log(self, message: str) -> None:
        logger.info("[aggregator] %s", message)
        self.manager._broadcast(f"[aggregator] {message}")

    def _running_states(self) -> list["ProxyState"]:
        return [
            s for s in self.manager.servers.values()
            if s.running and s.client_session is not None
        ]

    async def _run(self) -> None:
        """Lifecycle task — mirrors the anyio-safe pattern in ProxyState._run_backend."""
        self._resource_origin.clear()
        try:
            async with AsyncExitStack() as stack:
                server = Server("localmcp-aggregate")
                self._register_handlers(server)

                self.session_manager = StreamableHTTPSessionManager(
                    app=server,
                    event_store=None,
                    json_response=True,
                    stateless=True,
                )
                await stack.enter_async_context(self.session_manager.run())
                self._log("Aggregator live at /mcp")
                self._ready.set()

                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self._log("Stopping aggregator...")

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._startup_error = exc
            self._log(f"ERROR: {exc}")
            self._ready.set()
        finally:
            self.session_manager = None

    def _register_handlers(self, server: Server) -> None:
        @server.list_tools()
        async def list_tools() -> list:
            states = self._running_states()
            if not states:
                return []
            results = await asyncio.gather(
                *(s.client_session.list_tools() for s in states),
                return_exceptions=True,
            )
            tools: list = []
            for state, r in zip(states, results):
                if isinstance(r, BaseException):
                    if not _is_method_not_found(r):
                        self._log(f"{state.name} list_tools failed: {r}")
                    continue
                for t in getattr(r, "tools", []) or []:
                    tools.append(_qualified_copy(t, state.name))
            return tools

        @server.call_tool(validate_input=False)
        async def call_tool(qualified_name: str, arguments: dict[str, Any]) -> list:
            backend, original = _split_qualified(qualified_name)
            state = self.manager.servers.get(backend) if original else None
            if state is None or not state.running or state.client_session is None:
                return _error_tool_result(
                    f"Unknown or unavailable tool '{qualified_name}'"
                )
            self._log(f"call_tool: {qualified_name}")
            r = await state.client_session.call_tool(original, arguments)
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

        @server.list_prompts()
        async def list_prompts() -> list:
            states = self._running_states()
            if not states:
                return []
            results = await asyncio.gather(
                *(s.client_session.list_prompts() for s in states),
                return_exceptions=True,
            )
            prompts: list = []
            for state, r in zip(states, results):
                if isinstance(r, BaseException):
                    if not _is_method_not_found(r):
                        self._log(f"{state.name} list_prompts failed: {r}")
                    continue
                for p in getattr(r, "prompts", []) or []:
                    prompts.append(_qualified_copy(p, state.name))
            return prompts

        @server.get_prompt()
        async def get_prompt(qualified_name: str, arguments: dict[str, str] | None = None):
            backend, original = _split_qualified(qualified_name)
            state = self.manager.servers.get(backend) if original else None
            if state is None or not state.running or state.client_session is None:
                from mcp.types import GetPromptResult, PromptMessage, TextContent
                return GetPromptResult(
                    description=f"Unknown or unavailable prompt '{qualified_name}'",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type="text",
                                text=f"Unknown or unavailable prompt '{qualified_name}'",
                            ),
                        )
                    ],
                )
            self._log(f"get_prompt: {qualified_name}")
            return await state.client_session.get_prompt(original, arguments)

        @server.list_resources()
        async def list_resources() -> list:
            states = self._running_states()
            if not states:
                return []
            results = await asyncio.gather(
                *(s.client_session.list_resources() for s in states),
                return_exceptions=True,
            )
            out: list = []
            for state, r in zip(states, results):
                if isinstance(r, BaseException):
                    if not _is_method_not_found(r):
                        self._log(f"{state.name} list_resources failed: {r}")
                    continue
                for res in getattr(r, "resources", []) or []:
                    self._resource_origin[str(res.uri)] = state.name
                    out.append(res)
            return out

        @server.list_resource_templates()
        async def list_resource_templates() -> list:
            states = self._running_states()
            if not states:
                return []
            results = await asyncio.gather(
                *(s.client_session.list_resource_templates() for s in states),
                return_exceptions=True,
            )
            out: list = []
            for state, r in zip(states, results):
                if isinstance(r, BaseException):
                    if not _is_method_not_found(r):
                        self._log(f"{state.name} list_resource_templates failed: {r}")
                    continue
                for tmpl in getattr(r, "resourceTemplates", []) or []:
                    out.append(tmpl)
            return out

        @server.read_resource()
        async def read_resource(uri):
            uri_str = str(uri)
            already_tried: set[str] = set()
            last_error: BaseException | None = None

            # Tracked owner first.
            owner_name = self._resource_origin.get(uri_str)
            if owner_name:
                state = self.manager.servers.get(owner_name)
                if state and state.running and state.client_session is not None:
                    try:
                        self._log(f"read_resource: {uri_str} -> {owner_name}")
                        r = await state.client_session.read_resource(uri)
                        return _to_internal_contents(r.contents)
                    except Exception as exc:
                        last_error = exc
                        self._log(
                            f"{owner_name} read_resource '{uri_str}' failed; "
                            f"falling back: {exc}"
                        )
                        self._resource_origin.pop(uri_str, None)
                        already_tried.add(owner_name)
                else:
                    # Cached owner is no longer available; drop and fall through.
                    self._resource_origin.pop(uri_str, None)

            # Fan out across remaining backends; first success wins and is cached.
            for state in self._running_states():
                if state.name in already_tried:
                    continue
                try:
                    r = await state.client_session.read_resource(uri)
                except Exception as exc:
                    last_error = exc
                    continue
                self._resource_origin[uri_str] = state.name
                self._log(f"read_resource: {uri_str} -> {state.name} (fan-out)")
                return _to_internal_contents(r.contents)

            if last_error is not None:
                raise last_error
            raise RuntimeError(f"No backend available to serve '{uri_str}'")


def _is_method_not_found(exc: BaseException) -> bool:
    """True if ``exc`` is the JSON-RPC ``-32601`` response from a backend that
    simply doesn't advertise this capability (e.g. ``@modelcontextprotocol/
    server-filesystem`` doesn't implement ``prompts/list`` or
    ``resources/list``). Aggregator fans out list calls to every running
    backend in parallel and treats those replies as "skip this backend";
    they're protocol-level *expected* and shouldn't pollute the log."""
    return isinstance(exc, McpError) and exc.error.code == METHOD_NOT_FOUND


def _qualified_copy(item: Any, server_name: str):
    """Return a shallow copy of ``item`` with its ``name`` rewritten to qualified form."""
    qualified = f"{server_name}{SEP}{item.name}"
    # Pydantic v2 models expose model_copy; fall back gracefully.
    if hasattr(item, "model_copy"):
        return item.model_copy(update={"name": qualified})
    item.name = qualified
    return item


def _to_internal_contents(contents: Any) -> list[ReadResourceContents]:
    """Convert wire-protocol resource contents into the dataclass the Server expects.

    ``ClientSession.read_resource`` returns ``TextResourceContents`` /
    ``BlobResourceContents`` (the wire types with ``.text``/``.blob`` and
    ``.mimeType``). The low-level ``Server.read_resource`` decorator expects an
    iterable of :class:`ReadResourceContents` (``.content``, ``.mime_type``).
    """
    out: list[ReadResourceContents] = []
    for item in contents or []:
        if hasattr(item, "text") and item.text is not None:
            data: str | bytes = item.text
        elif hasattr(item, "blob") and item.blob is not None:
            try:
                data = base64.b64decode(item.blob)
            except Exception:
                data = item.blob
        else:
            continue
        out.append(
            ReadResourceContents(
                content=data,
                mime_type=getattr(item, "mimeType", None),
                meta=getattr(item, "meta", None),
            )
        )
    return out


def _split_qualified(qualified: str) -> tuple[str, str]:
    """Split ``"<server>__<original>"`` → (server, original); ("", "") if malformed."""
    if not isinstance(qualified, str):
        return "", ""
    head, sep, tail = qualified.partition(SEP)
    if not sep or not tail:
        return "", ""
    return head, tail


def _error_tool_result(message: str):
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        isError=True,
    )
