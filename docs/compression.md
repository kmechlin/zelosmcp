# Tool-list compression

LLMs see every MCP tool's full description and JSON schema in `tools/list`. With several backends loaded that easily means 15-25 KB of tokens **before** the conversation starts. zelosMCP **compresses every backend by default** — swapping each backend's full tool surface for a small wrapper surface on the aggregator (`/mcp`) — so the schema-fetch token cost stays small without losing functionality. The `compress` block on a backend lets you override or disable that default.

The agent flow becomes a targeted lookup:

1. The model sees `<backend>__get_tool_schema(tool_name)`, `<backend>__search_tools(query, limit?)`, and `<backend>__invoke_tool(tool_name, tool_input)` instead of N raw tools. The schema wrapper's description embeds a compact catalog (one short line per underlying tool) so the model can browse without paying schema-fetch latency on every name.
2. If the catalog is large, it calls `search_tools(query, limit?)` to get compressed matching catalog lines for targeted discovery.
3. When the model wants to use a tool, it calls `get_tool_schema(...)` to fetch the full schema for that one tool, then `invoke_tool(...)` to run it.

For very large backends, `level=max` collapses the wrappers into a single `list_tools()` call — even the inlined catalog goes away from `tools/list`.

> Inspired by [atlassian-labs/mcp-compressor](https://github.com/atlassian-labs/mcp-compressor) (their compressed mode). zelosMCP implements the same idea natively in the aggregator instead of running mcp-compressor as a subprocess in front of each backend.

## Schema

Every backend gets `compress: { level: "medium", scope: "aggregator" }` automatically. Add a `compress` block to any `mcpServers.<name>` entry only when you want to override the default or disable it:

```json
"kubernetes": {
  "command": "npx",
  "args": ["-y", "kubernetes-mcp-server@latest"],
  "compress": {
    "level": "high",
    "scope": "global"
  }
}
```

| Field | Required | Type | Default | Notes |
|---|---|---|---|---|
| `level` | no | string | `"medium"` | One of `"low"`, `"medium"`, `"high"`, `"max"`. Controls how aggressively the catalog is summarised. |
| `scope` | no | string | `"aggregator"` | One of `"catalog"`, `"aggregator"`, `"global"`. Controls which endpoints surface the wrappers (vs. the full tool list). |

The `compress` value can take any of these forms:

| Value | Effect |
|---|---|
| key omitted | Default — `level: "medium"`, `scope: "aggregator"` (compressed at `/mcp`). |
| `"compress": {}` | Same as omitting the key — fills in all defaults. |
| `"compress": {"level": "high"}` | Override one field; remaining fields take their defaults. |
| `"compress": null` | Opt **out** entirely. The backend's full tool surface flows through `/mcp` unchanged. |
| `"compress": false` | Same as `null` — convenience opt-out. |

The always-on builtin (`zelosmcp__*`) is never compressed — those tools are tiny and self-documenting, and compressing them would hide the discovery surface.

## Levels

| Level | What `tools/list` shows for the compressed backend |
|---|---|
| `low` | Full tool list, full descriptions. No compression on the wire — but the catalog cache is still populated for [the discovery tool](#discovery-tool). |
| `medium` (default) | Wrapper trio (`get_tool_schema` + `search_tools` + `invoke_tool`) whose catalog embeds `- name: first-sentence` per tool. |
| `high` | Wrapper trio whose catalog embeds `- name(arg1, arg2, ...)` per tool — names + parameter names only, no descriptions. |
| `max` | A single `list_tools` wrapper. The model has to call it explicitly to discover what's available. Use for backends with hundreds of rarely-used tools. |

The pincher backend at `level=medium` typically goes from ~5KB of tool descriptions in `tools/list` to ~600 bytes — about a 90% reduction. Kubernetes-mcp-server (~19 tools, verbose schemas) compresses similarly.

## Scopes

| `scope` | `/<name>/mcp` (raw passthrough) | `/mcp` (aggregator) | Discovery tools / cursor-rule generator |
|---|---|---|---|
| `catalog` | full uncompressed surface | full uncompressed surface | compressed |
| `aggregator` (default) | full uncompressed surface | wrappers only | compressed |
| `global` | wrappers only | wrappers only | compressed |

The discovery / cursor-rule paths always see the compressed catalog when `compress` is set, regardless of scope — they're documentation surfaces, not wire formats. `catalog` mode means "the wire stays exactly as it is today; only docs / agent-instructions get compressed."

### When to pick which scope

- **`catalog`** — you want the cursor rule and `zelosmcp__list_compressed_tools` to render compactly, but every direct MCP client connecting to the aggregator or to `/<name>/mcp` should see the full schema. Useful when you've curated the agent rule body but don't want to change the wire format for ad-hoc clients.
- **`aggregator`** (default, recommended) — agents going through the aggregator at `/mcp` (the typical Cursor / Claude Desktop / Copilot setup) see the compressed wrapper surface: the non-`max` wrapper trio, or the single `list_tools` wrapper at `level=max`. Direct connections to a single backend at `/<name>/mcp` keep the full surface (useful for debugging, scripts that already know the backend's API).
- **`global`** — you have clients that connect directly to `/<name>/mcp` and you want them to see the compression too. Both endpoints serve wrappers.

## Agent-side flow

For a backend at `level=medium, scope=aggregator`, the agent's experience at `/mcp`:

1. **Discover.** `tools/list` returns `<backend>__get_tool_schema`, `<backend>__search_tools`, and `<backend>__invoke_tool`. The `get_tool_schema` tool's description contains the full catalog of underlying tool names with one-sentence summaries — enough for the model to choose.
2. **Search (optional).** For targeted discovery, call `<backend>__search_tools(query="foo", limit=5)`. It returns compressed matching catalog lines using the backend's configured level.
3. **Schema fetch (optional).** If the model needs more than the catalog, it calls `<backend>__get_tool_schema(tool_name="foo")`. Returns the JSON-serialised `Tool` object for `foo`.
4. **Invoke.** `<backend>__invoke_tool(tool_name="foo", tool_input={...})`. Result content + `structuredContent` are forwarded verbatim — any `outputSchema` validation in the underlying tool still passes.

```mermaid
sequenceDiagram
    participant agent as Agent
    participant agg as Aggregator (/mcp)
    participant backend as Backend (e.g. pincher)
    agent->>agg: tools/list
    agg->>backend: list_tools (cached as full catalog)
    agg-->>agent: [<backend>__get_tool_schema,\n<backend>__search_tools,\n<backend>__invoke_tool]
    Note over agent: catalog inlined in get_tool_schema description
    opt Targeted discovery
        agent->>agg: tools/call <backend>__search_tools(query=foo, limit=5)
        agg-->>agent: matching compressed catalog lines
    end
    opt Schema fetch
        agent->>agg: tools/call <backend>__get_tool_schema(tool_name=foo)
        agg-->>agent: full Tool JSON for foo
    end
    agent->>agg: tools/call <backend>__invoke_tool(tool_name=foo, tool_input=...)
    agg->>backend: call_tool foo (...)
    backend-->>agg: result
    agg-->>agent: result (content + structuredContent)
```

## Discovery tool

The always-on built-in MCP exposes a `zelosmcp__list_compressed_tools` tool that returns the compressed catalog as JSON for any backend with `compress` configured — independent of scope:

- `backend` (optional string): limit to a single backend by name.
- `level` (optional string): re-render the catalog at a different level than what's configured. Useful for previewing what `level=high` would look like before changing the live config.

The same catalog feeds the cursor-rule generator (`/api/cursor-rule` and `zelosmcp__generate_cursor_rule`), so a backend at `scope=catalog` still gets a compressed rule body — that's the point.

## Defaults shipped with zelosMCP

Every backend (mandatory or user-supplied) gets `compress: { level: "medium", scope: "aggregator" }` automatically — no per-backend `compress` block is required. The explicit blocks you'll see in [configs/mandatory-zelosmcp.json](../configs/mandatory-zelosmcp.json) and [configs/default-zelosmcp.json](../configs/default-zelosmcp.json) are kept only as documentation; removing them does not change behavior.

To opt **out** for a specific backend, set `"compress": null` in its `mcpServers` entry. To keep the wrapper but stop summarising descriptions, set `"compress": { "level": "low" }` (full descriptions, no wrapper substitution at the wire).

## Token-savings sanity check

```bash
# Compressed (default mandatory + default config):
curl -sS -X POST http://localhost:8000/mcp \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | wc -c

# Compare with all `compress` blocks removed from your config — typically
# 5-10x larger.
```

## See also

- [docs/configuration.md](configuration.md) — the parent `mcpServers` schema.
- [docs/default-mcps.md](default-mcps.md) — which backends ship with compression on by default.
- [docs/built-in-mcp.md](built-in-mcp.md) — the `zelosmcp__list_compressed_tools` discovery tool.
- [atlassian-labs/mcp-compressor](https://github.com/atlassian-labs/mcp-compressor) — design reference for the wrapper-tool pattern and level taxonomy.
