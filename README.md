# LocalMCP

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

Wrap one or more MCP servers and re-expose them on stable local URLs. One Cursor or VSCode entry, every backend.

LocalMCP runs a single web server that fronts any number of MCP servers — stdio commands, SSE endpoints, or Streamable HTTP URLs. Each one gets a fixed local address (`http://localhost:8000/<name>/mcp`), and a bare `http://localhost:8000/mcp` aggregates tools, prompts, and resources from every running backend under a `<server>__<tool>` namespace. Plus a comprehensive Cursor / Copilot rule generator, a live tool catalog UI, and a small REST control plane.

## Quickstart

If you have Docker available:

```bash
make init-env       # optional one-time wizard: writes .env (USER_DATA_ROOT, ports, etc.)
make up             # build image (if missing) + start container + load default backends
```

That's it. `make up` is idempotent — re-running on a healthy container just re-applies the config. Skip `make init-env` if you're happy with defaults; the Makefile falls back to them.

Open [http://localhost:8000](http://localhost:8000) for the web UI, then wire your IDE — see [docs/quickstart.md](docs/quickstart.md) for Cursor and VSCode + Copilot snippets and the dynamic rule-file generator.

Don't have Docker? See [docs/quickstart-no-docker.md](docs/quickstart-no-docker.md) for the Python pip install path.

## Architecture in 60 seconds

```mermaid
flowchart LR
  subgraph clients [IDE clients]
    cursor["Cursor"]
    vscode["VSCode + Copilot"]
  end
  subgraph proxy [LocalMCP]
    dispatch["dispatcher"]
    agg["Aggregator (/mcp)"]
    builtin["BuiltinServer (/localmcp/mcp)"]
    other["pincher / filesystem / docker /\nkubernetes / (your own ...)"]
    api["HTTP API + Web UI"]
  end
  cursor -->|"streamable-http"| dispatch
  vscode -->|"http"| dispatch
  dispatch --> agg
  dispatch --> builtin
  dispatch --> other
  dispatch --> api
  agg -.->|"fan-out"| builtin
  agg -.->|"fan-out"| other
```

Three things to know:

- **`/<name>/mcp`** is a raw passthrough to one backend (original tool names).
- **`/mcp`** aggregates every running backend (names prefixed `<server>__`). This is what your IDE should connect to.
- **`/localmcp/mcp`** is an always-on built-in MCP that exposes self-introspection tools — `localmcp__generate_cursor_rule`, `localmcp__get_aggregated_tool_catalog`, etc. — so the agent can drive LocalMCP itself.

Deeper dive (component table, dispatcher flow, aggregator fan-out, lifespan sequence): [docs/architecture.md](docs/architecture.md).

## Documentation

| Topic | Doc |
|---|---|
| Architecture deep-dive | [docs/architecture.md](docs/architecture.md) |
| Quickstart (5 minutes, Cursor or VSCode) | [docs/quickstart.md](docs/quickstart.md) |
| Rancher Desktop setup (Docker daemon + kubeconfig) | [docs/setup-rancher-desktop.md](docs/setup-rancher-desktop.md) |
| Makefile reference + volume-mount customization | [docs/makefile.md](docs/makefile.md) |
| `mcpServers` config schema and `/api/start` lifecycle | [docs/configuration.md](docs/configuration.md) |
| Reverse-proxy backend HTTP sidecars under LocalMCP's port | [docs/reverse-proxy.md](docs/reverse-proxy.md) |
| Tool-list compression (`get_tool_schema` / `invoke_tool` wrappers) | [docs/compression.md](docs/compression.md) |
| Default MCP backends (pincher + filesystem mandatory; docker / kubernetes default) | [docs/default-mcps.md](docs/default-mcps.md) |
| Repositories panel (discover git repos + write rules + index in pincher) | [docs/repositories.md](docs/repositories.md) |
| Cursor integration + dynamic `.mdc` rule generation | [docs/cursor-integration.md](docs/cursor-integration.md) |
| VSCode + GitHub Copilot integration + `copilot-instructions.md` | [docs/vscode-integration.md](docs/vscode-integration.md) |
| Built-in MCP at `/localmcp/mcp` + `/catalog` page | [docs/built-in-mcp.md](docs/built-in-mcp.md) |
| HTTP API reference (`/api/*` and the MCP routes) | [docs/http-api.md](docs/http-api.md) |

Plus the interactive Swagger UI at [http://localhost:8000/docs](http://localhost:8000/docs) and ReDoc at [http://localhost:8000/redoc](http://localhost:8000/redoc).

## Project structure

```
pyproject.toml              # Package definition
Dockerfile                  # Upstream community-friendly image (no corp cert handling)
Makefile                    # Build + lifecycle targets
configs/
  default-localmcp.json     # Project-agnostic default backend set
  default-volumes.conf      # Default container volume mounts (host paths + named volumes)
docker-tools/               # Cert-aware build infrastructure (corporate proxy environments)
  Dockerfile                # Multi-stage: base-os -> extra-os -> localmcp
  buildx.Dockerfile         # Cert-aware buildkit builder image
  README.md                 # Build flow + Makefile pointers
docs/                       # All documentation (this README links into it)
src/localmcp/
  __init__.py
  __main__.py               # python -m localmcp
  app.py                    # Starlette app, ASGI dispatcher, OpenAPI routes
  aggregator.py             # Aggregator: union of tools/prompts at /mcp
  builtin.py                # Always-on built-in MCP at /localmcp/mcp + rule generator
  compression.py            # tool-list compression wrappers (get_tool_schema/invoke_tool)
  config.py                 # Cursor-compatible config parser + ServerSpec
  docs.py                   # in-app /api/docs markdown viewer
  manager.py                # ProxyManager: many ProxyStates + the aggregator
  proxy.py                  # ProxyState: single backend lifecycle + MCP forwarding
  savings.py                # per-call token-savings recorder
  savings_db.py             # SQLite store backing the savings dashboard
  ui.py                     # Web UI (single-page HTML/CSS/JS)
scripts/
  init_env.py               # interactive .env wizard (`make init-env`)
tests/                      # pytest suite (no Docker required)
```

## Enterprise / corporate-proxy deploy

If you're behind a TLS-intercepting corporate proxy (e.g. Palo Alto), the upstream `Dockerfile` won't get past the proxy on its `apt`/`pip`/`npm`/`uvx` calls. The [`docker-tools/`](docker-tools/) directory holds a cert-aware multi-stage build for that case, and the [`Makefile`](Makefile) wires up the full lifecycle — see [docs/makefile.md](docs/makefile.md) and [docs/setup-rancher-desktop.md](docs/setup-rancher-desktop.md) for the cert export, build, and run sequence.

## Contributing / hacking

```bash
pip install -e .
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

The test suite covers the dispatcher, aggregator fan-out, in-memory built-in transport, rule generator (both `cursor-mdc` and `copilot-instructions` formats), config parsing, and per-server lifecycle. CI-friendly — no Docker daemon required.

For the Python (non-Docker) install path see [docs/quickstart-no-docker.md](docs/quickstart-no-docker.md).
