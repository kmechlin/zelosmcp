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
    RULE_TARGET_PATHS,
    discover_repos,
    is_under_scan_root,
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


def _bounded_int_env(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _query_flag(request: Request, name: str) -> bool:
    raw = request.query_params.get(name)
    if raw is None:
        return False
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


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
    """Mount an upstream OpenAPI path under the public reverse-proxy prefix.

    Some upstream servers include their own mount prefix in their OpenAPI path
    keys (e.g. pincher exposes ``/pincher/v1/adr`` when mounted at
    ``/pincher``).  If the path already starts with *mount* we must not
    prepend it a second time — strip it first so we re-attach it cleanly.
    """
    normalized = path if path.startswith("/") else f"/{path}"
    if normalized == "/":
        return mount
    # Strip an accidental duplicate mount prefix that some upstreams include.
    mount_prefix = mount.rstrip("/")
    if mount_prefix and normalized.startswith(mount_prefix + "/"):
        normalized = normalized[len(mount_prefix):]
    return f"{mount_prefix}{normalized}"


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

    # ── Asset API ───────────────────────────────────────────────────────

    def _assets_unavailable() -> JSONResponse:
        return JSONResponse(
            {"error": "asset store not initialised"},
            status_code=503,
        )

    async def api_assets_list(request: Request) -> JSONResponse:
        """
        summary: List all asset store rows.
        description: |
          Returns every row optionally filtered by `?kind=`, `?backend=`,
          and/or `?target=`. Each row includes its kind, backend, name,
          target, body, meta, source, seed_version, and updated_at.
        tags: [introspection]
        responses:
          200:
            description: Array of asset rows.
            content:
              application/json: {}
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        kind = request.query_params.get("kind")
        backend = request.query_params.get("backend")
        target = request.query_params.get("target")
        rows = await manager.assets.list(kind=kind, backend=backend, target=target)
        return JSONResponse([r.to_dict() for r in rows])

    async def api_assets_kinds(request: Request) -> JSONResponse:
        """
        summary: List registered asset kinds.
        tags: [introspection]
        responses:
          200:
            description: Array of kind descriptors.
        """
        from zelosmcp.framework.assetstore import registry as _kinds
        return JSONResponse([
            {"id": k.id, "label": k.label, "description": k.description}
            for k in _kinds.known()
        ])

    async def api_assets_get(request: Request) -> JSONResponse:
        """
        summary: Get one asset row.
        tags: [introspection]
        parameters:
          - in: path
            name: kind
          - in: path
            name: backend
          - in: path
            name: name
        responses:
          200:
            description: Asset row.
          404:
            description: Not found.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        kind = request.path_params["kind"]
        backend = request.path_params["backend"]
        name = request.path_params["name"]
        target = request.query_params.get("target", "")
        row = await manager.assets.get(kind, backend, name, target)
        if row is None:
            return JSONResponse(
                {"error": f"asset '{kind}/{backend}/{name}' not found"},
                status_code=404,
            )
        return JSONResponse(row.to_dict())

    async def api_assets_put(request: Request) -> JSONResponse:
        """
        summary: Create or update an asset row (user override).
        tags: [lifecycle]
        requestBody:
          required: true
          content:
            application/json:
              schema:
                type: object
                properties:
                  body: { type: string }
                  meta: { type: object }
                  target: { type: string }
        responses:
          200:
            description: Updated row.
          400:
            description: Bad request.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        kind = request.path_params["kind"]
        backend = request.path_params["backend"]
        name = request.path_params["name"]
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

        from zelosmcp.framework.assetstore.row import AssetRow
        row = AssetRow(
            kind=kind,
            backend=backend,
            name=name,
            target=body.get("target", ""),
            body=body.get("body", ""),
            meta=body.get("meta") or {},
            source="user",
            seed_version=None,
        )
        await manager.assets.upsert(row)
        saved = await manager.assets.get(kind, backend, name, row.target)
        return JSONResponse((saved or row).to_dict())

    async def api_assets_delete(request: Request) -> JSONResponse:
        """
        summary: Delete a user-overridden asset row.
        description: |
          Removes the row; the next seed pass (on restart or reload) will
          re-insert the original seed content.
        tags: [lifecycle]
        responses:
          200:
            description: Deletion result.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        kind = request.path_params["kind"]
        backend = request.path_params["backend"]
        name = request.path_params["name"]
        target = request.query_params.get("target", "")
        removed = await manager.assets.delete(kind, backend, name, target)
        return JSONResponse({"ok": removed, "kind": kind, "backend": backend, "name": name})

    async def api_assets_extension_invoke(request: Request) -> JSONResponse:
        """
        summary: Invoke an extension asset (run its MCP tool call).
        tags: [lifecycle]
        requestBody:
          required: false
          content:
            application/json:
              schema:
                type: object
                properties:
                  ctx:
                    type: object
                    description: Context for args_template substitution.
        responses:
          200:
            description: Extension invocation result.
          404:
            description: Extension not found.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        backend = request.path_params["backend"]
        name = request.path_params["name"]
        try:
            body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        except Exception:
            body = {}
        ctx = body.get("ctx") or {} if isinstance(body, dict) else {}

        from zelosmcp.framework.assetstore.runner import invoke_extension
        result = await invoke_extension(
            manager.assets,
            manager,
            backend=backend,
            name=name,
            ctx=ctx,
        )
        return JSONResponse({
            "ok": result.ok,
            "message": result.message,
            "result": result.result,
            "error": result.error,
        })

    async def api_assets_push(request: Request) -> JSONResponse:
        """
        summary: Push an asset (or all assets for a backend) to a repo.
        tags: [lifecycle]
        requestBody:
          required: true
          content:
            application/json:
              schema:
                type: object
                required: [repo]
                properties:
                  repo: { type: string }
        responses:
          200:
            description: List of written files.
          400:
            description: Bad request or non-pushable kind.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        kind = request.path_params["kind"]
        backend = request.path_params["backend"]
        name = request.path_params["name"]
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict) or not body.get("repo"):
            return JSONResponse(
                {"error": "'repo' is required in request body"}, status_code=400
            )
        repo_name = body["repo"]

        from zelosmcp.repos import to_rw_path
        repo_rw_path = to_rw_path(f"/user_data_ro/{repo_name}")

        from zelosmcp.framework.assetstore.push import push_asset, NotPushable
        try:
            pushed = await push_asset(
                manager.assets,
                kind=kind,
                backend=backend,
                name=name,
                repo_rw_path=repo_rw_path,
            )
        except NotPushable as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

        return JSONResponse({
            "ok": all(p.ok for p in pushed),
            "files": [{"path": p.path, "mode": p.mode, "ok": p.ok, "error": p.error}
                      for p in pushed],
        })

    async def api_assets_push_kind(request: Request) -> JSONResponse:
        """
        summary: Push all assets of one kind to a repo.
        description: |
          Aggregates assets from the zelosmcp global backend AND every
          currently-running user backend, then writes the combined set into
          the target repo.

          For `rule`: writes to IDE targets specified by `targets`
          (default: both `cursor` and `vscode`).
          - cursor: `.cursor/rules/zelosmcp.mdc`
          - vscode: `.github/copilot-instructions.md` +
                    `.vscode/copilot-instructions.md`

          For `agent`: writes one SKILL.md per agent per active target.
          For `hook`: merges into per-target hook files.

          The legacy `fmt` field is still accepted as a single-target
          shortcut (`cursor-mdc` → cursor only, `copilot-instructions`
          → vscode only) and is overridden by `targets` when both are
          present.
        tags: [lifecycle]
        requestBody:
          required: true
          content:
            application/json:
              schema:
                type: object
                required: [repo]
                properties:
                  repo: { type: string }
                  fmt: { type: string, description: "Legacy single-target format selector. Prefer `targets`." }
                  targets:
                    type: array
                    items: { type: string, enum: [cursor, vscode] }
                    description: "IDE targets to push. Defaults to [cursor, vscode]."
                  access: { type: string }
                  tool_use: { type: string }
        responses:
          200:
            description: Files written.
          400:
            description: Bad request or non-pushable kind.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        kind = request.path_params["kind"]
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict) or not body.get("repo"):
            return JSONResponse(
                {"error": "'repo' is required in request body"}, status_code=400
            )
        repo_name = body["repo"]
        fmt = body.get("fmt", "cursor-mdc")
        access = body.get("access", "read-only")
        tool_use = body.get("tool_use", "priority")
        style = body.get("style", "always-apply")
        globs = body.get("globs", "")
        raw_targets = body.get("targets")
        targets: list[str] | None = (
            [t for t in raw_targets if t in ("cursor", "vscode")]
            if isinstance(raw_targets, list)
            else None
        )

        from zelosmcp.repos import to_rw_path
        repo_ro_path = f"/user_data_ro/{repo_name}"
        repo_rw_path = to_rw_path(repo_ro_path)

        from zelosmcp.framework.assetstore.push import (
            push_kind_for_all_running,
            NotPushable,
        )
        try:
            pushed = await push_kind_for_all_running(
                manager.assets,
                manager,
                kind=kind,
                repo_rw_path=repo_rw_path,
                repo_ro_path=repo_ro_path,
                fmt=fmt,
                access=access,
                tool_use=tool_use,
                style=style,
                globs=globs,
                targets=targets,
            )
        except NotPushable as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

        # Build a summary of running backends included in the push.
        running_user = [
            n for n, s in manager.servers.items()
            if n != "zelosmcp" and getattr(s, "running", False)
        ]
        return JSONResponse({
            "ok": all(p.ok for p in pushed),
            "kind": kind,
            "repo": repo_name,
            "backends_included": ["zelosmcp"] + running_user,
            "files": [
                {"path": p.path, "mode": p.mode, "ok": p.ok, "error": p.error}
                for p in pushed
            ],
        })

    async def api_assets_summary(request: Request) -> JSONResponse:
        """
        summary: Asset store stats.
        tags: [introspection]
        responses:
          200:
            description: Stats dict with total rows and per-kind / per-source breakdown.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        return JSONResponse(await manager.assets.summary())

    async def api_assets_seed(request: Request) -> JSONResponse:
        """
        summary: Re-run the asset seeder on demand.
        description: |
          Re-seeds the asset store from the bundled YAML files.  Safe to
          call at any time — idempotent for seed rows; user-overridden rows
          are never touched.  Accepts an optional JSON body
          `{"config_root": "<absolute path>"}` to override the YAML tree
          location (useful inside the container where the source tree is
          mounted at a non-default path).
        tags: [lifecycle]
        responses:
          200:
            description: Counts of rows seeded per kind.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        from zelosmcp.framework.assetstore.seeder import seed_all
        from pathlib import Path as _Path
        config_root = None
        try:
            body = await request.json()
            if isinstance(body, dict) and body.get("config_root"):
                config_root = _Path(body["config_root"])
        except Exception:
            pass
        counts = await seed_all(manager.assets, config_root=config_root)
        summary = await manager.assets.summary()
        return JSONResponse({"ok": True, "seeded": counts, "summary": summary})

    # ── YAML editor API ─────────────────────────────────────────────────

    async def api_assets_yaml_get(request: Request) -> Response:
        """
        summary: Render backend's current asset rows as unified YAML.
        tags: [introspection]
        responses:
          200:
            description: YAML text matching the unified per-backend file schema.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        backend = request.path_params["backend"]
        from zelosmcp.framework.assetstore.yaml_io import dump_backend_as_yaml
        text = await dump_backend_as_yaml(manager.assets, backend)
        return Response(text, media_type="text/yaml; charset=utf-8")

    async def api_assets_yaml_put(request: Request) -> JSONResponse:
        """
        summary: Replace backend's asset rows from a unified YAML document.
        tags: [lifecycle]
        requestBody:
          required: true
          content:
            text/yaml:
              schema: {}
        responses:
          200:
            description: Rows written.
          400:
            description: YAML parse error or schema validation failure.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        backend = request.path_params["backend"]
        try:
            body_bytes = await request.body()
            text = body_bytes.decode("utf-8")
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        from zelosmcp.framework.assetstore.yaml_io import (
            YAMLValidationError,
            parse_backend_yaml,
        )
        import yaml as _yaml

        try:
            rows = parse_backend_yaml(text, backend, source="user")
        except YAMLValidationError as exc:
            return JSONResponse(
                {"ok": False, "errors": [e.to_dict() for e in exc.errors]},
                status_code=400,
            )
        except _yaml.YAMLError as exc:
            return JSONResponse(
                {"ok": False, "errors": [{"path": "", "message": f"YAML parse error: {exc}"}]},
                status_code=400,
            )

        # Delete all existing rows for this backend, then re-insert.
        existing = await manager.assets.list(backend=backend)
        for row in existing:
            await manager.assets.delete(row.kind, row.backend, row.name, row.target)
        for row in rows:
            await manager.assets.upsert(row)
        return JSONResponse({"ok": True, "rows_written": len(rows)})

    async def api_assets_yaml_validate(request: Request) -> JSONResponse:
        """
        summary: Validate YAML text against the asset file schema.
        description: |
          Parses and validates the request body as a unified backend asset
          YAML document.  Never writes to the store.  Intended for live
          client-side lint (debounced on every editor keystroke).
        tags: [introspection]
        responses:
          200:
            description: Validation result with ok flag and errors list.
        """
        backend = request.path_params["backend"]
        try:
            body_bytes = await request.body()
            text = body_bytes.decode("utf-8")
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "errors": [{"path": "", "message": str(exc)}]}
            )

        from zelosmcp.framework.assetstore.yaml_io import validate_yaml_text
        errors = validate_yaml_text(text, backend)
        return JSONResponse({
            "ok": len(errors) == 0,
            "errors": [e.to_dict() for e in errors],
        })

    async def api_assets_yaml_delete(request: Request) -> JSONResponse:
        """
        summary: Delete all rows for a backend.
        description: |
          Drops every asset row for the named backend.  On the next seeder
          run (boot or POST /api/assets/seed), the YAML file's rows will be
          restored if a matching file exists.
        tags: [lifecycle]
        responses:
          200:
            description: Deletion result.
          503:
            description: Asset store not initialised.
        """
        if manager.assets is None:
            return _assets_unavailable()
        backend = request.path_params["backend"]
        existing = await manager.assets.list(backend=backend)
        count = 0
        for row in existing:
            if await manager.assets.delete(row.kind, row.backend, row.name, row.target):
                count += 1
        return JSONResponse({"ok": True, "deleted": count, "backend": backend})

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
        rule_assets_map = None
        if manager.assets is not None:
            try:
                from zelosmcp.framework.assetstore.kinds.rule import load_all_rule_assets
                backends = list(catalog.keys()) + ["zelosmcp"]
                rule_assets_map = await load_all_rule_assets(manager.assets, backends)
            except Exception:
                rule_assets_map = None

        # Compute compression metadata for backends whose wire surface at /mcp
        # is the wrapper trio (get_tool_schema / search_tools / invoke_tool)
        # rather than the full tool list.
        compressed_backends: dict[str, dict[str, Any]] = {}
        for name, spec in manager._specs.items():
            if spec is None or spec.compress is None:
                continue
            c = spec.compress
            if c.level == "low":
                continue
            if c.scope not in ("aggregator", "global"):
                continue
            compressed_backends[name] = {"level": c.level, "scope": c.scope}

        body = render_comprehensive_rule(
            catalog,
            access=access,
            style=style,
            globs=globs,
            fmt=fmt,
            tool_use=tool_use,
            mandatory_names=manager.mandatory_names(),
            rule_assets=rule_assets_map,
            compressed_backends=compressed_backends or None,
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
        repos = discover_repos(refresh=refresh, store=manager.assets)
        # Async-seed prefs for repos newly discovered without a DB row.
        if manager.assets is not None:
            from zelosmcp.repos import seed_repo_prefs_async
            await seed_repo_prefs_async(manager.assets, repos)
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
        summary: Generate rule(s) and write them into a discovered repo.
        description: |
          Builds the rule body via the same code path as
          ``GET /api/cursor-rule``, then writes files directly to the
          read-write mount.

          The ``targets`` array controls which IDE output files are written:
          - ``cursor``: ``.cursor/rules/zelosmcp.mdc`` (mdc with frontmatter)
          - ``vscode``: ``.github/copilot-instructions.md`` +
                        ``.vscode/copilot-instructions.md`` (plain markdown)

          Defaults to both targets.  The legacy ``format`` field is still
          accepted (``cursor-mdc`` → cursor only, ``copilot-instructions``
          → vscode only) and is overridden by ``targets`` when both are
          present.
        tags: [introspection]
        responses:
          200: { description: "Rules written. Returns ``{ok, files}``." }
          400: { description: Invalid path or unknown enum value. }
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

        # Resolve IDE targets: explicit list overrides legacy fmt.
        from zelosmcp.framework.assetstore.push import _resolve_targets
        raw_targets = body.get("targets")
        targets_list: list[str] | None = (
            [t for t in raw_targets if t in ("cursor", "vscode")]
            if isinstance(raw_targets, list)
            else None
        )
        effective_targets = _resolve_targets(targets_list, fmt)

        catalog = await collect_backend_full_catalog(manager, skip_self=True)
        _push_compressed: dict[str, dict[str, Any]] = {}
        for _n, _s in manager._specs.items():
            if _s is None or _s.compress is None:
                continue
            _c = _s.compress
            if _c.level == "low" or _c.scope not in ("aggregator", "global"):
                continue
            _push_compressed[_n] = {"level": _c.level, "scope": _c.scope}

        written_files: list[dict] = []

        # Write one render pass per format needed.
        for target_name in effective_targets:
            render_fmt = "cursor-mdc" if target_name == "cursor" else "copilot-instructions"
            render_style = style if target_name == "cursor" else "always-apply"
            render_globs = globs if target_name == "cursor" else None
            rule_body = render_comprehensive_rule(
                catalog,
                access=access,
                style=render_style,
                globs=render_globs,
                fmt=render_fmt,
                tool_use=tool_use,
                mandatory_names=manager.mandatory_names(),
                compressed_backends=_push_compressed or None,
            )

            for rel_path in RULE_TARGET_PATHS.get(target_name, []):
                abs_path = os.path.join(to_rw_path(path), rel_path)
                parent = os.path.dirname(abs_path)
                try:
                    os.makedirs(parent, exist_ok=True)
                    import pathlib as _pathlib
                    _pathlib.Path(abs_path).write_text(rule_body, encoding="utf-8")
                    written_files.append({
                        "ok": True,
                        "path": abs_path,
                        "bytes": len(rule_body.encode("utf-8")),
                        "target": target_name,
                    })
                except Exception as exc:
                    written_files.append({
                        "ok": False,
                        "path": abs_path,
                        "error": str(exc),
                        "target": target_name,
                    })

        all_ok = all(f["ok"] for f in written_files)
        return JSONResponse({
            "ok": all_ok,
            # Legacy compat: single-file case returns flat path/bytes.
            **(
                {"path": written_files[0]["path"], "bytes": written_files[0].get("bytes", 0)}
                if len(written_files) == 1
                else {}
            ),
            "files": written_files,
        })

    async def api_repo_prefs_get(request: Request) -> JSONResponse:
        """
        summary: Get stored per-project preferences.
        tags: [introspection]
        responses:
          200: { description: "``ProjectPrefs`` dict." }
          400: { description: Invalid or missing path. }
          404: { description: No prefs row found for this path. }
          503: { description: Asset store not initialised. }
        """
        if manager.assets is None:
            return JSONResponse({"error": "asset store not initialised"}, status_code=503)
        path = request.query_params.get("path", "")
        if not isinstance(path, str) or not is_under_scan_root(path):
            return JSONResponse({"error": "path must be under /user_data_ro"}, status_code=400)
        from zelosmcp.framework.assetstore.prefs import get_prefs
        prefs = await get_prefs(manager.assets, path)
        if prefs is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(prefs.to_dict())

    async def api_repo_prefs_put(request: Request) -> JSONResponse:
        """
        summary: Upsert per-project preferences.
        tags: [introspection]
        responses:
          200: { description: Persisted prefs dict. }
          400: { description: Invalid request body. }
          503: { description: Asset store not initialised. }
        """
        if manager.assets is None:
            return JSONResponse({"error": "asset store not initialised"}, status_code=503)
        try:
            body = await request.json()
        except Exception as exc:
            return JSONResponse({"error": f"invalid JSON: {exc}"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be an object"}, status_code=400)
        path = body.get("path", "")
        if not isinstance(path, str) or not is_under_scan_root(path):
            return JSONResponse({"error": "path must be under /user_data_ro"}, status_code=400)
        import os as _os
        from zelosmcp.framework.assetstore.prefs import ProjectPrefs, get_prefs, upsert_prefs
        existing = await get_prefs(manager.assets, path)
        prefs = ProjectPrefs(
            path_ro=path,
            name=existing.name if existing else (_os.path.basename(path.rstrip("/")) or path),
            targets=body.get("targets") or (existing.targets if existing else ["cursor", "vscode"]),
            tool_use=body.get("tool_use") or (existing.tool_use if existing else "priority"),
            access=body.get("access") or (existing.access if existing else "read-only"),
            style=body.get("style") or (existing.style if existing else "always-apply"),
            globs=body.get("globs", existing.globs if existing else ""),
            last_pushed_rule=existing.last_pushed_rule if existing else None,
            last_pushed_agent=existing.last_pushed_agent if existing else None,
            last_pushed_hook=existing.last_pushed_hook if existing else None,
        )
        await upsert_prefs(manager.assets, prefs)
        return JSONResponse(prefs.to_dict())

    async def api_repos_push_all_with_rules(request: Request) -> JSONResponse:
        """
        summary: Push rules + agents + hooks to every repo that already has zelosmcp rules.
        description: |
          Iterates every discovered repo with ``has_rule=true``, loads its
          stored ``project_prefs``, and runs ``push_kind_for_all_running`` for
          each requested kind (default: rule, agent, hook).  Runs sequentially
          to avoid overwhelming disk I/O.
        tags: [lifecycle]
        responses:
          200: { description: Per-repo push results. }
          503: { description: Asset store unavailable. }
        """
        if manager.assets is None:
            return JSONResponse({"error": "asset store not initialised"}, status_code=503)

        # Parse optional body
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        requested_kinds: list[str] = body.get("kinds") or ["rule", "agent", "hook", "skill"]

        from zelosmcp.repos import discover_repos as _discover, seed_repo_prefs_async, to_rw_path
        from zelosmcp.framework.assetstore.prefs import get_prefs, ProjectPrefs
        from zelosmcp.framework.assetstore.push import push_kind_for_all_running, NotPushable

        repos = _discover(refresh=True, store=manager.assets)
        await seed_repo_prefs_async(manager.assets, repos)
        repos_with_rules = [r for r in repos if r.has_rule]

        results = []
        for repo in repos_with_rules:
            prefs = await get_prefs(manager.assets, repo.path_ro)
            if prefs is None:
                prefs = ProjectPrefs(path_ro=repo.path_ro, name=repo.name)
            repo_rw = to_rw_path(repo.path_ro)
            repo_result: dict = {"repo": repo.name, "path_ro": repo.path_ro, "kinds": {}}
            for kind in requested_kinds:
                try:
                    pushed = await push_kind_for_all_running(
                        manager.assets,
                        manager,
                        kind=kind,
                        repo_rw_path=repo_rw,
                        repo_ro_path=repo.path_ro,
                        targets=prefs.targets,
                        access=prefs.access,
                        tool_use=prefs.tool_use,
                        style=prefs.style,
                        globs=prefs.globs,
                    )
                    ok = all(p.ok for p in pushed)
                    repo_result["kinds"][kind] = {
                        "ok": ok,
                        "files": [{"path": p.path, "ok": p.ok, "error": p.error} for p in pushed],
                    }
                except NotPushable as exc:
                    repo_result["kinds"][kind] = {"ok": False, "error": str(exc)}
                except Exception as exc:
                    repo_result["kinds"][kind] = {"ok": False, "error": str(exc)}
            results.append(repo_result)

        overall_ok = all(
            kd.get("ok", False)
            for r in results
            for kd in r["kinds"].values()
        )
        return JSONResponse({"ok": overall_ok, "repos": results})

    async def api_assets_remove_all(request: Request) -> JSONResponse:
        """
        summary: Remove all zelosmcp-managed assets from a repo.
        description: |
          Deletes all files created by zelosmcp push operations and cleans
          zelosmcp-owned entries from merge-mode files (hooks, mcp.json).
          Preserves `.cursor/`, `.github/`, `.vscode/` directories and any
          files not managed by zelosmcp.
        tags: [lifecycle]
        requestBody:
          required: true
          content:
            application/json:
              schema:
                type: object
                required: [repo]
                properties:
                  repo: { type: string, description: "Repo name (basename of scan root)." }
        responses:
          200: { description: List of removed/cleaned files. }
          400: { description: Bad request. }
          503: { description: Asset store not initialised. }
        """
        if manager.assets is None:
            return JSONResponse({"error": "asset store not initialised"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict) or not body.get("repo"):
            return JSONResponse(
                {"error": "'repo' is required in request body"}, status_code=400
            )

        repo_name = body["repo"]
        from zelosmcp.repos import to_rw_path
        repo_rw_path = to_rw_path(f"/user_data_ro/{repo_name}")

        from zelosmcp.framework.assetstore.push import remove_pushed_assets
        try:
            removed = await remove_pushed_assets(
                manager.assets,
                repo_rw_path=repo_rw_path,
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

        return JSONResponse({
            "ok": True,
            "repo": repo_name,
            "removed": [
                {"path": r.path, "action": r.action, "error": r.error}
                for r in removed
            ],
        })

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
            Route("/api/events", api_events),
            Route("/api/events/retention", api_events_retention),
            Route("/api/events/summary", api_events_summary),
            Route("/api/events/stream", api_events_stream),
            Route("/api/savings", api_savings),
            Route("/api/savings/stream", api_savings_stream),
            Route("/api/servers/{name}", api_server_get),
            Route("/api/servers/{name}/start", api_server_start, methods=["POST"]),
            Route("/api/servers/{name}/stop", api_server_stop, methods=["POST"]),
            Route("/api/repos", api_repos_list),
            Route("/api/repos/write-rule", api_repo_write_rule, methods=["POST"]),
            Route("/api/repos/prefs", api_repo_prefs_get),
            Route("/api/repos/prefs", api_repo_prefs_put, methods=["PUT"]),
            Route("/api/repos/push-all-with-rules", api_repos_push_all_with_rules, methods=["POST"]),
            Route("/api/assets/remove-all", api_assets_remove_all, methods=["POST"]),
            Route("/api/assets", api_assets_list),
            Route("/api/assets/kinds", api_assets_kinds),
            Route("/api/assets/summary", api_assets_summary),
            Route("/api/assets/seed", api_assets_seed, methods=["POST"]),
            Route("/api/assets/yaml/{backend}", api_assets_yaml_get),
            Route("/api/assets/yaml/{backend}", api_assets_yaml_put, methods=["PUT"]),
            Route("/api/assets/yaml/{backend}", api_assets_yaml_delete, methods=["DELETE"]),
            Route(
                "/api/assets/yaml/{backend}/validate",
                api_assets_yaml_validate,
                methods=["POST"],
            ),
            Route(
                "/api/assets/push/{kind}",
                api_assets_push_kind,
                methods=["POST"],
            ),
            Route(
                "/api/assets/{kind}/{backend}/{name}/invoke",
                api_assets_extension_invoke,
                methods=["POST"],
            ),
            Route(
                "/api/assets/{kind}/{backend}/{name}/push",
                api_assets_push,
                methods=["POST"],
            ),
            Route("/api/assets/{kind}/{backend}/{name}", api_assets_get),
            Route(
                "/api/assets/{kind}/{backend}/{name}",
                api_assets_put,
                methods=["PUT"],
            ),
            Route(
                "/api/assets/{kind}/{backend}/{name}",
                api_assets_delete,
                methods=["DELETE"],
            ),
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
