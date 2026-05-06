"""Documentation discovery + markdown rendering for the in-app Docs view.

Files served are read-only and shipped in the repo / image:

- ``<repo>/docs/*.md`` (or ``/app/docs/*.md`` / ``/opt/localmcp/docs/*.md``
  in Docker, depending on which Dockerfile built the image).

Slugs are derived from filenames (lowercase, ``.md`` stripped). Reads
are whitelist-bound to that derived list so user input is never
concatenated with a path — no traversal surface.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("localmcp.docs")


# ── Filesystem discovery ────────────────────────────────────────────────


def _docs_root() -> Path | None:
    """First existing candidate dir holding the project's docs.

    Tried in order:

    - editable install: ``parents[2]`` of this file is the repo root when
      installed via ``pip install -e .`` (e.g. ``/opt/localmcp/docs`` in
      the corp-cert image, ``<repo>/docs`` on a host checkout).
    - upstream Dockerfile image: ``/app/docs``.
    - cwd fallback: ``<cwd>/docs``.
    """
    candidates = (
        Path(__file__).resolve().parents[2] / "docs",
        Path("/app/docs"),
        Path("/opt/localmcp/docs"),
        Path.cwd() / "docs",
    )
    for c in candidates:
        if c.is_dir():
            return c
    return None


# ── Title extraction ────────────────────────────────────────────────────

# Match the first ATX H1 (``# Title``) anywhere in the first ~40 lines.
_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def _extract_title(md: str, fallback: str) -> str:
    head = "\n".join(md.splitlines()[:40])
    m = _H1_RE.search(head)
    if m:
        return m.group(1).strip()
    return fallback


# ── Index / listing ─────────────────────────────────────────────────────


def _slug_for(path: Path) -> str:
    return path.stem.lower().replace(" ", "-")


def list_docs() -> list[dict]:
    """Return the canonical doc index used to whitelist reads.

    Each entry is ``{"slug": str, "title": str}``. Only ``docs/*.md``
    files are surfaced — the top-level README intentionally isn't part
    of the in-app docs view. Sorted alphabetically by slug.
    """
    root = _docs_root()
    if root is None:
        return []
    entries: list[dict] = []
    for p in sorted(root.glob("*.md")):
        slug = _slug_for(p)
        try:
            md = p.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("failed to read %s: %s", p, exc)
            continue
        entries.append({"slug": slug, "title": _extract_title(md, p.stem)})
    entries.sort(key=lambda d: d["slug"])
    return entries


def _path_for(slug: str) -> Path | None:
    """Resolve ``slug`` to its on-disk path. Whitelist-bound."""
    root = _docs_root()
    if root is None:
        return None
    candidate = root / f"{slug}.md"
    # Belt-and-braces: confirm the resolved path is still under root and
    # that the slug appears in the public index. ``list_docs`` is the
    # source of truth for what's reachable.
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    if not any(d["slug"] == slug for d in list_docs()):
        return None
    return candidate


def read_doc(slug: str) -> dict | None:
    """Return ``{slug, title, markdown, html}`` or ``None`` if unknown."""
    path = _path_for(slug)
    if path is None:
        return None
    try:
        md = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("failed to read %s: %s", path, exc)
        return None
    return {
        "slug": slug,
        "title": _extract_title(md, path.stem),
        "markdown": md,
        "html": render_html(md),
    }


# ── Rendering ───────────────────────────────────────────────────────────

# A small explicit blocklist; the markdown package strips most HTML by
# default but agents have surfaced markdown that smuggles ``<script>``
# in raw blocks before. We render with ``safe_mode`` off (so we keep
# fenced code/tables/etc) and brute-force the dangerous tags after.
_SCRIPT_RE = re.compile(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL)
_ON_ATTR_RE = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)


@lru_cache(maxsize=1)
def _markdown_instance():
    """Lazy-init a single ``markdown.Markdown`` we can reuse per request."""
    import markdown  # local import; optional at install-time validation

    return markdown.Markdown(
        extensions=["fenced_code", "tables", "toc", "sane_lists"],
        output_format="html",
    )


def render_html(md: str) -> str:
    md_inst = _markdown_instance()
    md_inst.reset()  # ``toc`` extension keeps state across calls
    html = md_inst.convert(md)
    html = _SCRIPT_RE.sub("", html)
    html = _ON_ATTR_RE.sub("", html)
    return html
