"""Observability HTTP routes: savings, events, logs.

Inlines the ``_query_flag`` helper (formerly in app.py) since the events
handlers are its only callers.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from zelosmcp.manager import ProxyManager


def _query_flag(request: Request, name: str) -> bool:
    raw = request.query_params.get(name)
    if raw is None:
        return False
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def make_routes(manager: "ProxyManager") -> list[Route]:
    async def api_savings(request: Request) -> JSONResponse:
        """
        summary: Token-savings dashboard snapshot.
        description: |
          Aggregated token-savings metrics across three sources:
          (1) tool-list compression per backend (raw vs. compressed-wrapper
                    token/byte counts), (2) structured proxy-event accounting for every
                    transaction routed through this proxy, including raw upstream output
                    tokens versus transformed tokens returned to the IDE, and (3) pincher's
          self-reported BPE savings from the `_meta` envelope and the
          most recent `pincher__stats` snapshot. Returns 503 when the
          savings store hasn't started yet (e.g. the lifespan hook
          hasn't fired).
        tags: [introspection]
        responses:
          200:
            description: Savings snapshot.
            content:
              application/json: {}
          503:
            description: Savings store not yet initialised.
        """
        recorder = manager.savings
        if recorder is None:
            return JSONResponse(
                {"error": "savings store not initialised"},
                status_code=503,
            )
        snapshot = await recorder.snapshot()
        event_recorder = manager.events
        if event_recorder is not None:
            event_summary = await event_recorder.summary(top_n=20)
            totals = event_summary.get("totals") or {}
            snapshot["generated_at"] = max(
                float(snapshot.get("generated_at") or 0.0),
                float(event_summary.get("generated_at") or 0.0),
            )
            snapshot["calls"] = {
                "totals": {
                    **totals,
                    "transactions": totals.get("events", 0),
                },
                "per_backend": event_summary.get("per_backend", []),
                "per_method": event_summary.get("per_method", []),
                "top_tools": [
                    {
                        **row,
                        "calls": row.get("events", 0),
                        "tokens": row.get("token_volume", 0),
                    }
                    for row in event_summary.get("top_tools", [])
                ],
            }
            snapshot["proxy"] = event_summary
            snapshot["response_transforms"] = event_summary.get(
                "transform_types", []
            )
            snapshot["response_transform_saved_tokens_total"] = totals.get(
                "transform_saved_tokens", 0
            )
            snapshot["upstream_output_tokens_total"] = totals.get(
                "raw_output_tokens", 0
            )
            snapshot["returned_output_tokens_total"] = totals.get(
                "output_tokens", 0
            )
        snapshot["retention_hours"] = manager.event_retention_hours
        snapshot["prune_interval_mins"] = manager.event_prune_interval_mins
        return JSONResponse(snapshot)

    async def api_savings_stream(request: Request) -> StreamingResponse:
        """
        summary: Server-Sent-Events stream of incremental savings events.
        description: |
          Each frame is a JSON object with at least an `event` key
                    (`call`, `compression`, or `pincher_stats`) or a structured proxy-event
                    payload. Clients should listen for any frame to invalidate cached
                    `/api/savings` snapshots and trigger a fresh fetch.
        tags: [introspection]
        responses:
          200:
            description: SSE stream of savings events.
            content:
              text/event-stream: {}
          503:
            description: Savings store not yet initialised.
        """
        recorder = manager.savings
        if recorder is None:
            return JSONResponse(
                {"error": "savings store not initialised"},
                status_code=503,
            )
        q = recorder.subscribe()
        event_recorder = manager.events
        event_q = event_recorder.subscribe() if event_recorder is not None else None

        async def event_stream():
            savings_task: asyncio.Task[str] | None = None
            event_task: asyncio.Task[str] | None = None
            try:
                while True:
                    if savings_task is None:
                        savings_task = asyncio.create_task(q.get())
                    if event_q is not None and event_task is None:
                        event_task = asyncio.create_task(event_q.get())
                    waiters = [task for task in (savings_task, event_task) if task is not None]
                    done, _ = await asyncio.wait(
                        waiters,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if savings_task in done:
                        msg = savings_task.result()
                        savings_task = None
                        yield f"data: {msg}\n\n"
                    if event_task in done:
                        msg = event_task.result()
                        event_task = None
                        yield f"data: {msg}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                for task in (savings_task, event_task):
                    if task is not None:
                        task.cancel()
                recorder.unsubscribe(q)
                if event_q is not None and event_recorder is not None:
                    event_recorder.unsubscribe(event_q)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    async def api_events(request: Request) -> JSONResponse:
        """
        summary: Paginated proxy-event history.
        description: |
          Query the structured `proxy_events` stream with optional backend,
          method, tool-substring, and error filters.
        tags: [introspection]
        responses:
          200:
            description: Filtered event page.
            content:
              application/json: {}
          400:
            description: Invalid pagination parameters.
          503:
            description: Event store not yet initialised.
        """
        recorder = manager.events
        if recorder is None:
            return JSONResponse(
                {"error": "event store not initialised"},
                status_code=503,
            )
        try:
            limit = int(request.query_params.get("limit", "100"))
            offset = int(request.query_params.get("offset", "0"))
        except ValueError as exc:
            return JSONResponse(
                {"error": f"invalid pagination: {exc}"},
                status_code=400,
            )
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        backend = request.query_params.get("backend") or None
        method = request.query_params.get("method") or None
        tool = request.query_params.get("tool") or None
        errors_only = _query_flag(request, "errors_only")
        page = await recorder.query_events(
            backend=backend,
            method=method,
            tool=tool,
            errors_only=errors_only,
            limit=limit,
            offset=offset,
        )
        return JSONResponse({
            **page,
            "retention_hours": manager.event_retention_hours,
            "filters": {
                "backend": backend,
                "method": method,
                "tool": tool,
                "errors_only": errors_only,
                "limit": limit,
                "offset": offset,
            },
        })

    async def api_events_summary(request: Request) -> JSONResponse:
        """
        summary: Aggregate proxy-event metrics.
        description: |
          Returns totals, per-backend and per-method breakdowns, top tools,
          and transform-type distribution from the structured `proxy_events`
          table. Accepts an optional `backend` filter.
        tags: [introspection]
        responses:
          200:
            description: Aggregate event summary.
            content:
              application/json: {}
          503:
            description: Event store not yet initialised.
        """
        recorder = manager.events
        if recorder is None:
            return JSONResponse(
                {"error": "event store not initialised"},
                status_code=503,
            )
        backend = request.query_params.get("backend") or None
        top_n_raw = request.query_params.get("top_n", "20")
        try:
            top_n = max(1, min(int(top_n_raw), 100))
        except ValueError as exc:
            return JSONResponse(
                {"error": f"invalid top_n: {exc}"},
                status_code=400,
            )
        summary = await recorder.summary(backend=backend, top_n=top_n)
        return JSONResponse({
            **summary,
            "retention_hours": manager.event_retention_hours,
        })

    async def api_events_retention(request: Request) -> JSONResponse:
        """
        summary: Event retention and prune settings.
        tags: [introspection]
        responses:
          200:
            description: Current event retention configuration.
            content:
              application/json: {}
        """
        oldest_event_at = None
        latest_event_at = None
        recorder = manager.events
        if recorder is not None:
            summary = await recorder.summary(top_n=1)
            totals = summary.get("totals") or {}
            oldest_event_at = totals.get("oldest_event_at")
            latest_event_at = totals.get("latest_event_at")
        return JSONResponse({
            "retention_hours": manager.event_retention_hours,
            "prune_interval_mins": manager.event_prune_interval_mins,
            "oldest_event_at": oldest_event_at,
            "latest_event_at": latest_event_at,
        })

    async def api_events_stream(request: Request) -> StreamingResponse:
        """
        summary: Server-Sent-Events stream of structured proxy events.
        tags: [introspection]
        responses:
          200:
            description: SSE stream of proxy events.
            content:
              text/event-stream: {}
          503:
            description: Event store not yet initialised.
        """
        recorder = manager.events
        if recorder is None:
            return JSONResponse(
                {"error": "event store not initialised"},
                status_code=503,
            )
        q = recorder.subscribe()

        async def event_stream():
            try:
                while True:
                    msg = await q.get()
                    yield f"data: {msg}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                recorder.unsubscribe(q)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

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

    return [
        Route("/api/logs", api_logs),
        Route("/api/events", api_events),
        Route("/api/events/retention", api_events_retention),
        Route("/api/events/summary", api_events_summary),
        Route("/api/events/stream", api_events_stream),
        Route("/api/savings", api_savings),
        Route("/api/savings/stream", api_savings_stream),
    ]
