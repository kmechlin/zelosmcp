# VSCode + GitHub Copilot integration

VSCode (with the GitHub Copilot extension's agent mode) speaks MCP and can consume zelosMCP just like Cursor can. The mechanics are identical to [cursor-integration.md](cursor-integration.md) — same `/api/cursor-rule` generator, same aggregator at `/mcp`. Only the wrapper file format and the IDE config-key spelling differ.

Read [cursor-integration.md](cursor-integration.md) first if you haven't; this page just lists the differences.

## Three substitutions from Cursor

| Concept | Cursor | VSCode + Copilot |
|---|---|---|
| MCP config — top-level key | `mcpServers` | `servers` |
| MCP config — HTTP transport `type` | `streamable-http` | `http` |
| MCP config — workspace path | `.cursor/mcp.json` | `.vscode/mcp.json` |
| MCP config — user/global | `~/.cursor/mcp.json` | Command Palette → `MCP: Open User Configuration` |
| Instructions file path | `.cursor/rules/*.mdc` | `.github/copilot-instructions.md` |
| Instructions file format | Markdown with YAML frontmatter (`alwaysApply`, `globs`) | Plain markdown, no frontmatter |
| Generator query param | `format=cursor-mdc` (default) | `format=copilot-instructions` |

Both IDEs talk to the same `http://localhost:8000/mcp` endpoint and consume the same tool catalog.

## `mcp.json`

Aggregated entry — the recommended default:

```json
{
  "servers": {
    "zelosmcp-aggregate": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

VSCode prompts you to trust the server the first time it connects (a small banner in the chat view). Confirm and Copilot has access to every tool zelosMCP exposes.

> **Trust caveat:** if you start the MCP server directly from the `mcp.json` file (via the inline action), VSCode skips the trust prompt. The first-connection prompt is the canonical safety check; re-trigger via `MCP: Reset Trust` if you accidentally skipped it.

For per-backend entries (raw passthrough), see the equivalent [Cursor section](cursor-integration.md#per-backend-entries-raw-passthrough) and convert the keys per the table above.

### Merge semantics (safe upsert)

When zelosMCP pushes configs to a repo (via the dashboard, the `/api/push` endpoint, or `make push`), it **never overwrites** an existing `.vscode/mcp.json`. Instead it performs a safe merge:

1. Reads the existing `.vscode/mcp.json` from disk.
2. Parses it as JSON. If the file is missing, empty, or corrupt, a fresh file is written.
3. Upserts only the `zelosmcp-aggregate` entry under the `servers` key.
4. **Preserves** all other user-added server entries, `inputs` blocks, and any other top-level keys.

For example, if your existing `mcp.json` contains:

```json
{
  "inputs": [{ "id": "api_key", "type": "promptString" }],
  "servers": {
    "my-custom-server": { "type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"] }
  }
}
```

After a push, the file becomes:

```json
{
  "inputs": [{ "id": "api_key", "type": "promptString" }],
  "servers": {
    "my-custom-server": { "type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"] },
    "zelosmcp-aggregate": { "type": "http", "url": "http://localhost:8000/mcp" }
  }
}
```

Your `my-custom-server` entry and the `inputs` block are untouched.

> **`ZELOSMCP_PUBLIC_URL`:** If zelosMCP runs behind a reverse proxy, set this env var (e.g. `https://mcp.example.com`). The pushed `url` will be `$ZELOSMCP_PUBLIC_URL/mcp` instead of the localhost default.

## Push targets

When a push targets `"vscode"`, the following files are written to the repo:

| File | Content | Write mode |
| --- | --- | --- |
| `.vscode/mcp.json` | Aggregator server entry | **Merge** — user entries preserved |
| `.github/copilot-instructions.md` | Tool catalog + playbooks (plain markdown) | Overwrite |
| `.vscode/copilot-instructions.md` | Same as above (VS Code also reads from here) | Overwrite |
| `.vscode/zelosmcp.json` | Push metadata (targets, access, timestamps) | Overwrite |
| `.github/zelosmcp.json` | Same push metadata | Overwrite |

The instructions files (`.github/copilot-instructions.md` and `.vscode/copilot-instructions.md`) are always overwritten because they are fully generated from the current backend catalog — there is no user-authored content to preserve.

Push defaults to both `cursor` and `vscode` targets. To push only VS Code configs:

```bash
curl -X POST http://localhost:8000/api/push \
  -H 'Content-Type: application/json' \
  -d '{"targets": ["vscode"]}'
```

## `copilot-instructions.md`

Generate from the same `/api/cursor-rule` endpoint with `format=copilot-instructions`:

```bash
mkdir -p .github
curl -fsSL 'http://localhost:8000/api/cursor-rule?format=copilot-instructions' \
  > .github/copilot-instructions.md
```

The body is byte-identical to the Cursor `.mdc` body — just no YAML frontmatter wrapper. Every parameter ([cursor-integration.md](cursor-integration.md#access-read-only-vs-read-write) covers them: `access`, `tool_use`) works the same way; `style` and `globs` are silently ignored when `format=copilot-instructions` because Copilot uses its own scoping mechanism.

For agent-driven mutation work, swap to `read-write`:

```bash
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-write&format=copilot-instructions' \
  > .github/copilot-instructions.md
```

## Per-glob Copilot instructions (`applyTo:`)

Copilot has a `.github/instructions/*.instructions.md` directory for guidance scoped to particular file patterns. zelosMCP's generator doesn't natively emit this format — wrap the body manually:

```bash
mkdir -p .github/instructions
{
  printf -- '---\napplyTo: "**/*.py"\n---\n\n'
  curl -fsSL 'http://localhost:8000/api/cursor-rule?format=copilot-instructions'
} > .github/instructions/zelosmcp-python.instructions.md
```

## Workflows

### "Just want it to work"

```bash
# 1. Start zelosMCP (e.g. `make up`).
# 2. Push VS Code configs — this auto-creates/merges .vscode/mcp.json
#    and writes .github/copilot-instructions.md:
curl -X POST http://localhost:8000/api/push \
  -H 'Content-Type: application/json' \
  -d '{"targets": ["vscode"]}'
# 3. Reload VSCode (Command Palette: `Developer: Reload Window`).
```

Alternatively, generate the instructions file manually:

```bash
mkdir -p .github
curl -fsSL 'http://localhost:8000/api/cursor-rule?format=copilot-instructions' \
  > .github/copilot-instructions.md
```

And create `.vscode/mcp.json` by hand (see the `mcp.json` section above).

### "I want this in source control with my repo"

Same as above but commit `.vscode/mcp.json` and `.github/copilot-instructions.md`. Anyone cloning the repo + running zelosMCP at `http://localhost:8000` inherits the same setup. The `.vscode/mcp.json` merge guarantees that teammates' custom server entries won't be overwritten on subsequent pushes.

### "I added a new backend — refresh"

Re-run the push command (or `make push` if configured). The instructions files are regenerated with the updated catalog. `.vscode/mcp.json` is merged (no change needed — the aggregator entry points to the same `/mcp` endpoint). Reload VSCode to pick up the new instructions.

## Sandboxing

VSCode's MCP-server sandboxing only applies to stdio entries (`command:`/`args:`). zelosMCP is reached over `type: http`, so the sandbox doesn't apply. Restrict trust at the zelosMCP config level instead — only load backends you trust, and read [makefile.md](makefile.md#volume-mounts-zelosmcp_volumes_file) on the Docker-socket security tradeoff before enabling the `docker` backend.

## Verifying the integration

Once you've set up `.vscode/mcp.json` and started the server, confirm everything is working:

1. **Check the Output panel.** Go to **View → Output**, then select **"MCP"** from the dropdown. If "MCP" doesn't appear in the list, VSCode hasn't connected to any MCP server yet — see Troubleshooting below.
2. **List connected servers.** Open the Command Palette (`Cmd+Shift+P` / `Ctrl+Shift+P`) and run **`MCP: List Servers`**. You should see `zelosmcp-aggregate` with a connected status.
3. **Watch for tool call badges.** When Copilot uses an MCP tool, it displays a tool-call badge in the chat response (e.g. "Used `filesystem__read_text_file`"). If you see it using built-in tools like `Read file` or running `cat` in the terminal for tasks the MCP backends cover, the instructions may not be loaded.
4. **Ask Copilot directly.** In chat, ask: *"What MCP tools do you have available?"* — the response should list the zelosMCP-namespaced tools.
5. **Check stats.** Ask Copilot to call `pincher__stats` — if `tokens_saved` is 0 after a work session, the agent is bypassing the MCP tools.

## Troubleshooting

### No "MCP" entry in the Output panel

The MCP output channel only appears after VSCode successfully connects to at least one MCP server.

1. **Verify the server is running:**

   ```bash
   curl -s http://localhost:8000/mcp | head -c 200
   ```

   If this fails, start zelosMCP first.
2. **Verify `.vscode/mcp.json` exists** at the workspace root with the correct content (see the `mcp.json` section above).
3. **Reload VSCode:** Command Palette → `Developer: Reload Window`. VSCode reads `mcp.json` at startup; changes require a reload.

### `MCP: List Servers` shows disconnected / not found

- Confirm the URL in `.vscode/mcp.json` matches where the server is actually running.
- Check for port conflicts — another process may be using port 8000.
- If running behind a reverse proxy, ensure `ZELOSMCP_PUBLIC_URL` is set correctly.

### No MCP commands in the Command Palette

MCP support requires **VSCode 1.99+** (April 2025 release). Update VSCode if you don't see any `MCP:` commands.

### `servers` vs `mcpServers`

Easy mistake when copy-pasting a Cursor snippet. VSCode uses `"servers"` as the top-level key; `"mcpServers"` is the Cursor key. VSCode **silently ignores** the wrong key — no error, just no servers.

### `type: "streamable-http"` vs `type: "http"`

VSCode uses `"http"` for streamable HTTP transport. `"streamable-http"` is the Cursor-specific value and is rejected by VSCode.

### Copilot ignores the instructions file

- Confirm `.github/copilot-instructions.md` is at the **repo root's** `.github/` directory (not nested deeper).
- Check that the VS Code setting `github.copilot.chat.codeGeneration.useInstructionFiles` is `true` (this is the default).
- Reload VSCode after changing the file — Copilot caches instructions on startup.
- Note: the instructions are a **soft preference**, not a hard policy. Copilot may still use built-in tools when it judges them more appropriate.

### Push overwrote my `mcp.json`

This should not happen — zelosMCP uses merge semantics (see *Merge semantics* above). If it did happen, check that you are running a version with the merge logic (introduced in this release). The merge only touches the `zelosmcp-aggregate` entry under `servers`.

## See also

- [cursor-integration.md](cursor-integration.md) — canonical reference for the rule generator parameters (this page links into it for shared content).
- [built-in-mcp.md](built-in-mcp.md) — the `zelosmcp__generate_cursor_rule` MCP tool that backs `/api/cursor-rule`.
- [VSCode MCP docs](https://code.visualstudio.com/docs/copilot/customization/mcp-servers) — upstream Microsoft documentation.
