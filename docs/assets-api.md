# Assets HTTP API reference

This page documents every `/api/assets*` endpoint. The interactive Swagger UI at [`/docs`](http://localhost:8000/docs) has the same information with a live try-it console. For the web UI that drives most of these endpoints, see [assets-editor.md](assets-editor.md). For background on assets themselves, see [assets.md](assets.md).

All endpoints return `503` with `{"error": "asset store not initialised"}` if the asset store hasn't started yet (unlikely in normal operation but possible during startup).

---

## Endpoint table

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/assets` | List all rows (filterable by `kind`, `backend`, `target`) |
| GET | `/api/assets/kinds` | List registered asset kind descriptors |
| GET | `/api/assets/summary` | Asset store stats (row counts per kind / source) |
| POST | `/api/assets/seed` | Re-run the seeder from the bundled YAML files |
| GET | `/api/assets/yaml/{backend}` | Render a backend's current rows as unified YAML |
| PUT | `/api/assets/yaml/{backend}` | Replace a backend's rows from a YAML document |
| DELETE | `/api/assets/yaml/{backend}` | Delete all rows for a backend |
| POST | `/api/assets/yaml/{backend}/validate` | Validate YAML text without writing |
| GET | `/api/assets/{kind}/{backend}/{name}` | Get one row |
| PUT | `/api/assets/{kind}/{backend}/{name}` | Create or update one row (user override) |
| DELETE | `/api/assets/{kind}/{backend}/{name}` | Delete one row |
| POST | `/api/assets/{kind}/{backend}/{name}/invoke` | Invoke an extension asset |
| POST | `/api/assets/{kind}/{backend}/{name}/push` | Push one asset to a repo |
| POST | `/api/assets/push/{kind}` | Comprehensive push (aggregates all running backends) |

---

## `GET /api/assets`

Returns every row optionally filtered by query parameters.

**Query params**

| Param | Type | Default | Effect |
|---|---|---|---|
| `kind` | string | (none) | Filter to one kind: `rule`, `extension`, `agent`, or `hook`. |
| `backend` | string | (none) | Filter to one backend name. |
| `target` | string | (none) | Filter to one target: `""` (both), `"cursor"`, or `"vscode"`. |

**Response (200)**

```json
[
  {
    "kind": "rule",
    "backend": "pincher",
    "name": "playbook_read_only",
    "target": "",
    "body": "### `pincher` ...",
    "meta": {},
    "source": "seed",
    "seed_version": 2,
    "updated_at": 1715000000.0
  },
  ...
]
```

**Example**

```bash
# All rule assets for pincher
curl -sS 'http://localhost:8000/api/assets?kind=rule&backend=pincher' | jq
```

---

## `GET /api/assets/kinds`

Returns the list of registered asset kind descriptors.

**Response (200)**

```json
[
  { "id": "rule",      "label": "Rules",      "description": "Cursor .mdc and VS Code copilot-instructions.md rule content..." },
  { "id": "extension", "label": "Extensions", "description": "UI action buttons that invoke MCP tools or open links..." },
  { "id": "agent",     "label": "Agents",     "description": "Cursor Subagent / Skill definitions..." },
  { "id": "hook",      "label": "Hooks",      "description": "Cursor hook entries (event → command)..." }
]
```

---

## `GET /api/assets/summary`

Returns store-wide statistics.

**Response (200)**

```json
{
  "total": 42,
  "by_kind": { "rule": 28, "extension": 6, "agent": 4, "hook": 4 },
  "by_source": { "seed": 38, "user": 4 }
}
```

---

## `POST /api/assets/seed`

Re-runs the seeder from the bundled YAML files. Safe to call at any time — idempotent for seed rows; user-overridden rows are never touched.

**Request body (optional JSON)**

```json
{ "config_root": "/abs/path/to/configs/assets" }
```

Omit `config_root` to use the auto-discovered directory (same resolution order as startup — see [assets-yaml.md — File discovery](assets-yaml.md#file-discovery)).

**Response (200)**

```json
{
  "ok": true,
  "seeded": { "rule": 24, "extension": 4, "agent": 0, "hook": 0 },
  "summary": { "total": 42, "by_kind": { ... }, "by_source": { ... } }
}
```

**Example**

```bash
curl -sS -X POST http://localhost:8000/api/assets/seed | jq
```

---

## YAML editor endpoints

### `GET /api/assets/yaml/{backend}`

Renders the backend's current asset rows as a unified YAML document matching the file schema. The output is `text/yaml; charset=utf-8` and can be fed back to `PUT` unchanged.

**Path params**: `backend` — any backend name, including `zelosmcp` for the global backend.

**Response (200)** — `Content-Type: text/yaml`

```yaml
backend: pincher
seed_version: 2
rules:
  sections:
    playbook_read_only:
      body: |
        ### `pincher` ...
  tool_instructions:
    search:
      body: |
        Filter with `kind=Function` ...
extensions:
  index_project:
    label: Index in pincher
    ...
agents: {}
hooks: {}
```

**Example**

```bash
curl -sS 'http://localhost:8000/api/assets/yaml/pincher'
```

---

### `PUT /api/assets/yaml/{backend}`

Replaces all rows for the backend from a YAML document. The document is validated against the schema before any writes; the response includes the errors list if validation fails.

**Request** — `Content-Type: text/yaml` (body = YAML text)

**Response (200)**

```json
{ "ok": true, "rows_written": 12 }
```

**Response (400)** — validation failure

```json
{
  "ok": false,
  "errors": [
    { "path": "extensions.index_project.targets[0]",
      "message": "'repos_roww' is not one of ['repos_row', ...]" }
  ]
}
```

**Example**

```bash
curl -sS -X PUT 'http://localhost:8000/api/assets/yaml/pincher' \
  -H 'Content-Type: text/yaml' \
  --data-binary @my-pincher-assets.yaml | jq
```

---

### `DELETE /api/assets/yaml/{backend}`

Drops every asset row for the named backend. On the next seeder run (startup or `POST /api/assets/seed`), the bundled YAML file rows are restored if a matching file exists.

**Response (200)**

```json
{ "ok": true, "deleted": 12, "backend": "pincher" }
```

---

### `POST /api/assets/yaml/{backend}/validate`

Validates YAML text against the asset file schema without writing to the store. Intended for live client-side lint — call this on every editor keystroke (debounced).

**Request** — any `Content-Type`; body = raw YAML text

**Response (200)**

```json
{ "ok": true, "errors": [] }
```

or when errors exist:

```json
{
  "ok": false,
  "errors": [
    { "path": "backend", "message": "'' is too short" },
    { "path": "extensions.x.tool", "message": "'tool' is a required property" }
  ]
}
```

Even YAML parse errors are returned in the same shape — `path: ""` with a `YAML parse error: ...` message — so callers handle one response type.

---

## Per-row CRUD endpoints

### `GET /api/assets/{kind}/{backend}/{name}`

Returns one asset row.

**Path params**: `kind`, `backend`, `name`.

**Query params**: `target` (string, default `""`) — used as a discriminator when multiple rows share the same `(kind, backend, name)` with different targets.

**Response (200)** — one asset row object (same shape as the items in `GET /api/assets`)

**Response (404)**

```json
{ "error": "asset 'rule/pincher/playbook_read_only' not found" }
```

---

### `PUT /api/assets/{kind}/{backend}/{name}`

Creates or updates one asset row. The saved row always has `source='user'`, so the seeder will never overwrite it.

**Request body (JSON)**

```json
{
  "body": "### `pincher` — custom playbook\n\n...",
  "meta": {},
  "target": ""
}
```

All fields are optional; omitted fields default to empty. `meta` is stored as-is and interpreted differently per kind (see [asset-kinds.md](asset-kinds.md)).

**Response (200)** — the saved row

---

### `DELETE /api/assets/{kind}/{backend}/{name}`

Deletes one row. After deletion, the next seeder run restores the original seed content (if any exists in the bundled YAML).

**Query params**: `target` (string, default `""`)

**Response (200)**

```json
{ "ok": true, "kind": "rule", "backend": "pincher", "name": "playbook_read_only" }
```

`ok` is `false` if the row didn't exist.

---

## Extension invocation

### `POST /api/assets/{kind}/{backend}/{name}/invoke`

Invokes an extension asset — executes the MCP tool it describes or returns a link URL. `kind` must be `"extension"`.

**Request body (optional JSON)**

```json
{
  "ctx": {
    "repo": {
      "ro_path": "/user_data_ro/workspace/myrepo",
      "rw_path": "/user_data_rw/workspace/myrepo",
      "name": "myrepo"
    }
  }
}
```

The `ctx` object is used for `{ctx.repo.ro_path}` template substitution in `args_template`. Provide the repo context when invoking a `repos_row`-targeted extension. Omit or pass `{}` for other targets.

**Response (200)**

```json
{
  "ok": true,
  "message": "Indexed 1234 symbol(s) in 820ms",
  "result": { "symbols": 1234, "duration_ms": 820, "... ": "..." },
  "error": ""
}
```

On failure (`ok: false`):

```json
{
  "ok": false,
  "message": "Indexing failed: pincher returned an error",
  "result": null,
  "error": "pincher returned an error"
}
```

`503` is returned when the backend is required (`requires_running: true`) but is not running.

**Example**

```bash
curl -sS -X POST \
  'http://localhost:8000/api/assets/extension/pincher/index_project/invoke' \
  -H 'Content-Type: application/json' \
  -d '{"ctx": {"repo": {"ro_path": "/user_data_ro/workspace/myrepo"}}}' | jq
```

---

## Single-asset push

### `POST /api/assets/{kind}/{backend}/{name}/push`

Pushes one named asset (or all assets for a `kind`+`backend` combination when `name="*"`) to a repo via the `filesystem` MCP backend.

The `filesystem` backend must be running.

**Request body (JSON)**

```json
{ "repo": "workspace/myrepo" }
```

`repo` is the repo name as it appears in `GET /api/repos` (relative to `/user_data_ro/`).

**Response (200)**

```json
{
  "ok": true,
  "files": [
    { "path": "/user_data_rw/workspace/myrepo/.cursor/rules/zelosmcp.mdc",
      "mode": "overwrite", "ok": true, "error": "" }
  ]
}
```

**Response (400)** — kind doesn't support push (e.g. `extension`)

```json
{ "error": "asset kind 'extension' does not support push-to-project" }
```

---

## Comprehensive push

### `POST /api/assets/push/{kind}`

The high-level push endpoint used by the **Push rules**, **Push agents**, and **Push hooks** buttons. It collects assets from the `zelosmcp` global backend **and** every currently-running user backend, then writes the combined result to the target repo.

**Path params**: `kind` — `rule`, `agent`, or `hook`. Extensions are not pushable.

**Request body (JSON)**

```json
{
  "repo":     "workspace/myrepo",
  "fmt":      "cursor-mdc",
  "access":   "read-only",
  "tool_use": "priority"
}
```

| Field | Default | Notes |
|---|---|---|
| `repo` | (required) | Repo name relative to `/user_data_ro/`. |
| `fmt` | `"cursor-mdc"` | `"cursor-mdc"` writes `.cursor/rules/zelosmcp.mdc`; `"copilot-instructions"` writes `.github/copilot-instructions.md`. Only relevant for `kind=rule`. |
| `access` | `"read-only"` | Passed to the rule renderer; selects `playbook_read_only` vs `playbook_read_write`. Only relevant for `kind=rule`. |
| `tool_use` | `"priority"` | `"priority"` includes playbooks and the tool-use-priority directive; `"available"` emits a neutral catalog. Only relevant for `kind=rule`. |

**Response (200)**

```json
{
  "ok": true,
  "kind": "rule",
  "repo": "workspace/myrepo",
  "backends_included": ["zelosmcp", "pincher", "filesystem"],
  "files": [
    { "path": "/user_data_rw/workspace/myrepo/.cursor/rules/zelosmcp.mdc",
      "mode": "overwrite", "ok": true, "error": "" }
  ]
}
```

`backends_included` lists which backends contributed to the push: always `zelosmcp`, plus every user backend that was running at the time of the call.

**Response (400)** — non-pushable kind or missing `repo`

```json
{ "error": "'repo' is required in request body" }
```

**Response (503)** — `filesystem` backend not running

```json
{ "error": "filesystem backend is not running" }
```

**Examples**

```bash
# Push a comprehensive rule (read-only, Cursor format)
curl -sS -X POST 'http://localhost:8000/api/assets/push/rule' \
  -H 'Content-Type: application/json' \
  -d '{"repo": "workspace/myrepo", "access": "read-write", "fmt": "cursor-mdc"}' | jq

# Push all agents
curl -sS -X POST 'http://localhost:8000/api/assets/push/agent' \
  -H 'Content-Type: application/json' \
  -d '{"repo": "workspace/myrepo"}' | jq

# Merge all hooks
curl -sS -X POST 'http://localhost:8000/api/assets/push/hook' \
  -H 'Content-Type: application/json' \
  -d '{"repo": "workspace/myrepo"}' | jq
```

---

## Error reference

| HTTP status | When |
|---|---|
| 400 | Invalid JSON body; YAML parse error; schema validation failure; non-pushable kind; missing required field. |
| 404 | Asset row not found (`GET /api/assets/{kind}/{backend}/{name}`). |
| 503 | Asset store not initialised; or `filesystem` backend not running (for push endpoints). |
| 500 | Unexpected error (filesystem write failure, tool call error, etc.). |

YAML validation failures always include an `errors` array of `{path, message}` objects. Other errors use `{"error": "<message>"}`.
