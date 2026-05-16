
# zelosMCP backend tool catalog

Generated from the zelosMCP aggregator at `http://localhost:8000/mcp`. Every tool below is reachable as `<server>__<tool>` (double underscore) on that single Cursor entry. Prefer these over shelling out — they return structured data and keep paths inside the container's `/user_data_rw` (read-write) and `/user_data_ro` (kernel-enforced read-only) mounts.

Currently-loaded backends: `docker`, `pincher`.

## Access mode: READ-WRITE

Tools tagged `[mutates]` and `[destructive]` change backend state. Confirm with the user before calling `[destructive]` tools (irreversible). Tools tagged `[?]` have ambiguous mutability — call only when context makes it clear they're inspection-only.

## Tool-use priority

**Always prefer the MCP tools listed below over shell commands, subprocess invocations, or local CLIs** when an MCP tool covers the task. They return structured data, avoid subprocess cost, and keep paths inside the sandboxed mounts. Reach for `bash` / `python -c` / direct file reads only when no MCP tool fits, and say so explicitly when you do.

## Pre-flight check (run BEFORE every response)

Answer these four questions before issuing any tool call. The first matching YES dictates your FIRST tool call:

1. **Code structure / symbols / behavior?** ("summarize / explain / understand / find / trace / impact / blast radius" of repo, module, function, class) → if `pincher` is loaded, FIRST call MUST be `pincher__*`. Load the `zelosmcp-pincher` skill (`/zelosmcp-pincher`) for the full workflow.
2. **Files in the workspace?** ("read / edit / list / search / move / create" a file or directory) → if `filesystem` is loaded, use `filesystem__*`. Load the `zelosmcp-filesystem` skill (`/zelosmcp-filesystem`) for the full workflow.
3. **Other MCP-covered tasks?** (containers, pods, networks, volumes, images, etc.) → Check the tool catalog sections below. Use the relevant backend tools before falling back to shell.
4. **None of the above?** You may use `Shell` / `Read` / `Grep`, but only after stating which question you answered NO to and why no MCP tool fits.

## Mutability markers

- `[readonly]` &mdash; pure inspection (server declares `readOnlyHint: true`).
- `[mutates]` &mdash; changes backend state (e.g. file edits, container start).
- `[destructive]` &mdash; irreversible mutation (e.g. delete pod, remove file).
- `[?]` &mdash; mutability not declared by the server; treat as mutating.

## Tool naming convention

Tool, prompt, and resource names at the aggregate `/mcp` are `<server>__<original>` (double underscore). Don't strip the prefix when calling — it's how the aggregator routes the call back to the right backend.

## `docker`

`docker` exposes 19 tools via the aggregator at `/mcp` (namespaced `docker__<tool>`). Prefer these over equivalent shell commands.

- `docker__list_containers` `(all?, filters?)` [?]
  List all Docker containers
  `docker__list_containers` `(all?, filters?)` [?]
    List all Docker containers
- `docker__create_container` `(image, detach?, name?, entrypoint?, command?, network?, environment?, ports?, volumes?, labels?, auto_remove?)` [mutates]
  Create a new Docker container
  `docker__create_container` `(image, detach?, name?, entrypoint?, command?, network?, environment?, ports?, volumes?, labels?, auto_remove?)` [mutates]
    Create a new Docker container
- `docker__run_container` `(image, detach?, name?, entrypoint?, command?, network?, environment?, ports?, volumes?, labels?, auto_remove?)` [mutates]
  Run an image in a new Docker container (preferred over `create_container` + `start_container`)
  `docker__run_container` `(image, detach?, name?, entrypoint?, command?, network?, environment?, ports?, volumes?, labels?, auto_remove?)` [mutates]
    Run an image in a new Docker container (preferred over `create_container` + `start_container`)
- `docker__recreate_container` `(image, detach?, name?, entrypoint?, command?, network?, environment?, ports?, volumes?, labels?, auto_remove?, container_id?)` [?]
  Stop and remove a container, then run a new container. Fails if the container does not exist.
  `docker__recreate_container` `(image, detach?, name?, entrypoint?, command?, network?, environment?, ports?, volumes?, labels?, auto_remove?, container_id?)` [?]
    Stop and remove a container, then run a new container. Fails if the container does not exist.
- `docker__start_container` `(container_id)` [mutates]
  Start a Docker container
  `docker__start_container` `(container_id)` [mutates]
    Start a Docker container
- `docker__fetch_container_logs` `(container_id, tail?)` [?]
  Fetch logs for a Docker container
  `docker__fetch_container_logs` `(container_id, tail?)` [?]
    Fetch logs for a Docker container
- `docker__stop_container` `(container_id)` [mutates]
  Stop a Docker container
  `docker__stop_container` `(container_id)` [mutates]
    Stop a Docker container
- `docker__remove_container` `(container_id, force?)` [mutates]
  Remove a Docker container
  `docker__remove_container` `(container_id, force?)` [mutates]
    Remove a Docker container
- `docker__list_images` `(name?, all?, filters?)` [?]
  List Docker images
  `docker__list_images` `(name?, all?, filters?)` [?]
    List Docker images
- `docker__pull_image` `(repository, tag?)` [mutates]
  Pull a Docker image
  `docker__pull_image` `(repository, tag?)` [mutates]
    Pull a Docker image
- `docker__push_image` `(repository, tag?)` [mutates]
  Push a Docker image
  `docker__push_image` `(repository, tag?)` [mutates]
    Push a Docker image
- `docker__build_image` `(path, tag, dockerfile?)` [mutates]
  Build a Docker image from a Dockerfile
  `docker__build_image` `(path, tag, dockerfile?)` [mutates]
    Build a Docker image from a Dockerfile
- `docker__remove_image` `(image, force?)` [mutates]
  Remove a Docker image
  `docker__remove_image` `(image, force?)` [mutates]
    Remove a Docker image
- `docker__list_networks` `(filters?)` [?]
  List Docker networks
  `docker__list_networks` `(filters?)` [?]
    List Docker networks
- `docker__create_network` `(name, driver?, internal?, labels?)` [mutates]
  Create a Docker network
  `docker__create_network` `(name, driver?, internal?, labels?)` [mutates]
    Create a Docker network
- `docker__remove_network` `(network_id)` [mutates]
  Remove a Docker network
  `docker__remove_network` `(network_id)` [mutates]
    Remove a Docker network
- `docker__list_volumes` `(...)` [?]
  List Docker volumes
  `docker__list_volumes` `(...)` [?]
    List Docker volumes
- `docker__create_volume` `(name, driver?, labels?)` [mutates]
  Create a Docker volume
  `docker__create_volume` `(name, driver?, labels?)` [mutates]
    Create a Docker volume
- `docker__remove_volume` `(volume_name, force?)` [mutates]
  Remove a Docker volume
  `docker__remove_volume` `(volume_name, force?)` [mutates]
    Remove a Docker volume

## `pincher`

`pincher` exposes 22 tools via the aggregator at `/mcp` (namespaced `pincher__<tool>`). Prefer these over equivalent shell commands.

- `pincher__adr` `(action, project?, key?, value?)` [?]
  **Use to record decisions/conventions/gotchas** that should survive across sessions. Persistent project knowledge store. Actions: `set` (store), `get` (retrieve), `list` (all entries), `delete`. Examples: `adr set PURPOSE 'payment processing service'`; `adr set STACK 'Go+SQLite+Redis'`; `adr list` to recall everything stored. Call `adr list` early in unfamiliar work — prior agents' notes often save a `search` chain.
  Persistent project knowledge store. Actions: `set` (store), `get` (retrieve), `list` (all entries), `delete`. Call `adr list` early in unfamiliar work — prior agents' notes often save a search chain.
- `pincher__architecture` `(project?, include_tests?)` [?]
  **Call once at the start of unfamiliar work** to orient. Returns language breakdown, entry points, hotspot functions (most-called = highest change risk), and graph statistics. Hotspots default to production code only (test helpers are filtered); pass include_tests=true to surface them too. Much cheaper than reading files to understand the structure.
  Call once per session on any unfamiliar project to get language breakdown, entry points, and hotspot functions. Much cheaper than reading files to understand the codebase shape.
- `pincher__changes` `(project?, scope?, depth?, fields?)` [?]
  **Use before final response after code edits** to surface the blast radius. Maps `git diff` to affected symbols, BFS-traces impact, returns `changed_symbols` + impacted callers tagged CRITICAL/HIGH/MEDIUM/LOW + summary counts + `tests_to_run` (test functions that exercise the changed symbols, ranked by overlap descending — re-run the top entries before pushing). Scopes: `unstaged` (default) / `staged` / `all` (includes untracked) / `base:<branch>` (committed-only diff vs <branch>'s merge-base — use this to preview a PR's blast radius before opening it).
  Map `git diff` → affected symbols → impacted callers with risk labels. Run before committing to see blast radius. Use `scope=staged` for staged changes, `scope=all` for everything.
- `pincher__context` `(id, project?, fields?, lite?)` [?]
  **Use before editing a function** to read it together with everything it directly imports and calls — one shot, ~90% token reduction vs reading files. Returns `{symbol: {source, ...}, imports: [{source, ...}], callees: [{source, ...}]}` — `imports` is cross-package dependencies (IMPORTS edges), `callees` is the in-package helpers it directly calls (CALLS edges). De-duplicated so a symbol that's both imported and called only appears once. Prefer this over `symbol` whenever you need to understand how a function works in context, not just see its source. Pass `fields=symbol,callees` to drop sections you don't need. Pass `lite=true` for source-only retrieval — minimum-envelope shape used by the PreToolUse hook redirect when replacing a Read call.
  Returns the symbol body plus its direct imports and callees in one shot (~90% token reduction vs reading files). Pass `fields=symbol,callees` to drop imports when not needed.
- `pincher__dead_code` `(project?, language?, kinds?, min_confidence?, limit?)` [?]
  **Find unreachable internal functions/methods** — symbols with zero inbound edges (CALLS/READS/WRITES/REFERENCES/IMPORTS) that aren't exported, aren't entry points, and aren't tests. The inverse of `architecture` hotspots. Defaults bias toward precision: `language=Go` (1.0-confidence AST extraction) + `kinds=Function,Method`. Lower `min_confidence` and broaden `kinds` at the cost of false positives from regex-tier extractors that under-resolve cross-file edges. Test fixtures under `testdata/` and `__fixtures__/` are post-filtered out — they have no test runners but aren't real code either.
- `pincher__doctor` `(lookback_hours?, top?)` [?]
  **Diagnostic report from the local pincher database** — schema version, DB + WAL file sizes, per-project staleness, recent extraction failures, recent slow queries. Same data the `pincher doctor --json` CLI returns; exposed via MCP so dashboards and ops automations can poll without shelling out. Read-only; safe to call repeatedly.
- `pincher__fetch` `(url, project?, title?)` [?]
  **Use to pull external reference material into the project knowledge base** — API docs, library READMEs, specs, RFCs. Fetches a URL, extracts its text, stores it as a searchable `Document` symbol. After fetching, use `search kind:Document` to find it, or `symbol` with the returned ID to retrieve the full text. The Document kind lives in the `docs` corpus, so `corpus=docs` searches surface it alongside Markdown sections.
- `pincher__guide` `(task, project?)` [?]
  **Call first when you don't know which tool to use.** Takes a free-form task description ("fix login retry bug", "refactor the auth middleware", "understand how indexing works") and returns 2-3 recommended pincher tool calls with reasoning. A starter tool — eliminates the decision friction of choosing between search/context/trace/changes from scratch.
- `pincher__health` `(project?)` [?]
  **Use to verify extraction quality before trusting graph results**, or to detect a stale index. Returns schema version, index staleness, and per-language coverage with parser identity (AST vs Regex) and avg/p10/p50 confidence per (language, kind). A low p10 on a corpus you care about means `search` results in that area need a higher `min_confidence` to be reliable.
- `pincher__index` `(path?, force?)` [?]
  **Call once per project before using any other tool.** Indexes a repository: extracts symbols with byte offsets, builds the knowledge graph, populates FTS5 search — all in one pass. Incremental by default (content-hash checks skip unchanged files; the watcher keeps it fresh during a session). Pass `force=true` to re-parse every file (rare; only after schema/extractor changes).
- `pincher__init` `(target?, write?, project_path?)` [?]
  **Seed an editor's pincher usage policy file** without dropping into a separate shell. Same surface as `pincher init` CLI but defaults to dry-run for safety; pass `write=true` to actually mutate files. Targets: claude / cursor / cursor-legacy / windsurf / aider / detect / all. The continue target is rejected (always-global, escapes project scope from an MCP context). Returns per-target {target, path, action, diff_preview, bytes_in, bytes_out}.
- `pincher__list` `(active?, active_within_days?, include_dead?, prune_dead?, min_edges?, limit?, offset?)` [?]
  **Use to confirm which projects are indexed** before scoping a query with `project=`. Returns `[{name, path, files, symbols, edges, indexed_at}, ...]` for active projects. Paginated: defaults to 50 entries per call (limit/offset), with the next page surfaced in `_meta.next_steps` when more remain. Defaults filter out projects whose on-disk path no longer exists, whose last index is older than `active_within_days` (14 by default), or that have zero edges (typically empty worktrees). Pass `active=false`/`include_dead=true`/`min_edges=0` to widen the filter, `limit=0` for the legacy unbounded dump, `prune_dead=true` to physically remove dead-on-disk projects from the store.
- `pincher__neighborhood` `(id, project?, include_source?, include_self?, limit?, offset?)` [?]
  **Returns same-file symbols, NOT graph adjacency.** Despite the name (#498), this tool answers "what other symbols live in the same file as the seed?" — useful for in-file refactor planning. For graph adjacency (callers / callees / readers / writers), use `trace direction=both` instead. Given a seed symbol ID, returns every symbol in the same file (signatures + line ranges) ordered by source position. One round-trip vs N `symbol` calls or one whole-file `Read`. Paginated: defaults to 50 neighbors per call (limit/offset), with the next page surfaced in `_meta.next_steps` when the file has more. Default response excludes `source`; pass `include_source=true` to also fetch each neighbor's body.
- `pincher__query` `(pinchql?, cypher?, project?, max_rows?, min_confidence?)` [?]
  **Use when you need structural relationships, not text matches** — pinchQL graph queries over the symbol graph. pinchQL is a pragmatic Cypher-shaped subset: `MATCH`, `WHERE`, `RETURN`, `LIMIT`, single-hop joins (`-[:CALLS]->`), and bounded variable-length BFS (`-[:CALLS*1..3]->`). Examples: callers `MATCH (a)-[:CALLS]->(b) WHERE b.name="Open" RETURN a.name`; classes in a file `MATCH (n:Class) WHERE n.file_path CONTAINS "server" RETURN n.name`; multi-hop `MATCH (a)-[:CALLS*1..3]->(b) WHERE a.name="main" RETURN b.name`. The legacy `cypher` parameter name is still accepted as a soft alias for one release. Prefer `search` for name/text lookups, `trace` for fixed-shape callgraph BFS — both are cheaper.
- `pincher__rebuild_fts` `(confirm?)` [?]
  **Admin: rebuild every FTS5 index from source data.** Equivalent to `pincher rebuild-fts` CLI. Use after symptoms of FTS corruption (search results missing symbols you can confirm exist via `query`). Long-running on large indexes (~1 second per ~10k symbols). Mutates DB; requires confirm=true to actually run — without it, returns the projected work without touching anything.
- `pincher__schema` `(project?)` [?]
  **Use before writing a `query`** to see what node/edge kinds exist in this project. Returns node-kind counts (Function, Class, Method, …), edge-kind counts (CALLS, IMPORTS, …), and totals.
- `pincher__search` `(query, project?, kind?, language?, corpus?, limit?, offset?, fields?, min_confidence?)` [?]
  **Use before `Grep`/`Read`** when looking for code by name or content. Always start here when you don't know the exact symbol ID. Returns signature + a 5-line snippet for each result — often enough to answer without a follow-up call. Uses FTS5 BM25 ranking. Examples: 'processOrder' for a function, 'auth*' for prefix, '"token validation"' for a phrase. Filter by `kind=Function` / `language=Go` / `corpus=config|docs` to narrow. Use `context` on the result ID only if you need full source + dependencies.
  Filter with `kind=Function` / `language=Go` / `language=Python`; supports wildcards (`auth*`) and phrases (`"process order"`). Start here when you don't know a symbol's exact ID.
- `pincher__self_test` `(...)` [?]
  **Smoke-test the pincher install** by exercising the index → search → byte-offset-retrieve loop. Equivalent to `pincher self-test` CLI. Returns per-step pass/fail. Useful as a liveness check after a binary upgrade or in CI. Read-only; uses a temp project that's cleaned up before return.
- `pincher__stats` `(project?)` [?]
  **Use to track context-budget savings** for the current session and all-time. Returns tokens used, tokens saved (vs reading whole files), call count, plus per-project index size (files, symbols, edges). Useful as a sanity check that pincher tools are being preferred over `Read`/`Grep` — if `tokens_saved` is 0 after a chunk of work, the agent is probably bypassing the index.
- `pincher__symbol` `(id, project?, fields?)` [?]
  **Use after `search`** to read one symbol's source by stable ID. O(1) byte-offset seeking — never re-parses the file. ID format: `{file_path}::{qualified_name}#{kind}`. **Prefer `context`** when you also need the symbol's dependencies, or **`symbols`** for batching multiple lookups (one round trip instead of N). Pass `fields` (comma-separated) to project specific keys and skip the source disk read when not needed.
- `pincher__symbols` `(ids, project?, fields?)` [?]
  **Use instead of repeated `symbol` calls** when you have several IDs. Batch fetches up to 100 symbols in a single SQL round trip + per-symbol byte-offset reads. Returns `[{id, source, signature, file_path, start_line}, ...]` in the same order as the input `ids`. Missing IDs surface as `{id, error: "not found"}` rather than failing the whole batch. Pass `fields=id,name,signature` to drop unused fields and skip the disk-read for source.
- `pincher__trace` `(name?, id?, project?, direction?, depth?, risk?, min_confidence?, kinds?, include_tests?, fields?)` [?]
  **Use before changing behaviour** that other code depends on, to find callers (inbound) or what it calls (outbound). Risk labels: CRITICAL=direct callers, HIGH=2 hops, MEDIUM=3 hops. Pass `name` for the common case; when the name is ambiguous (multiple symbols share it) trace falls back to the first match and surfaces alternatives in `_meta.ambiguous_match`. To trace a specific alternative, pass `id=` with the exact symbol ID from search/symbols/query — that's the disambiguation escape hatch (#474). Default traversal follows CALLS-family edges; pass `kinds=READS,WRITES` to trace data-flow edges instead (or `kinds=CALLS,READS` to mix). Test files and testdata/ fixtures are filtered by default; pass `include_tests=true` to see test coverage of a symbol. When `depth` is omitted, the result is auto-trimmed to the smallest depth with ≥5 hops (so hotspots don't dump 100+ rows); `_meta.depth_used` reports the trim. Pass `depth=N` explicitly to skip the trim.
  Find callers (inbound) or callees (outbound) of a symbol. CRITICAL=direct callers, HIGH=2 hops, MEDIUM=3 hops. Use before changing a function that other code depends on.

## Don't do this

- Don't call `tools/list` between every step; the set is stable for the lifetime of the session.
- Don't reach for shell tools (`bash`, `python -c`, etc.) for tasks the MCP backends cover — you lose structured output and pay subprocess cost.
