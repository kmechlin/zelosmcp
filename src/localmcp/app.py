from __future__ import annotations

import asyncio
import logging
import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.schemas import SchemaGenerator

from localmcp.config import ConfigError
from localmcp.manager import ProxyManager
from localmcp.ui import HTML_TEMPLATE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


SCHEMA = SchemaGenerator(
    {
        "openapi": "3.0.3",
        "info": {
            "title": "LocalMCP",
            "version": "0.2.0",
            "description": (
                "Wrap one or more MCP servers and re-expose them on stable local URLs. "
                "Each configured server is mounted at `/<name>/mcp`; the optional "
                "`primaryMCP` is also mirrored at `/mcp`."
            ),
        },
        "servers": [{"url": "http://localhost:8000"}],
        "tags": [
            {"name": "lifecycle", "description": "Start/stop proxied MCP servers"},
            {"name": "introspection", "description": "Inspect status and logs"},
            {"name": "mcp", "description": "Streamable-HTTP MCP endpoints"},
        ],
    },
)


_SWAGGER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>LocalMCP API</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.onload = () => {
      window.ui = SwaggerUIBundle({
        url: "/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        layout: "BaseLayout",
      });
    };
  </script>
</body>
</html>
"""


_REDOC_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>LocalMCP API</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>body { margin: 0; padding: 0; }</style>
</head>
<body>
  <redoc spec-url="/openapi.json"></redoc>
  <script src="https://cdn.redocly.com/redoc/latest/bundles/redoc.standalone.js"></script>
</body>
</html>
"""


def create_app(manager: ProxyManager | None = None):
    """Build the ASGI application. Accepts an optional ProxyManager for testing."""
    if manager is None:
        manager = ProxyManager()

    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(HTML_TEMPLATE)

    async def docs(request: Request) -> HTMLResponse:
        """
        responses:
          200:
            description: Swagger UI for the LocalMCP HTTP API.
        """
        return HTMLResponse(_SWAGGER_HTML)

    async def redoc(request: Request) -> HTMLResponse:
        """
        responses:
          200:
            description: ReDoc rendering of the LocalMCP HTTP API.
        """
        return HTMLResponse(_REDOC_HTML)

    async def openapi_json(request: Request) -> JSONResponse:
        """
        responses:
          200:
            description: OpenAPI 3 schema describing every /api/* and /mcp endpoint.
        """
        schema = SCHEMA.get_schema(routes=request.app.routes)
        return JSONResponse(schema)

    async def api_status(request: Request) -> JSONResponse:
        """
        summary: Status of every configured proxy.
        tags: [introspection]
        responses:
          200:
            description: Aggregate status — list of servers, the primary name, and a global running flag.
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    primary:
                      type: string
                      nullable: true
                    running:
                      type: boolean
                    servers:
                      type: array
                      items:
                        type: object
                        properties:
                          name: { type: string }
                          transport: { type: string, enum: [stdio, sse, http] }
                          running: { type: boolean }
                          error: { type: string, nullable: true }
                          primary: { type: boolean }
        """
        return JSONResponse(manager.status())

    async def api_start(request: Request) -> JSONResponse:
        """
        summary: Start (or restart) the full set of proxies from a Cursor-style config.
        tags: [lifecycle]
        requestBody:
          required: true
          content:
            application/json:
              schema:
                type: object
                required: [mcpServers]
                properties:
                  primaryMCP:
                    type: string
                    description: Optional name from `mcpServers`. That server is also mounted at `/mcp`.
                  mcpServers:
                    type: object
                    additionalProperties:
                      oneOf:
                        - type: object
                          required: [command]
                          properties:
                            command: { type: string }
                            args: { type: array, items: { type: string } }
                            env:  { type: object, additionalProperties: { type: string } }
                            cwd:  { type: string }
                        - type: object
                          required: [type, url]
                          properties:
                            type: { type: string, enum: [sse, streamable-http] }
                            url:  { type: string, format: uri }
                            headers: { type: object, additionalProperties: { type: string } }
        responses:
          200:
            description: Per-server start results.
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    ok: { type: boolean }
                    primary: { type: string, nullable: true }
                    servers:
                      type: object
                      additionalProperties:
                        type: object
                        properties:
                          ok: { type: boolean }
                          error: { type: string, nullable: true }
          400:
            description: Configuration error (invalid JSON, missing fields, reserved name, etc).
        """
        try:
            data = await request.json()
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Invalid JSON: {exc}"}, status_code=400)

        try:
            result = await manager.start_all(data)
        except ConfigError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        all_ok = all(s["ok"] for s in result["servers"].values())
        return JSONResponse({"ok": all_ok, **result})

    async def api_stop(request: Request) -> JSONResponse:
        """
        summary: Stop every running proxy and clear the registry.
        tags: [lifecycle]
        responses:
          200:
            description: All proxies stopped.
        """
        try:
            await manager.stop_all()
            return JSONResponse({"ok": True})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    async def api_server_get(request: Request) -> JSONResponse:
        """
        summary: Status for one server.
        tags: [introspection]
        parameters:
          - in: path
            name: name
            required: true
            schema: { type: string }
        responses:
          200:
            description: Server status.
          404:
            description: No server registered with that name.
        """
        name = request.path_params["name"]
        state = manager.get(name)
        if state is None:
            return JSONResponse({"ok": False, "error": f"Unknown server '{name}'"}, status_code=404)
        full = manager.status()
        for entry in full["servers"]:
            if entry["name"] == name:
                return JSONResponse(entry)
        return JSONResponse({"ok": False, "error": f"Unknown server '{name}'"}, status_code=404)

    async def api_server_start(request: Request) -> JSONResponse:
        """
        summary: Start a single (already configured) server by name.
        tags: [lifecycle]
        parameters:
          - in: path
            name: name
            required: true
            schema: { type: string }
        responses:
          200: { description: Started. }
          404: { description: Unknown server. }
          400: { description: Already running or other error. }
        """
        name = request.path_params["name"]
        try:
            await manager.start_one(name)
            return JSONResponse({"ok": True})
        except KeyError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    async def api_server_stop(request: Request) -> JSONResponse:
        """
        summary: Stop a single server by name.
        tags: [lifecycle]
        parameters:
          - in: path
            name: name
            required: true
            schema: { type: string }
        responses:
          200: { description: Stopped. }
          404: { description: Unknown server. }
        """
        name = request.path_params["name"]
        try:
            await manager.stop_one(name)
            return JSONResponse({"ok": True})
        except KeyError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    async def api_logs(request: Request) -> StreamingResponse:
        """
        summary: Server-Sent-Events stream of activity logs across all proxies.
        tags: [introspection]
        responses:
          200:
            description: SSE stream. Each line is prefixed with `[<server-name>]`.
            content:
              text/event-stream: {}
        """
        q = manager.subscribe_logs()

        async def event_stream():
            try:
                while True:
                    msg = await q.get()
                    yield f"data: {msg}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                manager.unsubscribe_logs(q)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    _starlette = Starlette(
        routes=[
            Route("/", index),
            Route("/docs", docs),
            Route("/redoc", redoc),
            Route("/openapi.json", openapi_json),
            Route("/api/status", api_status),
            Route("/api/start", api_start, methods=["POST"]),
            Route("/api/stop", api_stop, methods=["POST"]),
            Route("/api/logs", api_logs),
            Route("/api/servers/{name}", api_server_get),
            Route("/api/servers/{name}/start", api_server_start, methods=["POST"]),
            Route("/api/servers/{name}/stop", api_server_stop, methods=["POST"]),
        ],
    )

    async def asgi_app(scope, receive, send) -> None:
        """Dispatch /<name>/mcp and /mcp before Starlette's router."""
        if scope["type"] == "http":
            path = scope["path"]
            normalized = path.rstrip("/") or "/"

            target = None
            target_label = None

            if normalized == "/mcp":
                target = manager.primary_state()
                target_label = "primary"
            elif normalized.endswith("/mcp"):
                # /<name>/mcp — exactly two slashes after stripping trailing.
                segments = [s for s in normalized.split("/") if s]
                if len(segments) == 2 and segments[1] == "mcp":
                    name = segments[0]
                    target = manager.get(name)
                    target_label = name
                else:
                    target = None
                    target_label = None

            if target_label is not None:
                if target is not None and target.session_manager is not None:
                    # Strip the routing prefix so the session manager sees a
                    # path it expects (e.g. "/mcp" or "").
                    forwarded = dict(scope)
                    forwarded["path"] = "/mcp"
                    forwarded["raw_path"] = b"/mcp"
                    return await target.session_manager.handle_request(
                        forwarded, receive, send
                    )
                if target_label == "primary":
                    msg = "No primary MCP server is running"
                else:
                    msg = f"No MCP server '{target_label}' is running"
                resp = JSONResponse({"error": msg}, status_code=503)
                return await resp(scope, receive, send)

        await _starlette(scope, receive, send)

    asgi_app._manager = manager  # type: ignore[attr-defined]
    asgi_app.routes = _starlette.routes  # type: ignore[attr-defined]
    return asgi_app


app = create_app()


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_graceful_shutdown=2)
