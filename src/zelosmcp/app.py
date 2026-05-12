from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
import logging
import os
from typing import Any

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

from zelosmcp.builtin import (
    collect_backend_full_catalog,
    render_comprehensive_rule,
)
from zelosmcp.config import ConfigError
from zelosmcp.docs import list_docs, read_doc
from zelosmcp.manager import ProxyManager
from zelosmcp.repos import (
    RULE_RELATIVE_PATHS,
    discover_repos,
    is_under_scan_root,
    rule_target,
    to_rw_path,
)
from zelosmcp.ui import CATALOG_HTML_TEMPLATE, HTML_TEMPLATE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


SCHEMA = SchemaGenerator(
    {
        "openapi": "3.0.3",
        "info": {
            "title": "zelosMCP",
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
                "- `/zelosmcp/mcp` is the always-on built-in MCP that exposes "
                "self-introspection and Cursor-rule-generation tools "
                "(`zelosmcp__*` at /mcp). It survives configuration reloads."
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
  <title>zelosMCP API</title>
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
  <title>zelosMCP API</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>body { margin: 0; padding: 0; }</style>
</head>
<body>
  <redoc spec-url="/openapi.json"></redoc>
  <script src="https://cdn.redocly.com/redoc/latest/bundles/redoc.standalone.js"></script>
</body>
</html>
"""


# Default path for the auth-providers config when ``ZELOSMCP_AUTH_PROVIDERS_FILE``
# isn't set. Container deploys override this to ``/etc/zelosmcp/auth-providers.json``
# (mounted from a Kubernetes Secret); local dev points at the repo path.
_DEFAULT_AUTH_PROVIDERS_PATH = "configs/auth-providers.json"


async def _autoload_auth_providers(manager) -> None:
    """Load ``configs/auth-providers.json`` (or whatever
    ``ZELOSMCP_AUTH_PROVIDERS_FILE`` points at) into the manager's
    auth registry at startup.

    Missing file is logged and skipped — a deployment with no
    providers is a valid (legacy-passthrough-only) configuration.
    Malformed file logs the error but doesn't crash the app; the
    registry stays empty and any backend referencing a provider
    will fail at /api/start time with a clear message.
    """
    import json as _json

    log = logging.getLogger("zelosmcp")
    path = os.environ.get(
        "ZELOSMCP_AUTH_PROVIDERS_FILE", _DEFAULT_AUTH_PROVIDERS_PATH
    )
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = _json.load(f)
    except FileNotFoundError:
        log.info(
            "auth-providers config not found at %s; starting with empty registry",
            path,
        )
        return
    except (OSError, _json.JSONDecodeError) as exc:
        log.error(
            "auth-providers config %s failed to load: %s", path, exc
        )
        return

    try:
        result = await manager.start_auth_providers(payload)
    except Exception as exc:
        log.error(
            "auth-providers config %s failed to register: %s", path, exc
        )
        return
    log.info(
        "auth-providers loaded from %s: %s", path, result.get("providers", {})
    )


def _flatten_call_result(result) -> dict | list | str | None:
    """Best-effort extraction of a single JSON payload from a
    ``CallToolResult``. Pincher returns a single ``TextContent`` whose
    ``.text`` is the JSON dump of its response; we parse that and return
    the dict so callers don't have to. Falls back to a list of strings or
    ``None`` if the response can't be JSON-parsed.
    """
    import json as _json

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    content = getattr(result, "content", None) or []
    texts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            texts.append(text)
    if not texts:
        return None
    if len(texts) == 1:
        try:
            return _json.loads(texts[0])
        except (ValueError, TypeError):
            return texts[0]
    return texts


def _extract_pincher_indexed_paths(result) -> set[str]:
    """Pull the absolute repo paths out of a ``pincher__list`` response.

    Pincher returns ``{"projects": [{"name", "path", "files", ...}]}``.
    We map those paths back to whatever the scanner reports so the UI can
    flag already-indexed repos. Anything unparseable -> empty set, since
    a missing pincher_indexed flag is preferable to a 500 in /api/repos.
    """
    payload = _flatten_call_result(result)
    if not isinstance(payload, dict):
        return set()
    projects = payload.get("projects") or []
    if not isinstance(projects, list):
        return set()
    out: set[str] = set()
    for p in projects:
        if isinstance(p, dict):
            path = p.get("path") or p.get("Path")
            if isinstance(path, str):
                out.add(path)
    return out


def _prefix_openapi_path(mount: str, path: str) -> str:
    """Mount an upstream OpenAPI path under the public reverse-proxy prefix."""
    normalized = path if path.startswith("/") else f"/{path}"
    if normalized == "/":
        return mount
    return f"{mount}{normalized}"


def _rewrite_component_refs(value: Any, ref_map: dict[str, str]) -> Any:
    """Recursively rewrite local OpenAPI component refs after namespacing."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str):
                out[key] = ref_map.get(item, item)
            else:
                out[key] = _rewrite_component_refs(item, ref_map)
        return out
    if isinstance(value, list):
        return [_rewrite_component_refs(item, ref_map) for item in value]
    return value


def _merge_upstream_openapi(
    base: dict[str, Any],
    upstream: dict[str, Any],
    *,
    backend: str,
    mount: str,
) -> None:
    """Merge one upstream OpenAPI document into the zelosMCP schema in-place."""
    ref_map: dict[str, str] = {}
    security_scheme_names: set[str] = set()
    upstream_components = upstream.get("components")
    if isinstance(upstream_components, dict):
        for section, values in upstream_components.items():
            if not isinstance(values, dict):
                continue
            if section == "securitySchemes":
                security_scheme_names.update(str(name) for name in values)
            dest_section = base.setdefault("components", {}).setdefault(section, {})
            if not isinstance(dest_section, dict):
                continue
            for name in values:
                ref_map[f"#/components/{section}/{name}"] = (
                    f"#/components/{section}/{backend}_{name}"
                )

    rewritten = _rewrite_component_refs(upstream, ref_map)

    components = rewritten.get("components")
    if isinstance(components, dict):
        for section, values in components.items():
            if not isinstance(values, dict):
                continue
            dest_section = base.setdefault("components", {}).setdefault(section, {})
            if not isinstance(dest_section, dict):
                continue
            for name, component in values.items():
                dest_section[f"{backend}_{name}"] = component

    paths = rewritten.get("paths")
    if not isinstance(paths, dict):
        return
    tags = base.setdefault("tags", [])
    if isinstance(tags, list) and not any(
        isinstance(tag, dict) and tag.get("name") == backend for tag in tags
    ):
        tags.append({"name": backend, "description": f"{backend} upstream API"})
    base_paths = base.setdefault("paths", {})
    for upstream_path, path_item in paths.items():
        if not isinstance(upstream_path, str) or not isinstance(path_item, dict):
            continue
        public_path = _prefix_openapi_path(mount, upstream_path)
        merged_item = deepcopy(path_item)
        for method, operation in list(merged_item.items()):
            if not isinstance(operation, dict):
                continue
            if method.lower() not in {
                "get",
                "put",
                "post",
                "delete",
                "options",
                "head",
                "patch",
                "trace",
            }:
                continue
            existing_tags = operation.get("tags")
            tags = existing_tags if isinstance(existing_tags, list) else []
            operation["tags"] = [backend] + [t for t in tags if t != backend]
            security = operation.get("security")
            if isinstance(security, list):
                operation["security"] = [
                    {
                        (
                            f"{backend}_{name}"
                            if name in security_scheme_names
                            else name
                        ): scopes
                        for name, scopes in requirement.items()
                    }
                    if isinstance(requirement, dict)
                    else requirement
                    for requirement in security
                ]
        base_paths[public_path] = merged_item


async def _with_upstream_openapi(
    schema: dict[str, Any],
    manager: ProxyManager,
    request: Request,
) -> dict[str, Any]:
    """Return the local schema plus any configured reverse-proxy contracts."""
    merged = deepcopy(schema)
    warnings: list[dict[str, str]] = []
    host = request.headers.get("host", "")
    for spec, state in manager.reverse_proxy_openapi_specs():
        running = state is not None and getattr(state, "running", False)
        if not running:
            continue
        rp = spec.reverse_proxy
        if rp is None:
            continue
        try:
            upstream = await manager.fetch_reverse_proxy_openapi(
                spec,
                scheme=request.url.scheme,
                host=host,
            )
            _merge_upstream_openapi(
                merged,
                upstream,
                backend=spec.name,
                mount=rp.mount,
            )
        except Exception as exc:  # noqa: BLE001 - docs must survive bad upstreams
            warnings.append({
                "backend": spec.name,
                "path": rp.openapi.path if rp.openapi is not None else "",
                "detail": str(exc),
            })
    if warnings:
        merged["x-zelosmcp-openapi-warnings"] = warnings
    return merged


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
            description: Swagger UI for the zelosMCP HTTP API.
        """
        return HTMLResponse(_SWAGGER_HTML)

    async def redoc(request: Request) -> HTMLResponse:
        """
        responses:
          200:
            description: ReDoc rendering of the zelosMCP HTTP API.
        """
        return HTMLResponse(_REDOC_HTML)

    async def openapi_json(request: Request) -> JSONResponse:
        """
        responses:
          200:
            description: OpenAPI 3 schema describing every /api/* and /mcp endpoint.
        """
        schema = SCHEMA.get_schema(routes=request.app.routes)
        schema = await _with_upstream_openapi(schema, manager, request)
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
              `zelosmcp__get_aggregated_tool_catalog` MCP tool return
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

    async def api_docs_index(request: Request) -> JSONResponse:
        """
        summary: List the markdown documents available to the in-app Docs view.
        tags: [introspection]
        responses:
          200:
            description: |
              Ordered list of ``{slug, title}`` entries. The top-level
              ``README.md`` (when present) is exposed under the slug
              ``readme`` and surfaced first; subsequent entries come from
              ``docs/*.md`` and are alphabetised by slug. The same
              whitelist gates ``GET /api/docs/{slug}`` reads.
            content:
              application/json:
                schema:
                  type: array
                  items:
                    type: object
                    properties:
                      slug:  { type: string }
                      title: { type: string }
        """
        return JSONResponse(list_docs())

    async def api_docs_get(request: Request) -> JSONResponse:
        """
        summary: Read one markdown document by slug, rendered to HTML.
        tags: [introspection]
        parameters:
          - in: path
            name: slug
            required: true
            schema: { type: string }
        responses:
          200:
            description: |
              ``{slug, title, markdown, html}``. ``html`` is rendered
              with the ``markdown`` package (extensions: ``fenced_code``,
              ``tables``, ``toc``, ``sane_lists``) and post-processed to
              strip ``<script>`` tags and inline ``on*`` handlers
              defensively.
            content:
              application/json: {}
          404:
            description: Slug isn't in the docs whitelist.
        """
        slug = request.path_params["slug"]
        doc = read_doc(slug)
        if doc is None:
            return JSONResponse(
                {"error": f"Unknown doc slug: {slug!r}"}, status_code=404
            )
        return JSONResponse(doc)

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
                    if state.state in (
                        DeviceFlowStateKind.COMPLETE,
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

    async def api_savings(request: Request) -> JSONResponse:
        """
        summary: Token-savings dashboard snapshot.
        description: |
          Aggregated token-savings metrics across three sources:
          (1) tool-list compression per backend (raw vs. compressed-wrapper
          token/byte counts), (2) per-call accounting for every tool
          invocation routed through this proxy, and (3) pincher's
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
        return JSONResponse(await recorder.snapshot())

    async def api_savings_stream(request: Request) -> StreamingResponse:
        """
        summary: Server-Sent-Events stream of incremental savings events.
        description: |
          Each frame is a JSON object with at least an `event` key
          (`call`, `compression`, or `pincher_stats`). Clients should
          listen for these to invalidate cached `/api/savings` snapshots
          and trigger a fresh fetch.
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

    async def api_repos_list(request: Request) -> JSONResponse:
        """
        summary: List git repositories discovered under the read-only mount.
        description: |
          Walks ``/user_data_ro`` (or whatever ``ZELOSMCP_REPO_SCAN_ROOT``
          points at) shallowly, returning every directory containing a
          ``.git`` entry. Each result includes the read-only path, the
          read-write twin under ``/user_data_rw`` (used by the filesystem
          MCP for writes), whether a ``.cursor/rules/zelosmcp.mdc`` already
          exists, and whether pincher has indexed the repo as a project.
          Results are cached for 30 s; pass ``refresh=1`` to bust the cache.
        tags: [introspection]
        parameters:
          - in: query
            name: refresh
            required: false
            schema: { type: string, enum: ["1"] }
            description: When set, ignore the in-process cache and rescan.
        responses:
          200:
            description: |
              ``{"repos": [{"name", "path_ro", "path_rw", "has_rule",
              "pincher_indexed"}]}``. Sorted by lower-case basename.
            content:
              application/json: {}
        """
        refresh = request.query_params.get("refresh") == "1"
        repos = discover_repos(refresh=refresh)
        indexed: set[str] = set()
        pi = manager.servers.get("pincher")
        if pi is not None and pi.running and pi.client_session is not None:
            try:
                result = await pi.client_session.call_tool("list", {})
                indexed = _extract_pincher_indexed_paths(result)
            except Exception as exc:
                logging.getLogger("zelosmcp").info(
                    "pincher__list failed during /api/repos: %s", exc
                )
        out = []
        for r in repos:
            d = r.to_dict()
            d["pincher_indexed"] = r.path_ro in indexed
            out.append(d)
        return JSONResponse({"repos": out})

    async def api_repo_write_rule(request: Request) -> JSONResponse:
        """
        summary: Generate a Cursor rule and write it into a discovered repo.
        description: |
          Builds the rule body via the same code path as
          ``GET /api/cursor-rule``, then forwards a ``write_file`` call to
          the running ``filesystem`` MCP backend. The target directory is
          computed by swapping the read-only mount prefix for the
          read-write one (e.g. ``/user_data_ro/foo`` ->
          ``/user_data_rw/foo/.cursor/rules/zelosmcp.mdc``). Filesystem's
          own sandbox refuses writes outside ``/user_data_rw``, so this
          handler trusts that gate after a single prefix check.
        tags: [introspection]
        responses:
          200: { description: "Rule written. Returns ``{ok, path, bytes}``." }
          400: { description: Invalid path or unknown enum value. }
          503: { description: filesystem backend not running. }
        """
        try:
            body = await request.json()
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"invalid JSON: {exc}"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "body must be an object"}, status_code=400)

        path = body.get("path")
        if not isinstance(path, str) or not is_under_scan_root(path):
            return JSONResponse(
                {"ok": False, "error": "path must be under /user_data_ro"},
                status_code=400,
            )
        access = body.get("access", "read-only")
        if access not in ("read-only", "read-write"):
            return JSONResponse({"ok": False, "error": f"Unknown access: {access!r}"}, status_code=400)
        fmt = body.get("format", "cursor-mdc")
        if fmt not in RULE_RELATIVE_PATHS:
            return JSONResponse({"ok": False, "error": f"Unknown format: {fmt!r}"}, status_code=400)
        style = body.get("style", "always-apply")
        if style not in ("always-apply", "scoped"):
            return JSONResponse({"ok": False, "error": f"Unknown style: {style!r}"}, status_code=400)
        tool_use = body.get("tool_use", "priority")
        if tool_use not in ("available", "priority"):
            return JSONResponse({"ok": False, "error": f"Unknown tool_use: {tool_use!r}"}, status_code=400)
        globs = body.get("globs")
        if globs is not None and not isinstance(globs, str):
            return JSONResponse({"ok": False, "error": "globs must be a string"}, status_code=400)

        fs = manager.servers.get("filesystem")
        if fs is None or not fs.running or fs.client_session is None:
            return JSONResponse(
                {"ok": False, "error": "filesystem backend not running"},
                status_code=503,
            )

        catalog = await collect_backend_full_catalog(manager, skip_self=True)
        rule_body = render_comprehensive_rule(
            catalog,
            access=access,
            style=style,
            globs=globs,
            fmt=fmt,
            tool_use=tool_use,
            mandatory_names=manager.mandatory_names(),
        )

        target = rule_target(path, fmt)
        parent = os.path.dirname(target)
        try:
            await fs.client_session.call_tool("create_directory", {"path": parent})
            await fs.client_session.call_tool(
                "write_file", {"path": target, "content": rule_body}
            )
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"filesystem write failed: {exc}"},
                status_code=500,
            )
        return JSONResponse(
            {"ok": True, "path": target, "bytes": len(rule_body.encode("utf-8"))}
        )

    async def api_repo_index(request: Request) -> JSONResponse:
        """
        summary: Index a discovered repository in pincher.
        description: |
          Forwards the request to ``pincher__index`` so the repo becomes
          a queryable project. The path must live under the read-only
          scan root; pincher does the actual filesystem work against its
          own ``/user_data_ro`` mount.
        tags: [introspection]
        responses:
          200: { description: Indexed. Returns pincher's structured response. }
          400: { description: Path is not under /user_data_ro. }
          503: { description: pincher backend not running. }
        """
        try:
            body = await request.json()
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"invalid JSON: {exc}"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "body must be an object"}, status_code=400)
        path = body.get("path")
        if not isinstance(path, str) or not is_under_scan_root(path):
            return JSONResponse(
                {"ok": False, "error": "path must be under /user_data_ro"},
                status_code=400,
            )
        pi = manager.servers.get("pincher")
        if pi is None or not pi.running or pi.client_session is None:
            return JSONResponse(
                {"ok": False, "error": "pincher backend not running"},
                status_code=503,
            )
        try:
            result = await pi.client_session.call_tool("index", {"path": path})
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"pincher index failed: {exc}"},
                status_code=500,
            )
        return JSONResponse(
            {
                "ok": not bool(getattr(result, "isError", False)),
                "path": path,
                "result": _flatten_call_result(result),
            }
        )

    @contextlib.asynccontextmanager
    async def lifespan(app):
        # Start the always-on builtin MCP before serving any traffic so
        # /zelosmcp/mcp answers immediately and the aggregator can already
        # fan tools/list out to the builtin's in-memory client session.
        try:
            await manager.start_builtin()
        except Exception as exc:  # never fail the whole app on builtin startup
            logging.getLogger("zelosmcp").error(
                "builtin failed to start: %s", exc, exc_info=True
            )
        # Bring up the reverse-proxy httpx client so the dispatcher can
        # forward requests as soon as the first backend with a configured
        # reverseProxy starts.
        try:
            await manager.start_http_client()
        except Exception as exc:
            logging.getLogger("zelosmcp").error(
                "reverse-proxy client failed to start: %s", exc, exc_info=True
            )
        # Open the encrypted auth store before any provider tries to read
        # / write user tokens. Failure is logged but non-fatal — the app
        # still boots; OAuth providers just won't work until the store is
        # available.
        try:
            await manager.start_auth_store()
        except Exception as exc:
            logging.getLogger("zelosmcp").error(
                "auth store failed to start: %s", exc, exc_info=True
            )
        # Auto-load the auth-providers config from disk before serving
        # any traffic so backend specs that reference providers can
        # resolve at /api/start time. Path follows the same priority
        # order as ZELOSMCP_CONFIG: explicit env > default. Missing
        # file is non-fatal (deployment with no providers is valid);
        # malformed file fails the lifespan so misconfig surfaces at
        # boot rather than the first auth attempt.
        await _autoload_auth_providers(manager)
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                await manager.stop_builtin()
            with contextlib.suppress(Exception):
                await manager.stop_all()
            with contextlib.suppress(Exception):
                await manager.stop_auth_store()
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
            Route("/api/docs", api_docs_index),
            Route("/api/docs/{slug}", api_docs_get),
            Route("/catalog", catalog_page),
            Route("/api/logs", api_logs),
            Route("/api/savings", api_savings),
            Route("/api/savings/stream", api_savings_stream),
            Route("/api/servers/{name}", api_server_get),
            Route("/api/servers/{name}/start", api_server_start, methods=["POST"]),
            Route("/api/servers/{name}/stop", api_server_stop, methods=["POST"]),
            Route("/api/repos", api_repos_list),
            Route("/api/repos/write-rule", api_repo_write_rule, methods=["POST"]),
            Route("/api/repos/index", api_repo_index, methods=["POST"]),
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
        ],
    )

    async def _handle_aggregate_with_challenge(
        session_manager,
        scope: dict,
        receive,
        send,
        challenge_cls,
    ) -> None:
        """Wrap aggregator ``session_manager.handle_request`` so a
        :class:`PassthroughChallengeError` raised by an aggregator
        handler surfaces as a transport-level 401 + WWW-Authenticate
        instead of getting buried in a JSON-RPC error envelope.

        Why we use a side-channel ContextVar (``pending_challenge``)
        instead of catching exceptions: MCP's lowlevel ``Server``
        catches *any* exception from a handler and serialises it as a
        JSON-RPC error response (HTTP 200). A plain ``raise`` would
        therefore never surface the challenge to the HTTP layer.
        The aggregator handlers set the ContextVar before returning;
        we check it here after ``handle_request`` returns and rewrite
        the response if set.

        We buffer ALL response messages until ``handle_request``
        completes so we can swap the entire response (status + headers
        + body) in one shot.
        """
        from zelosmcp.passthrough_pool import pending_challenge as _pending

        # Bind a fresh mutable list as the per-request signal slot.
        # The list is shared by reference into child tasks the SDK
        # spawns to run handlers, so a handler appending to the list
        # is visible to us after ``handle_request`` returns.
        challenge_box: list = []
        token = _pending.set(challenge_box)

        buffered: list[dict] = []

        async def buffered_send(message: dict) -> None:
            buffered.append(message)

        challenge: BaseException | None = None
        try:
            await session_manager.handle_request(scope, receive, buffered_send)
        except challenge_cls as exc:
            challenge = exc
        finally:
            # If a handler signalled a challenge via the list, prefer
            # the first one (closest to the failed call). A direct
            # exception from handle_request also wins over signals.
            if challenge is None and challenge_box:
                challenge = challenge_box[0]
            _pending.reset(token)

        if challenge is not None:
            ww = getattr(challenge, "www_authenticate", None) or "Bearer"
            status = getattr(challenge, "status", 401) or 401
            backend = getattr(challenge, "backend", "unknown")
            await send({
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", ww.encode("latin-1", errors="replace")),
                ],
            })
            import json as _json

            await send({
                "type": "http.response.body",
                "body": _json.dumps({
                    "error": "authentication_required",
                    "backend": backend,
                }).encode("utf-8"),
                "more_body": False,
            })
            return

        # No challenge: replay the buffered messages in order.
        for msg in buffered:
            await send(msg)

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
                # OAuth-passthrough backends have no zelosMCP-owned
                # session_manager; route them through the streaming HTTP
                # forwarder so the client's OAuth dance flows directly to
                # the upstream issuer. Only applies to /<name>/mcp — the
                # /mcp aggregator handles passthrough internally via the
                # session pool (Phase 2B).
                if (
                    target is not None
                    and target_label != "aggregate"
                    and getattr(target, "is_passthrough", False)
                ):
                    if not getattr(target, "running", False):
                        resp = JSONResponse(
                            {"error": f"No MCP server '{target_label}' is running"},
                            status_code=503,
                        )
                        return await resp(scope, receive, send)
                    spec = manager.get_spec(target_label)
                    if spec is None or not spec.passthrough:
                        # Inconsistent state — state says passthrough but
                        # spec disagrees. Fail loudly rather than silently
                        # routing through the wrong path.
                        resp = JSONResponse(
                            {
                                "error": (
                                    f"backend '{target_label}' is in passthrough "
                                    "state but has no matching ServerSpec"
                                )
                            },
                            status_code=500,
                        )
                        return await resp(scope, receive, send)
                    return await manager.proxy_mcp_request(spec, scope, receive, send)

                if target is not None and getattr(target, "session_manager", None) is not None:
                    # Strip the routing prefix so the session manager sees a
                    # path it expects (e.g. "/mcp" or "").
                    forwarded = dict(scope)
                    forwarded["path"] = "/mcp"
                    forwarded["raw_path"] = b"/mcp"
                    # Make the inbound HTTP Authorization header readable
                    # from inside MCP handlers via the ContextVar set
                    # below. The aggregator uses this to route passthrough
                    # backend calls through their per-token session pool;
                    # all other backends ignore it.
                    from zelosmcp.passthrough_pool import (
                        PassthroughChallengeError,
                        inbound_authorization,
                    )

                    auth_value: str | None = None
                    for k, v in scope.get("headers", []):
                        if k.lower() == b"authorization":
                            try:
                                auth_value = v.decode("latin-1")
                            except Exception:
                                auth_value = None
                            break
                    auth_token = inbound_authorization.set(auth_value)
                    try:
                        # Phase 2C: the middleware around session_manager
                        # converts a PassthroughChallengeError raised
                        # from inside an aggregator handler into a 401
                        # + WWW-Authenticate response. For the per-
                        # backend / non-aggregator path the session
                        # manager runs handlers as today.
                        if target_label == "aggregate":
                            return await _handle_aggregate_with_challenge(
                                target.session_manager,
                                forwarded,
                                receive,
                                send,
                                PassthroughChallengeError,
                            )
                        return await target.session_manager.handle_request(
                            forwarded, receive, send
                        )
                    finally:
                        inbound_authorization.reset(auth_token)
                if target_label == "aggregate":
                    msg = "No MCP servers are running"
                else:
                    msg = f"No MCP server '{target_label}' is running"
                resp = JSONResponse({"error": msg}, status_code=503)
                return await resp(scope, receive, send)

            # Reverse-proxy dispatch: a backend may declare a
            # `reverseProxy.mount` so its HTTP sidecar is reachable
            # under zelosMCP's port. Match on the original (un-stripped)
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
