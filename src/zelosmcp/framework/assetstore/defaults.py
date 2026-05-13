"""Dynamic default asset generation for backends without a YAML file.

When a backend is loaded and has no rows in the asset store,
:func:`ensure_default_assets` introspects the backend's live tool catalog
and auto-generates rule rows that list every tool with its mutability
marker (``[readonly]`` / ``[mutates]`` / ``[destructive]`` / ``[?]``).

This gives the agent useful, accurate guidance for backends like
``kubernetes`` and ``docker`` that ship no curated content, rather than
an empty or generic placeholder.

Only the ``rule`` kind is auto-generated; extensions/agents/hooks tabs
remain empty and the user adds them via the "Add" buttons in the GUI.
"""
from __future__ import annotations

import logging
from typing import Any

from zelosmcp.framework.assetstore.row import AssetRow
from zelosmcp.framework.assetstore.tool_classify import classify_tool, format_args

logger = logging.getLogger("zelosmcp.assets.defaults")

KIND_RULE = "rule"


def _mutating_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in tools if classify_tool(t) in ("mutates", "destructive", "?")]


def _playbook_compressed_ro(backend: str, tools: list[dict[str, Any]]) -> str:
    """Playbook for a compressed backend in read-only mode.

    Emits the same mutability table as ``_playbook_ro`` but frames every
    tool call as ``<backend>__invoke_tool(tool_name="<name>", ...)``.
    """
    n = len(tools)
    invoke_q = f"{backend}__invoke_tool"
    lines: list[str] = [
        f"### `{backend}` (auto-generated) — compressed",
        "",
        f"This backend is wire-compressed. Its {n} tool{'s' if n != 1 else ''} "
        f"are reachable only through the compressed wrapper trio. "
        f"Do NOT call underlying tool names directly via `{backend}__<tool>` "
        f"— use `{invoke_q}(tool_name=\"<tool>\", tool_input={{...}})` instead.",
        "",
        "| Tool | Args | Mutability |",
        "|---|---|---|",
    ]
    for t in tools:
        name = t.get("name") or "(unnamed)"
        args = format_args(t.get("inputSchema"))
        marker = classify_tool(t)
        lines.append(f"| `{name}` | `{args}` | [{marker}] |")

    no_invoke = [
        f"`{t.get('name')}`"
        for t in _mutating_tools(tools)
    ]
    lines.append("")
    lines.append(
        "In **read-only** mode, do **not** invoke any underlying tool tagged "
        "`[mutates]`, `[destructive]`, or `[?]` via the wrapper."
    )
    if no_invoke:
        lines.append(
            "Specifically, avoid invoking: " + ", ".join(no_invoke[:10])
            + ("…" if len(no_invoke) > 10 else "") + "."
        )
    lines.append("")
    lines.append(
        f"Edit this rule in the Assets pane (`Assets` button on the "
        f"`{backend}` server row) to add per-tool guidance, intent → "
        f"tool mappings, or forbidden fallbacks."
    )
    return "\n".join(lines) + "\n"


def _playbook_compressed_rw(backend: str, tools: list[dict[str, Any]]) -> str:
    """Playbook for a compressed backend in read-write mode."""
    n = len(tools)
    invoke_q = f"{backend}__invoke_tool"
    lines: list[str] = [
        f"### `{backend}` (auto-generated) — compressed",
        "",
        f"This backend is wire-compressed. Its {n} tool{'s' if n != 1 else ''} "
        f"are reachable only through the compressed wrapper trio. "
        f"Do NOT call underlying tool names directly via `{backend}__<tool>` "
        f"— use `{invoke_q}(tool_name=\"<tool>\", tool_input={{...}})` instead.",
        "",
        "| Tool | Args | Mutability |",
        "|---|---|---|",
    ]
    for t in tools:
        name = t.get("name") or "(unnamed)"
        args = format_args(t.get("inputSchema"))
        marker = classify_tool(t)
        lines.append(f"| `{name}` | `{args}` | [{marker}] |")

    destructive = [
        f"`{t.get('name')}`"
        for t in tools
        if classify_tool(t) == "destructive"
    ]
    lines.append("")
    lines.append(
        "In **read-write** mode, confirm with the user before invoking "
        "any underlying `[destructive]` tool via the wrapper (irreversible)."
    )
    if destructive:
        lines.append(
            "Destructive tools: " + ", ".join(destructive[:10])
            + ("…" if len(destructive) > 10 else "") + "."
        )
    lines.append("")
    lines.append(
        f"Edit this rule in the Assets pane (`Assets` button on the "
        f"`{backend}` server row) to add per-tool guidance, intent → "
        f"tool mappings, or forbidden fallbacks."
    )
    return "\n".join(lines) + "\n"


def _playbook_ro(backend: str, tools: list[dict[str, Any]]) -> str:
    n = len(tools)
    lines: list[str] = [
        f"### `{backend}` (auto-generated)",
        "",
        f"This backend advertises {n} tool{'s' if n != 1 else ''}. "
        f"Default mutability classification:",
        "",
        "| Tool | Args | Mutability |",
        "|---|---|---|",
    ]
    for t in tools:
        name = t.get("name") or "(unnamed)"
        qualified = f"{backend}__{name}"
        args = format_args(t.get("inputSchema"))
        marker = classify_tool(t)
        lines.append(f"| `{qualified}` | `{args}` | [{marker}] |")

    no_call = [
        f"`{backend}__{t.get('name')}`"
        for t in _mutating_tools(tools)
    ]
    lines.append("")
    lines.append(
        "In **read-only** mode, do **not** call any tool tagged "
        "`[mutates]`, `[destructive]`, or `[?]`."
    )
    if no_call:
        lines.append(
            "Specifically, avoid: " + ", ".join(no_call[:10])
            + ("…" if len(no_call) > 10 else "") + "."
        )
    lines.append("")
    lines.append(
        f"Edit this rule in the Assets pane (`Assets` button on the "
        f"`{backend}` server row) to add per-tool guidance, intent → "
        f"tool mappings, or forbidden fallbacks."
    )
    return "\n".join(lines) + "\n"


def _playbook_rw(backend: str, tools: list[dict[str, Any]]) -> str:
    n = len(tools)
    lines: list[str] = [
        f"### `{backend}` (auto-generated)",
        "",
        f"This backend advertises {n} tool{'s' if n != 1 else ''}. "
        f"Default mutability classification:",
        "",
        "| Tool | Args | Mutability |",
        "|---|---|---|",
    ]
    for t in tools:
        name = t.get("name") or "(unnamed)"
        qualified = f"{backend}__{name}"
        args = format_args(t.get("inputSchema"))
        marker = classify_tool(t)
        lines.append(f"| `{qualified}` | `{args}` | [{marker}] |")

    destructive = [
        f"`{backend}__{t.get('name')}`"
        for t in tools
        if classify_tool(t) == "destructive"
    ]
    lines.append("")
    lines.append(
        "In **read-write** mode, confirm with the user before calling "
        "any `[destructive]` tool (irreversible)."
    )
    if destructive:
        lines.append(
            "Destructive tools: " + ", ".join(destructive[:10])
            + ("…" if len(destructive) > 10 else "") + "."
        )
    lines.append("")
    lines.append(
        f"Edit this rule in the Assets pane (`Assets` button on the "
        f"`{backend}` server row) to add per-tool guidance, intent → "
        f"tool mappings, or forbidden fallbacks."
    )
    return "\n".join(lines) + "\n"


def generate_default_rule_rows(
    backend: str,
    tools: list[dict[str, Any]],
    *,
    seed_version: int = 0,
) -> list[AssetRow]:
    """Build dynamic default rule rows for a backend from its live tool catalog.

    Returns:
    - ``playbook_read_only``  — intro + mutability table, no-call list.
    - ``playbook_read_write`` — intro + mutability table, confirm-destructive list.
    - ``tool:<name>``         — one row per tool with its description + arg signature.

    Parameters
    ----------
    backend:
        The MCP backend name.
    tools:
        List of tool dicts as returned by ``collect_backend_full_catalog``.
    seed_version:
        The ``seed_version`` to stamp on the generated rows; defaults to 0
        so they can always be superseded by a user YAML file (seed_version≥1)
        or user edit (``source='user'``).
    """
    rows: list[AssetRow] = [
        AssetRow(
            kind=KIND_RULE,
            backend=backend,
            name="playbook_read_only",
            body=_playbook_ro(backend, tools),
            source="seed",
            seed_version=seed_version,
        ),
        AssetRow(
            kind=KIND_RULE,
            backend=backend,
            name="playbook_read_write",
            body=_playbook_rw(backend, tools),
            source="seed",
            seed_version=seed_version,
        ),
        AssetRow(
            kind=KIND_RULE,
            backend=backend,
            name="playbook_compressed_read_only",
            body=_playbook_compressed_ro(backend, tools),
            source="seed",
            seed_version=seed_version,
        ),
        AssetRow(
            kind=KIND_RULE,
            backend=backend,
            name="playbook_compressed_read_write",
            body=_playbook_compressed_rw(backend, tools),
            source="seed",
            seed_version=seed_version,
        ),
    ]

    for t in tools:
        name = t.get("name") or "(unnamed)"
        desc = (t.get("description") or "").strip().replace("\n", " ")
        args = format_args(t.get("inputSchema"))
        marker = classify_tool(t)
        body = f"`{backend}__{name}` `{args}` [{marker}]\n"
        if desc:
            body += f"  {desc}\n"
        rows.append(AssetRow(
            kind=KIND_RULE,
            backend=backend,
            name=f"tool:{name}",
            body=body,
            meta={"tool": name},
            source="seed",
            seed_version=seed_version,
        ))

    return rows


async def _live_backend_tools(manager: Any, backend: str) -> list[dict[str, Any]]:
    """Resolve the live tool list for *backend* from the aggregator catalog.

    Returns an empty list when the catalog is unavailable or the backend
    advertises an error payload (e.g. passthrough backend whose auth
    provider isn't connected — :func:`collect_backend_full_catalog`
    surfaces those as ``{"tools": {"error": "..."}}`` which we treat as
    "no tools known right now").
    """
    try:
        from zelosmcp.builtin import collect_backend_full_catalog
        catalog = await collect_backend_full_catalog(manager, skip_self=False)
    except Exception as exc:
        logger.warning(
            "default_assets: could not fetch catalog for '%s': %s",
            backend, exc,
        )
        return []

    backend_entry = catalog.get(backend) or {}
    tools = backend_entry.get("tools") or []
    if not isinstance(tools, list):
        return []
    return tools


async def ensure_default_assets(
    store: Any,
    manager: Any,
    backend: str,
) -> int:
    """Generate dynamic default rule rows for *backend* if none exist.

    Fetches the backend's live tool catalog via
    :func:`~zelosmcp.builtin.collect_backend_full_catalog` and inserts
    auto-generated playbook rows.  Idempotent — does nothing if the
    backend already has any rows in the asset store.

    Parameters
    ----------
    store:
        An open :class:`~sqlite.SQLiteAssetStore`.
    manager:
        The running :class:`~zelosmcp.manager.ProxyManager` instance.
    backend:
        The MCP backend name to check / populate.

    Returns
    -------
    Count of rows written.  ``0`` when the backend already had rows.
    """
    if store is None:
        return 0

    existing = await store.list(backend=backend)
    if existing:
        return 0

    tools = await _live_backend_tools(manager, backend)

    rows = generate_default_rule_rows(backend, tools, seed_version=0)
    n = 0
    for row in rows:
        try:
            if await store.upsert(row, only_if_seed_lt=1):
                n += 1
        except Exception as exc:
            logger.warning(
                "ensure_default_assets: upsert failed for %s/%s: %s",
                backend, row.name, exc,
            )
    logger.debug(
        "ensure_default_assets: %d rows written for backend '%s'", n, backend
    )
    return n


async def regenerate_default_assets(
    store: Any,
    manager: Any,
    backend: str,
) -> int:
    """Force-regenerate auto-generated default rule rows for *backend*.

    Unlike :func:`ensure_default_assets`, this skips the "no existing
    rows" guard.  It re-fetches the live catalog and re-writes the
    auto-default playbook + per-tool rows.  User edits and explicitly-
    versioned seed rows are preserved by the underlying
    ``upsert(only_if_seed_lt=1)`` logic:

    - ``source='user'`` rows are never touched.
    - ``source='seed'`` rows with ``seed_version >= 1`` (from a YAML
      file) are never touched.
    - ``source='seed'`` rows with ``seed_version=0`` (auto-defaults
      from a previous run) are overwritten with fresh content.

    Also removes auto-generated ``tool:<name>`` rows for tools that no
    longer appear in the live catalog, so a backend whose tool list
    shrinks (auth provider revoked, upstream catalog change) doesn't
    accumulate dead rows.

    Called from the auth-provider HTTP routes when a provider's
    per-user state transitions (callback completes, device flow
    finishes, revoke) — the live tool list typically goes from 0 → N
    or N → 0 at those moments, and the stored playbook must reflect
    the new reality.

    Returns the count of rows written.  ``0`` when the store is
    unavailable, the catalog fetch failed, or every row was skipped
    because of user edits / higher-version seeds.
    """
    if store is None:
        return 0

    tools = await _live_backend_tools(manager, backend)

    fresh_rows = generate_default_rule_rows(backend, tools, seed_version=0)
    fresh_names: set[str] = {r.name for r in fresh_rows}

    n_written = 0
    for row in fresh_rows:
        try:
            if await store.upsert(row, only_if_seed_lt=1):
                n_written += 1
        except Exception as exc:
            logger.warning(
                "regenerate_default_assets: upsert failed for %s/%s: %s",
                backend, row.name, exc,
            )

    # Prune stale auto-generated tool:* rows. Only delete rows that are
    # themselves auto-defaults; leave user edits and YAML-seeded rows
    # alone so a user who curated a tool rule keeps it even if the
    # upstream catalog later drops that tool.
    try:
        existing = await store.list(kind=KIND_RULE, backend=backend)
    except Exception as exc:
        logger.warning(
            "regenerate_default_assets: list failed for '%s': %s",
            backend, exc,
        )
        existing = []
    for row in existing:
        if not row.name.startswith("tool:"):
            continue
        if row.name in fresh_names:
            continue
        if row.source != "seed" or (row.seed_version or 0) != 0:
            continue
        try:
            await store.delete(KIND_RULE, backend, row.name, row.target or "")
        except Exception as exc:
            logger.warning(
                "regenerate_default_assets: delete failed for %s/%s: %s",
                backend, row.name, exc,
            )

    logger.debug(
        "regenerate_default_assets: %d rows written for backend '%s'",
        n_written, backend,
    )
    return n_written
