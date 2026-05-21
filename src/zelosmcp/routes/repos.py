"""Repo discovery / rule-writing / push-all routes."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from zelosmcp.builtin import (
    collect_backend_full_catalog,
    render_comprehensive_rule,
)
from zelosmcp.openapi import extract_pincher_indexed_paths
from zelosmcp.repos import (
    RULE_RELATIVE_PATHS,
    RULE_TARGET_PATHS,
    discover_repos,
    is_under_scan_root,
    to_rw_path,
)

if TYPE_CHECKING:
    from zelosmcp.manager import ProxyManager


def make_routes(manager: "ProxyManager") -> list[Route]:
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
                indexed = extract_pincher_indexed_paths(result)
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
          - ``vscode``: ``.github/copilot-instructions.md`` (plain markdown)

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
        _push_rule_assets = None
        _push_skill_assets = None
        if manager.assets is not None:
            try:
                from zelosmcp.framework.assetstore.kinds.rule import load_all_rule_assets
                _backends = list(catalog.keys()) + ["zelosmcp"]
                _push_rule_assets = await load_all_rule_assets(manager.assets, _backends)
            except Exception:
                _push_rule_assets = None
            try:
                from zelosmcp.framework.assetstore.kinds.skill import (
                    load_all_skill_summaries,
                )
                _backends = list(catalog.keys()) + ["zelosmcp"]
                _push_skill_assets = await load_all_skill_summaries(
                    manager.assets, _backends
                )
            except Exception:
                _push_skill_assets = None
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
                rule_assets=_push_rule_assets,
                skill_assets=_push_skill_assets,
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

    return [
        Route("/api/repos", api_repos_list),
        Route("/api/repos/write-rule", api_repo_write_rule, methods=["POST"]),
        Route("/api/repos/prefs", api_repo_prefs_get),
        Route("/api/repos/prefs", api_repo_prefs_put, methods=["PUT"]),
        Route(
            "/api/repos/push-all-with-rules",
            api_repos_push_all_with_rules,
            methods=["POST"],
        ),
    ]
