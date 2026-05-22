---
name: architecture-map
description: Build a compact architecture map of a repo area with pincher MCP wrappers (`pincher__invoke_tool`, `pincher__search_tools`, `pincher__get_tool_schema`). Use when you need entry points, boundaries, and risky touchpoints.
argument-hint: repo area, subsystem, or capability
context: fork
---
# Architecture Map

Build a high-signal architecture summary through pincher's
compressed MCP wrappers without reading the whole repo.

## Wrapper pattern

- Use `pincher__search_tools` if you are unsure which pincher
  operation fits.
- Use `pincher__get_tool_schema` only when the tool input shape is
  unclear.
- Use `pincher__invoke_tool(tool_name="...", tool_input={...})`
  for `architecture`, `search`, `context`, and `trace`.

## Workflow

1. Call `pincher__invoke_tool(tool_name="architecture", ...)`
   for the repo or subsystem.
2. Identify entry points, hotspots, and boundary modules.
3. Use `pincher__invoke_tool(tool_name="search", ...)`,
   `pincher__invoke_tool(tool_name="context", ...)`, and
   `pincher__invoke_tool(tool_name="trace", ...)` only for the
   relevant path.
4. Return the layers, main flows, risky touchpoints, and the next
   files worth reading.

## Output

- Entry points and boundary modules.
- Hotspots or risky touchpoints.
- The next files or symbols worth reading.

## Close

- Stop after one compact map of the requested area.
- Do not descend into implementation detail unless it is needed to
  explain a boundary, hotspot, or risk.
- If the request is too broad, name the next slice to map and end.
