"""Per-project preferences store.

Stores the rule-generator settings (targets, tool_use, access, style, globs)
that the user has chosen for each repository, together with timestamps for the
last successful push of each asset kind.  Backed by the ``project_prefs`` table
in the shared SQLite database.

On-disk mirror
--------------
The same information is written to ``zelosmcp.json`` inside ``.cursor/``,
``.github/``, and ``.vscode/`` in every repo whenever a push completes.  On
first discovery of a repo with no DB row, ``read_prefs_json_from_disk`` is
called to seed the DB from whichever copy exists on disk — enabling a fresh
container to pick up settings committed to the repo.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("zelosmcp.prefs")

# Filenames checked in priority order when seeding from disk.
_PREFS_JSON_LOCATIONS = [
    ".cursor/zelosmcp.json",
    ".github/zelosmcp.json",
    ".vscode/zelosmcp.json",
]

_DEFAULTS: dict[str, Any] = {
    "targets": ["cursor", "vscode"],
    "tool_use": "priority",
    "access": "read-only",
    "style": "always-apply",
    "globs": "",
}


@dataclass
class ProjectPrefs:
    """Mutable per-project rule-generator settings."""

    path_ro: str
    name: str = ""
    targets: list[str] = field(default_factory=lambda: ["cursor", "vscode"])
    tool_use: str = "priority"
    access: str = "read-only"
    style: str = "always-apply"
    globs: str = ""
    last_pushed_rule: float | None = None
    last_pushed_agent: float | None = None
    last_pushed_hook: float | None = None
    updated_at: float = field(default_factory=time.time)

    @property
    def has_assets(self) -> bool:
        """True when at least one kind has been pushed to this repo."""
        return any(
            v is not None
            for v in (self.last_pushed_rule, self.last_pushed_agent, self.last_pushed_hook)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_ro": self.path_ro,
            "name": self.name,
            "targets": self.targets,
            "tool_use": self.tool_use,
            "access": self.access,
            "style": self.style,
            "globs": self.globs,
            "last_pushed_rule": self.last_pushed_rule,
            "last_pushed_agent": self.last_pushed_agent,
            "last_pushed_hook": self.last_pushed_hook,
            "has_assets": self.has_assets,
        }


# ── JSON serialisation ──────────────────────────────────────────────────────


def _ts_to_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_to_ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def prefs_to_json(prefs: ProjectPrefs) -> str:
    """Serialise *prefs* to the on-disk ``zelosmcp.json`` format."""
    return json.dumps({
        "version": 1,
        "targets": prefs.targets,
        "tool_use": prefs.tool_use,
        "access": prefs.access,
        "style": prefs.style,
        "globs": prefs.globs,
        "last_pushed": {
            "rule": _ts_to_iso(prefs.last_pushed_rule),
            "agent": _ts_to_iso(prefs.last_pushed_agent),
            "hook": _ts_to_iso(prefs.last_pushed_hook),
        },
    }, indent=2)


def json_to_prefs(text: str, path_ro: str) -> ProjectPrefs | None:
    """Parse a ``zelosmcp.json`` string into a :class:`ProjectPrefs`.

    Returns ``None`` on any parse error so callers can fall through to defaults.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    lp = data.get("last_pushed") or {}
    return ProjectPrefs(
        path_ro=path_ro,
        name=os.path.basename(path_ro.rstrip("/")) or path_ro,
        targets=data.get("targets") or _DEFAULTS["targets"],
        tool_use=data.get("tool_use") or _DEFAULTS["tool_use"],
        access=data.get("access") or _DEFAULTS["access"],
        style=data.get("style") or _DEFAULTS["style"],
        globs=data.get("globs") or "",
        last_pushed_rule=_iso_to_ts(lp.get("rule")),
        last_pushed_agent=_iso_to_ts(lp.get("agent")),
        last_pushed_hook=_iso_to_ts(lp.get("hook")),
    )


def read_prefs_json_from_disk(path_ro: str) -> ProjectPrefs | None:
    """Try to load ``zelosmcp.json`` from the repo's IDE directories.

    Checks ``.cursor/``, ``.github/``, and ``.vscode/`` in that order and
    returns the first successfully parsed result.  Returns ``None`` when none
    of the files exist or can be parsed.
    """
    for rel in _PREFS_JSON_LOCATIONS:
        abs_path = os.path.join(path_ro, rel)
        try:
            with open(abs_path, "r", encoding="utf-8") as fh:
                text = fh.read()
            prefs = json_to_prefs(text, path_ro)
            if prefs is not None:
                return prefs
        except (OSError, IOError):
            continue
    return None


# ── DB helpers ──────────────────────────────────────────────────────────────


def _row_to_prefs(row: tuple) -> ProjectPrefs:
    (path_ro, name, targets_json, tool_use, access, style, globs,
     lp_rule, lp_agent, lp_hook, updated_at) = row
    try:
        targets = json.loads(targets_json or '["cursor","vscode"]')
    except (ValueError, TypeError):
        targets = ["cursor", "vscode"]
    return ProjectPrefs(
        path_ro=path_ro,
        name=name or "",
        targets=targets,
        tool_use=tool_use or "priority",
        access=access or "read-only",
        style=style or "always-apply",
        globs=globs or "",
        last_pushed_rule=lp_rule,
        last_pushed_agent=lp_agent,
        last_pushed_hook=lp_hook,
        updated_at=updated_at or time.time(),
    )


async def get_prefs(store: Any, path_ro: str) -> ProjectPrefs | None:
    """Return the prefs row for *path_ro*, or ``None`` if not found."""
    async with store._lock:
        db = store._conn()
        cur = await db.execute(
            """
            SELECT path_ro, name, targets, tool_use, access, style, globs,
                   last_pushed_rule, last_pushed_agent, last_pushed_hook, updated_at
            FROM project_prefs WHERE path_ro = ?
            """,
            (path_ro,),
        )
        row = await cur.fetchone()
        await cur.close()
    return _row_to_prefs(row) if row else None


async def upsert_prefs(store: Any, prefs: ProjectPrefs) -> None:
    """Insert or update the prefs row, preserving ``last_pushed_*`` from DB."""
    ts = time.time()
    targets_json = json.dumps(prefs.targets)
    async with store._lock:
        db = store._conn()
        await db.execute(
            """
            INSERT INTO project_prefs
                (path_ro, name, targets, tool_use, access, style, globs,
                 last_pushed_rule, last_pushed_agent, last_pushed_hook, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path_ro) DO UPDATE SET
                name              = excluded.name,
                targets           = excluded.targets,
                tool_use          = excluded.tool_use,
                access            = excluded.access,
                style             = excluded.style,
                globs             = excluded.globs,
                last_pushed_rule  = COALESCE(excluded.last_pushed_rule,  project_prefs.last_pushed_rule),
                last_pushed_agent = COALESCE(excluded.last_pushed_agent, project_prefs.last_pushed_agent),
                last_pushed_hook  = COALESCE(excluded.last_pushed_hook,  project_prefs.last_pushed_hook),
                updated_at        = excluded.updated_at
            """,
            (
                prefs.path_ro, prefs.name or os.path.basename(prefs.path_ro.rstrip("/")),
                targets_json, prefs.tool_use, prefs.access, prefs.style, prefs.globs,
                prefs.last_pushed_rule, prefs.last_pushed_agent, prefs.last_pushed_hook,
                ts,
            ),
        )
        await db.commit()


async def list_prefs(store: Any) -> list[ProjectPrefs]:
    """Return all prefs rows ordered by name."""
    async with store._lock:
        db = store._conn()
        cur = await db.execute(
            """
            SELECT path_ro, name, targets, tool_use, access, style, globs,
                   last_pushed_rule, last_pushed_agent, last_pushed_hook, updated_at
            FROM project_prefs ORDER BY name
            """
        )
        rows = await cur.fetchall()
        await cur.close()
    return [_row_to_prefs(r) for r in rows]


async def update_last_pushed(store: Any, path_ro: str, kind: str) -> None:
    """Bump the ``last_pushed_<kind>`` timestamp to now."""
    col_map = {
        "rule": "last_pushed_rule",
        "agent": "last_pushed_agent",
        "hook": "last_pushed_hook",
    }
    col = col_map.get(kind)
    if col is None:
        return
    ts = time.time()
    async with store._lock:
        db = store._conn()
        await db.execute(
            f"""
            UPDATE project_prefs SET {col} = ?, updated_at = ?
            WHERE path_ro = ?
            """,
            (ts, ts, path_ro),
        )
        await db.commit()


async def delete_prefs(store: Any, path_ro: str) -> None:
    """Remove the prefs row for a repo."""
    async with store._lock:
        db = store._conn()
        await db.execute(
            "DELETE FROM project_prefs WHERE path_ro = ?",
            (path_ro,),
        )
        await db.commit()
