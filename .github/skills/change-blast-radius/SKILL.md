---
name: change-blast-radius
description: Analyze the blast radius of current or proposed code changes with pincher MCP wrappers (`pincher__invoke_tool`, `pincher__search_tools`, `pincher__get_tool_schema`). Use before review, testing, or commit.
argument-hint: staged|unstaged|all|base:<branch>
context: fork
---
# Change Blast Radius

Use pincher's compressed MCP wrappers to map a diff to changed
symbols, impacted callers, and likely validation work.

## Wrapper pattern

- Use `pincher__search_tools` if you are unsure whether `changes`,
  `trace`, or `context` is the right follow-up operation.
- Use `pincher__get_tool_schema` only when the tool input shape is
  unclear.
- Use `pincher__invoke_tool(tool_name="...", tool_input={...})`
  to execute `changes`, `trace`, and `context`.

## Workflow

1. Run `pincher__invoke_tool(tool_name="changes", ...)` with the
   requested scope.
2. Follow up with `pincher__invoke_tool(tool_name="trace", ...)`
   or `pincher__invoke_tool(tool_name="context", ...)` only when
   a changed symbol needs more impact detail.
3. Group changed symbols by directness and risk.
4. Call out impacted callers, boundary APIs, and likely tests.
5. Return changed symbols, risk summary, and recommended validation.

## Output

- Changed symbols grouped by risk.
- Impacted callers, boundaries, or APIs.
- Recommended tests or validation to run next.

## Close

- Stop after the first scope that yields an actionable risk summary.
- If the requested scope has no diff, say so clearly and end.
- Do not keep widening the search once the validation advice is clear.
