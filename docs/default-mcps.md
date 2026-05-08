# Default MCP backends

zelosMCP ships with two layers of pre-wired MCP backends:

- **Mandatory** ([`configs/mandatory-zelosmcp.json`](../configs/mandatory-zelosmcp.json)) — `pincher` and `filesystem` are merged into every `/api/start` payload before parsing. User configs can override their args/env (same-name entries win), but you don't have to copy them into your own config to get them running.
- **Default** ([`configs/default-zelosmcp.json`](../configs/default-zelosmcp.json)) — `kubernetes` and `docker` ship in the file `make load` POSTs by default. Drop them, change them, or replace the whole file by overriding `ZELOSMCP_CONFIG`.

| Backend | Layer | Upstream | Transport | Purpose |
|---|---|---|---|---|
| [`pincher`](#pincher) | mandatory | [`pincherMCP`](https://github.com/kwad77/pincherMCP) | stdio (binary, baked into image) | Codebase intelligence: AST symbols, FTS5 full-text search, Cypher graph queries, BPE token-savings accounting. |
| [`filesystem`](#filesystem) | mandatory | [`@modelcontextprotocol/server-filesystem`](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem) | stdio (npx) | Read/edit/list files in `/user_data_rw`. |
| [`docker`](#docker) | default | [`mcp-server-docker`](https://github.com/ckreiling/mcp-server-docker) | stdio (uvx) | Inspect / manage Docker containers, images, networks, volumes. |
| [`kubernetes`](#kubernetes) | default | [`kubernetes-mcp-server`](https://github.com/manusa/kubernetes-mcp-server) | stdio (npx) | Inspect / manage Kubernetes resources. |

The aggregator at `/mcp` exposes their tools namespaced as `<backend>__<tool>` (e.g. `pincher__search`, `filesystem__read_text_file`). Every backend is compressed by default (`level: "medium"`, `scope: "aggregator"`), so the aggregator surfaces wrapper tools (`<backend>__get_tool_schema`, `<backend>__invoke_tool`) instead of the full schema. Set `"compress": null` on a backend's entry to opt out. See [compression.md](compression.md) for the full reference.

The web UI's right-column **Repositories** panel uses `filesystem__write_file` and `pincher__index` to install rules and onboard repos in two clicks per repo. See [repositories.md](repositories.md).

---

## `filesystem`

[`@modelcontextprotocol/server-filesystem`](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem) — official MCP-team filesystem server. Read, write, edit, search, and walk files within a sandboxed root directory.

### Spawn

```json
"filesystem": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/user_data_rw"]
}
```

The `/user_data_rw` argument is the sandbox — the server will refuse to read or write outside it. Inside the zelosMCP container, `/user_data_rw` is bind-mounted (read/write) to your host's `$USER_DATA_ROOT` (default `$HOME`). The same host directory is also mounted read-only at `/user_data_ro` for backends like pincher that should not be able to write.

### Tool surface (14 tools)

| Tool | What it does |
|---|---|
| `read_text_file` | Read a file as text. Optional `head` / `tail` line limits. |
| `read_media_file` | Read an image or audio file as base64. |
| `read_multiple_files` | Batch read; failed reads don't stop the rest. |
| `write_file` | Create or overwrite. |
| `edit_file` | Pattern-based selective edits. Supports `dryRun: true` for git-style diff preview — use it. |
| `create_directory` | `mkdir -p`. Idempotent. |
| `list_directory` | `ls`-style listing. |
| `list_directory_with_sizes` | Same plus file sizes and totals. |
| `directory_tree` | Recursive JSON tree. |
| `move_file` | Move/rename. Refuses to overwrite. |
| `search_files` | Recursive name match (glob). |
| `get_file_info` | `stat`-style metadata. |
| `list_allowed_directories` | Sandbox roots the server is operating on. |

All tools annotate their MCP `readOnlyHint` correctly, so the [zelosMCP rule generator](built-in-mcp.md) classifies them precisely (read-only inspection vs. mutating edit vs. destructive overwrite).

### Mounts needed

`$USER_DATA_ROOT -> /user_data_rw` and `$USER_DATA_ROOT -> /user_data_ro:ro` (default in [`configs/default-volumes.conf`](../configs/default-volumes.conf)).

### Gotchas

- Paths are container-relative. Tell the agent `/user_data_rw/workspace/foo.py`, not `~/workspace/foo.py`.
- `edit_file` supports `dryRun: true` for preview. Pin that into your Cursor rule for any change >5 lines.

---

## `pincher`

[`pincherMCP`](https://github.com/kwad77/pincherMCP) — codebase intelligence server. Single Go binary that indexes a codebase into three co-located layers (byte-offset symbol store + Cypher-queryable knowledge graph + FTS5 full-text search), all populated from one shared AST parse pass. Token-saving by design — every response includes a `_meta` envelope with real BPE token counts and cost-avoided accounting.

### Spawn

```json
"pincher": {
  "command": "pincher",
  "args": ["--data-dir", "/tmp/pincher"]
}
```

`pincher` is pre-built into the zelosMCP container image (Go binary copied in from a multi-stage build — see the `pincher-build` stages in [`Dockerfile`](../Dockerfile) and [`docker-tools/Dockerfile`](../docker-tools/Dockerfile)). `--data-dir /tmp/pincher` puts the SQLite index DB at a path mounted to a named Docker volume, so the index survives container restarts.

Bump the version pin via the `PINCHER_VERSION` build arg:

```bash
docker build --build-arg PINCHER_VERSION=v0.3.0 -t zelosmcp .
```

### Tool surface (15 tools)

#### Indexing & discovery

| Tool | What it does |
|---|---|
| `index` | Index or re-index a repo. One AST pass populates all three layers. xxh3 content-hash skips unchanged files; concurrent per-file goroutines. |
| `list` | All indexed projects with file/symbol/edge counts and last-indexed timestamp. |
| `changes` | `git diff` -> affected symbols -> BFS blast radius. Returns changed symbols + impacted callers with risk labels (CRITICAL/HIGH/MEDIUM/LOW). |

#### Symbol retrieval

| Tool | What it does |
|---|---|
| `symbol` | Source for one symbol by stable ID. O(1): 1 SQL + 1 `os.Seek` + 1 `os.Read`, no re-parse. Optional `fields` projection. |
| `symbols` | Batch retrieve up to 100 symbols in one call. Always prefer this over `symbol` in a loop. |
| `context` | Symbol + all direct callees in one call — the preferred tool for understanding a function. |

#### Search & graph

| Tool | What it does |
|---|---|
| `search` | FTS5 BM25 full-text across names, signatures, docstrings. Wildcards, phrases, AND/OR, kind/language filters. |
| `query` | Cypher-like graph queries (node scan / single-hop JOIN / variable-length BFS). |
| `trace` | BFS call-path trace — who calls this, or what does it call. Grouped by depth with risk labels. |

#### Architecture & knowledge

| Tool | What it does |
|---|---|
| `architecture` | Language breakdown, entry points, hotspot functions, graph stats. Start here on an unfamiliar project. |
| `schema` | Node-kind counts, edge-kind counts, totals. Use before `query` to see what's indexed. |
| `adr` | Persistent key/value store per project (get/set/list/delete). Survives context resets and binary upgrades. |
| `health` | Schema version, index staleness, per-language extraction coverage. |
| `stats` | Session savings as a formatted summary: tokens used vs. baseline, cost avoided, latency. Persists across reconnects. |
| `fetch` | Fetch a URL, extract its text, and store it as a searchable Document symbol in the project knowledge base. |

### Mounts needed

- `$USER_DATA_ROOT -> /user_data_ro` read-only (the host tree being indexed)
- `zelosmcp-pincher -> /tmp/pincher` (named volume — persists the SQLite index DB plus its WAL/SHM siblings across container restarts)

Pincher's WORKDIR is set to `/user_data_ro` in the container, so the kmechlin fork auto-indexes the entire user tree in the background a few minutes after spawn — no manual warm-up required. For the active git repo, `make load` chains `make index` so the current repo is queryable in seconds (toggle via `ZELOSMCP_WARM_ON_LOAD=0`). Use `make index-full` on demand to force a re-scan of the whole `/user_data_ro` mount; pincher's auto-scan covers the same ground asynchronously.

### Stable symbol IDs

Every symbol gets a human-readable ID that survives re-indexing:

```
{file_path}::{qualified_name}#{kind}

e.g.  "src/zelosmcp/aggregator.py::Aggregator.list_tools#Method"
```

When a file is renamed, pincher records a redirect in `symbol_moves`. The `symbol` tool resolves stale IDs transparently — agents don't get "not found" because a file moved.

### Mutability classification

Pincher's MCP server doesn't currently ship `readOnlyHint` / `destructiveHint` annotations on every tool, so the zelosMCP rule generator falls back to its name-prefix heuristic. `index` and `fetch` get tagged `[?]` (uncertain — they do mutate the index DB, but not the workspace itself); `search` / `query` / `symbol` etc. get tagged `[?]` too despite being pure reads. In `read-only` rule mode they're all blocked; in `read-write` mode they're all allowed. If precise classification matters, switch the rule to `read-write` for development sessions where you need pincher.

### Gotchas

- **First call needs `index`.** Pincher's tools (`search`, `symbol`, `query`, ...) return empty until at least one `pincher__index` call has populated the DB. With WORKDIR set to `/user_data_ro` the auto-scan handles this on its own a few minutes after spawn; `make load` (chained from `make up`) also calls `pincher__index` for the current repo so it's hot in seconds. Otherwise the agent has to call `pincher__index` itself before anything else.
- **Language coverage is uneven.** Go gets full AST extraction (confidence 1.0). Python / TypeScript / JavaScript / Rust / Java / Kotlin use stable regex (0.85). Ruby / PHP / C / C++ / C# use approximate regex (0.70). See [the README's language-support table](https://github.com/kwad77/pincherMCP#language-support) for the per-language list.
- **SQLite serializes writes.** Pincher uses `SetMaxOpenConns(1)` for the writer pool. Concurrent re-indexes (e.g. running `pincher index` from two terminals) will queue rather than collide; healthy default but worth knowing for large-repo workflows.

---

## `docker`

[`mcp-server-docker`](https://github.com/ckreiling/mcp-server-docker) (ckreiling) — talks to a Docker daemon via the standard Unix socket using the Python Docker SDK.

### Spawn

```json
"docker": {
  "command": "uvx",
  "args": ["mcp-server-docker"],
  "env": {
    "DOCKER_HOST": "unix:///var/run/docker.sock"
  }
}
```

`DOCKER_HOST` is set explicitly so the Python Docker SDK's `from_env()` picks the bind-mounted socket regardless of host quirks.

### Tool surface (19 tools)

Containers — `list_containers`, `create_container`, `run_container`, `recreate_container`, `start_container`, `stop_container`, `remove_container`, `fetch_container_logs`.

Images — `list_images`, `pull_image`, `push_image`, `build_image`, `remove_image`.

Networks — `list_networks`, `create_network`, `remove_network`.

Volumes — `list_volumes`, `create_volume`, `remove_volume`.

Plus a `docker_compose` prompt and per-container stats/logs resources.

### Mounts needed

`$DOCKER_SOCK_FILE -> /var/run/docker.sock` (default `/var/run/docker.sock` on the host). See [setup-rancher-desktop.md](setup-rancher-desktop.md) for which daemon you actually reach.

### Gotchas

- **Security:** mounting the Docker socket is effectively root-on-host. Only run this backend on dev machines.
- **No MCP annotations:** the upstream server doesn't ship `readOnlyHint` / `destructiveHint` annotations. The zelosMCP rule generator falls back to a name-prefix heuristic to classify mutability — `list_*` reads as `[?]` (uncertain) in read-only mode unless you switch to read-write.
- **Daemon mismatch:** if the zelosMCP container runs on Docker Desktop but `DOCKER_SOCK_FILE` points at Rancher Desktop's socket, the bind-mount fails. Both sides have to align — see [setup-rancher-desktop.md](setup-rancher-desktop.md#how-zelosmcp-picks-up-your-daemon).

---

## `kubernetes`

[`kubernetes-mcp-server`](https://github.com/manusa/kubernetes-mcp-server) — generic Kubernetes inspection + management. Multi-cluster aware: every tool call accepts an optional `context` argument, so the agent can target your local cluster, an EKS cluster, etc. as needed.

### Spawn

```json
"kubernetes": {
  "command": "npx",
  "args": ["-y", "kubernetes-mcp-server@latest"]
}
```

Reads the kubeconfig at `/root/.kube/config` (mounted from your host).

### Cluster routing — the `zelosmcp` context

zelosMCP runs in bridge networking, so the container's `127.0.0.1` is the container itself, not your Mac. Reaching Rancher Desktop / Docker Desktop's K8s API at `https://127.0.0.1:6443` from inside the container won't work directly.

`make up` solves this by adding a **`zelosmcp`** cluster + context to your `~/.kube/config` (idempotent host-side `kubectl config set-cluster/set-context`) that points at `https://host.docker.internal:6443`. Your existing contexts and `current-context` are unchanged.

The agent then uses the zelosmcp context for in-cluster work:

```jsonc
// Multi-cluster mode is on by default — every tool accepts `context`.
{ "name": "kubernetes__pods_list", "arguments": { "context": "zelosmcp" } }
```

For remote clusters (EKS, AKS, GKE, …) just pass that cluster's context name instead. You can verify the zelosmcp context from the host first: `kubectl --context zelosmcp get nodes`.

To remove the auto-added entries: `make clean-kubeconfig`.

### Tool surface (19 tools)

Cluster — `configuration_view`, `events_list`, `namespaces_list`.

Nodes — `nodes_log`, `nodes_stats_summary`, `nodes_top`.

Pods — `pods_list`, `pods_list_in_namespace`, `pods_get`, `pods_log`, `pods_top`, `pods_run`, `pods_exec`, `pods_delete`.

Resources (generic) — `resources_get`, `resources_list`, `resources_create_or_update`, `resources_delete`, `resources_scale`.

Plus a `cluster-health-check` prompt that walks the agent through a comprehensive health audit.

All tools annotate `readOnlyHint` / `destructiveHint` correctly, so destructive ones (`pods_delete`, `resources_delete`) are flagged distinctly in the rule generator output.

### Mounts needed

`$KUBERNETES_CONFIG_FILE -> /root/.kube/config:ro` (default `$HOME/.kube/config` on the host, mounted read-only).

### Gotchas

- **Auth credential helpers:** if your kubeconfig uses `exec`-based auth (cloud SSO, AWS EKS, etc.), the helper binary needs to exist inside the zelosMCP container, not just on your host. For Rancher Desktop's local k3s this isn't an issue — auth is via embedded certs in the kubeconfig.
- **Namespacing:** by default tools target whatever namespace is set as `current` in the kubeconfig context. Pass `namespace=` explicitly when you want to scope a query.

---

## Adding more backends

Anything in the [MCP server catalog](https://github.com/modelcontextprotocol/servers) (or any custom server you've built) can be added — see [configuration.md](configuration.md) for the JSON schema and [makefile.md](makefile.md) for how to load a custom config.

Stdio backends spawn `npx -y …` or `uvx …` from inside the zelosMCP container, so the runtime needs to be available there. The container ships with Python 3.12 + `uv`/`uvx`/`pipx` and Node.js 22 + `npx` preinstalled, which covers virtually all published MCP servers.
