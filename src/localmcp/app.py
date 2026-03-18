from __future__ import annotations

import asyncio
import logging

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from localmcp.proxy import ProxyState
from localmcp.ui import HTML_TEMPLATE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


def create_app(proxy: ProxyState | None = None):
    """Build the ASGI application. Accepts an optional ProxyState for testing."""
    if proxy is None:
        proxy = ProxyState()

    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(HTML_TEMPLATE)

    async def api_status(request: Request) -> JSONResponse:
        return JSONResponse({
            "running": proxy.running,
            "backend": proxy.backend_info,
            "error": proxy.error,
        })

    async def api_start(request: Request) -> JSONResponse:
        try:
            data = await request.json()
            await proxy.start(
                transport=data.get("transport", "stdio"),
                command=data.get("command"),
                url=data.get("url"),
                env=data.get("env"),
            )
            return JSONResponse({"ok": True})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    async def api_stop(request: Request) -> JSONResponse:
        try:
            await proxy.stop()
            return JSONResponse({"ok": True})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    async def api_logs(request: Request) -> StreamingResponse:
        q = proxy.subscribe_logs()

        async def event_stream():
            try:
                while True:
                    msg = await q.get()
                    yield f"data: {msg}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                proxy.unsubscribe_logs(q)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    _starlette = Starlette(
        routes=[
            Route("/", index),
            Route("/api/status", api_status),
            Route("/api/start", api_start, methods=["POST"]),
            Route("/api/stop", api_stop, methods=["POST"]),
            Route("/api/logs", api_logs),
        ],
    )

    async def asgi_app(scope, receive, send) -> None:
        """ASGI app: intercept /mcp before Starlette routing to avoid Mount redirect."""
        if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
            if proxy.session_manager:
                await proxy.session_manager.handle_request(scope, receive, send)
            else:
                r = JSONResponse({"error": "No MCP server running"}, status_code=503)
                await r(scope, receive, send)
        else:
            await _starlette(scope, receive, send)

    asgi_app._proxy = proxy  # type: ignore[attr-defined]
    return asgi_app


app = create_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info", timeout_graceful_shutdown=2)
