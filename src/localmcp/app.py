from __future__ import annotations

import asyncio
import contextlib
import logging
import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route
from starlette.schemas import SchemaGenerator

from localmcp.builtin import (
    collect_backend_full_catalog,
    render_comprehensive_rule,
)
from localmcp.config import ConfigError
from localmcp.manager import ProxyManager
from localmcp.ui import CATALOG_HTML_TEMPLATE, HTML_TEMPLATE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


SCHEMA = SchemaGenerator(
    {
        "openapi": "3.0.3",
        "info": {
            "title": "LocalMCP",
            "version": "0.3.0",
            "description": (
                "Wrap one or more MCP servers and re-expose them on stable local URLs.\n\n"
                "- Each configured server is mounted at `/<name>/mcp` (raw passthrough — "
                "tools, resources, and prompts unchanged).\n"
                "- `/mcp` is an aggregator that fans tools, prompts, and resources "
                "across every running backend. Tool and prompt names are surfaced as "
                "`<server>__<original>` (double underscore). Resource URIs are kept "
                "verbatim; reads are routed to the originating backend via a "
                "URI->backend cache populated from `resources/list`, with a fan-out "
                "fallback for URIs not previously listed.\n"
                "- `/localmcp/mcp` is the always-on built-in MCP that exposes "
                "self-introspection and Cursor-rule-generation tools "
                "(`localmcp__*` at /mcp). It survives configuration reloads."
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
                    deprecated: true
                    description: |
                      Deprecated. `/mcp` always aggregates every running server
                      under the `<server>__<tool>` namespace. The field is still
                      accepted for backward compatibility but is ignored at runtime.
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

    async def api_catalog(request: Request) -> JSONResponse:
        """
        summary: Read-only documentation snapshot of every running backend.
        tags: [introspection]
        responses:
          200:
            description: |
              Per-backend tool / prompt / resource / resource-template
              catalog. Each entry includes its full payload (name,
              description, inputSchema where applicable). Capabilities
              the backend doesn't implement (`-32601`) are returned as
              empty lists. Both this endpoint and the
              `localmcp__get_aggregated_tool_catalog` MCP tool return
              the same shape.
            content:
              application/json:
                schema:
                  type: object
                  additionalProperties:
                    type: object
                    properties:
                      transport: { type: string, nullable: true }
                      running:   { type: boolean }
                      tools: { type: array, items: { type: object } }
                      prompts: { type: array, items: { type: object } }
                      resources: { type: array, items: { type: object } }
                      resourceTemplates: { type: array, items: { type: object } }
        """
        catalog = await collect_backend_full_catalog(manager, skip_self=False)
        return JSONResponse(catalog)

    async def catalog_page(request: Request) -> HTMLResponse:
        """
        summary: Standalone, searchable documentation page for every running backend.
        tags: [introspection]
        responses:
          200:
            description: HTML page that fetches /api/catalog and renders it fully expanded.
        """
        return HTMLResponse(CATALOG_HTML_TEMPLATE)

    async def api_cursor_rule(request: Request) -> Response:
        """
        summary: Generate a comprehensive agent-instructions document from the loaded backends.
        tags: [introspection]
        parameters:
          - in: query
            name: access
            required: false
            schema:
              type: string
              enum: [read-only, read-write]
              default: read-only
            description: |
              read-only (default): rule body forbids the agent from
              calling tools tagged `[mutates]`, `[destructive]`, or
              `[?]`. read-write: tools are still tagged but the agent
              is allowed to call them with user confirmation for
              destructive ones.
          - in: query
            name: format
            required: false
            schema:
              type: string
              enum: [cursor-mdc, copilot-instructions]
              default: cursor-mdc
            description: |
              cursor-mdc (default): YAML frontmatter wrapper for
              `.cursor/rules/*.mdc` (Cursor IDE).
              copilot-instructions: plain markdown body for
              `.github/copilot-instructions.md` (VSCode + GitHub
              Copilot). When `format=copilot-instructions`, `style`
              and `globs` are silently ignored (Copilot uses a
              different scoping mechanism).
          - in: query
            name: style
            required: false
            schema:
              type: string
              enum: [always-apply, scoped]
              default: always-apply
          - in: query
            name: globs
            required: false
            schema: { type: string }
            description: Glob pattern when style=scoped (e.g. `**/*.py`).
          - in: query
            name: tool_use
            required: false
            schema:
              type: string
              enum: [available, priority]
              default: priority
            description: |
              priority (default): rule body adds a "prefer MCP tools
              over shell" directive plus a curated playbook for the
              mandatory backends (`filesystem`, `pincher`) filtered by
              access mode. available: neutral catalog with no
              prioritization directive or playbook section.
        responses:
          200:
            description: Markdown body of the generated rule (frontmatter + body, or body-only for copilot-instructions).
            content:
              text/markdown: {}
          400:
            description: Unknown `access`, `format`, `style`, or `tool_use` value.
        """
        access = request.query_params.get("access", "read-only")
        if access not in ("read-only", "read-write"):
            return JSONResponse(
                {"error": f"Unknown access: {access!r}"}, status_code=400
            )
        fmt = request.query_params.get("format", "cursor-mdc")
        if fmt not in ("cursor-mdc", "copilot-instructions"):
            return JSONResponse(
                {"error": f"Unknown format: {fmt!r}"}, status_code=400
            )
        style = request.query_params.get("style", "always-apply")
        if style not in ("always-apply", "scoped"):
            return JSONResponse(
                {"error": f"Unknown style: {style!r}"}, status_code=400
            )
        tool_use = request.query_params.get("tool_use", "priority")
        if tool_use not in ("available", "priority"):
            return JSONResponse(
                {"error": f"Unknown tool_use: {tool_use!r}"}, status_code=400
            )
        globs = request.query_params.get("globs")
        catalog = await collect_backend_full_catalog(manager, skip_self=True)
        body = render_comprehensive_rule(
            catalog,
            access=access,
            style=style,
            globs=globs,
            fmt=fmt,
            tool_use=tool_use,
            mandatory_names=manager.mandatory_names(),
        )
        return PlainTextResponse(body, media_type="text/markdown; charset=utf-8")

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
        snapshot, q = manager.subscribe_logs_with_history()

        async def event_stream():
            try:
                # Replay the buffered history first so the client sees
                # the full session timeline (including startup banners
                # that fired before this SSE subscriber connected).
                for line in snapshot:
                    yield f"data: {line}\n\n"
                while True:
                    msg = await q.get()
                    yield f"data: {msg}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                manager.unsubscribe_logs(q)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        # Start the always-on builtin MCP before serving any traffic so
        # /localmcp/mcp answers immediately and the aggregator can already
        # fan tools/list out to the builtin's in-memory client session.
        try:
            await manager.start_builtin()
        except Exception as exc:  # never fail the whole app on builtin startup
            logging.getLogger("localmcp").error(
                "builtin failed to start: %s", exc, exc_info=True
            )
        # Bring up the reverse-proxy httpx client so the dispatcher can
        # forward requests as soon as the first backend with a configured
        # reverseProxy starts.
        try:
            await manager.start_http_client()
        except Exception as exc:
            logging.getLogger("localmcp").error(
                "reverse-proxy client failed to start: %s", exc, exc_info=True
            )
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                await manager.stop_builtin()
            with contextlib.suppress(Exception):
                await manager.stop_all()
            with contextlib.suppress(Exception):
                await manager.stop_http_client()

    _starlette = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/", index),
            Route("/docs", docs),
            Route("/redoc", redoc),
            Route("/openapi.json", openapi_json),
            Route("/api/status", api_status),
            Route("/api/start", api_start, methods=["POST"]),
            Route("/api/stop", api_stop, methods=["POST"]),
            Route("/api/cursor-rule", api_cursor_rule),
            Route("/api/catalog", api_catalog),
            Route("/catalog", catalog_page),
            Route("/api/logs", api_logs),
            Route("/api/servers/{name}", api_server_get),
            Route("/api/servers/{name}/start", api_server_start, methods=["POST"]),
            Route("/api/servers/{name}/stop", api_server_stop, methods=["POST"]),
        ],
    )

    async def asgi_app(scope, receive, send) -> None:
        """Dispatch /<name>/mcp, /mcp, and any backend's reverseProxy mount
        before Starlette's router."""
        if scope["type"] == "http":
            path = scope["path"]
            normalized = path.rstrip("/") or "/"

            target = None
            target_label = None

            if normalized == "/mcp":
                target = manager.aggregator
                target_label = "aggregate"
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
                if target is not None and getattr(target, "session_manager", None) is not None:
                    # Strip the routing prefix so the session manager sees a
                    # path it expects (e.g. "/mcp" or "").
                    forwarded = dict(scope)
                    forwarded["path"] = "/mcp"
                    forwarded["raw_path"] = b"/mcp"
                    return await target.session_manager.handle_request(
                        forwarded, receive, send
                    )
                if target_label == "aggregate":
                    msg = "No MCP servers are running"
                else:
                    msg = f"No MCP server '{target_label}' is running"
                resp = JSONResponse({"error": msg}, status_code=503)
                return await resp(scope, receive, send)

            # Reverse-proxy dispatch: a backend may declare a
            # `reverseProxy.mount` so its HTTP sidecar is reachable
            # under LocalMCP's port. Match on the original (un-stripped)
            # path since mounts are absolute. /<name>/mcp wins above so
            # a backend named `pincher` mounted at `/pincher` keeps
            # `/pincher/mcp` for MCP and routes `/pincher/v1/...` here.
            match = manager.find_reverse_proxy(path)
            if match is not None:
                spec, state = match
                running = (
                    state is not None
                    and getattr(state, "running", False)
                )
                if not running:
                    resp = JSONResponse(
                        {"error": f"No MCP server '{spec.name}' is running"},
                        status_code=503,
                    )
                    return await resp(scope, receive, send)
                return await manager.proxy_request(spec, scope, receive, send)

        await _starlette(scope, receive, send)

    asgi_app._manager = manager  # type: ignore[attr-defined]
    asgi_app.routes = _starlette.routes  # type: ignore[attr-defined]
    return asgi_app


app = create_app()


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_graceful_shutdown=2)
