# Quickstart

Get zelosMCP running and wired into your IDE in five minutes.

## Step 1 — Start zelosMCP

If you have Docker, this is a single command:

```bash
make init-env       # optional one-time wizard: writes .env (USER_DATA_ROOT, ports, etc.)
make up             # build image (if missing) + start container + load default backends
```

`make up` chains image build (if missing) → container start → `load` (POSTs the default backend config) → `index` (warms pincher's index for the current repo) → `rule` (writes the Cursor `.mdc` rule file). The result: open [http://localhost:8000](http://localhost:8000) and the web UI shows three default backends running ([pincher](default-mcps.md#pincher), [docker](default-mcps.md#docker), [kubernetes](default-mcps.md#kubernetes)).

`make init-env` is optional — `make up` works against the Makefile defaults if you skip it.

> Behind a corporate TLS-intercepting proxy on macOS? `make init-env` auto-detects the corp cert in your keychain and points `DOCKERFILE` at the cert-aware multi-stage build. See [setup-rancher-desktop.md](setup-rancher-desktop.md) for the full detail.

> No Docker available? See [quickstart-no-docker.md](quickstart-no-docker.md) for the Python pip install path.

## Step 2 — Wire your IDE

### Cursor

In the zelosMCP web UI, copy the **Cursor mcp.json (aggregated)** snippet into `.cursor/mcp.json` (per-project) or `~/.cursor/mcp.json` (global):

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

The Cursor `.mdc` rule was already written for you by `make up` (at the path you picked in `make init-env`, default `.cursor/rules/zelosmcp.mdc`). It teaches the agent about every tool zelosMCP's aggregator exposes — so the agent picks `pincher__search` over recursive `grep`, `filesystem__edit_file` over `sed`, etc. Reload Cursor and you're done.

Full reference (rule generator parameters, scoped rules, IDE specifics): [cursor-integration.md](cursor-integration.md).

### VSCode + GitHub Copilot

VSCode uses a slightly different MCP config shape. Place this in `.vscode/mcp.json` (per-project) or via Command Palette → `MCP: Open User Configuration`:

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

For Copilot agent guidance, generate a `copilot-instructions.md`:

```bash
mkdir -p .github
curl -fsSL 'http://localhost:8000/api/cursor-rule?format=copilot-instructions' \
  > .github/copilot-instructions.md
```

Reload VSCode (Cmd+Shift+P → `Developer: Reload Window`) and Copilot has access to every zelosMCP tool plus the rule guiding which to use.

Full reference: [vscode-integration.md](vscode-integration.md).

## Step 3 — Try it out

Open a new chat in your IDE. Ask:

- **"Show me every container running on my Docker daemon."** — agent calls `docker__list_containers`.
- **"Find every Python file that imports `asyncio` in this repo."** — agent calls `pincher__search` against the pre-built symbol index.
- **"Summarize this codebase."** — agent calls `pincher__architecture`.

If the agent doesn't pick the MCP tool, the rule may not have loaded — check the file lives at the path you configured (`.cursor/rules/zelosmcp.mdc` per-project or `~/.cursor/rules/zelosmcp.mdc` global) and reload the IDE.

## When to refresh the rule

The `.mdc` is a snapshot of the catalog at generation time. It auto-refreshes whenever you `make load` (or `make up`, which chains `load`). For ad-hoc regeneration after editing config or toggling backends manually, run `make rule` standalone.

To switch between read-only and read-write tool access (controls whether the agent is allowed to call `[mutates]` and `[destructive]` tools), re-run `make init-env` — or set `ZELOSMCP_RULE_ACCESS=read-only` in `.env` and `make rule` again. See [cursor-integration.md](cursor-integration.md#access-read-only-vs-read-write) for the access-mode reference.

## Where next

Open issues you might hit (Docker socket on Rancher Desktop without admin, kubeconfig + bridge networking, etc.) live in [setup-rancher-desktop.md](setup-rancher-desktop.md). The README's [Documentation table](../README.md#documentation) indexes everything else.
