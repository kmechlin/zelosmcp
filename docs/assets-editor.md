# Assets editor — GUI walkthrough

The zelosMCP web UI provides a full-featured assets editor directly inside the dashboard. This page walks through every part of the interface: how to open the assets pane for a backend, how to edit individual assets, how to use the YAML editor, how to push assets to repos, and how extension buttons work.

For background on what assets are and how they're stored, see [assets.md](assets.md). For the per-kind schema, see [asset-kinds.md](asset-kinds.md). For the HTTP API backing all these actions, see [assets-api.md](assets-api.md).

---

## Opening the assets pane

Each server row in the right column of the dashboard has an **Assets** button next to the **Details** button. Clicking **Assets** on any running (or stopped) server opens that backend's per-backend assets pane in the middle panel.

```
Right column (server list)
┌────────────────────────────────────┐
│ pincher        [Details] [Assets]  │  ← click Assets
│ filesystem     [Details] [Assets]  │
│ docker         [Details] [Assets]  │
└────────────────────────────────────┘
```

The middle panel switches to the **Backend Assets** view and loads the assets for that backend from `GET /api/assets?backend=<name>`.

The pane header shows the backend name and a **Refresh** button. The action buttons (**Edit YAML**, **Export**, **Import**) appear on the right side of the header.

---

## Tabs

The assets pane has five tabs:

| Tab | What it shows | `+ Add` available? |
|---|---|---|
| **Rules** | `rule` kind rows | Yes |
| **Extensions** | `extension` kind rows | Yes |
| **Agents** | `agent` kind rows | Yes |
| **Hooks** | `hook` kind rows | Yes |
| **All** | All kinds for this backend | No |

Clicking a tab switches the content area without a network round-trip (assets are loaded once per pane open and filtered client-side per tab).

The **+ Add** button on a kind-specific tab calls `PUT /api/assets/{kind}/{backend}/__new__` with an empty stub body, creating a placeholder row that you can then edit.

---

## Asset cards and the edit modal

Each asset appears as a card showing its name and kind. The **Edit** button on the card opens the global asset edit modal.

### Edit modal

The modal shows:
- **Title** — `kind / backend / name`.
- **Meta** — source (`seed` or `user`) and `updated_at` timestamp.
- **Body textarea** — the full markdown or JSON body, editable.
- **Save** — calls `PUT /api/assets/{kind}/{backend}/{name}` with `source='user'`. The saved row persists in the SQLite store and survives restarts.
- **Revert to seed** — calls `DELETE /api/assets/{kind}/{backend}/{name}`. This removes the user row; on the next seeder run (startup or `POST /api/assets/seed`), the bundled default body is restored.

### Source indicator

The meta line under the title shows either `seed` (unmodified bundled content) or `user` (customized). A `user` row is never overwritten by the seeder, even if the bundled YAML is updated in a new zelosMCP release. Use **Revert to seed** to opt back into automatic updates for that asset.

---

## YAML editor

The YAML editor lets you view and replace an entire backend's assets in one operation — useful for bulk edits, importing a file you've edited locally, or reviewing everything in one place.

### Opening the editor

Click **Edit YAML** in the pane header. The asset list is replaced by a resizable `<textarea>` pre-populated with the current DB content rendered by `GET /api/assets/yaml/{backend}`. The textarea content matches the unified YAML file schema exactly, so you can copy it, edit it locally, and re-import it.

### Live lint

As you type, the editor debounces ~300 ms and then sends the current text to `POST /api/assets/yaml/{backend}/validate`. Errors appear below the textarea in a lint panel:

```
line 12: extensions.index_project.targets[0]: 'repos_roww' is not one of
         ['repos_row', 'server_details', 'server_row', 'assets_panel']
```

The **Save** button is disabled while there are lint errors. Fix all errors to re-enable it.

### Saving

**Save** sends the textarea content to `PUT /api/assets/yaml/{backend}`. The server:
1. Parses the YAML.
2. Validates against the schema (same validator as live lint).
3. Deletes all existing rows for the backend.
4. Inserts new rows from the parsed document (stamped `source='user'`).

All replaced rows become `source='user'`, which means they won't be silently overwritten by the seeder on the next restart. If you later want to go back to bundled defaults for the entire backend, delete all rows via `DELETE /api/assets/yaml/{backend}` (see [assets-api.md](assets-api.md#delete-apias setsy amlbackend)) and restart.

### Export

**Export** calls `GET /api/assets/yaml/{backend}` and triggers a browser download of the YAML text as `<backend>-assets.yaml`. The exported file is schema-valid and can be re-imported as-is or used as a starting point for a custom YAML file in `configs/assets/`.

### Import

**Import** opens a file picker (`.yaml`/`.yml`). After selecting a file, the content is loaded into the textarea for review and lint-checking before you click **Save**. Nothing is written to the server until you click **Save**.

### Closing the editor

**Cancel** dismisses the YAML editor and restores the asset card list without writing anything. Changes made in the textarea that haven't been saved are discarded.

---

## Repo details pane: push and execute extensions

The repo details pane (opened by clicking a row in the **Repositories** panel) is where assets are pushed to disk and extensions are executed.

### Push buttons

```
Push assets
──────────────────────────────────────────────────────────────
Push includes: zelosmcp + pincher, filesystem
[Push all]  [Push rules]  [Push agents]  [Push hooks]  [Preview rule]
```

| Button | What it does |
|---|---|
| **Push all** | Runs Push rules + Push agents + Push hooks in sequence. |
| **Push rules** | Calls `POST /api/assets/push/rule` — renders a comprehensive `.cursor/rules/zelosmcp.mdc` (or `copilot-instructions.md`) aggregating assets from `zelosmcp` + all running backends. |
| **Push agents** | Calls `POST /api/assets/push/agent` — writes each agent's `SKILL.md` for every running backend. |
| **Push hooks** | Calls `POST /api/assets/push/hook` — merges each hook entry into `.cursor/hooks.json`, preserving non-zelosMCP entries. |
| **Preview rule** | Fetches the rule body from `GET /api/cursor-rule?...` with the current format/access/tool_use settings and shows it in a code block below the buttons — no write. |

**Running-backends hint** — the line above the push buttons lists which backends are included: always `zelosmcp` (global) plus every currently-running user backend. Backends that are stopped are excluded from the push.

### Rule format controls

The four dropdowns above the push section control how the rule is rendered:

| Control | Values | Notes |
|---|---|---|
| **Format** | `cursor-mdc` (default), `copilot-instructions` | Determines the output file and frontmatter wrapper. |
| **Tool use** | `priority` (default), `available` | `priority` adds the "prefer MCP tools" directive and backend playbooks. |
| **Access** | `read-only` (default), `read-write` | Toggles which directive and which playbook variant is used. |
| **Style** | `always-apply` (default), `scoped` | Scoped activates the Globs input. Only meaningful for `cursor-mdc`. |

These settings are passed as-is to `POST /api/assets/push/rule` (`fmt`, `access`, `tool_use` body fields).

### Execute extensions

Below the push buttons, zelosMCP renders a button for every extension asset that has `targets: [repos_row]`:

```
Execute extensions
──────────────────────────────────────
[Index in pincher]  [...]
```

Each button is labelled with the extension's `label` field. Hovering shows the `description`. A button is **disabled** when `requires_running: true` and the extension's backend is not currently running (the tooltip explains why).

Clicking a button calls `POST /api/assets/extension/{backend}/{name}/invoke` with the current repo's path injected as `ctx.repo.ro_path`. The result message (from `success.message` or `error.message`) appears inline below the buttons.

---

## zelosmcp global assets

The `zelosmcp` backend is always-on and its assets (directives, compressed-tool rules, etc.) apply to every backend. You can view and edit them by clicking the **Assets** button on the `zelosmcp` row in the server list.

There is no separate "global" pane — `zelosmcp` is treated identically to any other backend in the GUI. Its rule assets are merged into every other backend's rule render automatically by the rule generator.
