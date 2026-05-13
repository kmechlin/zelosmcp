# Built-in MCP at `/zelosmcp/mcp`

zelosMCP ships an always-on, in-process MCP server that exposes self-introspection and rule-generation tools. It's reachable directly at `http://localhost:8000/zelosmcp/mcp` (raw passthrough), and aggregated into `http://localhost:8000/mcp` as `zelosmcp__*`. Unlike user backends, it survives configuration reloads — `POST /api/stop` and `POST /api/start` both leave it running.

This page covers the seven tools, how the live tool catalog UI works, and how the standalone `/catalog` documentation page fits in.

## Architecture (short version)

The built-in is a synthetic [`ProxyState`](../src/zelosmcp/proxy.py) — same attributes the dispatcher and aggregator already iterate over. Internally it wires one `mcp.server.lowlevel.Server` instance to two transports simultaneously: a `StreamableHTTPSessionManager` for HTTP (`/zelosmcp/mcp`) and an in-memory `ClientSession` for the aggregator's fan-out (`/mcp`). See [architecture.md](architecture.md#how-the-built-in-mcp-plugs-in) for the diagram.

## The seven tools

| Tool | Purpose |
|---|---|
| `zelosmcp__generate_cursor_rule` | Generate a comprehensive `.mdc` (Cursor) or `copilot-instructions.md` (VSCode + Copilot) rule body listing every tool from every loaded backend. |
| `zelosmcp__list_loaded_servers` | Compact JSON view of every registered backend (name, transport, running, errors). |
| `zelosmcp__get_aggregated_tool_catalog` | Fan `tools/list` / `prompts/list` / `resources/list` / `resources/templates/list` across every running backend. Returns the same shape as `GET /api/catalog`. |
| `zelosmcp__generate_cursor_mcp_json` | Returns the same `mcp.json` snippet the web UI shows. Inputs: `shape` (`aggregate` \| `per-backend`), `host`. |
| `zelosmcp__start_server` | Wraps `ProxyManager.start_one(name)`. Refuses `name="zelosmcp"`. |
| `zelosmcp__stop_server` | Wraps `ProxyManager.stop_one(name)`. Refuses `name="zelosmcp"`. |
| `zelosmcp__reload_config` | Replaces the entire backend set; same JSON shape `POST /api/start` accepts. |
| `zelosmcp__list_compressed_tools` | Returns the compressed catalog (one short line per tool) for any backend with a `compress` block configured. Independent of `compress.scope` — even `scope=catalog` backends surface here. Optional `backend` filter and `level` override (preview a different level without changing the live config). See [compression.md](compression.md). |

All eight are reachable via `/zelosmcp/mcp` (unprefixed) or `/mcp` (prefixed `zelosmcp__`).

### `zelosmcp__generate_cursor_rule` in detail

The most-used tool. Synthesizes an agent-instructions document from the live catalog of every loaded backend.

| Param | Default | Description |
|---|---|---|
| `access` | `read-only` | `read-only` (rule body forbids agent from calling tools tagged `[mutates]`/`[destructive]`/`[?]`) or `read-write` (lists same tools but allows them with confirmation). |
| `format` | `cursor-mdc` | `cursor-mdc` (YAML frontmatter for `.cursor/rules/*.mdc`) or `copilot-instructions` (no frontmatter for `.github/copilot-instructions.md`). |
| `style` | `always-apply` | `always-apply` or `scoped`. Only meaningful for `format=cursor-mdc`. |
| `globs` | (none) | Glob pattern when `style=scoped`. Only meaningful for `format=cursor-mdc`. |
| `tool_use` | `priority` | `priority` (rule body adds a "prefer MCP tools over shell" directive plus a curated playbook for the mandatory backends `filesystem` and `pincher`, filtered by `access`) or `available` (neutral catalog with no prioritization directive or playbook section). |

The same generator also drives `GET /api/cursor-rule` — both surfaces are guaranteed-equivalent (covered by `test_aggregate_call_tool_round_trip` in [tests/test_app_integration.py](../tests/test_app_integration.py)).

> **Asset-store sourced playbooks.** The per-backend playbook blocks (the mandatory `### \`pincher\`` and `### \`filesystem\`` sections in `priority` mode) are no longer hardcoded in `builtin.py`. They are loaded from the asset store via `load_all_rule_assets()` at render time. This means any edits you make to a backend's `playbook_read_only` / `playbook_read_write` rule assets in the web UI's Assets pane are immediately reflected the next time this tool is called — no restart needed. See [docs/asset-kinds.md#rule](asset-kinds.md#rule-rules) for the rule asset schema and [docs/assets-editor.md](assets-editor.md) for the editor walkthrough.

Mutability classification per tool:

| Marker | Trigger |
|---|---|
| `[readonly]` | `annotations.readOnlyHint === true` |
| `[destructive]` | `annotations.destructiveHint === true` (overrides others) |
| `[mutates]` | name starts with `create_`, `update_`, `set_`, `delete_`, `remove_`, `start_`, `stop_`, `restart_`, `run_`, `push_`, `pull_`, `build_`, `write_`, `edit_`, `move_`, `configure_`, or `reload_` |
| `[?]` | none of the above (unannotated, ambiguous name) |

See [cursor-integration.md](cursor-integration.md) and [vscode-integration.md](vscode-integration.md) for the IDE-side workflow.

## Live tool catalog (web UI)

Each row in the **Servers** panel of the zelosMCP web UI is click-to-expand. Clicking a row reveals that backend's tools, prompts, resources, and resource templates inline — pretty-printed input schemas tucked inside a nested `<details>` so the listing stays scannable.

The data comes from `GET /api/catalog`, which the UI fetches whenever the running-backends set changes. The catalog block per row is populated lazily — clicking expand on a row that hasn't been fetched yet shows "Loading..." for a moment.

### `/catalog` — standalone documentation page

The Servers card header has a **Full catalog** link to [`/catalog`](http://localhost:8000/catalog). That's a self-contained HTML page covering every running backend at once, with a top-of-page filter input that narrows by tool/prompt/resource name across all backends.

Use cases:

- "Search for any tool with 'image' in the name across all backends." — type `image` into the filter.
- "Print-friendly catalog for an architecture review." — the `/catalog` page has print CSS.
- "Bookmarkable URL for the team." — `http://localhost:8000/catalog` is the canonical link.

### `/api/catalog` — JSON for programmatic consumers

```bash
curl -sS http://localhost:8000/api/catalog | jq '."filesystem".tools[].name'
```

Returns:

```json
{
  "filesystem": {
    "transport": "stdio",
    "running": true,
    "tools": [
      {
        "name": "read_text_file",
        "description": "Read complete contents of a file as text",
        "inputSchema": { "type": "object", ... },
        "annotations": { "readOnlyHint": true }
      },
      ...
    ],
    "prompts": [],
    "resources": [],
    "resourceTemplates": []
  },
  "pincher": { ... },
  "zelosmcp": { ... }
}
```

Capabilities a backend doesn't implement (e.g. `@modelcontextprotocol/server-filesystem` doesn't expose prompts) come back as empty arrays, not errors. Real backend errors come back as `{ "<capability>": { "error": "<message>" } }` so a partial outage doesn't blank the whole row.

This is the same payload `zelosmcp__get_aggregated_tool_catalog` returns — both surfaces share one helper ([`collect_backend_full_catalog`](../src/zelosmcp/builtin.py)) so they're guaranteed-equivalent.

## Why the built-in survives `POST /api/stop`

User backends are explicitly stoppable; the built-in is not. This is so:

1. The rule generator + catalog are always available to the IDE agent, even between config reloads.
2. The aggregator at `/mcp` doesn't 503 just because all user backends are stopped — it still serves `zelosmcp__*` tools.
3. `zelosmcp__reload_config` is callable to fix a broken config without losing the introspection toolkit you need to debug it.

`POST /api/start` reloads user backends but the built-in keeps running. `make clean` (which removes the entire zelosMCP container) is the only way to actually take it down.

## See also

- [architecture.md](architecture.md) — how the built-in plugs into the dispatcher and aggregator.
- [http-api.md](http-api.md) — full reference for `/api/catalog`, `/api/cursor-rule`, and `/catalog`.
- [cursor-integration.md](cursor-integration.md) / [vscode-integration.md](vscode-integration.md) — IDE-side workflows that consume the rule generator.
- [assets.md](assets.md) — the assets framework whose store backs the rule generator's per-backend playbooks.
- [asset-kinds.md#rule](asset-kinds.md#rule-rules) — the `rule` kind schema that defines what the store contains.
