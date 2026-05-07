"""Discover git repositories under the read-only mount.

The container bind-mounts the same host tree at two locations:

  - ``/user_data_ro`` (read-only) — used by pincher for indexing and by this
    module for the discovery walk. The kernel-enforced read-only flag means
    accidental writes are rejected at the mount layer.
  - ``/user_data_rw`` (read-write) — used by the filesystem MCP for writes.
    Same files, different mount; we just swap the prefix when computing
    where to write a generated rule.

The scanner is a shallow ``os.walk`` (default depth 4) that prunes a
hand-curated skip list (``node_modules``, ``.venv``, ...) and stops descending
into discovered repos so nested submodules don't double-count.

The discovery root and depth are env-configurable so tests can point the
scanner at ``tmp_path`` without mounting anything:

  - ``LOCALMCP_REPO_SCAN_ROOT``  (default ``/user_data_ro``)
  - ``LOCALMCP_REPO_SCAN_DEPTH`` (default ``4``)
  - ``LOCALMCP_REPO_RW_ROOT``    (default ``/user_data_rw``)

Results are cached for ``_CACHE_TTL_SECS`` so the right-column UI panel
stays snappy on repeated opens; the ``refresh=True`` flag busts the cache
(the ``Refresh`` button in the UI sends ``?refresh=1``).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger("localmcp.repos")


_DEFAULT_RO_ROOT = "/user_data_ro"
_DEFAULT_RW_ROOT = "/user_data_rw"
_DEFAULT_SCAN_DEPTH = 4
_CACHE_TTL_SECS = 30.0

# Directory basenames we never descend into. These are always either build
# artefacts (``dist``, ``build``, ``target``), language-specific virtualenvs
# (``.venv``, ``__pycache__``), or cache directories (``.gradle``, ``.cache``)
# whose contents would balloon the walk and never contain a parent repo we
# care about. ``.git`` is in the list so we don't walk into a repo's own
# metadata (``HEAD``, ``objects/``, etc).
_SKIP_DIR_NAMES: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".gradle",
    "target",
    ".cache",
})

# Where we'd write the generated rule, relative to the repo root. Mirrors
# the two ``format`` options of the cursor-rule generator.
RULE_RELATIVE_PATHS: dict[str, str] = {
    "cursor-mdc": ".cursor/rules/localmcp.mdc",
    "copilot-instructions": ".github/copilot-instructions.md",
}


@dataclass(frozen=True)
class DiscoveredRepo:
    """One repository discovered under the scan root.

    ``path_ro`` and ``path_rw`` always reference the same on-host directory
    via the two container mounts; callers pick whichever they need (read =
    ro, write = rw). ``has_rule`` is a cheap stat against the read-only
    path because the host tree is identical.
    """
    name: str
    path_ro: str
    path_rw: str
    has_rule: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path_ro": self.path_ro,
            "path_rw": self.path_rw,
            "has_rule": self.has_rule,
        }


def _scan_root() -> str:
    return os.environ.get("LOCALMCP_REPO_SCAN_ROOT", _DEFAULT_RO_ROOT)


def _rw_root() -> str:
    return os.environ.get("LOCALMCP_REPO_RW_ROOT", _DEFAULT_RW_ROOT)


def _scan_depth() -> int:
    raw = os.environ.get("LOCALMCP_REPO_SCAN_DEPTH")
    if not raw:
        return _DEFAULT_SCAN_DEPTH
    try:
        depth = int(raw)
    except ValueError:
        logger.warning(
            "LOCALMCP_REPO_SCAN_DEPTH=%r is not an integer; using default %d",
            raw,
            _DEFAULT_SCAN_DEPTH,
        )
        return _DEFAULT_SCAN_DEPTH
    return max(1, depth)


def _is_repo(entry_path: str) -> bool:
    """A directory is a git repo if it contains a ``.git`` entry. The entry
    can be either a directory (regular clone) or a regular file (a worktree
    gitdir pointer like ``gitdir: /path/to/parent/.git/worktrees/foo``)."""
    git = os.path.join(entry_path, ".git")
    return os.path.isdir(git) or os.path.isfile(git)


def _has_rule(repo_root_ro: str) -> bool:
    """Quick stat to mark whether a localmcp.mdc already lives in the repo.
    We only check the cursor-mdc location; the copilot variant is rarer and
    the UI surfaces a Save action that overwrites either."""
    return os.path.isfile(
        os.path.join(repo_root_ro, RULE_RELATIVE_PATHS["cursor-mdc"])
    )


def _swap_prefix(path_ro: str, ro_root: str, rw_root: str) -> str:
    """Map a path under ``ro_root`` to its sibling under ``rw_root``. Both
    mounts target the same host directory so a string swap is enough."""
    if path_ro == ro_root:
        return rw_root
    if path_ro.startswith(ro_root + os.sep):
        return rw_root + path_ro[len(ro_root):]
    return path_ro


def _walk(
    root: str, max_depth: int, skip: frozenset[str] = _SKIP_DIR_NAMES
) -> Iterable[str]:
    """Yield absolute directory paths that are git repos under ``root``,
    pruning ``skip`` directories and stopping descent once a repo is found
    so nested submodules don't double-count.

    Special case: if the scan root itself is a git repo (very common when
    ``$HOME`` is version-controlled for dotfiles), we deliberately do NOT
    yield it — that single match would shadow every actual project repo
    nested below. Instead we descend into the root's children as if it
    weren't a repo. Nested repos at depth >= 1 are still yielded normally.
    """
    if not os.path.isdir(root):
        logger.info("scan root %s does not exist; returning no repos", root)
        return
    root = os.path.abspath(root)
    root_depth = root.count(os.sep)
    for dirpath, dirnames, _filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        depth = dirpath.count(os.sep) - root_depth
        if depth > max_depth:
            dirnames[:] = []
            continue
        if depth > 0 and _is_repo(dirpath):
            yield dirpath
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in skip]


@dataclass
class _Cache:
    repos: list[DiscoveredRepo] = field(default_factory=list)
    expires_at: float = 0.0


_CACHE = _Cache()


def discover_repos(*, refresh: bool = False) -> list[DiscoveredRepo]:
    """Return every git repo under the scan root. Cached for 30 s; pass
    ``refresh=True`` (the UI's ``Refresh`` button) to bust the cache."""
    now = time.time()
    if not refresh and _CACHE.expires_at > now:
        return list(_CACHE.repos)

    ro_root = _scan_root()
    rw_root = _rw_root()
    depth = _scan_depth()

    out: list[DiscoveredRepo] = []
    for repo_path in _walk(ro_root, depth):
        out.append(
            DiscoveredRepo(
                name=os.path.basename(repo_path) or repo_path,
                path_ro=repo_path,
                path_rw=_swap_prefix(repo_path, ro_root, rw_root),
                has_rule=_has_rule(repo_path),
            )
        )
    out.sort(key=lambda r: (r.name.lower(), r.path_ro))
    _CACHE.repos = out
    _CACHE.expires_at = now + _CACHE_TTL_SECS
    return list(out)


def is_under_scan_root(path: str) -> bool:
    """Path-safety check used by the write-rule and index POST handlers.

    The handlers accept a `path_ro` argument from the UI and forward it to
    pincher (read) or — after prefix swap — to the filesystem MCP (write).
    Both mounts trust this gate to refuse arbitrary host paths.
    """
    if not isinstance(path, str) or not path:
        return False
    abs_path = os.path.abspath(path)
    root = os.path.abspath(_scan_root())
    return abs_path == root or abs_path.startswith(root + os.sep)


def to_rw_path(path_ro: str) -> str:
    """Translate a read-only path inside the scan root to its read-write
    sibling. Callers MUST have already passed the input through
    :func:`is_under_scan_root`."""
    return _swap_prefix(path_ro, _scan_root(), _rw_root())


def rule_target(path_ro: str, fmt: str) -> str:
    """Return the absolute write target (under the rw mount) for a generated
    rule, given the source repo path under ro and the rule format."""
    rel = RULE_RELATIVE_PATHS.get(fmt)
    if rel is None:
        raise ValueError(f"Unknown rule format: {fmt!r}")
    return os.path.join(to_rw_path(path_ro), rel)
