---
name: codebase-explore
description: Read-only codebase exploration with pincher MCP wrappers (`pincher__invoke_tool`, `pincher__search_tools`, `pincher__get_tool_schema`). Use for symbol lookup, behavior tracing, and focused implementation planning.
argument-hint: question, symbol, or subsystem
context: fork
---
# Codebase Explore

Use pincher's compressed MCP wrappers to understand code with
minimal file reads.

## Wrapper pattern

- Use `pincher__search_tools` when you are unsure which pincher
  operation fits the task.
- Use `pincher__get_tool_schema` only when the input shape is
  unclear.
- Use `pincher__invoke_tool(tool_name="...", tool_input={...})`
  to execute pincher operations.

## Workflow

1. Start with `pincher__invoke_tool(tool_name="architecture", ...)`
   once per unfamiliar repo or subsystem.
2. Use `pincher__invoke_tool(tool_name="search", ...)` to find
   likely symbols.
3. Use `pincher__invoke_tool(tool_name="context", ...)` for one
   symbol at a time.
4. Use `pincher__invoke_tool(tool_name="trace", ...)` when
   callers, callees, or impact matter.
5. Return the owning symbols, important files, and the next step.

## Guardrails

- Stay read-only.
- Prefer `context` over reading many files.
- Keep the result compact and actionable.

## Output

- Answer the question directly.
- Name the owning symbols or files that support the answer.
- Give one next step only when it is useful.

## Close

- Stop once you have the minimum evidence needed to answer.
- If two paths remain plausible, return the top candidates and the
  deciding check instead of continuing to explore.
- Do not keep tracing once the answer or next step is clear.
