"""In-app docs viewer + catalog + cursor-rule routes.

Returns the markdown-rendered docs index/detail, the JSON tool catalog,
the standalone HTML catalog page, and the dynamic ``cursor-rule`` /
``copilot-instructions`` generator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from starlette.routing import Route

from zelosmcp.builtin import (
    collect_backend_full_catalog,
    render_comprehensive_rule,
)
from zelosmcp.docs import list_docs, read_doc
from zelosmcp.ui import CATALOG_HTML_TEMPLATE

if TYPE_CHECKING:
    from zelosmcp.manager import ProxyManager


def make_routes(manager: "ProxyManager") -> list[Route]:
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
        skill_assets_map = None
        if manager.assets is not None:
            try:
                from zelosmcp.framework.assetstore.kinds.rule import load_all_rule_assets
                backends = list(catalog.keys()) + ["zelosmcp"]
                rule_assets_map = await load_all_rule_assets(manager.assets, backends)
            except Exception:
                rule_assets_map = None
            try:
                from zelosmcp.framework.assetstore.kinds.skill import (
                    load_all_skill_summaries,
                )
                backends = list(catalog.keys()) + ["zelosmcp"]
                skill_assets_map = await load_all_skill_summaries(
                    manager.assets, backends
                )
            except Exception:
                skill_assets_map = None

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
            skill_assets=skill_assets_map,
            compressed_backends=compressed_backends or None,
        )
        return PlainTextResponse(body, media_type="text/markdown; charset=utf-8")
    return [
        Route("/api/catalog", api_catalog),
        Route("/api/docs", api_docs_index),
        Route("/api/docs/{slug}", api_docs_get),
        Route("/catalog", catalog_page),
        Route("/api/cursor-rule", api_cursor_rule),
    ]
