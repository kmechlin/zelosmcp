---
name: code-review
description: Review a change set for regressions, risky behavior, missing tests, and documentation gaps with pincher MCP wrappers (`pincher__invoke_tool`, `pincher__search_tools`, `pincher__get_tool_schema`). Use when validating code before merge.
argument-hint: diff, files, or feature to review
context: fork
---
# Code Review

Use this skill for focused review work that should not pollute the
parent context.

## Wrapper pattern

- Use `pincher__search_tools` when you are unsure whether
  `changes`, `trace`, or `context` is the right starting point.
- Use `pincher__get_tool_schema` only when the tool input shape is
  unclear.
- Use `pincher__invoke_tool(tool_name="...", tool_input={...})`
  to execute review-relevant pincher operations.

## Workflow

1. Identify the changed files or symbols.
2. Use `pincher__invoke_tool(tool_name="changes", ...)`,
   `pincher__invoke_tool(tool_name="trace", ...)`, and
   `pincher__invoke_tool(tool_name="context", ...)` to
   understand impact.
3. Check regressions, validation, error handling, public API changes,
   tests, and relevant docs.
4. Return only actionable findings, open risks, and recommended
   validation.

## Output

- Findings first
- Risks still unverified
- Recommended validation or follow-up
