---
name: tool-guide
description: Route a task to the right MCP backend and compressed wrapper calls such as `pincher__invoke_tool`, `filesystem__invoke_tool`, and `<backend>__search_tools`. Use when you know the goal but not the right tool surface.
argument-hint: task or backend to route
---
# Tool Guide

Use this skill when you know the task but not the backend or
wrapper call.

## Routing

- Code structure, symbols, callers, or change impact ->
  `pincher__search_tools` then `pincher__invoke_tool`
- Workspace files and edits -> `filesystem__search_tools` then
  `filesystem__invoke_tool`
- External package or framework docs -> `mcpdoc__search_tools`
  then `mcpdoc__invoke_tool` when available
- Containers or clusters -> `docker__search_tools` or
  `kubernetes__search_tools`, then the matching `__invoke_tool`

## Wrapper pattern

1. Use `<backend>__search_tools` to find the underlying tool.
2. Use `<backend>__get_tool_schema` only when you need the input shape.
3. Use `<backend>__invoke_tool(tool_name="...", tool_input={...})`
   to execute it.
4. Do not call compressed underlying tool names directly.

## Cost rule

Keep the parent thread small. Use forked skills for multi-step
analysis, research, planning, and review.
