# LocalMCP

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

Wrap one or more MCP servers and re-expose them on stable local URLs via Streamable HTTP.

LocalMCP runs a single web server. You point it at any number of MCP servers — stdio commands, SSE endpoints, or Streamable HTTP URLs — and each one gets a fixed local address: `http://localhost:8000/<name>/mcp`. The bare `http://localhost:8000/mcp` is an aggregator endpoint where tools and prompts from every running backend are served under a `<server>__<name>` namespace, so a single Cursor entry can talk to all of them at once.

## Install

```
pip install -e .
```

Requires Python 3.10+.

## Run

```
localmcp
```

Then open [http://localhost:8000](http://localhost:8000) in your browser. The interactive API explorer lives at [http://localhost:8000/docs](http://localhost:8000/docs) (Swagger UI) and [http://localhost:8000/redoc](http://localhost:8000/redoc).

## Cursor Setup

Two equivalent options — pick whichever you prefer.

**Option A — single aggregated entry** (simplest):

```json
{
  "mcpServers": {
    "aggregate": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Cursor sees the union of every running backend's tools, prompts, and resources. Tool and prompt names are prefixed `<server>__<name>` (double underscore — for example `filesystem__read_file`, `github__search_repos`). Resource URIs keep their original form; reads are routed back to the originating backend automatically.

**Option B — one entry per backend** (raw passthrough; preserves original tool names):

```json
{
  "mcpServers": {
    "filesystem": {
      "type": "streamable-http",
      "url": "http://localhost:8000/filesystem/mcp"
    },
    "github": {
      "type": "streamable-http",
      "url": "http://localhost:8000/github/mcp"
    }
  }
}
```

The web UI generates the right snippet automatically based on what you've started — copy from there for an exact match.

## Configuration

LocalMCP accepts the same `mcpServers` shape Cursor uses in its own `mcp.json`. No extra fields are required — `/mcp` aggregation is automatic.

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "github": {
      "type": "streamable-http",
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": { "Authorization": "Bearer $GITHUB_TOKEN" }
    },
    "linear": {
      "type": "sse",
      "url": "https://mcp.linear.app/sse"
    }
  }
}
```

Per-server fields, matching Cursor exactly:

- **Stdio** (presence of `command`): `command`, `args`, `env`, `cwd`.
- **SSE** (`"type": "sse"`): `url`, `headers`.
- **Streamable HTTP** (`"type": "streamable-http"`): `url`, `headers`.

A few names are reserved because they collide with built-in routes: `api`, `mcp`, `docs`, `redoc`, `openapi.json`, `static`.

> **Deprecated:** the old top-level `primaryMCP` field is still parsed for backward compatibility but no longer affects routing — `/mcp` always aggregates every running server. A one-line warning is logged whenever a config still includes it.

## Usage

1. Open the web UI at `http://localhost:8000`.
2. Paste a multi-server config into the textarea (the format above).
3. Click **Start**. Each server is mounted at `http://localhost:8000/<name>/mcp` (raw passthrough), and the aggregator goes live at `http://localhost:8000/mcp`.
4. The **Cursor mcp.json** snippet below the config refreshes itself with one entry per running server plus an `aggregate` entry. Copy it into your Cursor config.
5. Use the per-row **Start** / **Stop** buttons to toggle individual servers, or **Stop All** to tear everything down.

## API

The full HTTP API is documented at [`/docs`](http://localhost:8000/docs) (Swagger UI) and [`/redoc`](http://localhost:8000/redoc), with the underlying spec at [`/openapi.json`](http://localhost:8000/openapi.json).

| Method | Path                                | Purpose                                                      |
| ------ | ----------------------------------- | ------------------------------------------------------------ |
| GET    | `/`                                 | Web UI                                                       |
| GET    | `/docs`, `/redoc`, `/openapi.json`  | API explorer + spec                                          |
| GET    | `/api/status`                       | Aggregate status of every configured server                  |
| POST   | `/api/start`                        | Start (or replace) the full server set from a config payload |
| POST   | `/api/stop`                         | Stop every running server                                    |
| GET    | `/api/servers/{name}`               | Status of one server                                         |
| POST   | `/api/servers/{name}/start`         | Start a single configured server                             |
| POST   | `/api/servers/{name}/stop`          | Stop a single server                                         |
| GET    | `/api/logs`                         | SSE stream of activity logs (each line tagged `[<name>]`)    |
| ANY    | `/<name>/mcp`                       | Streamable-HTTP MCP endpoint for a named backend (raw passthrough)             |
| ANY    | `/mcp`                              | Aggregated Streamable-HTTP MCP endpoint — union of tools/prompts from every running server, namespaced as `<server>__<name>`             |

## Docker

A `Dockerfile` is included, pre-loaded with the runtimes stdio MCP servers most commonly need: Python 3.12 + `uv`/`uvx`/`pipx`, Node.js 20 (`npx`), `git`, and `build-essential`.

```bash
docker build -t localmcp .
docker run --rm -p 8000:8000 localmcp

# Mount a workspace so e.g. server-filesystem can see your files:
docker run --rm -p 8000:8000 -v "$PWD:/workspace" localmcp
```

When configuring stdio servers inside the container, remember that paths reference the container filesystem — point them at the volume mount (e.g. `/workspace`) rather than your host path.

## How It Works

LocalMCP runs a single Starlette app on port 8000. The dispatcher inspects each incoming request:

- `/<name>/mcp` is routed to that backend's own `ProxyState` session manager — pure passthrough, names are untouched.
- `/mcp` is routed to the `Aggregator`'s session manager. On every `tools/list`, `prompts/list`, `resources/list`, or `resources/templates/list`, the aggregator fans out to every running backend's `ClientSession` in parallel, prefixes tool/prompt names with `<server>__`, and merges the results. On `tools/call` / `prompts/get` it splits the prefix back off and forwards to the matching backend. On `resources/read`, it consults a `URI -> backend` cache (populated as a side-effect of `resources/list`) and forwards to the recorded owner; for URIs that were never listed (e.g. constructed from a template), it falls back to fan-out — first successful backend wins and is cached. Failures at any stage are logged with an `[aggregator]` tag and the aggregator returns whatever is available.
- Everything else (`/`, `/api/*`, `/docs`, `/openapi.json`, …) flows through the standard Starlette router.

A `ProxyManager` owns one `ProxyState` per configured backend (handling its own `/<name>/mcp`) plus the shared `Aggregator`.

### What is and isn't aggregated at `/mcp`

| Method                                       | At `/mcp`                                                                    | At `/<name>/mcp` |
| -------------------------------------------- | ---------------------------------------------------------------------------- | ---------------- |
| `tools/list`, `tools/call`                   | Aggregated; names prefixed `<server>__…`                                     | Raw passthrough  |
| `prompts/list`, `prompts/get`                | Aggregated; names prefixed `<server>__…`                                     | Raw passthrough  |
| `resources/list`, `resources/templates/list` | Aggregated; URIs unchanged (origin tracked for routing reads)                | Raw passthrough  |
| `resources/read`                             | Routed to the originating backend (cache hit) with fan-out fallback          | Raw passthrough  |
| `resources/subscribe` / `unsubscribe`        | **Not** aggregated (server-initiated notifications aren't relayed currently) | Raw passthrough  |

## Project Structure

```
pyproject.toml              # Package definition
Dockerfile                  # Container image with npx, uvx, git, build tools
src/localmcp/
  __init__.py
  __main__.py               # python -m localmcp
  app.py                    # Starlette app, ASGI dispatcher, OpenAPI routes
  aggregator.py             # Aggregator: union of tools/prompts at /mcp
  config.py                 # Cursor-compatible config parser + ServerSpec
  manager.py                # ProxyManager: many ProxyStates + the aggregator
  proxy.py                  # ProxyState: single backend lifecycle + MCP forwarding
  ui.py                     # Web UI (single-page HTML/CSS/JS)
tests/
  test_aggregator_unit.py
  test_app_integration.py
  test_config_unit.py
  test_manager_unit.py
  test_proxy_unit.py
```
