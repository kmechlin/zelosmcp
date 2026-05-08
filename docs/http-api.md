# HTTP API reference

zelosMCP exposes a small REST control plane plus the MCP routes themselves. The interactive Swagger UI lives at [`/docs`](http://localhost:8000/docs); ReDoc at [`/redoc`](http://localhost:8000/redoc); the underlying spec at [`/openapi.json`](http://localhost:8000/openapi.json). This page is the human-readable summary.

## Endpoint table

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Web UI |
| GET | `/docs`, `/redoc`, `/openapi.json` | API explorer + spec |
| GET | `/catalog` | Standalone, searchable HTML documentation page covering every running backend |
| GET | `/api/status` | Aggregate status of every configured server |
| POST | `/api/start` | Start (or replace) the full server set from a config payload |
| POST | `/api/stop` | Stop every running server |
| GET | `/api/servers/{name}` | Status of one server |
| POST | `/api/servers/{name}/start` | Start a single configured server |
| POST | `/api/servers/{name}/stop` | Stop a single server |
| GET | `/api/logs` | SSE stream of activity logs (each line tagged `[<name>]`) |
| GET | `/api/catalog` | JSON snapshot of every backend's tools / prompts / resources / templates with full payloads (`inputSchema`, etc.). Same shape as `zelosmcp__get_aggregated_tool_catalog`. |
| GET | `/api/cursor-rule` | Comprehensive agent-instructions body. Query params: `access`, `format`, `style`, `globs`. Returns `text/markdown`. |
| GET | `/api/repos` | List git repos discovered under `/user_data_ro`. Query param `refresh=1` busts the 30 s cache. See [docs/repositories.md](repositories.md). |
| POST | `/api/repos/write-rule` | Generate a Cursor / Copilot rule and write it into a discovered repo via the `filesystem` MCP. |
| POST | `/api/repos/index` | Forward a repo path to `pincher__index` so its symbols are queryable. |
| ANY | `/<name>/mcp` | Streamable-HTTP MCP endpoint for a named backend (raw passthrough) |
| ANY | `/zelosmcp/mcp` | Always-on built-in MCP (raw passthrough) — self-introspection + rule-generation tools |
| ANY | `/mcp` | **Recommended.** Aggregated Streamable-HTTP MCP — union of every running backend (incl. the built-in), namespaced as `<server>__<tool>` |

## `/api/cursor-rule` query params in detail

The rule generator endpoint deserves its own table because of the parameter combinations.

| Param | Default | Values | Effect |
|---|---|---|---|
| `access` | `read-only` | `read-only` \| `read-write` | `read-only`: rule body forbids the agent from calling tools tagged `[mutates]` / `[destructive]` / `[?]`. `read-write`: same body but allows mutating tools (still flags `[destructive]` for confirmation). |
| `format` | `cursor-mdc` | `cursor-mdc` \| `copilot-instructions` | `cursor-mdc`: YAML frontmatter wrapper for `.cursor/rules/*.mdc`. `copilot-instructions`: plain markdown body for `.github/copilot-instructions.md`. |
| `style` | `always-apply` | `always-apply` \| `scoped` | `always-apply`: `alwaysApply: true` in frontmatter. `scoped`: `alwaysApply: false` plus `globs:` line. **Only meaningful when `format=cursor-mdc`**; ignored otherwise. |
| `globs` | (none) | any string | Glob pattern for `style=scoped` (e.g. `**/*.py`). Defaults to `**/*` if `style=scoped` is set without `globs`. Ignored when `format=copilot-instructions`. |
| `tool_use` | `priority` | `priority` \| `available` | `priority`: rule body adds a "prefer MCP tools over shell" directive plus a curated playbook for the mandatory backends (`filesystem`, `pincher`) filtered by access mode. `available`: pure neutral catalog with no prioritization directive or playbook section. |

Returns 400 on unknown `access` / `format` / `style` / `tool_use` values. The body is `text/markdown; charset=utf-8`.

## curl examples

### Get the status

```bash
curl -sS http://localhost:8000/api/status | jq
```

```json
{
  "primary": null,
  "running": true,
  "servers": [
    { "name": "zelosmcp",    "running": true,  "builtin": true,  "transport": "builtin", ... },
    { "name": "filesystem",  "running": true,  "builtin": false, "transport": "stdio",   ... },
    { "name": "pincher",     "running": true,  "builtin": false, "transport": "stdio",   ... }
  ]
}
```

`running` at the top level reflects whether any **user** backend is up (the always-on builtin doesn't count). The `builtin` flag distinguishes the built-in row.

### Push a config

```bash
curl -sS -X POST http://localhost:8000/api/start \
  -H 'Content-Type: application/json' \
  --data-binary @configs/default-zelosmcp.json | jq
```

```json
{
  "ok": true,
  "primary": null,
  "servers": {
    "kubernetes":  { "ok": true },
    "filesystem":  { "ok": true },
    "pincher":     { "ok": true },
    "docker":      { "ok": true }
  }
}
```

### Stream activity logs

```bash
curl -N http://localhost:8000/api/logs
```

Server-Sent Events; each line is `data: [HH:MM:SS] [<name>] <message>`. Open multiple terminals — multiple subscribers are supported.

### Generate a Cursor rule (read-only)

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only' \
  > .cursor/rules/zelosmcp.mdc
```

### Generate a Copilot custom-instructions file

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?format=copilot-instructions' \
  > .github/copilot-instructions.md
```

### Generate a scoped Cursor rule (Python files only)

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?style=scoped&globs=**/*.py' \
  > .cursor/rules/zelosmcp-python.mdc
```

### Generate a neutral catalog rule (no prioritization)

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?tool_use=available' \
  > .cursor/rules/zelosmcp.mdc
```

### Snapshot the full tool catalog

```bash
curl -sS http://localhost:8000/api/catalog | jq 'keys'
```

```json
["docker", "filesystem", "kubernetes", "zelosmcp", "pincher"]
```

```bash
curl -sS http://localhost:8000/api/catalog | jq '."filesystem".tools | length'
14
```

```bash
curl -sS http://localhost:8000/api/catalog \
  | jq '."filesystem".tools[] | { name, hasInputSchema: (.inputSchema != null) }'
```

### Toggle a single backend

```bash
curl -sS -X POST http://localhost:8000/api/servers/docker/stop  | jq
curl -sS -X POST http://localhost:8000/api/servers/docker/start | jq
```

Refuses `zelosmcp` (the built-in is always-on).

### Stop everything

```bash
curl -sS -X POST http://localhost:8000/api/stop | jq
```

User backends shut down; the built-in MCP at `/zelosmcp/mcp` (and its tools surfaced as `zelosmcp__*` at `/mcp`) keep running.

## Status / start response shapes

### `GET /api/status`

```json
{
  "primary": null,
  "running": true,
  "servers": [
    {
      "name": "filesystem",
      "running": true,
      "error": null,
      "primary": false,
      "builtin": false,
      "transport": "stdio",
      "spec": {
        "name": "filesystem",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/user_data_rw"]
      }
    }
  ]
}
```

`spec` echoes back the JSON config sent to `/api/start`. `running` at the top level excludes the always-on `zelosmcp` builtin so the boolean still means "any user-configured backend is up".

### `POST /api/start`

```json
{
  "ok": true,                  // true iff every backend started successfully
  "primary": null,             // deprecated; always null
  "servers": {
    "filesystem": { "ok": true },
    "broken":     { "ok": false, "error": "spawn enoent" }
  }
}
```

400 + `{ ok: false, error: "..." }` on validation errors before any backend is started (invalid JSON, reserved name, missing field).

## MCP routes

The streamable-HTTP MCP routes (`/<name>/mcp`, `/zelosmcp/mcp`, `/mcp`) speak the [MCP protocol](https://modelcontextprotocol.io/) directly. They're not REST — calling them with curl requires constructing JSON-RPC envelopes.

A round-trip example (initialize + tools/list at `/mcp`):

```bash
# 1. initialize (needed once per session)
curl -sS -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}' \
  >/dev/null

# 2. tools/list
curl -sS -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | jq '.result.tools[].name'
```

Each tool / prompt / resource appears once, prefixed by its origin backend (`<server>__<tool>`). Unprefixed names are reachable on the corresponding `/<name>/mcp` endpoint.

For programmatic MCP clients, point at `http://localhost:8000/mcp` over streamable-HTTP and let your client library handle the envelope. See [cursor-integration.md](cursor-integration.md) and [vscode-integration.md](vscode-integration.md) for IDE wiring.

## See also

- [configuration.md](configuration.md) — the JSON schema `/api/start` accepts.
- [built-in-mcp.md](built-in-mcp.md) — the seven `zelosmcp__*` tools and how they relate to the HTTP API.
- [`/docs`](http://localhost:8000/docs) — interactive Swagger UI for the same endpoints.
