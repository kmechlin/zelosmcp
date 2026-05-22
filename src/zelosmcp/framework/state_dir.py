"""Resolve the persistent state directory for zelosmcp's SQLite stores.

Honors the Zelos suite container contract
(``zelosai/docs/architecture/07-container-contract.md``):

* In-cluster, the operator mounts a PVC at ``/var/lib/zelos/zelosmcp/`` and
  components root all file-state under that path.
* For local development the legacy ``~/.zelosmcp/`` location is still honored
  as a fallback.

Resolution order:

1. ``$ZELOSMCP_STATE_DIR`` — explicit override.
2. ``/var/lib/zelos/zelosmcp/`` when the directory exists and is writable
   (the operator-mounted path).
3. ``~/.zelosmcp/`` — legacy / local-dev fallback. If this is used but
   ``/var/lib/zelos/zelosmcp/`` exists, a deprecation warning is emitted.

The resolver is best-effort: returns the chosen path even when the directory
can't be created; callers downstream handle ``:memory:`` substitution.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_logger = logging.getLogger(__name__)

# Standard mount path per the suite container contract.
SUITE_STATE_DIR = Path("/var/lib/zelos/zelosmcp")

# Legacy local-dev fallback.
LEGACY_STATE_DIR = Path.home() / ".zelosmcp"


def resolve_state_dir() -> Path:
    """Return the directory where SQLite DBs and the Fernet key live."""
    explicit = os.environ.get("ZELOSMCP_STATE_DIR")
    if explicit:
        return Path(explicit)
    # Prefer the suite-standard mount when it's available.
    if _writable(SUITE_STATE_DIR):
        return SUITE_STATE_DIR
    # Fall back to legacy. Warn if the suite mount also exists but is unwritable,
    # so misconfigured deployments are visible without breaking.
    if SUITE_STATE_DIR.exists():
        _logger.warning(
            "zelosmcp: state dir %s exists but is not writable; falling back to %s",
            SUITE_STATE_DIR,
            LEGACY_STATE_DIR,
        )
    return LEGACY_STATE_DIR


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        # mkdir is enough — if the volume is RO this raises.
        return os.access(p, os.W_OK)
    except OSError:
        return False
