"""Auth provider HTTP routes (OAuth device/code flows + management)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from starlette.routing import Route

if TYPE_CHECKING:
    from zelosmcp.manager import ProxyManager


def make_routes(manager: "ProxyManager") -> list[Route]:
    async def api_auth_providers_list(request: Request) -> JSONResponse:
        """
        summary: List configured auth providers with per-user status.
        description: |
          Returns one entry per provider in the registry. Each entry
          carries the provider name, type, ready flag, optional
          identity badge fields (username, avatar_url) when the user
          has authenticated, the optional membership_hint, and a
          flag indicating whether device flow is supported. Used by
          the GUI Connections page to render one card per provider.

          User identity is derived from the inbound Authorization
          header (SHA-256 hashed via the same primitive the
          passthrough pool uses for upstream session keying). Local
          single-user deployments with no inbound Authorization
          map to the "anonymous" key.
        tags: [introspection]
        responses:
          200:
            description: Array of provider status objects.
        """
        from zelosmcp.passthrough_pool import hash_authorization
        user_key = hash_authorization(
            request.headers.get("authorization")
        )
        providers_out: list[dict[str, Any]] = []
        for provider in manager.auth_registry.values():
            try:
                status = await provider.status(user_key)
            except Exception as exc:
                logging.getLogger("zelosmcp").info(
                    "auth provider '%s' status failed: %s",
                    provider.name, exc,
                )
                providers_out.append({
                    "name": provider.name,
                    "type": provider.type,
                    "ready": False,
                    "identity": None,
                    "membership_hint": None,
                    "supports_device_flow": False,
                "supports_authorization_code": False,
                    "error": str(exc),
                })
                continue
            entry: dict[str, Any] = {
                "name": status.name,
                "type": status.type,
                "ready": status.ready,
                "membership_hint": status.membership_hint,
                "supports_device_flow": status.supports_device_flow,
                "supports_authorization_code": status.supports_authorization_code,
            }
            if status.identity is not None:
                entry["identity"] = {
                    "username": status.identity.username,
                    "avatar_url": status.identity.avatar_url,
                    "scopes": list(status.identity.scopes),
                    "expires_at": status.identity.expires_at,
                }
            else:
                entry["identity"] = None
            providers_out.append(entry)
        return JSONResponse({"providers": providers_out})

    async def api_auth_provider_start(request: Request) -> JSONResponse:
        """
        summary: Initiate a device-flow handshake for one provider.
        description: |
          Returns the user_code, verification URLs, and a session_id
          the GUI uses to poll for completion via the SSE stream
          endpoint. The verification_uri_complete (when the upstream
          provider supplies it) lets the GUI open a single-click
          browser tab with the code already entered.
        tags: [lifecycle]
        responses:
          200:
            description: Device-flow session metadata.
          404:
            description: Unknown provider name.
          400:
            description: Provider doesn't support device flow.
          502:
            description: Upstream device-code endpoint failed.
        """
        from zelosmcp.auth.protocol import (
            AuthProviderError,
            DeviceFlowError,
        )
        from zelosmcp.passthrough_pool import hash_authorization

        provider_name = request.path_params["provider"]
        provider = manager.auth_registry.get(provider_name)
        if provider is None:
            return JSONResponse(
                {"error": f"unknown provider '{provider_name}'"},
                status_code=404,
            )
        user_key = hash_authorization(
            request.headers.get("authorization")
        )
        try:
            session = await provider.start_device_flow(user_key)
        except DeviceFlowError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        except AuthProviderError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({
            "session_id": session.session_id,
            "user_code": session.user_code,
            "verification_uri": session.verification_uri,
            "verification_uri_complete": session.verification_uri_complete,
            "authorization_url": session.authorization_url,
            "expires_in": session.expires_in,
            "poll_interval": session.poll_interval,
        })

    async def _handle_auth_provider_callback(
        request: Request,
        provider_name: str,
    ) -> HTMLResponse:
        provider = manager.auth_registry.get(provider_name)
        if provider is None:
            return HTMLResponse(
                f"<h1>Unknown provider</h1><p>{provider_name}</p>",
                status_code=404,
            )
        handler = getattr(provider, "handle_callback", None)
        if handler is None:
            return HTMLResponse(
                "<h1>Unsupported provider</h1>"
                "<p>This provider does not support browser callbacks.</p>",
                status_code=400,
            )
        state = await handler(
            code=request.query_params.get("code"),
            state=request.query_params.get("state"),
            error=request.query_params.get("error"),
            error_description=request.query_params.get("error_description"),
        )
        if state.state.value == "complete":
            # Provider just transitioned to ready: refresh the live tool
            # catalog into the auto-generated playbook rows so the Assets
            # pane stops showing "0 tools" for backends gated on this
            # provider. User-edited rows are preserved by the underlying
            # upsert(only_if_seed_lt=1) logic.
            try:
                await manager.regenerate_assets_for_provider(provider_name)
            except Exception:
                logging.getLogger("zelosmcp").warning(
                    "auth callback: regenerate_assets_for_provider(%s) failed",
                    provider_name,
                    exc_info=True,
                )
            who = state.identity.username if state.identity else "your account"
            return HTMLResponse(
                "<!doctype html><html><body>"
                "<h1>Authorization complete</h1>"
                f"<p>Connected {who}. You can close this tab.</p>"
                "<script>setTimeout(() => window.close(), 1200)</script>"
                "</body></html>"
            )
        return HTMLResponse(
            "<!doctype html><html><body>"
            "<h1>Authorization failed</h1>"
            f"<p>{state.error_message or 'Unknown error'}</p>"
            "</body></html>",
            status_code=400,
        )

    async def api_auth_provider_callback(request: Request) -> HTMLResponse:
        """
        summary: Browser callback for Authorization Code + PKCE providers.
        description: |
          Okta Native apps redirect here after the user completes the
          Authorization Code flow. The provider validates state, exchanges the
          code using the stored PKCE verifier, stores tokens, and marks the
          pending auth session complete so the Connections UI SSE stream can
          update.
        tags: [lifecycle]
        responses:
          200:
            description: Small HTML completion / error page.
          404:
            description: Unknown provider.
          400:
            description: Provider does not support auth-code callbacks.
        """
        provider_name = request.path_params["provider"]
        return await _handle_auth_provider_callback(request, provider_name)

    async def api_auth_legacy_okta_callback(request: Request) -> HTMLResponse:
        """
        summary: Legacy Okta callback path.
        description: |
          Compatibility route for Okta apps configured with
          `/auth/okta/callback`. The opaque `state` value identifies the
          pending auth session, which includes the real provider name.
        tags: [lifecycle]
        responses:
          200:
            description: Small HTML completion / error page.
          404:
            description: Unknown or expired auth session.
        """
        state = request.query_params.get("state")
        if not state or manager.auth_store is None:
            return HTMLResponse(
                "<h1>Authorization failed</h1>"
                "<p>Missing or expired authorization session.</p>",
                status_code=404,
            )
        session = await manager.auth_store.get_device_session(state)
        if session is None:
            return HTMLResponse(
                "<h1>Authorization failed</h1>"
                "<p>Unknown or expired authorization session.</p>",
                status_code=404,
            )
        return await _handle_auth_provider_callback(request, session["provider"])

    async def api_auth_provider_stream(request: Request) -> StreamingResponse:
        """
        summary: Server-Sent-Events stream of device-flow state.
        description: |
          The GUI subscribes to this stream after starting a device
          flow. zelosMCP polls the upstream at the provider-prescribed
          interval and pushes one SSE frame per state change (or per
          poll, whichever is rarer). Stream terminates when the
          state reaches a terminal value (complete / error / expired)
          or when the session's expires_at passes.
        tags: [introspection]
        responses:
          200:
            description: SSE stream; each frame is a JSON object with `state` (and optional `identity` / `error`).
            content:
              text/event-stream: {}
          404:
            description: Unknown provider or unknown session_id.
        """
        from zelosmcp.auth.protocol import DeviceFlowStateKind

        provider_name = request.path_params["provider"]
        session_id = request.query_params.get("session")
        if not session_id:
            return JSONResponse(
                {"error": "missing required query param 'session'"},
                status_code=400,
            )
        provider = manager.auth_registry.get(provider_name)
        if provider is None:
            return JSONResponse(
                {"error": f"unknown provider '{provider_name}'"},
                status_code=404,
            )

        async def event_stream():
            import json as _json
            try:
                while True:
                    try:
                        state = await provider.poll_device_flow(session_id)
                    except Exception as exc:
                        frame = {"state": "error", "error": str(exc)}
                        yield f"data: {_json.dumps(frame)}\n\n"
                        return
                    payload: dict[str, Any] = {"state": state.state.value}
                    if state.identity is not None:
                        payload["identity"] = {
                            "username": state.identity.username,
                            "avatar_url": state.identity.avatar_url,
                            "scopes": list(state.identity.scopes),
                            "expires_at": state.identity.expires_at,
                        }
                    if state.error_message is not None:
                        payload["error"] = state.error_message
                    yield f"data: {_json.dumps(payload)}\n\n"
                    if state.state == DeviceFlowStateKind.COMPLETE:
                        # Provider just became ready for this user: kick
                        # off auto-default regeneration for backends
                        # wired to it so their playbooks reflect the
                        # newly-visible tool list. Background task so the
                        # SSE stream closes promptly.
                        asyncio.create_task(
                            manager.regenerate_assets_for_provider(
                                provider_name
                            )
                        )
                        return
                    if state.state in (
                        DeviceFlowStateKind.ERROR,
                        DeviceFlowStateKind.EXPIRED,
                    ):
                        return
                    # Re-fetch the session to honour the latest
                    # poll_interval (some providers slow_down the
                    # cadence on rate-limit feedback).
                    session_row = await manager.auth_store.get_device_session(
                        session_id
                    ) if manager.auth_store is not None else None
                    interval = (
                        float(session_row["poll_interval"])
                        if session_row is not None
                        and session_row.get("poll_interval")
                        else 5.0
                    )
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                pass

        return StreamingResponse(
            event_stream(), media_type="text/event-stream",
        )

    async def api_auth_provider_identity(request: Request) -> JSONResponse:
        """
        summary: Currently-authed identity for one provider.
        description: |
          Returns username + avatar + scopes + expiry for the
          inbound user against one provider. Used by the
          Connections card to render the user badge after a
          successful auth.
        tags: [introspection]
        responses:
          200:
            description: Identity object; when not authenticated the body has ready set to false and identity null.
          404:
            description: Unknown provider.
        """
        from zelosmcp.passthrough_pool import hash_authorization

        provider_name = request.path_params["provider"]
        provider = manager.auth_registry.get(provider_name)
        if provider is None:
            return JSONResponse(
                {"error": f"unknown provider '{provider_name}'"},
                status_code=404,
            )
        user_key = hash_authorization(
            request.headers.get("authorization")
        )
        try:
            status = await provider.status(user_key)
        except Exception as exc:
            return JSONResponse(
                {"error": str(exc), "ready": False}, status_code=200,
            )
        if status.identity is None:
            return JSONResponse({"ready": status.ready, "identity": None})
        return JSONResponse({
            "ready": status.ready,
            "identity": {
                "username": status.identity.username,
                "avatar_url": status.identity.avatar_url,
                "scopes": list(status.identity.scopes),
                "expires_at": status.identity.expires_at,
            },
        })

    async def api_auth_provider_revoke(request: Request) -> JSONResponse:
        """
        summary: Sign out — drop the stored token for one provider.
        description: |
          Best-effort upstream revocation followed by unconditional
          local removal. After this returns, the provider's
          is_ready returns False and the aggregator gates the
          backend's wrappers again.
        tags: [lifecycle]
        responses:
          200: { description: "Revoked." }
          404: { description: "Unknown provider." }
        """
        from zelosmcp.passthrough_pool import hash_authorization

        provider_name = request.path_params["provider"]
        provider = manager.auth_registry.get(provider_name)
        if provider is None:
            return JSONResponse(
                {"error": f"unknown provider '{provider_name}'"},
                status_code=404,
            )
        user_key = hash_authorization(
            request.headers.get("authorization")
        )
        try:
            await provider.revoke(user_key)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400,
            )
        # Provider went from ready → not-ready for this user: re-run
        # default asset generation so backends gated on this provider
        # don't keep showing a stale 'N tools' playbook from the
        # connected era. User-edited rows are preserved.
        try:
            await manager.regenerate_assets_for_provider(provider_name)
        except Exception:
            logging.getLogger("zelosmcp").warning(
                "auth revoke: regenerate_assets_for_provider(%s) failed",
                provider_name,
                exc_info=True,
            )
        return JSONResponse({"ok": True})

    async def api_auth_providers_config_get(request: Request) -> JSONResponse:
        """
        summary: Currently-loaded auth-providers config (redacted).
        description: |
          Returns the providers registry as the GUI's Connections page
          renders it. Secret-like fields (the bearer token on static
          providers) are replaced with three asterisks. The client_id
          field is non-sensitive (the public OAuth client identifier
          ships in zelosMCP's default config) and stays in the clear.
        tags: [introspection]
        responses:
          200:
            description: Object with a `providers` key mapping provider name to redacted spec.
        """
        return JSONResponse(manager.current_auth_providers_config(redacted=True))

    async def api_auth_providers_config_post(request: Request) -> JSONResponse:
        """
        summary: Replace the auth-providers config at runtime.
        description: |
          POST a JSON document with the same shape as the
          configs/auth-providers.json file (top-level providers mapping).
          Validates against the currently-loaded backend specs so the swap
          cannot drop a referenced provider. Existing tokens in the auth
          store survive provider renames; provider deletion drops the
          associated tokens via the manager (logged for audit).
        tags: [lifecycle]
        responses:
          200:
            description: Per-provider load result map.
          400:
            description: Invalid JSON or schema error.
        """
        try:
            data = await request.json()
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"invalid JSON: {exc}"},
                status_code=400,
            )
        try:
            result = await manager.start_auth_providers(data)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400,
            )
        return JSONResponse({"ok": True, **result})
    return [
        Route("/api/auth/providers/config", api_auth_providers_config_get),
        Route(
            "/api/auth/providers/config",
            api_auth_providers_config_post,
            methods=["POST"],
        ),
        Route("/api/auth/providers", api_auth_providers_list),
        Route(
            "/api/auth/{provider}/start",
            api_auth_provider_start,
            methods=["POST"],
        ),
        Route(
            "/api/auth/{provider}/callback",
            api_auth_provider_callback,
        ),
        Route(
            "/auth/okta/callback",
            api_auth_legacy_okta_callback,
        ),
        Route(
            "/api/auth/{provider}/stream",
            api_auth_provider_stream,
        ),
        Route(
            "/api/auth/{provider}/identity",
            api_auth_provider_identity,
        ),
        Route(
            "/api/auth/{provider}/revoke",
            api_auth_provider_revoke,
            methods=["POST"],
        ),
    ]
