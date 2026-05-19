---
name: zelosmcp-filesystem
description: Sandboxed file operations via the filesystem backend. Use when reading, editing, listing, or searching files in the workspace.
---
# Filesystem — Sandboxed File Access

## Calling convention

- **Direct:** `filesystem__<tool>(args)`
- **Compressed:** `filesystem__invoke_tool(tool_name="<tool>", tool_input={args})`

If filesystem is wire-compressed, do NOT call `filesystem__read_text_file` etc. directly — use `filesystem__invoke_tool` instead.

## Container paths (mandatory)

The filesystem backend runs inside the zelosMCP container. Host paths
such as `/Users/KMECHL/workspace/zelosmcp` do not exist there.

| Use case | Path to pass |
|---|---|
| Read-only file/directory access | `/user_data_ro/<repo>/...` |
| Write/edit/move/create access | `/user_data_rw/<repo>/...` |

Host root mapping: `/Users/KMECHL/workspace` maps to `/user_data_ro`
for read-only access and `/user_data_rw` for writes. Translate before
every filesystem tool call.

Examples:
- Read: `filesystem__read_text_file(path="/user_data_ro/zelosmcp/README.md")`
- Write/edit: `filesystem__edit_file(path="/user_data_rw/zelosmcp/README.md", edits=[...])`
- Bad: `filesystem__read_text_file(path="/Users/KMECHL/workspace/zelosmcp/README.md")`

## Intent → tool mapping

| Task | Tool |
|---|---|
| Read a file | `read_text_file` (use `head`/`tail` for large files) |
| Compare / summarize multiple files | `read_multiple_files` |
| List directory contents | `list_directory` or `directory_tree` |
| Find files by pattern | `search_files` (glob, `**/*.ext`) |
| Edit / patch a file | `edit_file` (preferred) or `write_file` |
| Create / move / rename | `create_directory` / `move_file` |
| File metadata | `get_file_info` |

## Workflow

- Every path must live under an allowed directory (check `list_allowed_directories`).
- Resolve every path to `/user_data_ro/<repo>/...` (read) or `/user_data_rw/<repo>/...` (write) BEFORE the tool call.
- Prefer `edit_file` over `write_file` — it returns a git-style diff and is non-destructive.
- `create_directory` is idempotent; `move_file` fails if destination exists (safe renames).
- Use `directory_tree` with `excludePatterns` to skip `node_modules`, `.venv`, etc.

## Forbidden fallbacks

Do NOT use these for tasks filesystem covers:
- `Shell` invocations of `cat`, `head`, `tail`, `ls`, `find`, `tree`, `wc`, `du`, `stat`
- `Read` on workspace paths when `read_text_file` works
- `sed`, `awk`, or `echo > file` for edits — use `edit_file`
- Passing host paths beginning with `/Users/`, `/home/`, or `/tmp/` — translate to `/user_data_ro/<repo>/...` or `/user_data_rw/<repo>/...` first.

In **read-only** mode, do NOT call `write_file`, `edit_file`, `move_file`, or `create_directory`.
