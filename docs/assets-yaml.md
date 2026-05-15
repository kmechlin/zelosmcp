# Assets YAML format

zelosMCP seeds its asset store from one YAML file per backend located under `configs/assets/`. Each file contains all five asset kinds (`rules`, `extensions`, `agents`, `hooks`, `skills`) for that backend in a single unified document.

For a quick orientation to assets in general, see [assets.md](assets.md). For per-kind field references, see [asset-kinds.md](asset-kinds.md).

## Top-level fields

```yaml
backend: <name>        # required — backend this file belongs to
seed_version: 2        # required — integer ≥ 0; controls upsert precedence
rules: { ... }         # optional
extensions: { ... }    # optional
agents: { ... }        # optional
hooks: { ... }         # optional
skills: { ... }        # optional
```

### `backend`

Must match `^[a-zA-Z][a-zA-Z0-9_.-]*$` — the same regex used for backend names everywhere in zelosMCP. It must equal the filename without the `.yaml` extension by convention (the seeder does not enforce a filename match, but the web UI's **Import** function does).

The special value `zelosmcp` is reserved for the always-on builtin backend. Its file (`configs/assets/global.yaml`) holds directives, access-mode headers, and compressed-tool rules that are pulled into **every** backend's rule render.

### `seed_version`

An integer that controls whether the seeder updates an existing row:

- The seeder calls `upsert(row, only_if_seed_lt=seed_version)`.
- If a row with the same `(kind, backend, name, target)` primary key already exists and its `seed_version` is **≥** the file's `seed_version`, the upsert is skipped.
- User rows (`source='user'`) are **never** overwritten by the seeder, regardless of `seed_version`.

Practical rules:
- Start at `1` for the initial published version of a file (auto-generated defaults use `0`).
- Bump by 1 any time you want existing installations to pick up updated content on the next restart or `POST /api/assets/seed`.
- Never decrease the version — that would prevent updates from propagating.

### `rules:`

See [asset-kinds.md — Rule](asset-kinds.md#rule-rules) for the complete sub-schema. Short form:

```yaml
rules:
  sections:
    <section_name>:
      body: |
        Markdown text
      targets: [cursor]    # optional
  tool_instructions:
    <tool_name>:
      body: |
        Short per-tool guidance
```

### `extensions:`

See [asset-kinds.md — Extension](asset-kinds.md#extension-extensions). Short form:

```yaml
extensions:
  <ext_name>:
    label: "Button label"
    type: tool          # or "link"
    tool: <tool_name>
    args_template: { path: "{ctx.repo.ro_path}" }
    targets: [repos_row]
```

### `agents:`

See [asset-kinds.md — Agent](asset-kinds.md#agent-agents). Short form:

```yaml
agents:
  <agent_name>:
    name: "Display name"
    description: "..."
    targets: [cursor]
    body: |
      # Agent markdown ...
```

### `hooks:`

See [asset-kinds.md — Hook](asset-kinds.md#hook-hooks). Short form:

```yaml
hooks:
  <hook_name>:
    event: pre_commit
    command: "ruff check ."
    targets: [cursor]
```

### `skills:`

See [asset-kinds.md — Skill](asset-kinds.md#skill-skills). Short form:

```yaml
skills:
  <skill_name>:
    description: "One-line description"
    paths:
      - "**/*.py"
    targets: [cursor, vscode]
    body: |
      # Skill markdown ...
```

## Schema validator

Every section uses `additionalProperties: false` at every nesting level. This means typos like `extentions:` (instead of `extensions:`) or `playbook_readonly:` (instead of `playbook_read_only`) are surfaced as hard errors rather than silently no-op-seeding.

The validator is invoked:
- By the seeder before any DB writes on startup.
- By `POST /api/assets/yaml/{backend}/validate` for live client-side lint.
- By `PUT /api/assets/yaml/{backend}` before applying changes from the YAML editor.

Validation errors are returned as a list of `{path, message}` objects where `path` uses dot-notation (e.g. `extensions.index_project.targets[0]`).

The canonical schema lives in `src/zelosmcp/framework/assetstore/schema.py`.

## File discovery

The seeder resolves the seed-file directory in this order:

1. **`ZELOSMCP_ASSETS_DIR` environment variable** — explicit override, absolute path.
2. **Walk upward** from `src/zelosmcp/framework/assetstore/seeder.py` looking for a `configs/assets/` subdirectory (up to 8 levels up). This finds the directory reliably in a standard source checkout.
3. **Well-known container paths** — `/app/configs/assets`, then `/opt/zelosmcp/configs/assets`.
4. **Current working directory** — `configs/assets` relative to `$PWD`.

Once resolved, the seeder globs `*.yaml` in that directory and processes every file found. Files are processed in lexicographic order; there is no dependency ordering between files.

## Minimal valid file

```yaml
backend: mybackend
seed_version: 1
```

This is the smallest file that passes schema validation. All four kind sections are optional.

## Fully-featured example

```yaml
backend: mybackend
seed_version: 1

rules:
  sections:
    playbook_read_only:
      body: |
        ### `mybackend`

        Prefer `mybackend__list_items` over shell commands. Do not call
        `mybackend__delete_item` in read-only mode.

    playbook_read_write:
      body: |
        ### `mybackend`

        Prefer `mybackend__list_items` over shell commands. Confirm with
        the user before calling `mybackend__delete_item` (destructive).

  tool_instructions:
    list_items:
      body: |
        Returns all items matching the optional `filter` arg. Paginated —
        check `next_cursor` in the response.
    delete_item:
      body: |
        Permanently deletes an item by `id`. Cannot be undone.

extensions:
  refresh_cache:
    label: "Refresh cache"
    description: "Clears and rebuilds the mybackend item cache"
    tool: refresh_cache
    args_template: {}
    targets:
      - server_details
    requires_running: true
    confirm: true
    success:
      message: "Cache refreshed in {result.duration_ms}ms"
    error:
      message: "Cache refresh failed: {error}"

agents:
  mybackend_helper:
    name: "Mybackend Helper"
    description: "Knows the mybackend API and can query and update items"
    targets: [cursor]
    push:
      cursor: ".cursor/skills/mybackend_helper/SKILL.md"
    body: |
      # Mybackend Helper

      You are an expert on the mybackend API. Use `mybackend__list_items`
      to inspect the current state and `mybackend__delete_item` only when
      the user explicitly confirms deletion.

hooks:
  run_tests:
    name: "Run mybackend tests"
    event: pre_commit
    command: "pytest tests/mybackend/ -q"
    targets: [cursor]
```

## The `zelosmcp` global backend

`configs/assets/global.yaml` (backend: `zelosmcp`) holds content that applies to **every** backend:

- `directive_read_only` / `directive_read_write` — the access-mode header block at the top of every generated rule.
- `directive_tool_use_priority` — the "prefer MCP tools over shell" instruction.
- `self_check_gate` — the four-question pre-flight check.
- `compressed_rules_read_only` / `compressed_rules_read_write` — usage instructions for compressed (`get_tool_schema` / `invoke_tool`) backends.

When the rule generator loads assets for a backend, it merges global rows first and then lets per-backend rows override:

```
backend rule assets = zelosmcp global rows (base) + backend-specific rows (override)
```

This means if you want to change the access-mode header text globally, edit `global.yaml` and bump `seed_version`. If you only want to change how the header reads for `pincher`, add a `directive_read_only` section in `pincher.yaml`.

## Adding a new backend file

1. Create `configs/assets/<backendname>.yaml` with at least `backend: <backendname>` and `seed_version: 1`.
2. Add your rules, extensions, agents, or hooks.
3. Either restart zelosMCP or call `POST /api/assets/seed` to load the new content without a restart.

The new file is picked up automatically — no code changes are needed.
