"""HTML page templates served by the Starlette app.

The actual markup, stylesheet, and client-side JS live as plain files
under ``src/zelosmcp/static/`` so they can be edited with normal tooling
and (eventually) served via a ``StaticFiles`` mount with browser caching.
This module just loads the three files for each page and inlines them
back into a single HTML string at import time, preserving the original
``HTML_TEMPLATE`` / ``CATALOG_HTML_TEMPLATE`` public surface that
``app.py`` and the smoke tests import.
"""

from __future__ import annotations

from importlib.resources import files

_CSS_PLACEHOLDER = "__CSS_GOES_HERE__"
_JS_PLACEHOLDER = "__JS_GOES_HERE__"


def _assemble(page: str) -> str:
    """Load ``<page>.html`` and substitute the inline CSS / JS placeholders.

    Uses ``str.replace`` rather than ``str.format`` because CSS and JS bodies
    are full of unescaped ``{`` / ``}`` characters.
    """
    pkg = files("zelosmcp.static")
    shell = pkg.joinpath(f"{page}.html").read_text(encoding="utf-8")
    # The CSS / JS files end with a trailing newline (POSIX). The placeholder
    # sits on its own line in the shell, so it already contributes a newline
    # before the closing tag — drop the file's trailing newline to avoid
    # inserting a blank line.
    css = pkg.joinpath(f"{page}.css").read_text(encoding="utf-8").removesuffix("\n")
    js = pkg.joinpath(f"{page}.js").read_text(encoding="utf-8").removesuffix("\n")
    return shell.replace(_CSS_PLACEHOLDER, css).replace(_JS_PLACEHOLDER, js)


HTML_TEMPLATE = _assemble("index")
CATALOG_HTML_TEMPLATE = _assemble("catalog")

__all__ = ["HTML_TEMPLATE", "CATALOG_HTML_TEMPLATE"]
