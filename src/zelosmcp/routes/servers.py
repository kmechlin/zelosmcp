"""Server lifecycle HTTP routes.

Handles the bulk lifecycle endpoints (``/api/status``, ``/api/start``,
``/api/stop``) and the per-backend lifecycle endpoints
(``/api/servers/{name}``, ``/api/servers/{name}/start``,
``/api/servers/{name}/stop``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from zelosmcp.config import ConfigError

if TYPE_CHECKING:
    from zelosmcp.manager import ProxyManager


def make_routes(manager: "ProxyManager") -> list[Route]:
    async def api_status(request: Request) -> JSONResponse:
        """
        summary: Status of every configured proxy.
        tags: [introspection]
        responses:
          200:
            description: Aggregate status — list of servers, the primary name, a global running flag, and the active BuiltinConfig.
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
                    builtin:
                      type: object
                      description: |
                        Active BuiltinConfig (defaults omitted, matches the
                        top-level `builtin` key accepted by /api/start).
                        Always present; `{}` when all defaults are in effect.
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
    return [
        Route("/api/status", api_status),
        Route("/api/start", api_start, methods=["POST"]),
        Route("/api/stop", api_stop, methods=["POST"]),
        Route("/api/servers/{name}", api_server_get),
        Route("/api/servers/{name}/start", api_server_start, methods=["POST"]),
        Route("/api/servers/{name}/stop", api_server_stop, methods=["POST"]),
    ]
