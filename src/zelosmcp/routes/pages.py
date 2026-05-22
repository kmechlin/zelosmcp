"""Static HTML / OpenAPI page routes.

The minimal set of GET handlers that return HTML shells or the
OpenAPI schema. Extracted from ``app.create_app`` so the dispatcher
module stays focused on ASGI/lifespan plumbing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
from starlette.schemas import SchemaGenerator

from zelosmcp.openapi import with_upstream_openapi
from zelosmcp.ui import HTML_TEMPLATE

if TYPE_CHECKING:
    from zelosmcp.manager import ProxyManager


def make_routes(
    manager: "ProxyManager",
    schema_generator: SchemaGenerator,
    swagger_html: str,
    redoc_html: str,
) -> list[Route]:
    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(HTML_TEMPLATE)

    async def docs(request: Request) -> HTMLResponse:
        """
        responses:
          200:
            description: Swagger UI for the zelosMCP HTTP API.
        """
        return HTMLResponse(swagger_html)

    async def redoc(request: Request) -> HTMLResponse:
        """
        responses:
          200:
            description: ReDoc rendering of the zelosMCP HTTP API.
        """
        return HTMLResponse(redoc_html)

    async def openapi_json(request: Request) -> JSONResponse:
        """
        responses:
          200:
            description: OpenAPI 3 schema describing every /api/* and /mcp endpoint.
        """
        schema = schema_generator.get_schema(routes=request.app.routes)
        schema = await with_upstream_openapi(schema, manager, request)
        return JSONResponse(schema)
    return [
        Route("/", index),
        Route("/docs", docs),
        Route("/redoc", redoc),
        Route("/openapi.json", openapi_json),
    ]
