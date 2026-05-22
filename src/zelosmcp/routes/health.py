"""Health-probe HTTP routes honoring the Zelos suite container contract.

Standard endpoints (zelosai/docs/architecture/07-container-contract.md):

* ``GET /healthz`` — liveness (process is alive).
* ``GET /readyz``  — readiness (dependencies reachable, PVC writable, ...).
* ``GET /``        — sanity check returning name, version, status.

These are aliases over the richer ``/api/status`` endpoint so the operator's
standard probe configuration (httpGet :http /healthz, /readyz) works without
component-specific branching.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

if TYPE_CHECKING:  # pragma: no cover
    from zelosmcp.manager import ProxyManager


def make_routes(manager: "ProxyManager") -> list[Route]:
    async def healthz(_req: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def readyz(_req: Request) -> JSONResponse:
        # Ready as soon as the ProxyManager is initialized; passthrough pool
        # warmup is async and best-effort.
        return JSONResponse({"status": "ready"})

    async def root(_req: Request) -> JSONResponse:
        return JSONResponse(
            {
                "name": "zelosmcp",
                "version": _read_version(),
                "status": "ok",
            }
        )

    return [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/readyz", readyz, methods=["GET"]),
        Route("/", root, methods=["GET"]),
    ]


def _read_version() -> str:
    # Avoid importing zelosmcp at module-load time (it triggers heavy init).
    try:
        from zelosmcp import __version__  # type: ignore
        return __version__
    except Exception:
        return os.environ.get("ZELOSMCP_VERSION", "0.0.0")
