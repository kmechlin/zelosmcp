# Quickstart

Get LocalMCP running and wired into your IDE in five minutes. Two parallel paths below — pick the one that matches your IDE.

## Step 1 — Start LocalMCP

Two ways to run it; pick whichever matches your environment.

**As a Python process** (works anywhere with Python 3.10+):

```bash
pip install -e .
localmcp
```

**As a Docker container** (recommended on macOS — bundles Node.js, `uv`, `git`, and persistent volumes for the npx/uv caches):

```bash
make localmcp-up        # starts the container
make localmcp-load      # POSTs configs/default-localmcp.json into it
```

Either way, open `http://localhost:8000` to confirm the web UI is up. The four default backends ([kubernetes, filesystem, pincher, docker](default-mcps.md)) should be running.

> Behind a corporate TLS-intercepting proxy on macOS? See [setup-rancher-desktop.md](setup-rancher-desktop.md) for the cert-aware build path.

## Step 2 — Wire your IDE into LocalMCP

### Path A — Cursor

In the LocalMCP web UI, find the **Cursor mcp.json (aggregated)** panel and click **Copy**. The snippet looks like:

```json
{
  "mcpServers": {
    "localmcp-aggregate": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Paste it into:

- **Per-project**: `.cursor/mcp.json` in your repo root, or
- **Globally**: `~/.cursor/mcp.json`

Cursor picks it up on next reload.

Now scroll to the **Cursor rule (.mdc)** panel, leave the access dropdown on **Read-only (safe)**, and click **Copy**. Save the body to your project as `.cursor/rules/localmcp.mdc`:

```bash
mkdir -p .cursor/rules
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only' \
  > .cursor/rules/localmcp.mdc
```

This rule tells the Cursor agent every tool the aggregator exposes, with arg summaries and mutability markers — so it knows to prefer `filesystem__edit_file` over `sed`, `pincher__search` over recursive `grep`, etc.

Full walkthrough: [cursor-integration.md](cursor-integration.md).

### Path B — VSCode + GitHub Copilot

VSCode uses a slightly different MCP config shape (`servers` key, `type: "http"`):

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

Place it in:

- **Per-project**: `.vscode/mcp.json` in your repo root, or
- **Globally**: Command Palette → `MCP: Open User Configuration`

VSCode will prompt you to trust the server the first time it connects. Confirm and Copilot has access to every tool LocalMCP exposes.

For agent guidance, generate a Copilot custom-instructions file (same body as the Cursor rule, no YAML frontmatter):

```bash
mkdir -p .github
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only&format=copilot-instructions' \
  > .github/copilot-instructions.md
```

Full walkthrough: [vscode-integration.md](vscode-integration.md).

## Step 3 — Try it out

Open a new chat in your IDE. Ask:

- **"Show me every container running on my Docker daemon."** Agent should call `docker__list_containers` (or `list_containers` in raw passthrough mode).
- **"Find every Python file that imports `asyncio`."** Agent should reach for `pincher__search` (FTS5 query against the pre-built symbol index).
- **"Read the contents of the README and summarize."** Agent should use `filesystem__read_text_file`.

If it doesn't pick the MCP tool, the rule may not be loaded — verify the file lives in `.cursor/rules/` (Cursor) or `.github/` (VSCode) and reload the IDE.

## Step 4 — Re-generate the rule when backends change

The rule is a snapshot of the catalog at the time you generated it. When you start/stop backends or add new ones, regenerate:

```bash
# Cursor users
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only' \
  > .cursor/rules/localmcp.mdc

# VSCode users
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only&format=copilot-instructions' \
  > .github/copilot-instructions.md
```

Or just reopen the **Cursor rule (.mdc)** panel in the web UI — it auto-refreshes whenever you toggle backends, so re-clicking Copy is enough.

## Step 5 — When to switch to read-write mode

Read-only is the default because it's safe: the rule body explicitly forbids the agent from calling tools that may mutate state (file edits, container starts, pod deletes, etc.). Switch the dropdown (or `?access=read-write`) when you actively want the agent making changes — for example, when pair-programming a feature where the agent should be editing files freely.

[cursor-integration.md](cursor-integration.md) and [vscode-integration.md](vscode-integration.md) walk through the access modes in detail.

## Where next

| You want to... | Read |
|---|---|
| Understand what's happening under the hood | [architecture.md](architecture.md) |
| Set up Rancher Desktop for the Docker socket | [setup-rancher-desktop.md](setup-rancher-desktop.md) |
| Customize the volume mounts on the LocalMCP container | [makefile.md](makefile.md) |
| Add your own MCP backends to the loaded set | [configuration.md](configuration.md) |
| Learn what the four default backends do | [default-mcps.md](default-mcps.md) |
| Inspect the live tool catalog | [built-in-mcp.md](built-in-mcp.md) (the `/catalog` page) |
