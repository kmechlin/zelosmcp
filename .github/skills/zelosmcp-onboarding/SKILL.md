---
name: zelosmcp-onboarding
description: Understand available zelosMCP backends, compressed MCP wrappers (`<backend>__invoke_tool`, `<backend>__search_tools`, `<backend>__get_tool_schema`), access modes, and the zelos orchestration workflow.
argument-hint: question about zelosmcp capabilities or setup
---
# zelosMCP Onboarding

zelosMCP exposes multiple MCP backends behind
`http://localhost:8000/mcp`.

## Core facts

- Tools are namespaced as `<backend>__<tool>`.
- Many backends are compressed, so you call them through
  `<backend>__search_tools`, `<backend>__get_tool_schema`, and
  `<backend>__invoke_tool(tool_name="...", tool_input={...})`.
- Read paths go through `/user_data_ro/<repo>` and write paths go
  through `/user_data_rw/<repo>`.

## Wrapper pattern

- Use `<backend>__search_tools` when you are unsure which
  underlying backend operation matches the task.
- Use `<backend>__get_tool_schema` only when the input shape is
  unclear.
- Use `<backend>__invoke_tool(tool_name="...", tool_input={...})`
  to execute backend operations directly.
- For code work, prefer `pincher__invoke_tool` and
  `filesystem__invoke_tool` over shell fallback.

## Working model

- Use `zelos` to decompose work.
- Use persona agents for focused execution.
- Use skills for detailed workflows; forked skills keep token-heavy
  work out of the parent thread.
- Use prompts for narrow repeatable shortcuts.

## Management

Visit the zelosMCP web UI at `http://localhost:8000` to start or
stop backends, edit assets, and push updates into the repo.
