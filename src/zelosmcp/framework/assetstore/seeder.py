"""Generic seeder driver for the asset store.

:func:`seed_all` is the single entry point called from the manager's
lifespan hook.  It walks ``configs/assets/*.yaml`` once, and for each
per-backend YAML file dispatches each top-level section key to the
matching registered :class:`~registry.AssetKind` via its
``parse_section`` function.

The old per-kind subdirectory layout (``rules/``, ``extensions/``, etc.)
is no longer used.  All asset kinds for one backend live together in a
single file (e.g. ``configs/assets/pincher.yaml``).  Adding a new kind
only requires a new registered ``AssetKind`` with the right
``section_key`` — no driver changes needed.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("zelosmcp.assets.seeder")

_DEFAULT_ASSETS_DIR_NAME = "configs/assets"


def _default_config_root() -> Path:
    """Return the ``configs/assets`` directory.

    Resolution order:
    1. ``ZELOSMCP_ASSETS_DIR`` env var (explicit override).
    2. Walk upward from this module's file to find ``configs/assets/``.
    3. Well-known container paths: ``/app/configs/assets``,
       ``/opt/zelosmcp/configs/assets``.
    4. Last-resort: ``configs/assets`` relative to the current working
       directory.
    """
    env = os.environ.get("ZELOSMCP_ASSETS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for _ in range(8):
        candidate = here / _DEFAULT_ASSETS_DIR_NAME
        if candidate.is_dir():
            return candidate
        here = here.parent
    for container_path in (
        Path("/app") / _DEFAULT_ASSETS_DIR_NAME,
        Path("/opt/zelosmcp") / _DEFAULT_ASSETS_DIR_NAME,
    ):
        if container_path.is_dir():
            return container_path
    return Path(_DEFAULT_ASSETS_DIR_NAME)


async def seed_all(
    store: Any,
    *,
    config_root: Path | None = None,
) -> dict[str, int]:
    """Seed the store from all per-backend ``*.yaml`` files in ``config_root``.

    Each file is expected to follow the unified schema::

        backend: <name>
        seed_version: <int>
        rules:
          sections: { ... }
          tool_instructions: { ... }
        extensions:
          <ext_name>: { ... }
        agents:
          <agent_name>: { ... }
        hooks:
          <hook_name>: { ... }

    Any top-level section key that matches a registered
    :class:`~registry.AssetKind`'s ``section_key`` is parsed by that
    kind's ``parse_section`` callable.  Unknown keys are silently ignored
    so that future YAML fields don't break older versions.

    Returns
    -------
    ``{kind_id: count_seeded}`` mapping for logging.
    """
    from zelosmcp.framework.assetstore import registry as _registry

    root = config_root or _default_config_root()
    counts: dict[str, int] = {kind.id: 0 for kind in _registry.known()}

    yaml_files = sorted(root.glob("*.yaml")) if root.exists() else []
    if not yaml_files:
        logger.debug("seed_all: no *.yaml files found in %s", root)
        return counts

    # Build a lookup: section_key -> AssetKind
    section_map: dict[str, Any] = {
        kind.section_key: kind for kind in _registry.known()
        if kind.section_key and kind.parse_section is not None
    }

    for yaml_file in yaml_files:
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("seed_all: failed to parse %s: %s", yaml_file, exc)
            continue

        if not isinstance(data, dict):
            logger.warning("seed_all: %s is not a YAML mapping; skipping", yaml_file)
            continue

        backend = data.get("backend")
        if not isinstance(backend, str) or not backend:
            logger.warning("seed_all: %s missing 'backend'; skipping", yaml_file)
            continue

        seed_version = data.get("seed_version")
        if not isinstance(seed_version, int):
            logger.warning(
                "seed_all: %s missing integer 'seed_version'; skipping", yaml_file
            )
            continue

        for section_key, kind in section_map.items():
            section_data = data.get(section_key)
            if section_data is None:
                continue
            if not isinstance(section_data, dict):
                logger.warning(
                    "seed_all: %s key '%s' is not a mapping; skipping",
                    yaml_file, section_key,
                )
                continue

            try:
                rows = kind.parse_section(section_data, backend, seed_version)
            except Exception as exc:
                logger.warning(
                    "seed_all: %s kind '%s' parse_section failed: %s",
                    yaml_file, kind.id, exc,
                )
                continue

            for row in rows:
                try:
                    written = await store.upsert(row, only_if_seed_lt=seed_version)
                    if written:
                        counts[kind.id] += 1
                except Exception as exc:
                    logger.warning(
                        "seed_all: upsert %s/%s/%s failed: %s",
                        row.backend, row.kind, row.name, exc,
                    )

        logger.debug(
            "seed_all: processed %s (backend=%s, seed_version=%d)",
            yaml_file.name, backend, seed_version,
        )

    total = sum(counts.values())
    logger.info("seed_all: %d total rows seeded from %s: %s", total, root, counts)
    return counts
