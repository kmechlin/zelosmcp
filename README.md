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

Two ways to wire LocalMCP into Cursor; **the aggregated entry is the recommended default**. Reach for the per-backend variant only when you have a specific reason to talk to a single backend (e.g. needing original tool names without the `<server>__` prefix, or scoping one backend to a separate Cursor profile).

Every entry the LocalMCP UI generates for your `~/.cursor/mcp.json` is prefixed `localmcp-` so it's obvious in Cursor's UI which entries are LocalMCP-proxied (and so they don't collide with backends you may already have configured directly).

### Option A — aggregated entry (recommended)

One Cursor entry, every backend's tools and prompts. The web UI's "Cursor mcp.json (aggregated)" panel shows this snippet verbatim:

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

Cursor sees the union of every running backend's tools, prompts, and resources. Tool and prompt names are prefixed `<server>__<name>` (double underscore — for example `filesystem__read_file`, `github__search_repos`). Resource URIs keep their original form; reads are routed back to the originating backend automatically. The always-on built-in MCP's tools also surface here as `localmcp__*` (see [Built-in MCP](#built-in-mcp-at-localmcpmcp) below).

### Option B — one entry per backend (raw passthrough)

Use this when you want a backend's original tool names (no `<server>__` prefix) or a single backend wired into a different Cursor profile. The web UI's "Cursor full mcp.json" panel auto-populates this with one entry per running backend plus the aggregate:

```json
{
  "mcpServers": {
    "localmcp-filesystem": {
      "type": "streamable-http",
      "url": "http://localhost:8000/filesystem/mcp"
    },
    "localmcp-github": {
      "type": "streamable-http",
      "url": "http://localhost:8000/github/mcp"
    }
  }
}
```

The web UI generates the right snippet automatically based on what you've started — copy from there for an exact match.

### Cursor rule (dynamic, comprehensive)

The web UI's **Cursor rule (.mdc)** panel and the `GET /api/cursor-rule` HTTP endpoint generate a [Cursor rule](https://docs.cursor.com/context/rules-for-ai) on-the-fly from whatever backends are currently loaded. Every tool from every backend is listed with its description, arg summary, and a `[readonly]` / `[mutates]` / `[destructive]` / `[?]` mutability marker derived from MCP annotations + a name-prefix fallback. There is no static rule example checked into the repo — the canonical artifact is whatever your live aggregator currently exposes.

Two access modes (toggleable in the UI dropdown or via `?access=`):

- **`read-only`** (default) — rule body explicitly forbids the agent from calling any tool tagged `[mutates]`, `[destructive]`, or `[?]`. Safe for code review, demos, or any workspace where the agent should only inspect.
- **`read-write`** — tools are still tagged but the agent is permitted to call them; `[destructive]` tools require explicit user confirmation per the directive.

Save the body to your project's rules dir:

```bash
mkdir -p .cursor/rules
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only' \
  > .cursor/rules/localmcp.mdc
```

Or globally:

```bash
mkdir -p ~/.cursor/rules
curl -fsSL 'http://localhost:8000/api/cursor-rule?access=read-only' \
  > ~/.cursor/rules/localmcp.mdc
```

Or just hit **Copy** in the web UI panel and paste. Pass `?style=scoped&globs=**/*.py` if you only want the rule active when matching files are open. Re-run any time the backend set changes — the rule is dynamic.

### Built-in MCP at `/localmcp/mcp`

LocalMCP ships an always-on, in-process MCP server reachable at `http://localhost:8000/localmcp/mcp` (raw passthrough) and aggregated into `/mcp` as `localmcp__*`. It survives configuration reloads and exposes self-introspection plus a Cursor-rule generator that's aware of whichever backends you currently have running. The seven tools:

| Tool | Purpose |
|---|---|
| `localmcp__generate_cursor_rule` | Generate a comprehensive `.mdc` rule body listing every tool from every loaded backend with description, arg summary, and `[readonly]`/`[mutates]`/`[destructive]`/`[?]` mutability marker. Inputs: `access` (`read-only` \| `read-write`, default `read-only`), `style` (`always-apply` \| `scoped`), `globs` (when scoped). |
| `localmcp__list_loaded_servers` | Compact JSON view of every registered backend. |
| `localmcp__get_aggregated_tool_catalog` | Fan `tools/list` across every running backend; returns the same shape as `GET /api/catalog`. |
| `localmcp__generate_cursor_mcp_json` | Returns the same `mcp.json` snippet the UI shows. Inputs: `shape` (`aggregate` \| `per-backend`), `host`. |
| `localmcp__start_server` / `stop_server` | Wraps `ProxyManager.start_one`/`stop_one`. Refuses self-targeting. |
| `localmcp__reload_config` | Replace the entire backend set; same JSON shape `POST /api/start` accepts. |

The web UI's **Cursor rule (.mdc)** panel calls the same generator via `GET /api/cursor-rule` (`text/markdown`), refreshing whenever the running-backends set OR the access-mode dropdown changes. Copy the body straight into `.cursor/rules/localmcp.mdc`.

#### Live tool catalog

Each row in the web UI's **Servers** panel is click-to-expand: clicking the row reveals that backend's tools, prompts, resources, and resource templates inline — pretty-printed input schemas tucked into a nested `<details>` so the listing stays scannable. The **Full catalog** link in the Servers card header opens [`/catalog`](http://localhost:8000/catalog), a standalone, searchable, print-friendly documentation page covering every running backend at once.

The data comes from the new `GET /api/catalog` endpoint, which fans `list_tools` / `list_prompts` / `list_resources` / `list_resource_templates` across every running backend (capabilities a backend doesn't implement are coerced to `[]`). The same payload is reachable over MCP via `localmcp__get_aggregated_tool_catalog` — both surfaces share one helper, so they're guaranteed-equivalent.

> **Reserved name.** `localmcp` is now a reserved server name (alongside `api`, `mcp`, `docs`, `redoc`, `openapi.json`, `static`). Configs that include an `mcpServers.localmcp` entry will fail to load with a `ConfigError`. Pick a different name (e.g. `local-tools`).

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
| GET    | `/api/cursor-rule`                  | Comprehensive Cursor `.mdc` rule body (text/markdown) listing every tool from every loaded backend with description, arg summary, and mutability marker. Query params: `access=read-only\|read-write` (default `read-only` &mdash; forbids mutating tools), `style=always-apply\|scoped` (default `always-apply`), `globs=...` (for `style=scoped`). |
| GET    | `/api/catalog`                      | Read-only documentation snapshot of every running backend: tools, prompts, resources, and resource templates with their full payloads (`inputSchema`, etc.). Same shape as `localmcp__get_aggregated_tool_catalog`. |
| GET    | `/catalog`                          | Standalone, searchable HTML documentation page (consumes `/api/catalog`). Print-friendly. |
| ANY    | `/<name>/mcp`                       | Streamable-HTTP MCP endpoint for a named backend (raw passthrough)             |
| ANY    | `/localmcp/mcp`                     | Always-on built-in MCP (raw passthrough) — self-introspection + rule generation tools |
| ANY    | `/mcp`                              | **Recommended.** Aggregated Streamable-HTTP MCP endpoint — union of tools/prompts from every running server (incl. the built-in), namespaced as `<server>__<name>`             |

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

- `/<name>/mcp` is routed to that backend's own `ProxyState` session manager — pure passthrough, names are untouched. `/localmcp/mcp` is the same route into the always-on built-in (`BuiltinServer`); the dispatcher doesn't special-case it.
- `/mcp` is routed to the `Aggregator`'s session manager. On every `tools/list`, `prompts/list`, `resources/list`, or `resources/templates/list`, the aggregator fans out to every running backend's `ClientSession` in parallel (including the built-in's in-memory client session), prefixes tool/prompt names with `<server>__`, and merges the results. On `tools/call` / `prompts/get` it splits the prefix back off and forwards to the matching backend. On `resources/read`, it consults a `URI -> backend` cache (populated as a side-effect of `resources/list`) and forwards to the recorded owner; for URIs that were never listed (e.g. constructed from a template), it falls back to fan-out — first successful backend wins and is cached. Failures at any stage are logged with an `[aggregator]` tag (capability mismatches via `-32601` are silently skipped) and the aggregator returns whatever is available.
- Everything else (`/`, `/api/*`, `/docs`, `/openapi.json`, …) flows through the standard Starlette router.

A `ProxyManager` owns one `ProxyState` per configured backend (handling its own `/<name>/mcp`), the always-on `BuiltinServer` at `/localmcp/mcp`, and the shared `Aggregator`. The built-in is started by a Starlette `lifespan` hook before any HTTP request arrives, and survives `POST /api/start` config reloads.

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
Dockerfile                  # Upstream community-friendly image (no corp cert handling)
Makefile                    # Nike enterprise build + lifecycle helpers
configs/
  default-localmcp.json     # Project-agnostic default backend set
  default-volumes.conf      # Default container volume mounts (host paths + named volumes)
docker-tools/               # Nike enterprise build infrastructure (cert-aware)
  Dockerfile                # Multi-stage: base-os -> extra-os -> localmcp
  buildx.Dockerfile         # Cert-aware buildkit builder image
  README.md                 # Build flow + Makefile target pointers
src/localmcp/
  __init__.py
  __main__.py               # python -m localmcp
  app.py                    # Starlette app, ASGI dispatcher, OpenAPI routes
  aggregator.py             # Aggregator: union of tools/prompts at /mcp
  builtin.py                # Always-on built-in MCP at /localmcp/mcp + rule generator
  config.py                 # Cursor-compatible config parser + ServerSpec
  manager.py                # ProxyManager: many ProxyStates + the aggregator
  proxy.py                  # ProxyState: single backend lifecycle + MCP forwarding
  ui.py                     # Web UI (single-page HTML/CSS/JS)
tests/
  test_aggregator_unit.py
  test_app_integration.py
  test_builtin_unit.py
  test_config_unit.py
  test_manager_unit.py
  test_proxy_unit.py
```

## Nike enterprise deploy

If you're behind Nike's TLS-intercepting proxy (Palo Alto), the upstream build instructions above need an extra step to install the corporate root CA before `apt`/`pip`/`npm`/`uvx` can clear the proxy. The [`docker-tools/`](docker-tools/) directory holds a cert-aware multi-stage build for exactly that, and the [`Makefile`](Makefile) wires up the full lifecycle.

Quickstart:

```bash
make get-corp-root-authority-cert    # exports docker-tools/cert.pem from macOS keychain
make localmcp-image-build            # builds the cert-aware image (localmcp:dev)
make localmcp-up                     # runs the container with kubeconfig + workspace mounted
make localmcp-load                   # POSTs configs/default-localmcp.json to /api/start
```

Verify it's working:

```bash
make localmcp-status                 # container running? + curl probes
make localmcp-list-tools             # tools exposed by each backend
```

Day-to-day:

| Target | What it does |
|---|---|
| `make localmcp-up` | Start the container (auto-builds the image if missing). |
| `make localmcp-load LOCALMCP_CONFIG=path/to/config.json` | Push a custom backend config (consumer projects supply their own). |
| `make localmcp-up LOCALMCP_VOLUMES_FILE=path/to/volumes.conf` | Start the container with a custom volume-mount list (defaults below). |
| `make localmcp-warm-index` | Pre-populate code-index's file index for first-call latency. |
| `make localmcp-shell` | Bash inside the running container. |
| `make localmcp-logs` | Tail container logs. |
| `make localmcp-restart` | Bounce the container. |
| `make localmcp-down` | Stop + remove the container. |
| `make clean` | Tear down container, builder, and the cert-aware buildx image. |

The default `LOCALMCP_CONFIG` is [`configs/default-localmcp.json`](configs/default-localmcp.json), which boots four backends out of the box:

- `rancher-k3s` — `kubernetes-mcp-server` against the kubeconfig you mount in.
- `filesystem` — `@modelcontextprotocol/server-filesystem` rooted at `/workspace`.
- `code-index` — `code-index-mcp` initialised against `/workspace` (run `make localmcp-warm-index` to pre-populate the file + symbol index).
- `docker` — `mcp-server-docker` talking to the host Docker daemon via the bind-mounted socket (Rancher Desktop's `~/.rd/docker.sock` by default).

Consumer projects (e.g. `nike.automation_abstraction_infra`) ship their own `localmcp.json` with backends pre-configured for their workspace and load it via `make localmcp-load LOCALMCP_CONFIG=/path/to/their/config.json`.

### Volume mounts ([`configs/default-volumes.conf`](configs/default-volumes.conf))

`make localmcp-up` reads its `docker run -v` list from `$(LOCALMCP_VOLUMES_FILE)` (default [`configs/default-volumes.conf`](configs/default-volumes.conf)). One mount per line, in `<host-or-named-volume>:<container-path>[:options]` form, with `$HOME` / `$WORKSPACE_DIR` / `$KUBERNETES_CONFIG_FILE` / `$DOCKER_SOCK_FILE` expanded at startup. The defaults wire up:

| Mount | Purpose |
|---|---|
| `$KUBERNETES_CONFIG_FILE -> /root/.kube/config:ro` | `kubernetes-mcp-server` read-only access to your cluster. |
| `$WORKSPACE_DIR -> /workspace` | Source tree the `filesystem` and `code-index` backends operate on. |
| `$DOCKER_SOCK_FILE -> /var/run/docker.sock` | `mcp-server-docker` access to the host Docker daemon. Default `/var/run/docker.sock` works with Docker Desktop and with Rancher Desktop in admin-access mode (the path is exposed inside both VMs). The daemon you actually reach is whichever the active `docker context` points at when `make localmcp-up` runs. |
| `localmcp-npm -> /root/.npm` | Persistent `npx` cache (named volume). |
| `localmcp-cache -> /root/.cache` | Persistent `uv`/`pip` cache (named volume). |
| `localmcp-code-index -> /tmp/code_indexer` | Persistent code-index DB so reindexing isn't re-run on every container restart. |

Override `DOCKER_SOCK_FILE` if you run Rancher Desktop **without** admin access and want `mcp-server-docker` driving Rancher Desktop's daemon. The override has to be paired with switching docker contexts first, otherwise `docker run` itself goes through the wrong daemon and fails on the bind mount:

```bash
docker context use rancher-desktop
make localmcp-up DOCKER_SOCK_FILE=$HOME/.rd/docker.sock
```

Add an extra mount by editing the conf, or point `LOCALMCP_VOLUMES_FILE` at your own copy:

```bash
make localmcp-up LOCALMCP_VOLUMES_FILE=$HOME/.config/localmcp/volumes.conf
```

> **Security note:** mounting the Docker socket is effectively root-on-host — only do it on dev machines. To opt out, comment the `$DOCKER_SOCK_FILE:/var/run/docker.sock` line in your volumes conf and remove the `docker` backend from `default-localmcp.json`.

See [`docker-tools/README.md`](docker-tools/README.md) for the build flow and the rationale for keeping the upstream `Dockerfile` and the cert-aware build separate.
