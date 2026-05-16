---
name: zelosmcp-pincher
description: Codebase intelligence with pincher: find symbols, read functions, trace callers, analyze blast radius. Use for any code understanding, exploration, or impact analysis task.
---
# Pincher — Codebase Intelligence

## Calling convention

- **Direct:** `pincher__<tool>(args)`
- **Compressed:** `pincher__invoke_tool(tool_name="<tool>", tool_input={args})`

If pincher is wire-compressed, do NOT call `pincher__architecture` etc. directly — the aggregator rejects them. Use `pincher__invoke_tool` instead.

## Container paths (mandatory)

Pincher runs inside the zelosMCP container. Host paths such as
`/Users/KMECHL/workspace/zelosmcp` do not exist there.

| Input | Correct value |
|---|---|
| `project` | repo basename only, e.g. `zelosmcp` |
| `path` for `index` | `/user_data_ro/<repo>` |
| file paths in explanations | `/user_data_ro/<repo>/...` |

Host root mapping: `/Users/KMECHL/workspace` maps to `/user_data_ro`
for pincher reads. Translate before every tool call.

Examples:
- Compressed index: `pincher__invoke_tool(tool_name="index", tool_input={"path": "/user_data_ro/zelosmcp"})`
- Direct index: `pincher__index(path="/user_data_ro/zelosmcp")`
- Project-scoped architecture: `pincher__invoke_tool(tool_name="architecture", tool_input={"project": "zelosmcp"})`
- Blast radius: `pincher__invoke_tool(tool_name="changes", tool_input={"project": "zelosmcp", "scope": "staged"})`

## Intent → tool mapping

| User intent | Tool |
|---|---|
| Understand repo / project / codebase | `architecture` |
| Find a symbol by name | `search` |
| Read function + dependencies | `context` |
| Callers / callees / impact of change | `trace` |
| Blast radius of git diff | `changes` |
| Store / recall decisions & conventions | `adr` |
| Ingest external docs | `fetch` |

## Workflow

- **Orient first.** Call `architecture` on any unfamiliar project for language breakdown, entry points, and hotspot functions.
- **Scope to the active project.** Always pass `project=<repo basename>` (for example `zelosmcp`). Never pass host paths as `project`. Use `list` to confirm indexed project names.
- **Index before querying** (read-write only). Run `index` once per project — incremental by default.
- **Find symbols by name.** Use `search` (FTS5 BM25; wildcards `auth*`, phrases `"process order"`, `kind=Function`/`language=Go` filters).
- **Read source efficiently.** Prefer `context` over `symbol` — includes deps in one call (~90% token savings).
- **Batch lookups.** Use `symbols` (plural, max 100 IDs per call) instead of calling `symbol` in a loop.
- **Impact analysis.** Use `trace` for inbound/outbound call paths (CRITICAL=1 hop, HIGH=2, MEDIUM=3).
- **Pre-commit safety.** Run `changes` before committing for blast-radius analysis.
- **Graph queries.** Use `query` with the Cypher subset; call `schema` first to see node/edge kinds.
- **Persist knowledge.** `adr` set/get/list/delete survives across sessions.
- **Stable IDs.** Format: `{file_path}::{qualified_name}#{kind}` (e.g. `internal/db/db.go::db.Open#Function`).

## Forbidden fallbacks

Do NOT use these for tasks pincher covers:
- `Shell` invocations of `find`, `tree`, `wc -l`, `ls -R`, `git ls-files`
- `Grep` to find a symbol by name (use `search`)
- `Read` on 3+ files to understand one function (use `context`)
- Passing host paths beginning with `/Users/`, `/home/`, or `/tmp/` — translate to `/user_data_ro/<repo>/...` first.

If you must violate, say so explicitly.
