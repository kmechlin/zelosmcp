"""OpenAPI / call-result helpers used by the app and route modules.

Extracted from ``app.py`` so the dispatcher module can stay focused on
ASGI dispatch + lifespan. These helpers are pure and have no manager /
Starlette dependency apart from :class:`ProxyManager` for the public
``with_upstream_openapi`` entry point.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request

    from zelosmcp.manager import ProxyManager


def flatten_call_result(result) -> dict | list | str | None:
    """Best-effort extraction of a single JSON payload from a ``CallToolResult``.

    Pincher returns a single ``TextContent`` whose ``.text`` is the JSON dump
    of its response; we parse that and return the dict so callers don't have
    to. Falls back to a list of strings or ``None`` if the response can't be
    JSON-parsed.
    """
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
            return json.loads(texts[0])
        except (ValueError, TypeError):
            return texts[0]
    return texts


def extract_pincher_indexed_paths(result) -> set[str]:
    """Pull the absolute repo paths out of a ``pincher__list`` response.

    Pincher returns ``{"projects": [{"name", "path", "files", ...}]}``.
    We map those paths back to whatever the scanner reports so the UI can
    flag already-indexed repos. Anything unparseable -> empty set, since
    a missing pincher_indexed flag is preferable to a 500 in /api/repos.
    """
    payload = flatten_call_result(result)
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


def prefix_openapi_path(mount: str, path: str) -> str:
    """Mount an upstream OpenAPI path under the public reverse-proxy prefix.

    Some upstream servers include their own mount prefix in their OpenAPI path
    keys (e.g. pincher exposes ``/pincher/v1/adr`` when mounted at
    ``/pincher``). If the path already starts with *mount* we must not prepend
    it a second time — strip it first so we re-attach it cleanly.
    """
    normalized = path if path.startswith("/") else f"/{path}"
    if normalized == "/":
        return mount
    mount_prefix = mount.rstrip("/")
    if mount_prefix and normalized.startswith(mount_prefix + "/"):
        normalized = normalized[len(mount_prefix):]
    return f"{mount_prefix}{normalized}"


def rewrite_component_refs(value: Any, ref_map: dict[str, str]) -> Any:
    """Recursively rewrite local OpenAPI component refs after namespacing."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str):
                out[key] = ref_map.get(item, item)
            else:
                out[key] = rewrite_component_refs(item, ref_map)
        return out
    if isinstance(value, list):
        return [rewrite_component_refs(item, ref_map) for item in value]
    return value


def merge_upstream_openapi(
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

    rewritten = rewrite_component_refs(upstream, ref_map)

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
        public_path = prefix_openapi_path(mount, upstream_path)
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


async def with_upstream_openapi(
    schema: dict[str, Any],
    manager: "ProxyManager",
    request: "Request",
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
            merge_upstream_openapi(
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


__all__ = [
    "flatten_call_result",
    "extract_pincher_indexed_paths",
    "prefix_openapi_path",
    "rewrite_component_refs",
    "merge_upstream_openapi",
    "with_upstream_openapi",
]
