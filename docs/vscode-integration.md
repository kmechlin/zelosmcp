# VSCode + GitHub Copilot integration

VSCode (with the GitHub Copilot extension's agent mode) speaks MCP and can consume LocalMCP just like Cursor can. The integration model is the same: a config file points the IDE at LocalMCP's aggregator, and an instructions file teaches the agent which aggregated tool to reach for. Only the file shapes differ.

## The three differences from Cursor

If you've already wired LocalMCP into Cursor, the VSCode setup is mechanically identical with three substitutions:

| | Cursor | VSCode + Copilot |
|---|---|---|
| MCP config — top-level key | `mcpServers` | `servers` |
| MCP config — HTTP transport `type` | `streamable-http` | `http` |
| MCP config — workspace path | `.cursor/mcp.json` | `.vscode/mcp.json` |
| MCP config — user/global | `~/.cursor/mcp.json` | Command Palette → `MCP: Open User Configuration` |
| Instructions file path | `.cursor/rules/*.mdc` | `.github/copilot-instructions.md` |
| Instructions file format | Markdown with YAML frontmatter (`alwaysApply`, `globs`) | Plain markdown, no frontmatter |

Both IDEs talk to the same `http://localhost:8000/mcp` endpoint and consume the same tool catalog.

## `mcp.json` — the IDE-to-LocalMCP wiring

VSCode reads MCP server config from two locations:

- **Per-project**: `.vscode/mcp.json` in your repo root (shareable, version-controlled).
- **User profile**: open with Command Palette → `MCP: Open User Configuration` (applies across all your VSCode workspaces).

Or use the `MCP: Add Server` command for a guided add flow that prompts you to pick workspace vs. user scope.

### Aggregated entry (recommended)

```json
{
  "servers": {
    "localmcp-aggregate": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

VSCode prompts you to trust the server the first time it connects (a small banner in the chat view). Confirm and Copilot has access to every tool LocalMCP exposes.

> **Trust caveat:** if you start the MCP server directly from the `mcp.json` file (via the inline action), VSCode skips the trust prompt. The first-connection prompt is the canonical safety check.

### Per-backend entries

Same idea as Cursor — copy from the **Cursor full mcp.json** panel in the web UI and convert keys (`mcpServers` → `servers`, `streamable-http` → `http`):

```json
{
  "servers": {
    "localmcp-filesystem": {
      "type": "http",
      "url": "http://localhost:8000/filesystem/mcp"
    },
    "localmcp-aggregate": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

LocalMCP's web UI doesn't currently have a "VSCode mcp.json" panel — the snippets it generates are Cursor-shaped. Until that's added, the manual conversion is the workflow. (Track [the future-work notes below](#future-vscode-mcp-json-panel-in-the-web-ui).)

### Cross-IDE config sharing

VSCode supports auto-discovery of MCP server configs from other tools (Claude Desktop, etc.) via the `chat.mcp.discovery.enabled` setting. If you enable it, VSCode will pick up Cursor-shaped configs automatically too — though the reverse isn't true.

## `copilot-instructions.md` — the agent guidance

GitHub Copilot reads agent guidance from `.github/copilot-instructions.md` (per-project, single file). The file is plain markdown with no frontmatter — it's prepended verbatim to Copilot's system prompt on every chat.

LocalMCP's `/api/cursor-rule` endpoint can output a Copilot-compatible body via the `format=copilot-instructions` query param. The generator produces the same per-tool catalog body as the Cursor `.mdc` rule, just without the YAML frontmatter wrapper.

### Generate it

```bash
mkdir -p .github
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only&format=copilot-instructions' \
  > .github/copilot-instructions.md
```

For agent-driven mutation work, switch to read-write:

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-write&format=copilot-instructions' \
  > .github/copilot-instructions.md
```

The body content is identical to what the Cursor `.mdc` rule produces — every tool from every backend with description, arg summary, and `[readonly]`/`[mutates]`/`[destructive]`/`[?]` mutability marker. See [cursor-integration.md](cursor-integration.md#whats-in-the-body) for the full body shape.

### `format` is the new param

| Param | Output |
|---|---|
| `format=cursor-mdc` (default) | YAML frontmatter (`description`, `alwaysApply`, optionally `globs`) + body. Suitable for `.cursor/rules/*.mdc`. |
| `format=copilot-instructions` | Body only — no frontmatter. Suitable for `.github/copilot-instructions.md`. |

The body is byte-for-byte identical between the two formats. Only the wrapper differs.

`style` and `globs` are silently ignored when `format=copilot-instructions` because Copilot uses a different scoping mechanism (see below).

## Per-glob instructions (Copilot's `applyTo:`)

Copilot has a `.github/instructions/*.instructions.md` directory for instructions scoped to particular file patterns. The format adds an `applyTo:` frontmatter:

```markdown
---
applyTo: "**/*.py"
---

# Python guidance for the agent
... your guidance ...
```

LocalMCP's generator does **not** currently emit this format — there's no `format=copilot-scoped-instructions` (yet). If you want scoped Copilot instructions, the easiest path is to:

1. `curl ?format=copilot-instructions` for the body.
2. Wrap it manually in `applyTo:` frontmatter and save as `.github/instructions/localmcp-python.instructions.md`.

```bash
mkdir -p .github/instructions
{
  printf -- '---\napplyTo: "**/*.py"\n---\n\n'
  curl -fsSL 'http://localhost:8000/api/cursor-rule?format=copilot-instructions'
} > .github/instructions/localmcp-python.instructions.md
```

## Workflows

### "I just want it to work"

```bash
# 1. Open Command Palette, run `MCP: Open User Configuration`.
#    Paste the aggregated `servers` JSON (above).
# 2. Generate the instructions file:
mkdir -p .github
curl -fsSL 'http://localhost:8000/api/cursor-rule?format=copilot-instructions' \
  > .github/copilot-instructions.md
# 3. Reload VSCode (Command Palette: `Developer: Reload Window`).
```

Copilot now has every LocalMCP tool plus the rule guiding which to use.

### "I want this in source control with my repo"

```bash
mkdir -p .vscode .github
cat > .vscode/mcp.json <<'JSON'
{
  "servers": {
    "localmcp-aggregate": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
JSON
curl -fsSL 'http://localhost:8000/api/cursor-rule?format=copilot-instructions' \
  > .github/copilot-instructions.md
```

Commit both. Anyone cloning the repo and starting LocalMCP at `http://localhost:8000` will inherit the same setup.

### "I added a new backend — refresh the instructions"

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?format=copilot-instructions' \
  > .github/copilot-instructions.md
```

Reload VSCode for Copilot to pick up the change.

### "I added a new backend — refresh the MCP catalog without restarting"

VSCode supports the `chat.mcp.autoStart` (Experimental) setting that auto-restarts the MCP server when the config file changes. Without it, you can manually re-trigger via the Extensions view → MCP SERVERS - INSTALLED → right-click → Restart.

Note: VSCode reconnecting to LocalMCP doesn't require restarting LocalMCP itself — the LocalMCP container keeps running across VSCode reconnects. You only need to update the instructions file (which Copilot rereads on the next chat).

## Future: VSCode mcp.json panel in the web UI

LocalMCP's web UI generates Cursor-shaped `mcp.json` snippets in the **Cursor mcp.json (aggregated)** and **Cursor full mcp.json** panels. A symmetric pair of VSCode panels (with the `servers` key + `type: "http"`) is a natural follow-up — the data is the same, only the wrapper key/value pair differs. Until that ships, manually convert by replacing:

- `"mcpServers"` → `"servers"`
- `"type": "streamable-http"` → `"type": "http"`

(See the [comparison table](#the-three-differences-from-cursor) above.)

## Sandboxing (macOS / Linux only)

VSCode supports MCP-server sandboxing on macOS and Linux that restricts what stdio servers can read/write. Since LocalMCP is reached over HTTP rather than spawned as a subprocess by VSCode, the sandbox doesn't apply — sandboxing is only for `command:`/`args:` (stdio) entries in `mcp.json`, not for `type: http` ones.

The right place to think about restricting LocalMCP is at the LocalMCP config level: only load backends you actually trust, and read [makefile.md](makefile.md#security-note-on-docker_sock_file) on the Docker-socket security tradeoff before enabling the `docker` backend.

## Common gotchas

- **`servers` vs `mcpServers`.** Easy mistake when copy-pasting a Cursor snippet into VSCode. VSCode silently ignores the wrong key.
- **`type: "streamable-http"` vs `type: "http"`.** VSCode rejects the Cursor-style `streamable-http` value. Use `http`.
- **Trust prompt skipped.** If you used the inline action in `mcp.json` to start the server, VSCode never prompted you to trust it. That's a known VSCode warning ([MCP server trust](https://code.visualstudio.com/docs/copilot/customization/mcp-servers#_mcp-server-trust)) — re-trigger the prompt by running `MCP: Reset Trust`.
- **Copilot ignores instructions.** Make sure `.github/copilot-instructions.md` is in the right place (repo root's `.github/`, not somewhere nested), and that Copilot's "Use repository instructions" setting is on (default). Reload VSCode to pick up file changes.

## See also

- [cursor-integration.md](cursor-integration.md) — the equivalent walkthrough for Cursor.
- [built-in-mcp.md](built-in-mcp.md) — the rule generator under the hood + `localmcp__generate_cursor_rule` MCP tool.
- [http-api.md](http-api.md) — full `/api/cursor-rule` parameter reference including `format`.
- [VSCode MCP docs](https://code.visualstudio.com/docs/copilot/customization/mcp-servers) — upstream Microsoft documentation.
