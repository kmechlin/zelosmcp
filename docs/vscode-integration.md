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
# 1. Open Command Palette, run `MCP: Open User Configuration`.
#    Paste the aggregated `servers` JSON above.
# 2. The instructions file was already written by `make up`
#    (which chains `make rule`). It lives at ZELOSMCP_RULE_FILE,
#    default .cursor/rules/zelosmcp.mdc — but Copilot reads from
#    .github/copilot-instructions.md. Generate it once:
mkdir -p .github
curl -fsSL 'http://localhost:8000/api/cursor-rule?format=copilot-instructions' \
  > .github/copilot-instructions.md
# 3. Reload VSCode (Command Palette: `Developer: Reload Window`).
```

### "I want this in source control with my repo"

Same as above but commit `.vscode/mcp.json` and `.github/copilot-instructions.md`. Anyone cloning the repo + running zelosMCP at `http://localhost:8000` inherits the same setup.

### "I added a new backend — refresh"

`make load` (auto-chained from `make up`) regenerates the Cursor `.mdc` only. For Copilot, re-run the curl above. Reload VSCode for Copilot to pick up the change.

## Sandboxing

VSCode's MCP-server sandboxing only applies to stdio entries (`command:`/`args:`). zelosMCP is reached over `type: http`, so the sandbox doesn't apply. Restrict trust at the zelosMCP config level instead — only load backends you trust, and read [makefile.md](makefile.md#volume-mounts-zelosmcp_volumes_file) on the Docker-socket security tradeoff before enabling the `docker` backend.

## Common gotchas

- **`servers` vs `mcpServers`.** Easy mistake when copy-pasting a Cursor snippet into VSCode. VSCode silently ignores the wrong key.
- **`type: "streamable-http"` vs `type: "http"`.** VSCode rejects the Cursor-style `streamable-http` value.
- **Copilot ignores instructions.** Make sure `.github/copilot-instructions.md` is at the repo root's `.github/` (not nested), and Copilot's "Use repository instructions" setting is on (default). Reload VSCode to pick up file changes.

## See also

- [cursor-integration.md](cursor-integration.md) — canonical reference for the rule generator parameters (this page links into it for shared content).
- [built-in-mcp.md](built-in-mcp.md) — the `zelosmcp__generate_cursor_rule` MCP tool that backs `/api/cursor-rule`.
- [VSCode MCP docs](https://code.visualstudio.com/docs/copilot/customization/mcp-servers) — upstream Microsoft documentation.
