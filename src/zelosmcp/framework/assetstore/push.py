"""Push-to-project writer.

:func:`push_asset` translates one or more :class:`~row.AssetRow` objects
into files in the target repo via the ``filesystem`` MCP backend.
The write strategy per file is determined by the kind's
:attr:`~kinds.AssetKind.render_for_project` function:

- ``mode='overwrite'`` — calls :func:`filesystem__write_file` directly.
- ``mode='merge'`` — reads the existing file first, delegates to the
  kind-specific merge helper (e.g. the hook JSON merger), then writes.

All paths are written under ``repo_rw_path``; attempts to escape that
root are rejected.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from zelosmcp.framework.assetstore.registry import RepoCtx  # noqa: F401

logger = logging.getLogger("zelosmcp.assets.push")


@dataclass
class PushedFile:
    """Record of one file written by the push writer."""

    path: str
    mode: str  # "overwrite" | "merge"
    ok: bool = True
    error: str = ""


class NotPushable(ValueError):
    """Raised when a kind does not support push-to-project."""


async def _fs_read(fs_session: Any, path: str) -> str:
    """Best-effort read via the filesystem MCP.  Returns ``""`` on error."""
    try:
        result = await fs_session.call_tool("read_text_file", {"path": path})
        texts = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                texts.append(text)
        return "\n".join(texts)
    except Exception as exc:
        logger.debug("push: read %s failed (will treat as empty): %s", path, exc)
        return ""


async def _fs_ensure_dir(fs_session: Any, path: str) -> None:
    """Create the parent directory of *path* via the filesystem MCP.

    ``filesystem__create_directory`` is idempotent (safe on existing dirs).
    We call it before every write so that new directories such as
    ``.github/``, ``.vscode/``, ``.github/skills/<slug>/`` etc. are
    created automatically — ``write_file`` does not create missing parents.
    """
    import os
    parent = os.path.dirname(path)
    if parent:
        try:
            await fs_session.call_tool("create_directory", {"path": parent})
        except Exception as exc:
            logger.debug("push: create_directory %s failed: %s", parent, exc)


async def _fs_write(fs_session: Any, path: str, body: str) -> None:
    """Ensure the parent directory exists, then write *path* via the filesystem MCP."""
    await _fs_ensure_dir(fs_session, path)
    await fs_session.call_tool("write_file", {"path": path, "content": body})


def _safe_abs_path(repo_rw_path: str, rel_path: str) -> str:
    """Build an absolute path and reject path traversal attempts."""
    import os
    repo_rw_path = repo_rw_path.rstrip("/")
    full = os.path.normpath(f"{repo_rw_path}/{rel_path}")
    if not full.startswith(repo_rw_path + "/") and full != repo_rw_path:
        raise ValueError(
            f"push: rel_path {rel_path!r} escapes repo root {repo_rw_path!r}"
        )
    return full


async def push_asset(
    store: Any,
    fs_session: Any,
    *,
    kind: str,
    backend: str,
    name: str,
    repo_rw_path: str,
) -> list[PushedFile]:
    """Push one named asset to the target repo.

    Parameters
    ----------
    store:
        Open :class:`~sqlite.SQLiteAssetStore`.
    fs_session:
        An MCP :class:`mcp.client.session.ClientSession` connected to the
        ``filesystem`` backend.
    kind:
        Asset kind id (``"rule"``, ``"agent"``, ``"hook"``).
    backend:
        Backend the asset is associated with.
    name:
        Asset name, or ``"*"`` to push all assets for the given
        ``kind``+``backend`` combination.
    repo_rw_path:
        Absolute read-write path of the repo (e.g.
        ``/user_data_rw/myrepo``).

    Returns
    -------
    List of :class:`PushedFile` records (one per file written).
    """
    from zelosmcp.framework.assetstore import registry as _kinds
    from zelosmcp.framework.assetstore.registry import RepoCtx, ProjectFile

    kind_def = _kinds.lookup(kind)
    if kind_def is None or kind_def.render_for_project is None:
        raise NotPushable(f"asset kind '{kind}' does not support push-to-project")

    if name == "*":
        rows = await store.list(kind=kind, backend=backend)
    else:
        row = await store.get(kind, backend, name)
        rows = [row] if row is not None else []

    if not rows:
        return []

    ctx = RepoCtx(
        name=repo_rw_path.rstrip("/").rsplit("/", 1)[-1],
        ro_path=repo_rw_path.replace("/user_data_rw/", "/user_data_ro/", 1),
        rw_path=repo_rw_path,
    )

    pushed: list[PushedFile] = []

    for row in rows:
        try:
            project_files: list[ProjectFile] = kind_def.render_for_project(row, ctx)
        except Exception as exc:
            logger.warning(
                "push: render_for_project failed for %s/%s/%s: %s",
                kind, backend, row.name, exc,
            )
            pushed.append(PushedFile(
                path=f"{repo_rw_path}/<render error>",
                mode="?",
                ok=False,
                error=str(exc),
            ))
            continue

        for pf in project_files:
            try:
                abs_path = _safe_abs_path(repo_rw_path, pf.rel_path)
            except ValueError as exc:
                pushed.append(PushedFile(
                    path=pf.rel_path, mode=pf.mode, ok=False, error=str(exc)
                ))
                continue

            try:
                if pf.mode == "merge":
                    existing = await _fs_read(fs_session, abs_path)
                    merged_body = _merge_file(kind, pf, existing)
                else:
                    merged_body = pf.body

                await _fs_write(fs_session, abs_path, merged_body)
                pushed.append(PushedFile(path=abs_path, mode=pf.mode, ok=True))
            except Exception as exc:
                logger.warning("push: write %s failed: %s", abs_path, exc)
                pushed.append(PushedFile(
                    path=abs_path, mode=pf.mode, ok=False, error=str(exc)
                ))

    return pushed


def _merge_file(kind: str, pf: "Any", existing: str) -> str:
    """Dispatch to the per-kind merge helper for ``mode='merge'`` files."""
    if kind == "hook":
        rel = pf.rel_path
        if rel == ".cursor/hooks.json":
            from zelosmcp.framework.assetstore.kinds.hook import merge_hooks_json
            try:
                new_entry = json.loads(pf.body)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"hook push: body is not valid JSON: {exc}") from exc
            return merge_hooks_json(existing, new_entry)
        elif rel.endswith("hooks.json") or rel.startswith(".github/hooks/"):
            # VS Code hook files: .vscode/hooks.json and .github/hooks/zelosmcp.json
            from zelosmcp.framework.assetstore.kinds.hook import merge_vscode_hooks_json
            try:
                new_entry = json.loads(pf.body)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"hook push: body is not valid JSON: {exc}") from exc
            return merge_vscode_hooks_json(existing, new_entry)
    # Generic fallback: overwrite.
    return pf.body


# ── Comprehensive push ─────────────────────────────────────────────────


async def push_kind_for_all_running(
    store: Any,
    fs_session: Any,
    manager: Any,
    *,
    kind: str,
    repo_rw_path: str,
    fmt: str = "cursor-mdc",
    access: str = "read-only",
    tool_use: str = "priority",
    style: str = "always-apply",
    globs: str = "",
    targets: list[str] | None = None,
    repo_ro_path: str | None = None,
) -> list[PushedFile]:
    """Push every asset of the given kind across the zelosmcp global backend
    AND every currently-running user backend.

    Parameters
    ----------
    store, fs_session, manager:
        Standard push dependencies.
    kind:
        ``"rule"``, ``"agent"``, or ``"hook"``.  Extensions are not pushed.
    repo_rw_path:
        Absolute read-write path of the target repo.
    fmt, access, tool_use, style, globs:
        Passed to the rule renderer for ``kind='rule'`` only.  ``fmt`` is
        kept for backward compatibility; when ``targets`` is also specified
        it takes precedence.
    targets:
        For ``kind='rule'``: list of IDE targets to write.  Supported
        values are ``"cursor"`` and ``"vscode"``.  Defaults to both.
        For other kinds, targets are driven by each row's ``meta.targets``
        field.
    repo_ro_path:
        Read-only path of the repo (for prefs DB update + zelosmcp.json
        write).  Inferred from ``repo_rw_path`` when omitted.

    Returns
    -------
    Flat list of all :class:`PushedFile` records across all backends.
    """
    from zelosmcp.framework.assetstore import registry as _registry

    kind_def = _registry.lookup(kind)
    if kind_def is None or kind_def.render_for_project is None:
        raise NotPushable(f"kind '{kind}' does not support push-to-project")

    # Collect running backends + always-on zelosmcp global.
    backends_to_push: list[str] = ["zelosmcp"]
    for name, state in manager.servers.items():
        if name == "zelosmcp":
            continue
        if getattr(state, "running", False):
            backends_to_push.append(name)

    if kind == "rule":
        # For rules, use the comprehensive renderer rather than per-row push.
        effective_targets = _resolve_targets(targets, fmt)
        pushed = await _push_comprehensive_rule(
            store=store,
            fs_session=fs_session,
            manager=manager,
            repo_rw_path=repo_rw_path,
            access=access,
            tool_use=tool_use,
            backends=backends_to_push,
            targets=effective_targets,
        )
    else:
        # For agents and hooks: collect all rows across global + running backends.
        pushed = []
        for backend in backends_to_push:
            p = await push_asset(
                store,
                fs_session,
                kind=kind,
                backend=backend,
                name="*",
                repo_rw_path=repo_rw_path,
            )
            pushed.extend(p)

    # After any push: update the prefs DB row and write zelosmcp.json to all
    # three IDE directories so the next discovery seeds from disk correctly.
    if store is not None and any(p.ok for p in pushed):
        await _post_push_update_prefs(
            store=store,
            fs_session=fs_session,
            kind=kind,
            repo_rw_path=repo_rw_path,
            repo_ro_path=repo_ro_path,
            targets=_resolve_targets(targets, fmt),
            tool_use=tool_use,
            access=access,
            style=style,
            globs=globs,
        )

    return pushed


async def _post_push_update_prefs(
    store: Any,
    fs_session: Any,
    *,
    kind: str,
    repo_rw_path: str,
    repo_ro_path: str | None,
    targets: list[str],
    tool_use: str,
    access: str,
    style: str,
    globs: str,
) -> None:
    """Update the prefs DB + write zelosmcp.json to all three IDE dirs."""
    import os
    from zelosmcp.framework.assetstore.prefs import (
        ProjectPrefs,
        get_prefs,
        upsert_prefs,
        update_last_pushed,
        prefs_to_json,
    )
    from zelosmcp.framework.assetstore.registry import ProjectFile

    # Derive ro path from rw path when not supplied.
    ro_path = repo_ro_path
    if ro_path is None:
        ro_path = repo_rw_path.replace("/user_data_rw/", "/user_data_ro/", 1)

    name = os.path.basename(ro_path.rstrip("/")) or ro_path

    # Load existing prefs to preserve other last_pushed_* values.
    existing = await get_prefs(store, ro_path)
    prefs = ProjectPrefs(
        path_ro=ro_path,
        name=name,
        targets=targets,
        tool_use=tool_use,
        access=access,
        style=style,
        globs=globs,
        last_pushed_rule=existing.last_pushed_rule if existing else None,
        last_pushed_agent=existing.last_pushed_agent if existing else None,
        last_pushed_hook=existing.last_pushed_hook if existing else None,
    )
    # Update the just-pushed kind timestamp.
    import time as _time
    ts = _time.time()
    if kind == "rule":
        prefs.last_pushed_rule = ts
    elif kind == "agent":
        prefs.last_pushed_agent = ts
    elif kind == "hook":
        prefs.last_pushed_hook = ts

    await upsert_prefs(store, prefs)

    # Write zelosmcp.json to .cursor/, .github/, .vscode/.
    json_body = prefs_to_json(prefs)
    for rel in (
        ".cursor/zelosmcp.json",
        ".github/zelosmcp.json",
        ".vscode/zelosmcp.json",
    ):
        try:
            abs_path = _safe_abs_path(repo_rw_path, rel)
            await _fs_write(fs_session, abs_path, json_body)
        except Exception as exc:
            logger.debug("prefs: failed to write %s: %s", rel, exc)


def _resolve_targets(
    targets: list[str] | None,
    fmt: str,
) -> list[str]:
    """Resolve the effective rule targets from explicit ``targets`` or legacy ``fmt``.

    When ``targets`` is provided it is used directly.  Otherwise ``fmt`` is
    translated: ``"cursor-mdc"`` → ``["cursor"]``,
    ``"copilot-instructions"`` → ``["vscode"]``, any other value → both.
    """
    if targets is not None:
        return [t for t in targets if t in ("cursor", "vscode")]
    if fmt == "cursor-mdc":
        return ["cursor"]
    if fmt == "copilot-instructions":
        return ["vscode"]
    return ["cursor", "vscode"]


async def _push_comprehensive_rule(
    store: Any,
    fs_session: Any,
    manager: Any,
    *,
    repo_rw_path: str,
    access: str,
    tool_use: str,
    backends: list[str],
    targets: list[str],
) -> list[PushedFile]:
    """Render comprehensive rule document(s) and write them to the repo.

    One render pass per distinct format is performed so the catalog is
    built only once per call.  For ``"cursor"`` target the ``cursor-mdc``
    format is used; for ``"vscode"`` the ``copilot-instructions`` format is
    used and the body is written to both ``.github/copilot-instructions.md``
    and ``.vscode/copilot-instructions.md``.
    """
    from zelosmcp.builtin import (
        collect_backend_full_catalog,
        render_comprehensive_rule,
    )
    from zelosmcp.framework.assetstore.kinds.rule import load_all_rule_assets
    from zelosmcp.framework.assetstore.registry import ProjectFile

    catalog = await collect_backend_full_catalog(manager, skip_self=False)
    rule_assets = await load_all_rule_assets(store, list(catalog.keys()) + ["zelosmcp"])

    project_files: list[ProjectFile] = []

    if "cursor" in targets:
        cursor_body = render_comprehensive_rule(
            catalog,
            access=access,
            fmt="cursor-mdc",
            tool_use=tool_use,
            rule_assets=rule_assets,
        )
        project_files.append(ProjectFile(
            rel_path=".cursor/rules/zelosmcp.mdc",
            body=cursor_body,
            mode="overwrite",
        ))

    if "vscode" in targets:
        vscode_body = render_comprehensive_rule(
            catalog,
            access=access,
            fmt="copilot-instructions",
            tool_use=tool_use,
            rule_assets=rule_assets,
        )
        project_files.append(ProjectFile(
            rel_path=".github/copilot-instructions.md",
            body=vscode_body,
            mode="overwrite",
        ))
        project_files.append(ProjectFile(
            rel_path=".vscode/copilot-instructions.md",
            body=vscode_body,
            mode="overwrite",
        ))

    pushed: list[PushedFile] = []
    for pf in project_files:
        try:
            abs_path = _safe_abs_path(repo_rw_path, pf.rel_path)
            await _fs_write(fs_session, abs_path, pf.body)
            pushed.append(PushedFile(path=abs_path, mode="overwrite", ok=True))
        except Exception as exc:
            pushed.append(PushedFile(
                path=pf.rel_path, mode="overwrite", ok=False, error=str(exc)
            ))
    return pushed
