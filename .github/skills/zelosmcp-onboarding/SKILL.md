---
name: zelosmcp-onboarding
description: Understand available zelosMCP backends, their tools, and calling conventions. Use when asked about setup, available tools, getting started, or how to use zelosMCP.
---
# zelosMCP Onboarding

zelosMCP is an MCP aggregator proxy. It unifies multiple MCP backends
behind a single endpoint at `http://localhost:8000/mcp`.

## Tool naming convention

Every tool is namespaced as `<backend>__<tool>` (double underscore).
The aggregator routes the call to the correct backend.

## Compressed vs direct backends

Some backends are wire-compressed: their tools are only reachable via
a wrapper trio (`<backend>__get_tool_schema`, `<backend>__search_tools`,
`<backend>__invoke_tool`). Do NOT call underlying tool names directly
for compressed backends — use `invoke_tool(tool_name="...", tool_input={...})`.

## Access modes

Rules are generated with either `read-only` or `read-write` access.
- **Read-only**: Do not call tools tagged `[mutates]`, `[destructive]`, or `[?]`.
- **Read-write**: Confirm with the user before calling `[destructive]` tools.

## Managing rules and assets

Visit the zelosMCP web UI at `http://localhost:8000` to:
- Start/stop backends
- Regenerate rules with different access modes
- Edit per-backend tool instructions, skills, agents, and hooks
- Push updated files to your repo
