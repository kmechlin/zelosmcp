# LocalMCP

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

Wrap one or more MCP servers and re-expose them on stable local URLs via Streamable HTTP.

LocalMCP runs a single web server. You point it at any number of MCP servers — stdio commands, SSE endpoints, or Streamable HTTP URLs — and each one gets a fixed local address: `http://localhost:8000/<name>/mcp`. An optional `primaryMCP` mirrors one of them at the bare `http://localhost:8000/mcp` so existing single-MCP configs keep working.

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

Add an entry per server you've configured. Any combination of these works:

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
    },
    "primary": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

The web UI generates this snippet automatically and updates it whenever you start, stop, or change servers — copy from there for an exact match.

## Configuration

LocalMCP accepts the same `mcpServers` shape Cursor uses in its own `mcp.json`, plus one extension: an optional top-level `primaryMCP` field naming the server that should also be served at the bare `/mcp` path.

```json
{
  "primaryMCP": "filesystem",
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

## Usage

1. Open the web UI at `http://localhost:8000`.
2. Paste a multi-server config into the textarea (the format above).
3. Click **Start**. Each server is mounted at `http://localhost:8000/<name>/mcp`; if you set `primaryMCP`, that server is also reachable at `http://localhost:8000/mcp`.
4. The **Cursor mcp.json** snippet below the config refreshes itself with one entry per running server. Copy it into your Cursor config.
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
| ANY    | `/<name>/mcp`                       | Streamable-HTTP MCP endpoint for a named backend             |
| ANY    | `/mcp`                              | Streamable-HTTP MCP endpoint for the `primaryMCP` (if any)   |

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

- `/<name>/mcp` and `/mcp` are routed to the matching `ProxyState`'s session manager.
- Everything else (`/`, `/api/*`, `/docs`, `/openapi.json`, …) flows through the standard Starlette router.

A `ProxyManager` owns a `ProxyState` per configured server. When you start a server, that `ProxyState` spawns the backend (or connects to a remote one), establishes an MCP client session, and forwards every tool call, resource read, and prompt request transparently — no prefixing or transformation.

## Project Structure

```
pyproject.toml              # Package definition
Dockerfile                  # Container image with npx, uvx, git, build tools
src/localmcp/
  __init__.py
  __main__.py               # python -m localmcp
  app.py                    # Starlette app, ASGI dispatcher, OpenAPI routes
  config.py                 # Cursor-compatible config parser + ServerSpec
  manager.py                # ProxyManager: many ProxyStates + primary pointer
  proxy.py                  # ProxyState: single backend lifecycle + MCP forwarding
  ui.py                     # Web UI (single-page HTML/CSS/JS)
tests/
  test_app_integration.py
  test_config_unit.py
  test_manager_unit.py
  test_proxy_unit.py
```
