"""Push-to-project writer.

:func:`push_asset` translates one or more :class:`~row.AssetRow` objects
into files in the target repo using direct filesystem I/O.
The write strategy per file is determined by the kind's
:attr:`~kinds.AssetKind.render_for_project` function:

- ``mode='overwrite'`` — writes the file directly.
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


def _local_read(path: str) -> str:
    """Best-effort read from disk.  Returns ``""`` on error."""
    import pathlib
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("push: read %s failed (will treat as empty): %s", path, exc)
        return ""


def _local_write(path: str, body: str) -> None:
    """Ensure parent directory exists, then write *path* to disk."""
    import os
    import pathlib
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    pathlib.Path(path).write_text(body, encoding="utf-8")


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
                    existing = _local_read(abs_path)
                    merged_body = _merge_file(kind, pf, existing)
                else:
                    merged_body = pf.body

                _local_write(abs_path, merged_body)
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
    store, manager:
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
            _local_write(abs_path, json_body)
        except Exception as exc:
            logger.debug("prefs: failed to write %s: %s", rel, exc)

    # When the VS Code target is active, also write .vscode/mcp.json with the
    # aggregator MCP server entry so VS Code Copilot picks up zelosMCP via
    # the single /mcp endpoint without further setup.
    if "vscode" in targets:
        try:
            mcp_body = _build_vscode_mcp_json()
            abs_path = _safe_abs_path(repo_rw_path, ".vscode/mcp.json")
            existing = _local_read(abs_path)
            merged = _merge_vscode_mcp_json(existing, mcp_body)
            _local_write(abs_path, merged)
        except Exception as exc:
            logger.debug("vscode mcp.json write failed: %s", exc)


# ── VS Code mcp.json helpers ─────────────────────────────────────────────


_DEFAULT_AGGREGATOR_URL = "http://localhost:8000/mcp"
_AGGREGATOR_ENTRY_NAME = "zelosmcp-aggregate"


def _aggregator_url() -> str:
    """Return the public URL of the zelosMCP aggregator endpoint.

    Honours the ``ZELOSMCP_PUBLIC_URL`` env var (e.g. when the proxy runs
    behind a reverse proxy on a non-default host/port).  Falls back to the
    hardcoded ``http://localhost:8000/mcp`` default that the rest of the
    codebase already assumes.
    """
    import os
    base = os.environ.get("ZELOSMCP_PUBLIC_URL")
    if base:
        return base.rstrip("/") + "/mcp"
    return _DEFAULT_AGGREGATOR_URL


def _build_vscode_mcp_json() -> str:
    """Render the VS Code-flavoured ``mcp.json`` body for the aggregator.

    VS Code's MCP config uses the top-level ``servers`` key (Cursor uses
    ``mcpServers``) and ``type: "http"`` for streamable HTTP servers
    (Cursor uses ``streamable-http``).  See
    https://code.visualstudio.com/docs/copilot/customization/mcp-servers
    """
    payload = {
        "servers": {
            _AGGREGATOR_ENTRY_NAME: {
                "type": "http",
                "url": _aggregator_url(),
            },
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def _merge_vscode_mcp_json(existing_text: str, new_body: str) -> str:
    """Merge our aggregator entry into a (possibly empty) VS Code mcp.json.

    Preserves any user-added entries under ``servers`` and only overwrites
    the single ``zelosmcp-aggregate`` key.  Falls back to overwriting the
    whole file if the existing content can't be parsed as JSON (corrupt /
    empty / missing).
    """
    try:
        new_payload = json.loads(new_body)
    except (ValueError, TypeError):
        return new_body

    if not existing_text.strip():
        return json.dumps(new_payload, indent=2) + "\n"

    try:
        data = json.loads(existing_text)
    except (ValueError, TypeError):
        return json.dumps(new_payload, indent=2) + "\n"

    if not isinstance(data, dict):
        return json.dumps(new_payload, indent=2) + "\n"

    servers = data.get("servers")
    if not isinstance(servers, dict):
        servers = {}
    new_servers = new_payload.get("servers", {})
    for name, spec in new_servers.items():
        servers[name] = spec
    data["servers"] = servers
    return json.dumps(data, indent=2) + "\n"


# ── Remove pushed assets ──────────────────────────────────────────────


@dataclass
class RemovedFile:
    """Record of one file or entry removed by the cleanup."""

    path: str
    action: str  # "deleted" | "cleaned" | "skipped"
    error: str = ""


def _remove_file(path: str) -> bool:
    """Delete *path* if it exists.  Returns True when removed."""
    import pathlib
    p = pathlib.Path(path)
    if p.is_file():
        p.unlink()
        return True
    return False


def _remove_dir_if_empty(path: str) -> None:
    """Remove *path* if it is an empty directory."""
    import pathlib
    p = pathlib.Path(path)
    if p.is_dir():
        try:
            p.rmdir()  # only succeeds if empty
        except OSError:
            pass


def _clean_cursor_hooks(path: str) -> str:
    """Remove zelosmcp-owned entries from ``.cursor/hooks.json``.

    Returns ``"cleaned"`` if the file was rewritten, ``"deleted"`` if the
    file ended up empty and was removed, or ``"skipped"`` if it did not
    exist.
    """
    import pathlib

    p = pathlib.Path(path)
    if not p.is_file():
        return "skipped"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, TypeError):
        return "skipped"

    hooks = data.get("hooks")
    if not isinstance(hooks, list):
        return "skipped"

    cleaned = [h for h in hooks if h.get("_owner") != "zelosmcp"]
    if cleaned:
        data["hooks"] = cleaned
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return "cleaned"
    else:
        # No hooks left — remove the file.
        p.unlink()
        return "deleted"


def _clean_vscode_hooks(path: str) -> str:
    """Remove zelosmcp-owned entries from VS Code hooks files.

    Returns ``"cleaned"``, ``"deleted"``, or ``"skipped"``.
    """
    import pathlib

    p = pathlib.Path(path)
    if not p.is_file():
        return "skipped"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, TypeError):
        return "skipped"

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return "skipped"

    any_remaining = False
    for event, entries in list(hooks.items()):
        if isinstance(entries, list):
            cleaned = [h for h in entries if h.get("_owner") != "zelosmcp"]
            if cleaned:
                hooks[event] = cleaned
                any_remaining = True
            else:
                del hooks[event]

    if any_remaining:
        data["hooks"] = hooks
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return "cleaned"
    else:
        p.unlink()
        return "deleted"


def _clean_vscode_mcp_json(path: str) -> str:
    """Remove the ``zelosmcp-aggregate`` entry from ``.vscode/mcp.json``.

    Returns ``"cleaned"``, ``"deleted"``, or ``"skipped"``.
    """
    import pathlib

    p = pathlib.Path(path)
    if not p.is_file():
        return "skipped"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, TypeError):
        return "skipped"

    servers = data.get("servers")
    if not isinstance(servers, dict):
        return "skipped"

    if _AGGREGATOR_ENTRY_NAME not in servers:
        return "skipped"

    del servers[_AGGREGATOR_ENTRY_NAME]

    if servers:
        data["servers"] = servers
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return "cleaned"
    else:
        # No servers left — remove the file.
        p.unlink()
        return "deleted"


async def remove_pushed_assets(
    store: Any,
    *,
    repo_rw_path: str,
) -> list[RemovedFile]:
    """Remove all zelosmcp-managed files from a repo.

    Deletes overwrite-mode files created by :func:`push_asset` and
    :func:`push_kind_for_all_running`, cleans zelosmcp-owned entries from
    merge-mode files (hooks, mcp.json), and removes the ``zelosmcp.json``
    prefs manifests.

    Parent directories (``.cursor/``, ``.github/``, ``.vscode/``) are
    preserved — only zelosmcp-specific files inside them are removed.
    Empty subdirectories (e.g. ``.cursor/skills/my_agent/``) are cleaned
    up after their files are removed.
    """
    import os

    results: list[RemovedFile] = []
    rw = repo_rw_path.rstrip("/")

    # ── 1. Overwrite files: delete outright ──────────────────────────

    # Rule files
    for rel in (
        ".cursor/rules/zelosmcp.mdc",
        ".github/copilot-instructions.md",
        ".vscode/copilot-instructions.md",
    ):
        abs_path = os.path.join(rw, rel)
        if _remove_file(abs_path):
            results.append(RemovedFile(path=abs_path, action="deleted"))

    # zelosmcp.json prefs manifests
    for rel in (
        ".cursor/zelosmcp.json",
        ".github/zelosmcp.json",
        ".vscode/zelosmcp.json",
    ):
        abs_path = os.path.join(rw, rel)
        if _remove_file(abs_path):
            results.append(RemovedFile(path=abs_path, action="deleted"))

    # Agent skill directories — enumerate from the store to find names,
    # then also do a filesystem sweep to catch any leftover dirs.
    agent_names: set[str] = set()
    if store is not None:
        try:
            rows = await store.list(kind="agent")
            agent_names = {r.name for r in rows}
        except Exception as exc:
            logger.debug("remove: failed to list agents: %s", exc)

    # Filesystem sweep of skills directories for any names not in the store.
    import re
    for skills_dir_rel in (
        ".cursor/skills",
        ".github/skills",
        ".vscode/skills",
    ):
        skills_abs = os.path.join(rw, skills_dir_rel)
        if os.path.isdir(skills_abs):
            for entry in os.listdir(skills_abs):
                agent_names.add(entry)

    for agent_name in sorted(agent_names):
        # Cursor target
        slug = re.sub(r"[^a-z0-9]+", "-", agent_name.lower()).strip("-")[:64] or "skill"
        for skills_rel, name_to_use in (
            (".cursor/skills", agent_name),
            (".github/skills", slug),
            (".vscode/skills", slug),
        ):
            skill_file = os.path.join(rw, skills_rel, name_to_use, "SKILL.md")
            if _remove_file(skill_file):
                results.append(RemovedFile(path=skill_file, action="deleted"))
            # Clean up the now-empty agent directory.
            _remove_dir_if_empty(os.path.join(rw, skills_rel, name_to_use))

        # Clean up the skills/ directory itself if empty.
    for skills_dir_rel in (".cursor/skills", ".github/skills", ".vscode/skills"):
        _remove_dir_if_empty(os.path.join(rw, skills_dir_rel))

    # ── 2. Merge files: strip zelosmcp entries ───────────────────────

    # Cursor hooks
    cursor_hooks = os.path.join(rw, ".cursor/hooks.json")
    action = _clean_cursor_hooks(cursor_hooks)
    if action != "skipped":
        results.append(RemovedFile(path=cursor_hooks, action=action))

    # VS Code hooks
    for rel in (".vscode/hooks.json", ".github/hooks/zelosmcp.json"):
        abs_path = os.path.join(rw, rel)
        action = _clean_vscode_hooks(abs_path)
        if action != "skipped":
            results.append(RemovedFile(path=abs_path, action=action))
    # Clean up .github/hooks/ if empty.
    _remove_dir_if_empty(os.path.join(rw, ".github/hooks"))

    # .vscode/mcp.json — remove zelosmcp-aggregate entry.
    mcp_json = os.path.join(rw, ".vscode/mcp.json")
    action = _clean_vscode_mcp_json(mcp_json)
    if action != "skipped":
        results.append(RemovedFile(path=mcp_json, action=action))

    # ── 3. Clear prefs DB row ────────────────────────────────────────

    if store is not None:
        try:
            from zelosmcp.framework.assetstore.prefs import delete_prefs
            ro_path = repo_rw_path.replace("/user_data_rw/", "/user_data_ro/", 1)
            await delete_prefs(store, ro_path)
        except Exception as exc:
            logger.debug("remove: failed to delete prefs: %s", exc)

    return results


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
            _local_write(abs_path, pf.body)
            pushed.append(PushedFile(path=abs_path, mode="overwrite", ok=True))
        except Exception as exc:
            pushed.append(PushedFile(
                path=pf.rel_path, mode="overwrite", ok=False, error=str(exc)
            ))
    return pushed
