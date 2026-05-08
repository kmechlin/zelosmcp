# Quickstart without Docker

The recommended way to run zelosMCP is the Docker container ([quickstart.md](quickstart.md)) — it bundles every runtime the default backends need (Node.js + `npx`, Python + `uv`/`uvx`, the `pincher` Go binary), persistent caches, and the `/user_data_ro` / `/user_data_rw` mounts pincher and filesystem backends rely on.

If Docker isn't an option, you can still run the zelosMCP web server as a Python process and POST your own backend config. The trade-off: any backend whose runtime isn't on your host PATH will fail to spawn.

## Install + run

```bash
git clone https://github.com/nike-qre/zelosmcp
cd zelosmcp

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

zelosmcp                    # serves http://localhost:8000
```

`zelosmcp` is the console-script entry point declared in [`pyproject.toml`](../pyproject.toml). It boots the Starlette app on `:8000` and serves the always-on built-in MCP at `/zelosmcp/mcp` and the (empty) aggregator at `/mcp`. No user backends are loaded yet.

Open [http://localhost:8000](http://localhost:8000) — the web UI's **Configuration** textarea is your control plane. Paste an `mcpServers` JSON config and click **START**.

## What works without Docker

| Backend | Runtime needed on host | Works without Docker? |
|---|---|---|
| The aggregator + built-in MCP at `/mcp` and `/zelosmcp/mcp` | Python 3.10+ only | Yes — these are in-process. |
| `pincher` (codebase intelligence) | The `pincher` binary on `PATH`. Build from [pincherMCP](https://github.com/kwad77/pincherMCP) (Go 1.24+) and `cp pincher /usr/local/bin/`. | Yes, once installed. |
| `docker` ([mcp-server-docker](https://github.com/ckreiling/mcp-server-docker)) | `uvx` on `PATH` (`pip install uv`) plus the Docker daemon (which kind of defeats the point of "no Docker"). | Possible but contradictory. |
| `kubernetes` ([kubernetes-mcp-server](https://github.com/manusa/kubernetes-mcp-server)) | `npx` on `PATH` (Node.js 22+) plus a reachable kubeconfig. | Yes. |
| `filesystem` ([@modelcontextprotocol/server-filesystem](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)) | `npx` on `PATH`. | Yes. |
| Any other stdio MCP from the [catalog](https://github.com/modelcontextprotocol/servers) | Whatever runtime that server expects. | Depends. |

The container ships Node.js 22, `uv`/`uvx`, and the pincher binary preinstalled — that's the value `make up` provides. On the host you need to install these yourself.

## Minimum-viable host config

If you only want the always-on built-in MCP at `/zelosmcp/mcp` (rule generator, catalog inspector, server toggles), run `zelosmcp` and don't load any backends. The web UI works, the `/api/cursor-rule` endpoint generates rules describing whatever's currently loaded (initially: just the built-in), and `/mcp` aggregates the seven `zelosmcp__*` tools.

## Loading a backend manually

Sample minimal config (filesystem only — pure Node.js, easiest to bootstrap):

```bash
cat > /tmp/my-config.json <<'JSON'
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "${HOME}"]
    }
  }
}
JSON

curl -sS -X POST http://localhost:8000/api/start \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/my-config.json
```

The command and arg list runs as a subprocess of the zelosMCP Python process. Whatever `npx -y @modelcontextprotocol/server-filesystem $HOME` resolves to on your host, that's what runs. Same for `uvx`-launched servers.

## What you lose vs. the Docker path

- **Persistent caches.** The container's named volumes (`zelosmcp-npm`, `zelosmcp-cache`, `zelosmcp-pincher`, `zelosmcp-savings`) survive restarts. On the host, the npx/uv caches are wherever your user's home dir puts them — fine in practice, just less explicit.
- **Bundled pincher binary.** You'll need to install pincher yourself.
- **`/user_data_ro` + `/user_data_rw` mounts.** Backends that expected those container-relative paths (e.g. the default `filesystem` config arg `/user_data_rw`) need to be rewritten to host-relative paths. Substitute your actual home or workspace dir.
- **Kubeconfig bridge networking.** `make up` adds a `zelosmcp` cluster + context to your kubeconfig pointing at `host.docker.internal:6443` because the container can't reach `127.0.0.1:6443` directly. On the host, `127.0.0.1:6443` works as-is — use whatever context you already have.
- **`make` lifecycle targets.** `make up`, `make down`, `make logs`, etc. all assume the Docker container. On the host you manage the `zelosmcp` Python process directly (Ctrl+C, `kill`, supervisor of choice).

## When you're ready to switch

Just install Docker (or [Rancher Desktop](setup-rancher-desktop.md) on macOS), then follow [quickstart.md](quickstart.md). zelosMCP's behaviour is identical — same endpoints, same aggregator, same rule generator. Only the runtime sourcing changes.
