# Compression benchmark

The benchmark harness in `benchmarks/` measures the token-cost difference across zelosMCP's five compression modes using two complementary methods: **static** (counts tool-definition tokens by querying `tools/list` under each mode — no model required) and **dynamic** (sends a suite of prompts via `@cursor/sdk` and records actual token usage from the Cursor usage API).

The dynamic path runs agents inside Cursor's local runtime and requires the Cursor IDE to be open and authenticated on the same machine.

## Prerequisites

- zelosMCP running (default `http://localhost:8000`)
- Node 18+: `cd benchmarks && npm install`
- `CURSOR_API_KEY` environment variable — required for `run`; the other commands work without it
- Cursor IDE open and signed in — required for `run` (the `static`, `refetch`, and `report` commands do not need it)

## Commands

All commands are run from the `benchmarks/` directory:

```bash
npm run bench -- <command> [flags]
```

### static — count tool-definition tokens

Cycles zelosMCP through each compression mode, calls `tools/list` at each level, and counts tokens using js-tiktoken. No `CURSOR_API_KEY` needed.

```bash
npm run bench -- static --url http://localhost:8000
```

Example output:

```
Mode               Tools       Tokens
--------------------------------------
no compression        44        9,727
low                   44        9,727
medium                14        2,703
high                  14        2,224
max                   10        1,368

Savings vs no compression:
  low:    0 tokens saved (0.0%)
  medium: 7,024 tokens saved (72.2%)
  high:   7,503 tokens saved (77.1%)
  max:    8,359 tokens saved (85.9%)
```

| Flag | Default | Description |
|---|---|---|
| `--url <url>` | `http://localhost:8000` | zelosMCP base URL |
| `--json` | off | Output raw JSON instead of a table |

### run — send prompts and record token usage

Runs the full benchmark in one shot:

1. **Asset cleanup** — removes any previously pushed Cursor rules and assets from `.cursor/` so the first stage has a clean, no-assets baseline. Disable with `--no-clean-assets`.
2. **Static analysis** — cycles all five modes, counts tool-definition tokens, writes `results/static-results.json`.
3. **Prompt suite** — for each mode: applies the compression config via `POST /api/start`, waits 2 s for backends to settle, then runs each prompt in `prompts/suite.json` via `@cursor/sdk`. Each `RunLogEntry` is appended to the run log immediately.
4. **Asset refresh** — when `--rules` is active, pushes fresh assets after each mode reload (skipped on the first stage to preserve the no-assets baseline).
5. **Restore** — resets the proxy to `medium` compression (skipped when it was already `medium` and no pin overrides were applied).
6. **Final refetch** — re-queries the Cursor usage API for all entries to pick up late-arriving usage events.
7. **Report** — writes a self-contained HTML report next to the run log (always written, even on partial failure, as long as at least one entry exists).

```bash
export CURSOR_API_KEY="cursor_..."
npm run bench -- run
```

| Flag | Default | Description |
|---|---|---|
| `--url <url>` | `http://localhost:8000` | zelosMCP base URL |
| `--model <model>` | `composer-2` | Cursor model ID |
| `--mode <modes>` | all five | Comma-separated subset: `null,low,medium,high,max` |
| `--delay <ms>` | `5000` | Delay between prompts (ms) |
| `--output <path>` | `results/run-log.json` | Run log output path |
| `--prompts <path>` | `prompts/suite.json` | Prompt suite JSON path |
| `--rules` | off | Load project `.cursor/rules/` into each agent run |
| `--no-clean-assets` | off | Skip pre-run asset cleanup |
| `--refresh-assets` / `--no-refresh-assets` | mirrors `--rules` | Push fresh assets after each mode reload |
| `--pin-response-format <format>` | (unset) | Hold `response_format` constant across all modes (`raw`, `toon`, `compact_json`) to isolate the compression-only signal |
| `--pin-strip-meta` | (unset) | Hold `strip_meta` constant (`true` or `false`) across all modes |

> **`--rules`:** By default agents run with no project rules loaded, keeping token counts isolated to compression behaviour alone. Pass `--rules` to include `.cursor/rules/` — useful when measuring the combined effect of compression + context rules, or when you want results that reflect normal IDE use.

> **`--mode`:** `null` and `low` currently produce identical `tools/list` output (full uncompressed schemas). Running both is only useful to confirm they are identical. For a faster run that still covers the full range of distinct outputs, `--mode null,medium,high,max` is sufficient.

### refetch — re-query token data for an existing run log

Re-queries the Cursor usage API for every entry in a run log and updates token fields for any entry where the API now returns a higher total. Useful when a run log looks low due to API propagation lag.

```bash
npm run bench -- refetch --run-log results/run-log.json
```

| Flag | Default | Description |
|---|---|---|
| `--run-log <path>` | *(required)* | Path to `run-log.json` |
| `--poll-interval <ms>` | `2000` | Delay between polls per entry (ms) |
| `--max-polls <n>` | `2` | Maximum polls per entry |

Requires a Cursor session token (read automatically from `state.vscdb`, or set `CURSOR_SESSION_TOKEN`).

### report — regenerate HTML from existing data

Reads an existing run log and writes a self-contained HTML report. If a `static-results.json` sidecar is present in the same directory, the report automatically includes the static analysis charts.

```bash
npm run bench -- report \
  --run-log results/run-log.json \
  --output results/report.html
```

| Flag | Default | Description |
|---|---|---|
| `--run-log <path>` | *(required)* | Path to `run-log.json` |
| `--output <path>` | Same dir as run log, `.html` extension | HTML output path |

## Output files

| File | Contents |
|---|---|
| `results/run-log.json` | Array of `RunLogEntry` — one entry per prompt × mode combination |
| `results/static-results.json` | Tool-definition byte and token counts per compression mode |
| `results/run-log.html` | Self-contained HTML with Chart.js bar charts and a raw data table |

Each `RunLogEntry` in the run log contains:

| Field | Description |
|---|---|
| `mode` | Compression mode (`null` / `low` / `medium` / `high` / `max`) |
| `promptId` | Prompt ID from `prompts/suite.json` |
| `model` | Model ID passed via `--model` |
| `startTime` / `endTime` | ISO 8601 wall-clock timestamps |
| `status` | `ok` or `error` |
| `inputTokens` | Non-cached input tokens (from Cursor usage API) |
| `outputTokens` | Output tokens |
| `cacheWriteTokens` | Tokens written to the prompt cache |
| `cacheReadTokens` | Tokens served from the prompt cache |
| `totalTokens` | Sum of all four token fields |

## Compression modes

| Mode | `compress` value | `tools/list` behaviour |
|---|---|---|
| `null` | `null` | Full uncompressed JSON schemas for every tool |
| `low` | `{ level: "low" }` | Full schemas; no wrapper substitution on the wire |
| `medium` | `{ level: "medium" }` | Wrapper trio with one-line-per-tool catalog inlined |
| `high` | `{ level: "high" }` | Wrapper trio with names + parameter names only (no descriptions) |
| `max` | `{ level: "max" }` | Single `list_tools` wrapper; no inline catalog |

See [compression.md](compression.md) for a full explanation of how each level works and when to choose between them.

## Prompt suite

Prompts live in `benchmarks/prompts/suite.json`. Each entry has three fields:

| Field | Description |
|---|---|
| `id` | Kebab-case slug used as the `promptId` in run-log entries |
| `text` | Full natural-language prompt sent to the agent |
| `category` | Grouping label (e.g. `code-exploration`, `symbol-lookup`, `search`) |

The five built-in prompts target architecture overview, entry-point tracing, symbol lookup, config search, and call tracing — tasks that require the agent to make multiple MCP tool calls, making the token cost of tool definitions meaningfully visible. Edit or replace `prompts/suite.json` to benchmark with your own prompts.

## How token data is collected

After each `run.wait()`, the runner reads your Cursor session token from the local SQLite database (`~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` on macOS; equivalent paths on Windows and Linux) and calls:

```
POST https://cursor.com/api/dashboard/get-filtered-usage-events
```

with the run's `startTime` and `endTime` as the query window. All usage events that fall within that window are summed to produce the per-run token totals. A final refetch pass re-queries every entry at the end of the suite, giving earlier entries more time for events to propagate before the final totals are written.

If the session token cannot be read from SQLite, set `CURSOR_SESSION_TOKEN` to your `WorkosCursorSessionToken` cookie value and the runner uses that instead.

> **Attribution accuracy:** Token counts are attributed by time window only — there is no per-run ID in the Cursor usage API. Any concurrent Cursor activity (chat, completions) whose events fall in the same window will be summed into the entry for that run. For cleanest results, avoid using Cursor for other work while the benchmark is running.

## End-to-end workflow

```bash
# Full run: static analysis + all prompts + report
export CURSOR_API_KEY="cursor_..."
npm run bench -- run

# Run only selected modes to save time
npm run bench -- run --mode null,medium,max

# Static analysis only (no model, no API key needed)
npm run bench -- static

# Re-query token data if counts look low after the fact
npm run bench -- refetch --run-log results/run-log.json

# Regenerate HTML report from existing data
npm run bench -- report --run-log results/run-log.json
```

## See also

- [compression.md](compression.md) — how the five compression levels work and the wrapper-tool pattern
- [configuration.md](configuration.md) — the `mcpServers` schema including the `compress` block
- [http-api.md](http-api.md) — the `/api/start` and `/api/status` endpoints the benchmark drives
- [benchmarks/README.md](../benchmarks/README.md) — quick-start focused on the `benchmarks/` directory
