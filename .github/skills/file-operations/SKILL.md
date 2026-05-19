---
name: file-operations
description: Sandboxed file access and editing via filesystem MCP wrappers (`filesystem__invoke_tool`, `filesystem__search_tools`, `filesystem__get_tool_schema`). Use when reading, searching, or changing files in the workspace.
argument-hint: path, search, or edit goal
---
# File Operations

Use this skill for workspace file access through filesystem's
compressed MCP wrappers with correct path translation and minimal
shell fallback.

## Wrapper pattern

- Use `filesystem__search_tools` when you are unsure which
  filesystem operation fits the task.
- Use `filesystem__get_tool_schema` only when the input shape is
  unclear.
- Use
  `filesystem__invoke_tool(tool_name="...", tool_input={...})`
  to execute filesystem operations.

## Path rule

- Translate host paths before every call.
- Read with `/user_data_ro/<repo>/...`.
- Write with `/user_data_rw/<repo>/...`.

## Workflow

1. Read with
   `filesystem__invoke_tool(tool_name="read_text_file", ...)` or
   `filesystem__invoke_tool(tool_name="read_multiple_files", ...)`.
2. Search with
   `filesystem__invoke_tool(tool_name="search_files", ...)` or
   `filesystem__invoke_tool(tool_name="directory_tree", ...)`.
3. Edit with
   `filesystem__invoke_tool(tool_name="edit_file", ...)`; reserve
   `filesystem__invoke_tool(tool_name="write_file", ...)` for
   full rewrites.
4. Review the returned diff before continuing.

## Guardrails

- Do not shell out for `cat`, `ls`, `find`, or `sed` when
  filesystem covers the task.
- Do not write in read-only mode.

## Output

- The translated path or paths used.
- The files read, searched, or edited.
- The returned diff or a concise summary of the result.

## Close

- Stop after the requested read, search, or edit is complete.
- Do not keep scanning adjacent files unless they are needed to
  finish the stated request.
- If a path, mode, or exact edit target is missing, return that
  blocker and end instead of guessing.
