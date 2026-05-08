# Cursor integration

Cursor is the IDE zelosMCP was originally built for. The integration has two halves: an `mcp.json` entry that points Cursor at the zelosMCP aggregator, and a `.mdc` rule file that teaches the agent which aggregated tool to reach for. Both are dynamic — the zelosMCP web UI generates them from your live backend set.

## `mcp.json` — the IDE-to-zelosMCP wiring

Cursor reads MCP server config from two locations:

- **Per-project**: `.cursor/mcp.json` in your repo root (shareable, version-controlled).
- **Globally**: `~/.cursor/mcp.json` (applies to every Cursor workspace).

There are two ways to wire zelosMCP into either file. The aggregated entry is the recommended default.

### Aggregated entry (recommended)

One Cursor entry, every backend's tools and prompts. Tool names are namespaced as `<server>__<tool>` (e.g. `filesystem__read_file`, `pincher__search`). Resource URIs are kept verbatim; reads are routed to the originating backend automatically.

```json
{
  "mcpServers": {
    "zelosmcp-aggregate": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

This is what the **Cursor mcp.json (aggregated)** panel in the zelosMCP web UI hands you on click of **Copy**. The always-on built-in MCP's tools surface here too, as `zelosmcp__*` (see [built-in-mcp.md](built-in-mcp.md)).

### Per-backend entries (raw passthrough)

Use this when you want a backend's original tool names (no `<server>__` prefix) or one backend wired into a separate Cursor profile.

```json
{
  "mcpServers": {
    "zelosmcp-filesystem": {
      "type": "streamable-http",
      "url": "http://localhost:8000/filesystem/mcp"
    },
    "zelosmcp-pincher": {
      "type": "streamable-http",
      "url": "http://localhost:8000/pincher/mcp"
    },
    "zelosmcp-aggregate": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

The web UI's **Cursor full mcp.json** panel auto-populates this with every running backend plus the aggregate. Copy from there for an exact match to your current set.

The `zelosmcp-` prefix on each entry is a convention — it's obvious in Cursor's UI which entries are zelosMCP-proxied (and there's no collision with backends you may already have configured directly).

## `.mdc` rules — the agent guidance

Cursor reads `.mdc` files from `.cursor/rules/` (per-project) or `~/.cursor/rules/` (global). They're prepended to the system prompt on every Cursor session.

zelosMCP's rule generator produces a comprehensive `.mdc` body listing every tool from every backend, with descriptions, arg summaries, and mutability markers. That gives the agent enough context to pick the right MCP tool for any task instead of falling back to shell calls.

### Generate it

The web UI has a **Cursor rule (.mdc)** panel that auto-refreshes whenever you toggle backends. Pick the **Tool use** and **Access** modes in the dropdowns, click **Copy**, paste into a `.mdc` file. Or `curl` the same content from `/api/cursor-rule`:

```bash
mkdir -p .cursor/rules
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only' \
  > .cursor/rules/zelosmcp.mdc
```

For global use:

```bash
mkdir -p ~/.cursor/rules
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only' \
  > ~/.cursor/rules/zelosmcp.mdc
```

The rule is dynamic — re-fetch any time you start/stop backends or add new ones.

### What's in the body

```
---
description: zelosMCP backend tool catalog (read-only mode)
alwaysApply: true
---

# zelosMCP backend tool catalog

Generated from the zelosMCP aggregator at `http://localhost:8000/mcp`. Every tool below is reachable as `<server>__<tool>` (double underscore) on that single Cursor entry. ...

Currently-loaded backends: `pincher`, `filesystem`, `docker`, `kubernetes`. (Plus any others you've added to your config.)

## Access mode: READ-ONLY

**Do not call** any tool tagged `[mutates]`, `[destructive]`, or `[?]`. ...

## Mutability markers

- `[readonly]` — pure inspection (server declares `readOnlyHint: true`).
- `[mutates]` — changes backend state (e.g. file edits, container start).
- `[destructive]` — irreversible mutation (e.g. delete pod, remove file).
- `[?]` — mutability not declared by the server; treat as mutating.

## Tool naming convention

Tool, prompt, and resource names at the aggregate `/mcp` are `<server>__<original>` (double underscore). ...

## `filesystem`

`filesystem` exposes 14 tools via the aggregator at `/mcp` (namespaced `filesystem__<tool>`). ...

- `filesystem__read_text_file` `(path, head?, tail?)` [readonly]
  Read complete contents of a file as text
- `filesystem__edit_file` `(path, edits, dryRun?)` [destructive]
  Make selective edits using advanced pattern matching
- ...

## `pincher`

...

## Don't do this

- Don't call `tools/list` between every step; the set is stable for the lifetime of the session.
- Don't reach for shell tools (`bash`, `python -c`, etc.) for tasks the MCP backends cover ...
```

Every tool gets one line: `<qualified-name> (arg-summary) [marker]` followed by the description. Arg summaries are extracted from the tool's `inputSchema`; required args first, then optionals with `?`. Mutability markers come from MCP annotations + a name-prefix fallback (see [built-in-mcp.md](built-in-mcp.md) for the full classification logic).

### `access`: read-only vs read-write

The `access` query param flips a single block at the top of the rule.

| Mode | Directive | Use when |
|---|---|---|
| `read-only` (default) | "**Do not call** any tool tagged `[mutates]`, `[destructive]`, or `[?]`." | Code review, demos, Q&A — anywhere the agent should only inspect. |
| `read-write` | "Tools tagged `[mutates]` and `[destructive]` change backend state. Confirm with the user before calling `[destructive]` tools." | Pair-programming, agent-driven feature work, anywhere mutation is the goal. |

Toggle in the web UI dropdown, or pass `?access=read-write` to the endpoint:

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-write' \
  > .cursor/rules/zelosmcp.mdc
```

The body content (per-tool entries) is identical between modes — only the directive header changes. So an agent loading a read-write rule still has the same tool catalog; it's just allowed to use it.

### `tool_use`: priority vs available

The `tool_use` query param controls how aggressively the rule pushes the agent toward MCP tools.

| Mode | What gets added | Use when |
|---|---|---|
| `priority` (default) | "Prefer MCP tools over shell" directive plus a curated **Mandatory backend playbook** section with detailed workflow guidance for `filesystem` and `pincher` (filtered by `access`). | Default — you actually want Cursor to lean on the MCP tools instead of shelling out, and you want the agent oriented toward the canonical pincher / filesystem workflow. |
| `available` | Pure neutral catalog. No prioritization directive, no playbook, no "prefer these over shelling out" phrasing in the per-backend headers. | You want the rule to stay informational only — no opinion on which tools the agent should reach for first. |

Toggle in the web UI dropdown, or pass `?tool_use=available`:

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?tool_use=available' \
  > .cursor/rules/zelosmcp.mdc
```

The mandatory backend playbook in `priority` mode is filtered by `access`: in `read-only` mode it lists only the inspection tools and explicitly forbids the mutating ones (`write_file`, `edit_file`, `pincher__index`, `pincher__fetch`, `pincher__adr` set/delete). In `read-write` mode it adds the full workflow including `edit_file` vs `write_file` guidance and the `adr`/`fetch` knowledge-store loop.

### `style`: always-apply vs scoped

Cursor `.mdc` files have two activation modes via frontmatter. The generator supports both.

**`style=always-apply`** (default):

```
---
description: zelosMCP backend tool catalog (read-only mode)
alwaysApply: true
---
```

The rule applies to every Cursor session in the workspace.

**`style=scoped`**:

```
---
description: zelosMCP backend tool catalog (read-only mode)
globs: **/*.py
alwaysApply: false
---
```

The rule activates only when files matching `globs` are open. Useful when the rule's tool guidance is only relevant for certain file types, or when you have multiple competing rules and want to avoid them all firing at once.

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?style=scoped&globs=**/*.py' \
  > .cursor/rules/zelosmcp-python.mdc
```

`globs` defaults to `**/*` if not specified — the rule still activates everywhere but stays in `alwaysApply: false` mode (which Cursor treats subtly differently in some cases).

## A few canonical workflows

### "I just want it to work"

```bash
# Web UI: copy the aggregated mcp.json snippet into ~/.cursor/mcp.json,
# copy the read-only rule into ~/.cursor/rules/zelosmcp.mdc.
# Reload Cursor. Done.
```

### "I want a per-project rule that scopes to my Python code"

```bash
mkdir -p .cursor/rules
curl -fsSL 'http://localhost:8000/api/cursor-rule?style=scoped&globs=**/*.py' \
  > .cursor/rules/zelosmcp-python.mdc
```

### "I'm doing agent-driven feature work — let it edit files"

```bash
# Switch the Web UI dropdown to Read-write, hit Copy.
# Or curl:
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-write' \
  > .cursor/rules/zelosmcp.mdc
```

### "I added a new backend — refresh the rule"

The rule is a snapshot. Re-fetch:

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only' \
  > .cursor/rules/zelosmcp.mdc
```

…or just reopen the **Cursor rule (.mdc)** panel in the web UI — it auto-refreshes whenever the backend set changes.

## Bulk-installing the rule into discovered repos

The web UI's **Repositories** panel (right column, collapsed by default) walks `/user_data_ro` for git repos and lets you write `.cursor/rules/zelosmcp.mdc` directly into any of them with one click — same generator, same dropdowns, just persisted to disk via the running `filesystem` MCP. The same panel also exposes a `Index in pincher` button so the repo's symbols become queryable straight away. See [docs/repositories.md](repositories.md).

## See also

- [built-in-mcp.md](built-in-mcp.md) — the `zelosmcp__generate_cursor_rule` MCP tool (same generator, called via MCP), the inline catalog UI, the `/catalog` standalone page.
- [repositories.md](repositories.md) — write the generated rule into any discovered git repo without leaving the UI.
- [vscode-integration.md](vscode-integration.md) — same workflow for VSCode + GitHub Copilot.
- [http-api.md](http-api.md) — full reference for `/api/cursor-rule`.
