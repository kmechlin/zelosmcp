# zelosMCP Compression Benchmark Tool

Measures the token-cost difference between zelosMCP's `null`, `medium`, and `max` compression modes. Two measurement types:

- **Static** -- counts tokens in the `tools/list` response under each mode (no Cursor API key needed).
- **Dynamic** -- sends prompts via `@cursor/sdk` and fetches per-run token counts directly from the Cursor usage API after each run. Token data is captured automatically; no manual CSV export is required.

## Quickstart

```bash
cd benchmarks
npm install
export CURSOR_API_KEY="cursor_..."
npm run bench -- run
```

Opens `results/report.html` when done — includes both the static tool-definition token chart and the dynamic runtime token breakdown.

## Setup

```bash
cd benchmarks
npm install
```

Set your Cursor API key (required for the `run` command):

```bash
export CURSOR_API_KEY="cursor_..."
```

Token usage is read automatically from Cursor's local session (no extra credentials needed). If auto-detection fails, set `CURSOR_SESSION_TOKEN` to your `WorkosCursorSessionToken` cookie value as a fallback.

## Commands

All commands are run from the `benchmarks/` directory via `npm run bench`.

### `static` -- Count tokens in tool definitions

Reconfigures zelosMCP under each compression mode, fetches `tools/list`, and counts tokens with js-tiktoken.

```bash
npm run bench -- static --url http://localhost:8000
```

Example output:

```
Mode               Tools       Tokens
--------------------------------------
no compression        33       14,200
medium                 6          620
max                    2          210

Savings vs no compression:
  medium: 13,580 tokens saved (95.6%)
  max: 13,990 tokens saved (98.5%)
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--url <url>` | `http://localhost:8000` | zelosMCP base URL |
| `--json` | off | Output raw JSON instead of a table |

### `run` -- Send prompts and record token usage

Runs the full benchmark in one shot:

1. Static analysis pass — cycles through all three compression modes, counts tool-definition tokens, writes `static-results.json`.
2. Prompt suite — sends each prompt from `prompts/suite.json` under each mode via `@cursor/sdk`, fetches token counts from the Cursor usage API after each run, appends entries to the run log.
3. Final refetch — re-queries token data for all entries to catch late-arriving usage events.
4. Report — generates `results/report.html` with both the static and dynamic charts.

```bash
npm run bench -- run
```

This produces three output files:

| File | Contents |
|------|----------|
| `results/run-log.json` | Per-run dynamic token usage entries |
| `results/static-results.json` | Tool-definition token counts per compression mode |
| `results/report.html` | HTML report with both charts |

Each run-log entry includes:

| Field | Source |
|-------|--------|
| `mode` | Compression mode (`null` / `medium` / `max`) |
| `promptId` | Prompt ID from `prompts/suite.json` |
| `model` | CLI `--model` arg |
| `startTime` / `endTime` | Wall-clock timestamps |
| `status` | `ok` or `error` |
| `inputTokens` | From Cursor usage API |
| `outputTokens` | From Cursor usage API |
| `cacheWriteTokens` | From Cursor usage API |
| `cacheReadTokens` | From Cursor usage API |
| `totalTokens` | Sum of all four token fields |

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--url <url>` | `http://localhost:8000` | zelosMCP base URL |
| `--model <model>` | `composer-2` | Cursor model ID |
| `--delay <ms>` | `5000` | Delay between prompts (ms) |
| `--output <path>` | `results/run-log.json` | Run log output path |
| `--prompts <path>` | `prompts/suite.json` | Prompt suite JSON path |
| `--rules` | off | Load project Cursor rules (`.cursor/rules/`) into each agent run |

Requires `CURSOR_API_KEY` in the environment.

> **Note on `--rules`:** By default, agents run with `settingSources: []` so project rules are excluded, keeping token counts isolated to compression behaviour. Pass `--rules` to include project rules — useful when you want to measure their token impact or benchmark in a setting that reflects normal IDE use. The runner automatically detects the project root by walking up from the current directory to the nearest `.git` folder, so `.cursor/rules/` is always found correctly regardless of which directory you run the command from.

### `refetch` -- Re-query token data for an existing run log

Re-queries the Cursor usage API for every entry in a run log and updates any entry whose token count has increased since it was first recorded. Useful if a run log looks low due to API propagation lag.

```bash
npm run bench -- refetch --run-log results/run-log.json
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--run-log <path>` | *(required)* | Path to run-log.json |
| `--poll-interval <ms>` | `2000` | Delay between polls per entry (ms) |
| `--max-polls <n>` | `2` | Max polls per entry |

### `report` -- Generate an HTML report

Reads a run log and writes a self-contained HTML file. If a `static-results.json` sidecar exists in the same directory as the run log (produced automatically by `run`), the report includes a static analysis section with side-by-side charts for tool-definition tokens and tool count per mode, followed by the dynamic stacked token chart and raw data table — all on one page.

```bash
npm run bench -- report \
  --run-log results/run-log.json \
  --output results/report.html
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--run-log <path>` | *(required)* | Path to run-log.json |
| `--output <path>` | Same dir as run-log, `.html` extension | HTML output path |

## Token data source

Each agent run uses the project root (detected by walking up from the current directory to the nearest `.git` folder) as its working directory, so the agent has full project context regardless of where the command is invoked from.

After each `run.wait()`, the runner reads your Cursor session token from the local SQLite database (`~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` on macOS) and calls `POST https://cursor.com/api/dashboard/get-filtered-usage-events` with the run's time window. All events within that window are summed to produce the per-run token totals. The runner polls up to 3 times (3 s apart) until the event count stabilizes, then performs a final refetch over all entries at the end of the suite.

If the session token cannot be read from SQLite, set the `CURSOR_SESSION_TOKEN` environment variable to your `WorkosCursorSessionToken` cookie value and the runner will use that instead.

## End-to-end workflow

```bash
# Run everything and produce the report in one command
export CURSOR_API_KEY="cursor_..."
npm run bench -- run

# (Optional) Re-query if token counts look low after the fact
npm run bench -- refetch --run-log results/run-log.json

# (Optional) Regenerate the report from existing data
npm run bench -- report --run-log results/run-log.json
```

To run static analysis on its own without a full prompt suite:

```bash
npm run bench -- static --url http://localhost:8000
```

## Compression modes

| Mode | `compress` value | What `tools/list` returns |
|------|-----------------|---------------------------|
| `null` | `null` | Full uncompressed JSON schemas for every tool |
| `medium` | `{ level: "medium" }` | Wrapper trio with one-line-per-tool catalog |
| `max` | `{ level: "max" }` | Single `list_tools` wrapper, no inline catalog |

## Prompt suite

Prompts live in `prompts/suite.json`. Each entry has an `id`, `text`, and `category`.

Edit or replace the file to customize which prompts are benchmarked.

## Tests

```bash
npm test
```

Runs vitest against all modules with mocked fetch, `@cursor/sdk`, and `usage-api` calls.
