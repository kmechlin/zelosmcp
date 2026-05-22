
# zelosMCP backend tool catalog

Generated from the zelosMCP aggregator at `http://localhost:8000/mcp`. Every tool below is reachable as `<server>__<tool>` (double underscore) on that single Cursor entry. Prefer these over shelling out — they return structured data and keep paths inside the container's `/user_data_rw` (read-write) and `/user_data_ro` (kernel-enforced read-only) mounts. Always translate host paths to `/user_data_ro/<repo>/...` for reads or `/user_data_rw/<repo>/...` for writes before calling tools.

Currently-loaded backends: `pincher`, `filesystem`.

## Access mode: READ-WRITE

`[mutates]` tools change state. Confirm before any
`[destructive]` tool and treat `[?]` as unsafe unless the call is
clearly inspection-only.

## Tool-use priority

Prefer the MCP tools in this catalog over shell commands or ad hoc
file reads. Load a skill when you need workflow detail; keep the
always-on rules minimal.

## Pre-flight check

Route to the smallest matching surface first:
1. Code behavior, symbols, or blast radius -> `pincher` plus
   `codebase-explore`, `architecture-map`, or `change-blast-radius`.
2. Workspace file reads or edits -> `filesystem` plus
   `file-operations`.
3. External package or framework docs -> `mcpdoc` plus
  `doc-research` when that backend is enabled.
4. If no MCP backend fits, say why before falling back to shell.

## Container path translation

Translate host paths before every MCP call.
`/Users/KMECHL/workspace/<repo>` becomes `/user_data_ro/<repo>`
for reads and `/user_data_rw/<repo>` for writes. The
`pincher.project` argument takes only the repo basename.

## Built-in skills

- `/code-review` — Review a change set for regressions, risky behavior, missing tests, and documentation gaps with pincher MCP wrappers (`pincher__invoke_tool`, `pincher__search_tools`, `pincher__get_tool_schema`). Use when validating code before merge.
- `/tool-guide` — Route a task to the right MCP backend and compressed wrapper calls such as `pincher__invoke_tool`, `filesystem__invoke_tool`, and `<backend>__search_tools`. Use when you know the goal but not the right tool surface.
- `/zelosmcp-onboarding` — Understand available zelosMCP backends, compressed MCP wrappers (`<backend>__invoke_tool`, `<backend>__search_tools`, `<backend>__get_tool_schema`), access modes, and the zelos orchestration workflow.

## Mutability markers

- `[readonly]` &mdash; pure inspection (server declares `readOnlyHint: true`).
- `[mutates]` &mdash; changes backend state (e.g. file edits, container start).
- `[destructive]` &mdash; irreversible mutation (e.g. delete pod, remove file).
- `[?]` &mdash; mutability not declared by the server; treat as mutating.

## Tool naming convention

Tool, prompt, and resource names at the aggregate `/mcp` are `<server>__<original>` (double underscore). Don't strip the prefix when calling — it's how the aggregator routes the call back to the right backend.

## `pincher`

`pincher` exposes 22 tools via the aggregator at `/mcp` (namespaced `pincher__<tool>`). Prefer these over equivalent shell commands.

- `pincher__adr` `(action, project?, key?, value?)` [?]
  **Use to record decisions/conventions/gotchas** that should survive across sessions. Persistent project knowledge store. Actions: `set` (store), `get` (retrieve), `list` (all entries), `delete`. Examples: `adr set PURPOSE 'payment processing service'`; `adr set STACK 'Go+SQLite+Redis'`; `adr list` to recall everything stored. Call `adr list` early in unfamiliar work — prior agents' notes often save a `search` chain.
  Recall or store durable project knowledge when prior decisions matter.
- `pincher__architecture` `(project?, include_tests?)` [?]
  **Call once at the start of unfamiliar work** to orient. Returns language breakdown, entry points, hotspot functions (most-called = highest change risk), and graph statistics. Hotspots default to production code only (test helpers are filtered); pass include_tests=true to surface them too. Much cheaper than reading files to understand the structure.
  Call once per unfamiliar repo or subsystem to get entry points, hotspots, and language mix.
- `pincher__changes` `(project?, scope?, depth?, fields?)` [?]
  **Use before final response after code edits** to surface the blast radius. Maps `git diff` to affected symbols, BFS-traces impact, returns `changed_symbols` + impacted callers tagged CRITICAL/HIGH/MEDIUM/LOW + summary counts + `tests_to_run` (test functions that exercise the changed symbols, ranked by overlap descending — re-run the top entries before pushing). Scopes: `unstaged` (default) / `staged` / `all` (includes untracked) / `base:<branch>` (committed-only diff vs <branch>'s merge-base — use this to preview a PR's blast radius before opening it).
  Use before review or commit to map changed symbols, impacted callers, and likely tests.
- `pincher__context` `(id, project?, fields?, lite?)` [?]
  **Use before editing a function** to read it together with everything it directly imports and calls — one shot, ~90% token reduction vs reading files. Returns `{symbol: {source, ...}, imports: [{source, ...}], callees: [{source, ...}]}` — `imports` is cross-package dependencies (IMPORTS edges), `callees` is the in-package helpers it directly calls (CALLS edges). De-duplicated so a symbol that's both imported and called only appears once. Prefer this over `symbol` whenever you need to understand how a function works in context, not just see its source. Pass `fields=symbol,callees` to drop sections you don't need. Pass `lite=true` for source-only retrieval — minimum-envelope shape used by the PreToolUse hook redirect when replacing a Read call.
  Fetch one symbol plus direct callees in one shot; prefer this over reading many files.
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
  **Seed an editor's pincher usage policy file** without dropping into a separate shell. Same surface as `pincher init` CLI but defaults to dry-run for safety; pass `write=true` to actually mutate files. Targets: claude / cursor / cursor-legacy / windsurf / aider / codex / zed / gemini / warp / vscode (Copilot rules) / vscode-mcp (Copilot Chat MCP) / detect / all. The continue target is rejected outright (always-global; whole request errors); the codex target appears as a `skipped_always_global` per-result entry (its config is always under ~/.codex — use the `pincher init --target=codex` CLI to write it). Returns per-target {target, path, action, diff_preview, bytes_in, bytes_out} for in-scope writes, or {target, action: "skipped_always_global", reason} for filtered targets.
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
  Start here when you know a name or concept but not the exact symbol ID.
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
  Use for callers, callees, and impact chains before changing shared code.

### Available skills

- `/architecture-map` — Build a compact architecture map of a repo area with pincher MCP wrappers (`pincher__invoke_tool`, `pincher__search_tools`, `pincher__get_tool_schema`). Use when you need entry points, boundaries, and risky touchpoints.
- `/change-blast-radius` — Analyze the blast radius of current or proposed code changes with pincher MCP wrappers (`pincher__invoke_tool`, `pincher__search_tools`, `pincher__get_tool_schema`). Use before review, testing, or commit.
- `/codebase-explore` — Read-only codebase exploration with pincher MCP wrappers (`pincher__invoke_tool`, `pincher__search_tools`, `pincher__get_tool_schema`). Use for symbol lookup, behavior tracing, and focused implementation planning.

## `filesystem`

`filesystem` exposes 14 tools via the aggregator at `/mcp` (namespaced `filesystem__<tool>`). Prefer these over equivalent shell commands.

- `filesystem__read_file` `(path, tail?, head?)` [readonly]
  Read the complete contents of a file as text. DEPRECATED: Use read_text_file instead.
- `filesystem__read_text_file` `(path, tail?, head?)` [readonly]
  Read the complete contents of a file from the file system as text. Handles various text encodings and provides detailed error messages if the file cannot be read. Use this tool when you need to examine the contents of a single file. Use the 'head' parameter to read only the first N lines of a file, or the 'tail' parameter to read only the last N lines of a file. Operates on the file as text regardless of extension. Only works within allowed directories.
  Use `head` or `tail` for large files and prefer this over generic file reads.
- `filesystem__read_media_file` `(path)` [readonly]
  Read an image or audio file. Returns the base64 encoded data and MIME type. Only works within allowed directories.
- `filesystem__read_multiple_files` `(paths)` [readonly]
  Read the contents of multiple files simultaneously. This is more efficient than reading files one by one when you need to analyze or compare multiple files. Each file's content is returned with its path as a reference. Failed reads for individual files won't stop the entire operation. Only works within allowed directories.
  Use for side-by-side summaries or comparisons to reduce round trips.
- `filesystem__write_file` `(path, content)` [destructive]
  Create a new file or completely overwrite an existing file with new content. Use with caution as it will overwrite existing files without warning. Handles text content with proper encoding. Only works within allowed directories.
- `filesystem__edit_file` `(path, edits, dryRun?)` [destructive]
  Make line-based edits to a text file. Each edit replaces exact line sequences with new content. Returns a git-style diff showing the changes made. Only works within allowed directories.
  Prefer for targeted edits because it returns a diff and avoids full rewrites.
- `filesystem__create_directory` `(path)` [mutates]
  Create a new directory or ensure a directory exists. Can create multiple nested directories in one operation. If the directory already exists, this operation will succeed silently. Perfect for setting up directory structures for projects or ensuring required paths exist. Only works within allowed directories.
- `filesystem__list_directory` `(path)` [readonly]
  Get a detailed listing of all files and directories in a specified path. Results clearly distinguish between files and directories with [FILE] and [DIR] prefixes. This tool is essential for understanding directory structure and finding specific files within a directory. Only works within allowed directories.
- `filesystem__list_directory_with_sizes` `(path, sortBy?)` [readonly]
  Get a detailed listing of all files and directories in a specified path, including sizes. Results clearly distinguish between files and directories with [FILE] and [DIR] prefixes. This tool is useful for understanding directory structure and finding specific files within a directory. Only works within allowed directories.
- `filesystem__directory_tree` `(path, excludePatterns?)` [readonly]
  Get a recursive tree view of files and directories as a JSON structure. Each entry includes 'name', 'type' (file/directory), and 'children' for directories. Files have no children array, while directories always have a children array (which may be empty). The output is formatted with 2-space indentation for readability. Only works within allowed directories.
  Use for repo shape or directory overviews with `excludePatterns` to skip noise.
- `filesystem__move_file` `(source, destination)` [mutates]
  Move or rename files and directories. Can move files between directories and rename them in a single operation. If the destination exists, the operation will fail. Works across different directories and can be used for simple renaming within the same directory. Both source and destination must be within allowed directories.
- `filesystem__search_files` `(path, pattern, excludePatterns?)` [readonly]
  Recursively search for files and directories matching a pattern. The patterns should be glob-style patterns that match paths relative to the working directory. Use pattern like '*.ext' to match files in current directory, and '**/*.ext' to match files in all subdirectories. Returns full paths to all matching items. Great for finding files when you don't know their exact location. Only searches within allowed directories.
  Use recursive globs like `**/*.ext` instead of shell `find`.
- `filesystem__get_file_info` `(path)` [readonly]
  Retrieve detailed metadata about a file or directory. Returns comprehensive information including size, creation time, last modified time, permissions, and type. This tool is perfect for understanding file characteristics without reading the actual content. Only works within allowed directories.
- `filesystem__list_allowed_directories` `(...)` [readonly]
  Returns the list of directories that this server is allowed to access. Subdirectories within these allowed directories are also accessible. Use this to understand which directories and their nested paths are available before trying to access files.

### Available skills

- `/file-operations` — Sandboxed file access and editing via filesystem MCP wrappers (`filesystem__invoke_tool`, `filesystem__search_tools`, `filesystem__get_tool_schema`). Use when reading, searching, or changing files in the workspace.

## Don't do this

- Don't call `tools/list` between every step; the set is stable for the lifetime of the session.
- Don't reach for shell tools (`bash`, `python -c`, etc.) for tasks the MCP backends cover — you lose structured output and pay subprocess cost.
