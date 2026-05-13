# Asset kinds reference

This page documents each of the four asset kinds (`rule`, `extension`, `agent`, `hook`) in detail — what the YAML section looks like, what `AssetRow` fields each kind uses, how it surfaces in the GUI, and what happens when you push it to a repo.

For the top-level YAML file format and `seed_version` semantics, see [assets-yaml.md](assets-yaml.md). For the HTTP API, see [assets-api.md](assets-api.md). For the GUI editor, see [assets-editor.md](assets-editor.md).

---

## Rule (`rules:`)

Rule assets contain the markdown content the rule generator embeds in `.cursor/rules/zelosmcp.mdc` (Cursor) and `.github/copilot-instructions.md` (VS Code / GitHub Copilot). They hold per-backend playbooks, per-tool guidance, and access-mode directives.

### AssetRow shape

| Field | Value |
|---|---|
| `kind` | `"rule"` |
| `backend` | The MCP backend name (e.g. `"pincher"`, `"filesystem"`) or `"zelosmcp"` for global directives. |
| `name` | Section name (e.g. `"playbook_read_only"`) or `"tool:<tool_name>"` for per-tool guidance. |
| `target` | `""` (both IDEs), `"cursor"`, or `"vscode"`. |
| `body` | Markdown text that gets spliced into the generated rule. |
| `meta` | For `tool:*` rows: `{"tool": "<tool_name>"}`. Empty for section rows. |

### YAML section format

```yaml
rules:
  sections:
    <section_name>:
      body: |
        Markdown content for this section.
      targets: [cursor]        # optional; omit for both IDEs (default)
  tool_instructions:
    <tool_name>:
      body: |
        One-line or short guidance for this specific tool.
```

Valid `section_name` values follow the pattern `playbook_<suffix>`, `compressed_rules_<suffix>`, `directive_<suffix>`, `self_check_gate`, or `directive_tool_use_priority` — where `<suffix>` is `read_only` or `read_write`. Custom names with the `playbook_` prefix are also accepted.

`tool_instructions` entries are stored with `name="tool:<tool_name>"` in the asset store and rendered inline next to the matching tool in the generated rule.

### Example (from `configs/assets/pincher.yaml`)

```yaml
rules:
  sections:
    playbook_read_only:
      body: |
        ### `pincher` (codebase intelligence)

        **MANDATORY: For any of the following user intents, your FIRST tool call MUST be a `pincher__*` tool:**

        | User intent | Required tool |
        |---|---|
        | find a function / class / method named X | `pincher__search` |
        | show me function X / how does X work | `pincher__context` |
        | what calls X / blast radius | `pincher__trace` |
        | blast radius before commit | `pincher__changes` |

        **Forbidden:** `Grep` for symbol names (use `pincher__search`).

    playbook_read_write:
      body: |
        ### `pincher` (codebase intelligence)
        # ... full read-write variant ...

  tool_instructions:
    search:
      body: |
        Filter with `kind=Function` / `language=Python`; supports wildcards (`auth*`)
        and phrases (`"process order"`). Start here when you don't know a symbol's ID.
    context:
      body: |
        Returns the symbol body plus direct imports and callees in one shot.
        Pass `fields=symbol,callees` to drop imports when not needed.
```

### Push behavior

When you push rules (via **Push rules** or **Push all** in the repo details pane, or `POST /api/assets/push/rule`), zelosMCP invokes `render_comprehensive_rule`, which:

1. Collects the live tool catalog for every running backend.
2. Loads `rule` assets for each backend and for the `zelosmcp` global backend.
3. Splices `playbook_read_only` or `playbook_read_write` (based on the chosen access mode) for each backend that has one.
4. Renders a complete markdown document with tool catalog + playbooks + per-tool instructions + directives.
5. Writes the output to `.cursor/rules/zelosmcp.mdc` (format `cursor-mdc`) or `.github/copilot-instructions.md` (format `copilot-instructions`).

The `zelosmcp` global backend contributes its `directive_*`, `self_check_gate`, and `compressed_rules_*` sections to every rendered rule, regardless of which user backends are running.

### GUI surface

- **Rules tab** in the backend assets pane. Each section row shows its name. Click **Edit** to open the markdown body in the edit modal.
- Modifications become `source='user'` rows. Click **Revert to seed** to restore the bundled default.
- Rules are also the only kind the **YAML editor** seeds from the `rules:` section in the per-backend YAML.

### Validation

- `name` and `backend` must be non-empty strings.
- `target` must be `""`, `"cursor"`, or `"vscode"`.

---

## Extension (`extensions:`)

Extension assets define UI action buttons that appear in specific locations in the zelosMCP web UI. An extension either invokes an MCP tool through the aggregator or opens a URL.

Extensions are **not** pushed to disk — they live in the browser and execute against the running zelosMCP instance.

### AssetRow shape

| Field | Value |
|---|---|
| `kind` | `"extension"` |
| `backend` | The MCP backend whose session is called when the button is clicked. |
| `name` | Unique extension identifier within the backend (e.g. `"index_project"`). |
| `target` | Always `""` (extensions have no IDE target). |
| `body` | JSON serialization of the full extension definition dict. |
| `meta` | Structured definition: `type`, `label`, `description`, `tool`/`href`, `args_template`, `targets`, `requires_running`, `confirm`, `success`, `error`. |

### YAML section format

```yaml
extensions:
  <extension_name>:
    label: "Human-readable button label"
    description: "Tooltip / title text"
    type: tool          # "tool" (default) or "link"
    tool: <tool_name>   # required when type=tool; name as the backend exposes it
    args_template:      # key/value args passed to the tool; supports {ctx.*} templates
      path: "{ctx.repo.ro_path}"
    targets:            # where the button appears; see target placement table below
      - repos_row
    requires_running: true   # disable the button when the backend is not running
    confirm: false           # ask the user to confirm before invoking
    success:
      message: "Indexed {result.symbols} symbol(s) in {result.duration_ms}ms"
    error:
      message: "Failed: {error}"
```

For `type: link`:

```yaml
extensions:
  view_dashboard:
    label: "Open dashboard"
    type: link
    href: "{ctx.proxy.mount}/v1/dashboard"
    targets:
      - server_details
    requires_running: true
```

### Target placement

| `targets` value | Where the button appears |
|---|---|
| `repos_row` | Under **Execute extensions** in the repo details pane (right of the push buttons). Receives `{ctx.repo.ro_path}`. |
| `server_details` | On the server details view when the backend's details are expanded. |
| `server_row` | Inline on each server row in the right column. |
| `assets_panel` | Inside the backend's Assets pane (Extensions tab). |

An extension can appear in multiple targets:

```yaml
    targets:
      - repos_row
      - assets_panel
```

### Template substitution

`args_template` values and `href` strings support `{ctx.<key>}` references. Available context keys:

| Key | Value |
|---|---|
| `ctx.repo.ro_path` | Read-only path of the selected repo (e.g. `/user_data_ro/workspace/myrepo`). Populated when the button is clicked from `repos_row`. |
| `ctx.repo.rw_path` | Read-write path of the selected repo. |
| `ctx.repo.name` | Repo basename. |
| `ctx.proxy.mount` | Base URL of the backend's reverse-proxy mount (populated from `reverseProxy.mount`). |

Unknown template keys are left as `{ctx.key}` rather than raising an error.

### Success and error messages

The `success.message` and `error.message` strings support simple `{key}` substitution:

| Key | Available in |
|---|---|
| `{result.*}` | Any field from the tool's JSON response body (e.g. `{result.symbols}`). |
| `{backend}` | The backend name. |
| `{tool}` | The tool name. |
| `{error}` | The error message string (in `error.message` only). |

### Example (from `configs/assets/pincher.yaml`)

```yaml
extensions:
  index_project:
    label: "Index in pincher"
    description: "Run pincher__index on the selected repo path"
    tool: index
    args_template:
      path: "{ctx.repo.ro_path}"
    targets:
      - repos_row
    requires_running: true
    confirm: false
    success:
      message: "Indexed {result.symbols} symbol(s) in {result.duration_ms}ms"
    error:
      message: "Indexing failed: {error}"

  view_dashboard:
    label: "Open dashboard"
    description: "Open the pincher analytics dashboard in a new tab"
    type: link
    href: "{ctx.proxy.mount}/v1/dashboard"
    targets:
      - server_details
    requires_running: true
```

### Push behavior

Extensions are not pushed to disk. They are invoked in-browser via `POST /api/assets/{kind}/{backend}/{name}/invoke`.

### Validation

- `backend` and `name` must be non-empty.
- `type: tool` requires a non-empty `tool` field.
- `type: link` requires a non-empty `href` field.
- `targets` values must be one of the four accepted strings.

---

## Agent (`agents:`)

Agent assets contain Cursor Subagent / Skill definitions: a markdown `body` the agent uses as its system prompt/skill file, plus metadata controlling where it gets written when pushed to a repo.

### AssetRow shape

| Field | Value |
|---|---|
| `kind` | `"agent"` |
| `backend` | The MCP backend this agent is associated with. |
| `name` | Agent identifier (e.g. `"code_reviewer"`). Also the default directory name under `.cursor/skills/`. |
| `target` | Always `"cursor"` (agents are a Cursor concept). |
| `body` | Markdown content of the `SKILL.md` file. |
| `meta` | `{"name": "<display name>", "description": "...", "targets": ["cursor"], "push": {"cursor": ".cursor/skills/<name>/SKILL.md"}}` |

### YAML section format

```yaml
agents:
  <agent_name>:
    name: "Display Name"
    description: "One-line description of what this agent does"
    targets: [cursor]           # optional; defaults to [cursor]
    push:
      cursor: ".cursor/skills/<agent_name>/SKILL.md"   # optional; this is the default
    body: |
      # Agent Name
      You are a ...

      ## What I do
      ...
```

If `push.cursor` is omitted, the push writer defaults to `.cursor/skills/<agent_name>/SKILL.md`.

### Example (minimal stub)

```yaml
agents:
  code_reviewer:
    name: "Code Reviewer"
    description: "Reviews diffs for bugs and style issues before committing"
    targets: [cursor]
    body: |
      # Code Reviewer

      You are a careful code reviewer. When the user asks you to review a diff:

      1. Check for logical bugs, missing error handling, and off-by-one errors.
      2. Note style issues only when they're likely to cause bugs.
      3. Keep feedback concise — one sentence per finding.
```

### Push behavior

`POST /api/assets/push/agent` (or **Push agents** in the repo details pane) writes each agent's `body` to the path specified in `meta.push.cursor`. Write mode is `overwrite`. The file is created with any necessary parent directories.

### GUI surface

- **Agents tab** in the backend assets pane. Click **Edit** to modify the `body` in the markdown edit modal.
- Click **+ Add** on the Agents tab to create a stub row.

### Validation

- `backend` and `name` must be non-empty strings.

---

## Hook (`hooks:`)

Hook assets define [Cursor hook](https://docs.cursor.com/context/rules#hooks) entries: an event name (e.g. `pre_commit`, `post_edit`) paired with a shell command to run.

When pushed, hooks are **merged** into `.cursor/hooks.json` rather than overwriting the whole file. zelosMCP tracks which entries it owns via a `_owner: "zelosmcp"` + `_key: "<hook_name>"` tag on each entry, so user-added hooks in the same file are preserved.

### AssetRow shape

| Field | Value |
|---|---|
| `kind` | `"hook"` |
| `backend` | The MCP backend this hook is associated with. |
| `name` | Hook identifier within the backend (e.g. `"pre_commit_lint"`). Used as the `_key` tag in `hooks.json`. |
| `target` | Always `"cursor"` (Cursor-specific format). |
| `body` | JSON object: `{"name": "...", "event": "...", "command": "...", "_owner": "zelosmcp", "_key": "<name>"}`. |
| `meta` | `{"name": "...", "event": "...", "command": "...", "targets": ["cursor"]}` |

### YAML section format

```yaml
hooks:
  <hook_name>:
    name: "Human-readable label"   # optional; defaults to hook_name
    event: pre_commit              # Cursor hook event name
    command: "ruff check ."        # shell command to run
    targets: [cursor]              # optional; defaults to [cursor]
```

### Example

```yaml
hooks:
  pre_commit_lint:
    name: "Pre-commit lint"
    event: pre_commit
    command: "ruff check . && mypy src/"
    targets: [cursor]
```

### Push behavior

`POST /api/assets/push/hook` (or **Push hooks** in the repo details pane) reads `.cursor/hooks.json` in the target repo, merges each hook entry (matched by `_owner='zelosmcp'` + `_key`), and writes the result back. Existing entries whose `_owner` is not `"zelosmcp"` are left untouched. Entries managed by zelosMCP are upserted — if the command changes, the existing entry is replaced.

The resulting `hooks.json` shape:

```json
{
  "hooks": [
    {
      "name": "Pre-commit lint",
      "event": "pre_commit",
      "command": "ruff check . && mypy src/",
      "_owner": "zelosmcp",
      "_key": "pre_commit_lint"
    }
  ]
}
```

### GUI surface

- **Hooks tab** in the backend assets pane. Click **Edit** to modify the event or command.
- Click **+ Add** on the Hooks tab to create a stub row.

### Validation

- `backend` and `name` must be non-empty.
- `meta.event` must be non-empty.
- `meta.command` must be non-empty.

---

## Dynamic default rules (auto-generated)

When a backend starts and has **no** rows in the asset store (i.e. no YAML file exists for it and no prior user edits), zelosMCP auto-generates rule rows from the backend's live tool catalog. This ensures every backend gets at least some useful agent guidance even before you write a YAML file for it.

The auto-generated rows are:

- **`playbook_read_only`** — an intro paragraph + a markdown table listing every tool with its arg signature and mutability marker (`[readonly]` / `[mutates]` / `[destructive]` / `[?]`), plus a list of tools that must not be called in read-only mode.
- **`playbook_read_write`** — same table, plus a list of `[destructive]` tools requiring confirmation.
- **`tool:<name>`** one row per tool — `<backend>__<name> (<args>) [<marker>]` followed by the tool's description.

These rows are stamped with `seed_version=0` so any YAML file (`seed_version ≥ 1`) or user edit takes precedence without you having to delete them first. You can also overwrite them directly via the **Edit** button in the Rules tab.

The generation logic lives in `src/zelosmcp/framework/assetstore/defaults.py` (`generate_default_rule_rows`, `ensure_default_assets`). The classification that assigns each tool its mutability marker lives in `src/zelosmcp/framework/assetstore/tool_classify.py`.
