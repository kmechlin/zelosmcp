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
from mcp.types import METHOD_NOT_FOUND, Tool

from zelosmcp.compression import (
    compressed_tool_list,
    handle_compressed_call,
    wrapper_tool_names,
)
from zelosmcp.passthrough_pool import (
    PassthroughChallengeError,
    hash_authorization,
    inbound_authorization,
    signal_challenge,
)
from zelosmcp.response import transform_response
from zelosmcp.savings import (
    measure_call,
    measure_event,
    render_call_output_text,
)

if TYPE_CHECKING:
    from zelosmcp.manager import ProxyManager
    from zelosmcp.proxy import ProxyState

logger = logging.getLogger("zelosmcp")

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
        # Cached full tool catalog per compressed backend, populated on
        # every list_tools(). The compression scope determines whether
        # the aggregator's own tools/list output uses the wrappers (in
        # {aggregator, global}) or the full prefixed list (catalog).
        # Either way the cache is filled so /zelosmcp/list_compressed_tools
        # and the cursor-rule generator can render the compressed view.
        self.compressed_catalog: dict[str, dict[str, Tool]] = {}

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
        self.manager._broadcast_tagged("aggregator", message)

    def _running_states(self) -> list["ProxyState"]:
        return [
            s for s in self.manager.servers.values()
            if s.running and s.client_session is not None
        ]

    def _passthrough_states(self) -> list["ProxyState"]:
        """Running passthrough backends. Distinct from
        :meth:`_running_states` because these have no shared
        ``client_session`` — each request resolves its own session via
        the per-backend :class:`PassthroughSessionPool`.
        """
        return [
            s for s in self.manager.servers.values()
            if s.running
            and getattr(s, "is_passthrough", False)
            and getattr(s, "passthrough_pool", None) is not None
        ]

    async def _passthrough_session(
        self, state: "ProxyState"
    ):
        """Fetch (or create) a per-Cursor :class:`ClientSession` for one
        passthrough backend, keyed by the inbound HTTP Authorization.

        When the backend has an ``auth.provider`` configured AND the
        provider returns a token via :meth:`AuthProvider.mint_token`,
        the minted token replaces the inbound Authorization on the
        upstream connection. This is what lets zelosMCP forward
        per-user GitHub OAuth tokens to ``api.githubcopilot.com/mcp/``
        even though Cursor's MCP transport never saw the OAuth dance.

        When no provider is configured (legacy passthrough) or the
        provider returns ``None`` (e.g. user not authenticated yet),
        the inbound Authorization is forwarded verbatim — same
        behaviour as before this PR.

        Raises :class:`PassthroughChallengeError` when the upstream
        returns 401 — the caller decides whether to surface (call_tool)
        or skip silently (list_tools).
        """
        pool = state.passthrough_pool
        if pool is None:
            raise PassthroughChallengeError(
                backend=state.name,
                www_authenticate='Bearer error="invalid_token"',
            )
        auth = inbound_authorization.get()
        spec = self.manager._specs.get(state.name)
        provider = self.manager.auth_registry.get_for_backend(
            state.name,
            spec.auth_provider if spec is not None else None,
        )
        if provider is not None:
            user_key = hash_authorization(auth)
            try:
                minted = await provider.mint_token(
                    user_key,
                    spec.auth_audience if spec is not None else None,
                )
            except Exception as exc:
                self._log(
                    f"{state.name} provider '{provider.name}' "
                    f"mint_token failed: {exc}; falling back to inbound auth"
                )
                minted = None
            if minted is not None:
                auth = minted
        return await pool.get_or_create(auth)

    async def _run(self) -> None:
        """Lifecycle task — mirrors the anyio-safe pattern in ProxyState._run_backend."""
        self._resource_origin.clear()
        try:
            async with AsyncExitStack() as stack:
                server = Server("zelosmcp-aggregate")
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
            event_recorder = getattr(self.manager, "events", None)
            returned_count = 0
            snapshot_count = 0
            backend_count = 0

            async def _dispatch() -> list:
                nonlocal backend_count, returned_count, snapshot_count
                states = self._running_states()
                passthrough_states = list(self._passthrough_states())
                backend_count = len(states) + len(passthrough_states)
                self.compressed_catalog.clear()
                tools: list = []
                snapshots: list[tuple[str, str, list, list]] = []
                if states:
                    results = await asyncio.gather(
                        *(s.client_session.list_tools() for s in states),
                        return_exceptions=True,
                    )
                    for state, r in zip(states, results):
                        if isinstance(r, BaseException):
                            if not _is_method_not_found(r):
                                self._log(f"{state.name} list_tools failed: {r}")
                            continue
                        spec = self.manager._specs.get(state.name)
                        _bcfg = getattr(self.manager, "_builtin_config", None)
                        compress = (
                            spec.compress if spec is not None
                            else (_bcfg.compress if _bcfg is not None else None)
                        )
                        backend_tools = list(getattr(r, "tools", []) or [])
                        if compress is not None:
                            self.compressed_catalog[state.name] = {
                                t.name: t for t in backend_tools
                            }
                        applies_to_aggregator = (
                            compress is not None
                            and compress.scope in ("aggregator", "global")
                            and compress.level != "low"
                        )
                        if compress is not None:
                            raw_payload = [
                                _tool_to_wire(_qualified_copy(t, state.name))
                                for t in backend_tools
                            ]
                            compressed_payload = [
                                _tool_to_wire(t)
                                for t in compressed_tool_list(
                                    prefix=state.name,
                                    tools=backend_tools,
                                    level=compress.level,
                                )
                            ]
                            snapshots.append(
                                (
                                    state.name,
                                    compress.level,
                                    raw_payload,
                                    compressed_payload,
                                )
                            )
                        if not applies_to_aggregator:
                            for t in backend_tools:
                                tools.append(_qualified_copy(t, state.name))
                            continue
                        tools.extend(
                            compressed_tool_list(
                                prefix=state.name,
                                tools=backend_tools,
                                level=compress.level,
                            )
                        )

                user_key = hash_authorization(inbound_authorization.get())
                for state in passthrough_states:
                    spec = self.manager._specs.get(state.name)
                    compress = spec.compress if spec is not None else None
                    if compress is None:
                        continue

                    auth_provider = self.manager.auth_registry.get_for_backend(
                        state.name,
                        spec.auth_provider if spec is not None else None,
                    )
                    if auth_provider is not None:
                        try:
                            ready = await auth_provider.is_ready(user_key)
                        except Exception as exc:
                            self._log(
                                f"{state.name} auth provider "
                                f"'{auth_provider.name}' is_ready failed: {exc}; "
                                "treating as not-ready and gating wrappers"
                            )
                            ready = False
                        if not ready:
                            continue

                    backend_tools: list = []
                    auth_pending = False
                    try:
                        session = await self._passthrough_session(state)
                        try:
                            r = await session.list_tools()
                            backend_tools = list(getattr(r, "tools", []) or [])
                            state.passthrough_catalog = {
                                t.name: t for t in backend_tools
                            }
                        except Exception as exc:
                            if not _is_method_not_found(exc):
                                self._log(f"{state.name} list_tools failed: {exc}")
                            backend_tools = list(state.passthrough_catalog.values())
                    except PassthroughChallengeError:
                        if state.passthrough_catalog:
                            backend_tools = list(state.passthrough_catalog.values())
                        else:
                            auth_pending = True
                    except Exception as exc:
                        self._log(
                            f"{state.name} list_tools session failed: {exc}"
                        )
                        backend_tools = list(state.passthrough_catalog.values())

                    if backend_tools:
                        self.compressed_catalog[state.name] = {
                            t.name: t for t in backend_tools
                        }

                    tools.extend(
                        compressed_tool_list(
                            prefix=state.name,
                            tools=backend_tools,
                            level=compress.level,
                            auth_pending=auth_pending,
                        )
                    )

                returned_count = len(tools)
                snapshot_count = len(snapshots)
                self._log(f"list_tools -> {returned_count} tools")
                recorder = getattr(self.manager, "savings", None)
                if recorder is not None and snapshots:
                    for backend_name, level, raw_payload, comp_payload in snapshots:
                        asyncio.create_task(
                            recorder.record_compression(
                                backend=backend_name,
                                level=level,
                                raw_payload=raw_payload,
                                compressed_payload=comp_payload,
                            )
                        )
                return tools

            return await measure_event(
                recorder=event_recorder,
                method="tools/list",
                backend=None,
                dispatch=_dispatch,
                compressed_provider=lambda: snapshot_count > 0,
                meta_provider=lambda _result: {
                    "backend_count": backend_count,
                    "returned_count": returned_count,
                    "snapshot_count": snapshot_count,
                },
            )

        @server.call_tool(validate_input=False)
        async def call_tool(qualified_name: str, arguments: dict[str, Any]) -> list:
            backend, original = _split_qualified(qualified_name)
            recorder = getattr(self.manager, "savings", None)
            event_recorder = getattr(self.manager, "events", None)

            # Resolve per-backend response format.
            _spec = self.manager._specs.get(backend) if backend else None
            _builtin_cfg = getattr(
                self.manager, "_builtin_config", None
            )
            _resp_fmt = (
                _spec.response_format
                if _spec is not None
                else (
                    _builtin_cfg.response_format
                    if _builtin_cfg is not None
                    else "raw"
                )
            )

            # Compression-wrapper interception. Two trigger conditions:
            #   (a) Session-bound backend in compressed_catalog (legacy
            #       behaviour) AND the call name matches a wrapper.
            #   (b) Passthrough backend with compression configured AND
            #       the call name matches a wrapper. This branch fires
            #       even when the catalog isn't cached yet — that's the
            #       whole point of the lazy-OAuth-on-invoke design.
            if backend:
                spec = self.manager._specs.get(backend)
                _bcfg2 = getattr(
                    self.manager, "_builtin_config", None
                )
                state = self.manager.servers.get(backend)
                _comp = (
                    spec.compress
                    if spec is not None
                    else (
                        _bcfg2.compress
                        if _bcfg2 is not None
                        else None
                    )
                )
                level = (
                    _comp.level if _comp is not None
                    else "medium"
                )
                is_wrapper_call = original in wrapper_tool_names(level)
                is_session_wrapper = (
                    is_wrapper_call and backend in self.compressed_catalog
                )
                is_passthrough_wrapper = (
                    is_wrapper_call
                    and spec is not None
                    and getattr(spec, "passthrough", False)
                    and spec.compress is not None
                    and state is not None
                    and getattr(state, "is_passthrough", False)
                )

                if is_passthrough_wrapper:
                    if state is None or not state.running:
                        return _error_tool_result(
                            f"Unknown or unavailable tool '{qualified_name}'"
                        )
                    # Lazily open the upstream session for the inbound
                    # caller. On 401 the middleware in app.py rewrites
                    # the response to 401 + WWW-Authenticate, so Cursor
                    # opens a browser flow with the upstream issuer.
                    try:
                        session = await self._passthrough_session(state)
                    except PassthroughChallengeError as exc:
                        signal_challenge(exc)
                        return _error_tool_result(
                            f"backend '{backend}' requires authentication"
                        )
                    # Refresh the catalog cache if empty so handle_compressed_call
                    # can validate tool_name. Catalog is shared across users
                    # because the tool list itself doesn't vary per identity.
                    if not state.passthrough_catalog:
                        try:
                            r = await session.list_tools()
                            state.passthrough_catalog = {
                                t.name: t
                                for t in (getattr(r, "tools", []) or [])
                            }
                            self.compressed_catalog[backend] = (
                                state.passthrough_catalog
                            )
                        except PassthroughChallengeError as exc:
                            signal_challenge(exc)
                            return _error_tool_result(
                                f"backend '{backend}' requires authentication"
                            )
                        except Exception as exc:
                            return _error_tool_result(
                                f"backend '{backend}' catalog fetch failed: {exc}"
                            )

                    catalog = state.passthrough_catalog
                    self._log(f"call_tool (compressed passthrough): {qualified_name}")

                    async def _passthrough_dispatch():
                        try:
                            return await handle_compressed_call(
                                catalog=catalog,
                                op=original,
                                args=arguments,
                                dispatch=session.call_tool,
                                level=level,
                            )
                        except PassthroughChallengeError as exc:
                            # Token expired mid-call. Surface for OAuth
                            # refresh just like the open-session path.
                            signal_challenge(exc)
                            from mcp.types import CallToolResult
                            return CallToolResult(
                                content=[],
                                structuredContent=None,
                                isError=True,
                                meta=None,
                            )

                    return await measure_call(
                        recorder=recorder,
                        event_recorder=event_recorder,
                        backend=backend,
                        tool=original,
                        qualified=qualified_name,
                        compressed=True,
                        arguments=arguments,
                        dispatch=_passthrough_dispatch,
                    )

                if is_session_wrapper:
                    if state is None or not state.running or state.client_session is None:
                        return _error_tool_result(
                            f"Unknown or unavailable tool '{qualified_name}'"
                        )
                    self._log(f"call_tool (compressed): {qualified_name}")
                    return await measure_call(
                        recorder=recorder,
                        event_recorder=event_recorder,
                        backend=backend,
                        tool=original,
                        qualified=qualified_name,
                        compressed=True,
                        arguments=arguments,
                        dispatch=lambda: handle_compressed_call(
                            catalog=self.compressed_catalog[backend],
                            op=original,
                            args=arguments,
                            dispatch=state.client_session.call_tool,
                            level=level,
                        ),
                    )
            state = self.manager.servers.get(backend) if original else None
            if state is None or not state.running:
                return _error_tool_result(
                    f"Unknown or unavailable tool '{qualified_name}'"
                )
            self._log(f"call_tool: {qualified_name}")
            from mcp.types import CallToolResult

            # Passthrough backends resolve a per-Cursor session lazily.
            # On a PassthroughChallengeError we set the side-channel
            # ContextVar — the ASGI middleware in app.py reads it after
            # handle_request returns and rewrites the response to 401 +
            # WWW-Authenticate. We can't surface the 401 by raising:
            # the MCP SDK catches handler exceptions and serialises them
            # as JSON-RPC error envelopes (HTTP 200), which would never
            # trigger the client's OAuth handler.
            if getattr(state, "is_passthrough", False):
                try:
                    session = await self._passthrough_session(state)
                except PassthroughChallengeError as exc:
                    signal_challenge(exc)
                    return _error_tool_result(
                        f"backend '{backend}' requires authentication"
                    )
                raw_output_tokens: int | None = None
                raw_output_bytes: int | None = None
                transform_type: str | None = None

                async def _dispatch_passthrough() -> CallToolResult:
                    nonlocal raw_output_tokens, raw_output_bytes, transform_type
                    try:
                        r = await session.call_tool(original, arguments)
                    except PassthroughChallengeError as exc:
                        signal_challenge(exc)
                        return CallToolResult(
                            content=[],
                            structuredContent=None,
                            isError=True,
                            meta=None,
                        )
                    if event_recorder is not None:
                        try:
                            raw_output_text = render_call_output_text(r)
                        except Exception:
                            raw_output_text = ""
                        raw_output_tokens = event_recorder.count_payload(
                            raw_output_text
                        )
                        raw_output_bytes = len(raw_output_text.encode("utf-8"))
                        transform_type = _resp_fmt
                    content = list(r.content)
                    meta = getattr(r, "meta", None)
                    meta_dict = (
                        dict(meta) if meta else None
                    )
                    content, meta_dict = transform_response(
                        content,
                        response_format=_resp_fmt,
                        meta=meta_dict,
                    )
                    return CallToolResult(
                        content=content,
                        structuredContent=getattr(r, "structuredContent", None),
                        isError=bool(r.isError),
                        meta=meta_dict,
                    )

                return await measure_call(
                    recorder=recorder,
                    event_recorder=event_recorder,
                    backend=backend,
                    tool=original,
                    qualified=qualified_name,
                    compressed=False,
                    arguments=arguments,
                    dispatch=_dispatch_passthrough,
                    raw_output_tokens_provider=lambda: raw_output_tokens,
                    raw_output_bytes_provider=lambda: raw_output_bytes,
                    transform_type_provider=lambda: transform_type,
                )

            if state.client_session is None:
                return _error_tool_result(
                    f"Unknown or unavailable tool '{qualified_name}'"
                )

            raw_output_tokens: int | None = None
            raw_output_bytes: int | None = None
            transform_type: str | None = None

            async def _dispatch() -> CallToolResult:
                nonlocal raw_output_tokens, raw_output_bytes, transform_type
                r = await state.client_session.call_tool(original, arguments)
                if event_recorder is not None:
                    try:
                        raw_output_text = render_call_output_text(r)
                    except Exception:
                        raw_output_text = ""
                    raw_output_tokens = event_recorder.count_payload(
                        raw_output_text
                    )
                    raw_output_bytes = len(raw_output_text.encode("utf-8"))
                    transform_type = _resp_fmt
                content = list(r.content)
                meta = getattr(r, "meta", None)
                meta_dict = (
                    dict(meta) if meta else None
                )
                content, meta_dict = transform_response(
                    content,
                    response_format=_resp_fmt,
                    meta=meta_dict,
                )
                return CallToolResult(
                    content=content,
                    structuredContent=getattr(r, "structuredContent", None),
                    isError=bool(r.isError),
                    meta=meta_dict,
                )

            return await measure_call(
                recorder=recorder,
                event_recorder=event_recorder,
                backend=backend,
                tool=original,
                qualified=qualified_name,
                compressed=False,
                arguments=arguments,
                dispatch=_dispatch,
                raw_output_tokens_provider=lambda: raw_output_tokens,
                raw_output_bytes_provider=lambda: raw_output_bytes,
                transform_type_provider=lambda: transform_type,
            )

        @server.list_prompts()
        async def list_prompts() -> list:
            event_recorder = getattr(self.manager, "events", None)
            prompt_count = 0
            backend_count = 0

            async def _dispatch() -> list:
                nonlocal backend_count, prompt_count
                states = self._running_states()
                passthrough_states = list(self._passthrough_states())
                backend_count = len(states) + len(passthrough_states)
                prompts: list = []
                if states:
                    results = await asyncio.gather(
                        *(s.client_session.list_prompts() for s in states),
                        return_exceptions=True,
                    )
                    for state, r in zip(states, results):
                        if isinstance(r, BaseException):
                            if not _is_method_not_found(r):
                                self._log(f"{state.name} list_prompts failed: {r}")
                            continue
                        for p in getattr(r, "prompts", []) or []:
                            prompts.append(_qualified_copy(p, state.name))

                for state in passthrough_states:
                    try:
                        session = await self._passthrough_session(state)
                    except PassthroughChallengeError:
                        continue
                    except Exception as exc:
                        self._log(
                            f"{state.name} list_prompts session failed: {exc}"
                        )
                        continue
                    try:
                        r = await session.list_prompts()
                    except PassthroughChallengeError:
                        continue
                    except Exception as exc:
                        if not _is_method_not_found(exc):
                            self._log(f"{state.name} list_prompts failed: {exc}")
                        continue
                    for p in getattr(r, "prompts", []) or []:
                        prompts.append(_qualified_copy(p, state.name))
                prompt_count = len(prompts)
                self._log(f"list_prompts -> {prompt_count} prompts")
                return prompts

            return await measure_event(
                recorder=event_recorder,
                method="prompts/list",
                backend=None,
                dispatch=_dispatch,
                meta_provider=lambda _result: {
                    "backend_count": backend_count,
                    "count": prompt_count,
                },
            )

        @server.get_prompt()
        async def get_prompt(qualified_name: str, arguments: dict[str, str] | None = None):
            backend, original = _split_qualified(qualified_name)
            event_recorder = getattr(self.manager, "events", None)

            async def _dispatch():
                state = self.manager.servers.get(backend) if original else None
                if state is None or not state.running:
                    from mcp.types import GetPromptResult, PromptMessage, TextContent

                    return GetPromptResult(
                        description=f"Unknown or unavailable prompt '{qualified_name}'",
                        messages=[
                            PromptMessage(
                                role="user",
                                content=TextContent(
                                    type="text",
                                    text=(
                                        f"Unknown or unavailable prompt '{qualified_name}'"
                                    ),
                                ),
                            )
                        ],
                    )
                self._log(f"get_prompt: {qualified_name}")
                if getattr(state, "is_passthrough", False):
                    try:
                        session = await self._passthrough_session(state)
                    except PassthroughChallengeError as exc:
                        signal_challenge(exc)
                        from mcp.types import GetPromptResult, PromptMessage, TextContent

                        return GetPromptResult(
                            description=f"backend '{backend}' requires authentication",
                            messages=[
                                PromptMessage(
                                    role="user",
                                    content=TextContent(
                                        type="text",
                                        text=(
                                            f"backend '{backend}' requires authentication"
                                        ),
                                    ),
                                )
                            ],
                        )
                    try:
                        return await session.get_prompt(original, arguments)
                    except PassthroughChallengeError as exc:
                        signal_challenge(exc)
                        from mcp.types import GetPromptResult, PromptMessage, TextContent

                        return GetPromptResult(
                            description=f"backend '{backend}' requires authentication",
                            messages=[
                                PromptMessage(
                                    role="user",
                                    content=TextContent(
                                        type="text",
                                        text=(
                                            f"backend '{backend}' requires authentication"
                                        ),
                                    ),
                                )
                            ],
                        )
                if state.client_session is None:
                    from mcp.types import GetPromptResult, PromptMessage, TextContent

                    return GetPromptResult(
                        description=f"Unknown or unavailable prompt '{qualified_name}'",
                        messages=[
                            PromptMessage(
                                role="user",
                                content=TextContent(
                                    type="text",
                                    text=(
                                        f"Unknown or unavailable prompt '{qualified_name}'"
                                    ),
                                ),
                            )
                        ],
                    )
                return await state.client_session.get_prompt(original, arguments)

            return await measure_event(
                recorder=event_recorder,
                method="prompts/get",
                backend=backend or None,
                tool=original or None,
                qualified=qualified_name or None,
                input_payload=arguments,
                dispatch=_dispatch,
                meta_provider=lambda result: {
                    "message_count": len(getattr(result, "messages", []) or []),
                },
            )

        @server.list_resources()
        async def list_resources() -> list:
            event_recorder = getattr(self.manager, "events", None)
            resource_count = 0
            backend_count = 0

            async def _dispatch() -> list:
                nonlocal backend_count, resource_count
                states = self._running_states()
                passthrough_states = list(self._passthrough_states())
                backend_count = len(states) + len(passthrough_states)
                out: list = []
                if states:
                    results = await asyncio.gather(
                        *(s.client_session.list_resources() for s in states),
                        return_exceptions=True,
                    )
                    for state, r in zip(states, results):
                        if isinstance(r, BaseException):
                            if not _is_method_not_found(r):
                                self._log(f"{state.name} list_resources failed: {r}")
                            continue
                        for res in getattr(r, "resources", []) or []:
                            self._resource_origin[str(res.uri)] = state.name
                            out.append(res)

                for state in passthrough_states:
                    try:
                        session = await self._passthrough_session(state)
                    except PassthroughChallengeError:
                        continue
                    except Exception as exc:
                        self._log(
                            f"{state.name} list_resources session failed: {exc}"
                        )
                        continue
                    try:
                        r = await session.list_resources()
                    except PassthroughChallengeError:
                        continue
                    except Exception as exc:
                        if not _is_method_not_found(exc):
                            self._log(f"{state.name} list_resources failed: {exc}")
                        continue
                    for res in getattr(r, "resources", []) or []:
                        self._resource_origin[str(res.uri)] = state.name
                        out.append(res)
                resource_count = len(out)
                self._log(f"list_resources -> {resource_count} resources")
                return out

            return await measure_event(
                recorder=event_recorder,
                method="resources/list",
                backend=None,
                dispatch=_dispatch,
                meta_provider=lambda _result: {
                    "backend_count": backend_count,
                    "count": resource_count,
                },
            )

        @server.list_resource_templates()
        async def list_resource_templates() -> list:
            event_recorder = getattr(self.manager, "events", None)
            template_count = 0
            backend_count = 0

            async def _dispatch() -> list:
                nonlocal backend_count, template_count
                states = self._running_states()
                passthrough_states = list(self._passthrough_states())
                backend_count = len(states) + len(passthrough_states)
                out: list = []
                if states:
                    results = await asyncio.gather(
                        *(s.client_session.list_resource_templates() for s in states),
                        return_exceptions=True,
                    )
                    for state, r in zip(states, results):
                        if isinstance(r, BaseException):
                            if not _is_method_not_found(r):
                                self._log(
                                    f"{state.name} list_resource_templates failed: {r}"
                                )
                            continue
                        for tmpl in getattr(r, "resourceTemplates", []) or []:
                            out.append(tmpl)

                for state in passthrough_states:
                    try:
                        session = await self._passthrough_session(state)
                    except PassthroughChallengeError:
                        continue
                    except Exception as exc:
                        self._log(
                            f"{state.name} list_resource_templates session failed: {exc}"
                        )
                        continue
                    try:
                        r = await session.list_resource_templates()
                    except PassthroughChallengeError:
                        continue
                    except Exception as exc:
                        if not _is_method_not_found(exc):
                            self._log(
                                f"{state.name} list_resource_templates failed: {exc}"
                            )
                        continue
                    for tmpl in getattr(r, "resourceTemplates", []) or []:
                        out.append(tmpl)
                template_count = len(out)
                self._log(
                    f"list_resource_templates -> {template_count} templates"
                )
                return out

            return await measure_event(
                recorder=event_recorder,
                method="resources/templates",
                backend=None,
                dispatch=_dispatch,
                meta_provider=lambda _result: {
                    "backend_count": backend_count,
                    "count": template_count,
                },
            )

        @server.read_resource()
        async def read_resource(uri):
            uri_str = str(uri)
            event_recorder = getattr(self.manager, "events", None)
            resolved_backend: str | None = None
            fanout_used = False

            async def _dispatch() -> Any:
                nonlocal fanout_used, resolved_backend
                already_tried: set[str] = set()
                last_error: BaseException | None = None

                async def _read_via(state) -> Any:
                    if getattr(state, "is_passthrough", False):
                        session = await self._passthrough_session(state)
                        return await session.read_resource(uri)
                    return await state.client_session.read_resource(uri)

                owner_name = self._resource_origin.get(uri_str)
                if owner_name:
                    state = self.manager.servers.get(owner_name)
                    owner_running = state and state.running and (
                        state.client_session is not None
                        or getattr(state, "is_passthrough", False)
                    )
                    if owner_running:
                        try:
                            self._log(f"read_resource: {uri_str} -> {owner_name}")
                            r = await _read_via(state)
                            resolved_backend = owner_name
                            return _to_internal_contents(r.contents)
                        except PassthroughChallengeError as exc:
                            signal_challenge(exc)
                            raise
                        except Exception as exc:
                            last_error = exc
                            self._log(
                                f"{owner_name} read_resource '{uri_str}' failed; "
                                f"falling back: {exc}"
                            )
                            self._resource_origin.pop(uri_str, None)
                            already_tried.add(owner_name)
                    else:
                        self._resource_origin.pop(uri_str, None)

                candidates = list(self._running_states()) + list(self._passthrough_states())
                for state in candidates:
                    if state.name in already_tried:
                        continue
                    try:
                        r = await _read_via(state)
                    except PassthroughChallengeError:
                        last_error = RuntimeError(
                            f"backend '{state.name}' requires authentication"
                        )
                        continue
                    except Exception as exc:
                        last_error = exc
                        continue
                    self._resource_origin[uri_str] = state.name
                    resolved_backend = state.name
                    fanout_used = True
                    self._log(f"read_resource: {uri_str} -> {state.name} (fan-out)")
                    return _to_internal_contents(r.contents)

                if last_error is not None:
                    raise last_error
                raise RuntimeError(f"No backend available to serve '{uri_str}'")

            return await measure_event(
                recorder=event_recorder,
                method="resources/read",
                backend=None,
                qualified=uri_str,
                input_payload={"uri": uri_str},
                dispatch=_dispatch,
                backend_provider=lambda: resolved_backend,
                meta_provider=lambda _result: {
                    "uri": uri_str,
                    "fanout": fanout_used,
                },
            )


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


def _tool_to_wire(tool: Any) -> Any:
    """Render a Tool (or Tool-like) into its JSON-serializable wire shape.

    Used by the savings recorder so the byte/token counts on raw vs.
    compressed catalogs reflect what actually goes over the wire (with
    aliases like ``inputSchema`` resolved via Pydantic's ``by_alias``
    mode).
    """
    if hasattr(tool, "model_dump"):
        return tool.model_dump(by_alias=True, exclude_none=True)
    return dict(tool) if isinstance(tool, dict) else {"name": str(tool)}


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
