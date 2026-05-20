"""Asset-store HTTP routes.

Extracted from ``app.create_app`` so the asset CRUD / push / YAML /
seed surface lives in one place. Each handler closes over the
``ProxyManager`` passed to :func:`make_routes`. Docstrings are
intentionally preserved as Starlette's :class:`SchemaGenerator` scans
them to build the OpenAPI document.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

if TYPE_CHECKING:
    from zelosmcp.manager import ProxyManager


def make_routes(manager: "ProxyManager") -> list[Route]:
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
          - vscode: `.github/copilot-instructions.md`

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

    async def api_assets_remove_all(request: Request) -> JSONResponse:
        """
        summary: Remove all zelosmcp-managed assets from a repo.
        description: |
          Deletes all files created by zelosmcp push operations and cleans
          zelosmcp-owned entries from merge-mode files (hooks, mcp.json).
          Preserves `.cursor/`, `.vscode/` directories and any
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

    return [
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
    ]
