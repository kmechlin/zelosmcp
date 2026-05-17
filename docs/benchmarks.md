# Compression benchmark

The benchmark harness in `benchmarks/` measures the token-cost difference across zelosMCP's five compression modes using two complementary methods:

- **Static** — counts tokens in the `tools/list` response under each mode by cycling the server config. No IDE or model required.
- **Dynamic** — sends a prompt suite via an IDE adapter under each compression mode and records per-run metrics.

Two IDE adapters are supported:

- **Cursor** — uses `@cursor/sdk` and collects token usage from the Cursor usage API after each run.
- **Copilot** — uses the official GitHub `copilot` CLI with MCP tool connectivity via `~/.copilot/mcp-config.json`.

## Prerequisites

### Common

- zelosMCP running at `http://localhost:8000` (or pass `--url`)
- Node 18+: `cd benchmarks && npm install`

### Cursor adapter

- `CURSOR_API_KEY` — set from [cursor.com/settings](https://cursor.com/settings). Required for the `run` command; the `static`, `refetch`, and `report` commands do not need it.
- Cursor IDE open and signed in on the same machine — required for prompt execution.

### Copilot adapter

Install the `copilot` CLI and authenticate once:

```bash
# Install (choose one)
brew install copilot-cli
# or: npm i -g @github/copilot

# Authenticate (device-flow login, one-time)
copilot /login
```

For CI or headless environments where interactive login is not possible, set `GITHUB_TOKEN` to a token with Copilot access:

- Fine-grained PAT with the **GitHub Copilot** permission
- Classic PAT with the `copilot` scope
- Output of `gh auth token` (if `gh` is signed in with Copilot access)

The Copilot adapter auto-configures `~/.copilot/mcp-config.json` before each run so the CLI connects to zelosMCP as an MCP server.

## Secrets

The CLI auto-detects a `.bench.env` file in the working directory or the `benchmarks/` directory. Copy the template and fill in your values:

```bash
cd benchmarks
cp .bench.env.example .bench.env
# Edit .bench.env — set CURSOR_API_KEY and/or GITHUB_TOKEN
```

Existing environment variables take precedence over `.bench.env`. Use `--secrets-file <path>` to specify a different file explicitly.

`.bench.env` is gitignored and must never be committed.

## Commands

All commands are run from the `benchmarks/` directory:

```bash
npm run bench -- <command> [flags]
```

### static — count tool-definition tokens

Cycles zelosMCP through each compression mode, calls `tools/list` at each level, and counts tokens using js-tiktoken. No `CURSOR_API_KEY` or IDE needed.

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

Sends the prompt suite through the configured IDE adapter(s) under each compression mode.

```bash
# Cursor (default)
export CURSOR_API_KEY="crsr_..."
npm run bench -- run

# Copilot
npm run bench -- run --ide copilot

# Both adapters, two modes only
npm run bench -- run --ide all --mode null,medium

# With transcript logging
npm run bench -- run --ide copilot --log-transcripts
```

| Flag | Default | Description |
|---|---|---|
| `--url <url>` | `http://localhost:8000` | zelosMCP base URL |
| `--ide <ide>` | `cursor` | IDE adapter: `cursor`, `copilot`, or `all` |
| `--model <model>` | (per-adapter default) | Model ID applied to all adapters |
| `--cursor-model <model>` | `composer-2` | Model ID for Cursor (overrides `--model`) |
| `--copilot-model <model>` | `claude-sonnet-4.5` | Model ID for Copilot (overrides `--model`) |
| `--mode <modes>` | all five | Comma-separated subset: `null,low,medium,high,max` |
| `--prompts <path>` | `prompts/suite.json` | Prompt suite JSON path |
| `--delay <ms>` | `5000` | Delay between prompts (ms) |
| `--output <path>` | `results/run-log.json` | Run log output path |
| `--rules` | off | Load project IDE rules (`.cursor/rules/`, `.github/copilot-instructions.md`) into each agent run |
| `--rules-dir <path>` | (auto-detect) | Custom rules directory |
| `--log-transcripts` | off | Save per-prompt conversation transcripts to `results/transcripts/` |
| `--secrets-file <path>` | auto-detect `.bench.env` | Dotenv-style secrets file |
| `--no-clean-assets` | off | Skip pre-run removal of previously pushed IDE assets |
| `--refresh-assets` / `--no-refresh-assets` | mirrors `--rules` | Push fresh assets after each mode config reload |
| `--pin-response-format <fmt>` | (unset) | Pin `response_format` constant across all modes (`raw`, `toon`, `compact_json`) to isolate the compression-only signal |
| `--pin-strip-meta <true\|false>` | (unset) | Pin `strip_meta` constant across all modes |

> **`--rules`:** By default agents run with no project rules, keeping token counts isolated to compression behaviour alone. Pass `--rules` to include IDE-specific rules — useful when measuring the combined effect of compression + context, or benchmarking in a configuration that mirrors normal IDE use.

> **`--mode`:** `null` and `low` produce identical `tools/list` output (full uncompressed schemas). Running both is only useful to confirm they are identical. For a faster run that still covers the full range of distinct outputs, `--mode null,medium,high,max` is sufficient.

### refetch — re-query token data (Cursor only)

Re-queries the Cursor usage API for every entry in a run log and updates token fields where the API now returns a higher total. Useful when a run log looks low due to API propagation lag.

```bash
npm run bench -- refetch --run-log results/run-log.json
```

| Flag | Default | Description |
|---|---|---|
| `--run-log <path>` | *(required)* | Path to `run-log.json` |
| `--poll-interval <ms>` | `2000` | Delay between polls per entry |
| `--max-polls <n>` | `2` | Maximum polls per entry |

Requires a Cursor session token (read automatically from `state.vscdb`, or set `CURSOR_SESSION_TOKEN`). Not applicable to Copilot runs.

### report — regenerate HTML from existing data

Reads an existing run log and writes a self-contained HTML report. If a `static-results.json` sidecar is present in the same directory, the report includes static analysis charts.

```bash
npm run bench -- report \
  --run-log results/run-log.json \
  --output results/report.html
```

| Flag | Default | Description |
|---|---|---|
| `--run-log <path>` | *(required)* | Path to `run-log.json` |
| `--output <path>` | Same dir as run log, `.html` extension | HTML output path |

## Run lifecycle

When `run` is invoked, the following steps execute in order:

### Step 1 — Asset cleanup

Before any prompts run, previously pushed IDE assets are removed from the project so each benchmark stage starts from a known, clean state:

- **Cursor**: removes `{root}/.cursor/rules/zelosmcp.mdc`, `.cursor/zelosmcp.json`, and any skills/agents/commands directories that were pushed by a prior run.
- **Copilot**: removes `{root}/.github/copilot-instructions.md`, `.vscode/zelosmcp.json`, and any skills/agents/prompts directories.

Asset names are resolved by querying `GET /api/assets?kind={skill,agent,prompt}` so the cleanup tracks exactly what the server exposes.

Disable with `--no-clean-assets` if you want to benchmark with assets already in place.

### Step 2 — Static analysis

Cycles all five compression modes, calls `tools/list` at each level, counts tokens via js-tiktoken, and writes `results/static-results.json`. This step does not use an IDE adapter.

### Step 3 — Mode loop

For each compression mode:

1. Applies the new config via `POST /api/start`. The current config is saved before the loop starts; see [Config restore](#config-restore-and-crash-recovery) below.
2. Waits 2 seconds for backends to settle after the config reload.
3. **Asset refresh** (if `--rules` or `--refresh-assets`): pushes fresh IDE assets via `POST /api/assets/push/{kind}` for each adapter. The very first stage is skipped when asset cleanup ran, to preserve a no-assets baseline for the first mode.
4. Runs each prompt in the suite through each IDE adapter. Each result is appended to the run log immediately.

### Step 4 — Config restore

After the mode loop completes (or if it fails with an exception), the runner restores the server to **the exact config it had when the benchmark started**. This runs in a `finally` block so it always executes, even on prompt failures or partial runs.

If the restore itself fails (for example, the server is unreachable), the runner logs a warning and continues — the original exception (if any) is not suppressed.

> **Killed processes**: if the process is terminated with SIGTERM during an `applyConfig` call, or killed with SIGKILL or OOM, the finally block cannot run. In that case the server may be left in a test compression mode. See [Config restore and crash recovery](#config-restore-and-crash-recovery) for manual remediation.

### Step 5 — Token refetch

For adapters that support token usage collection (currently Cursor only), re-queries usage data for all entries in the run log. This catches usage events that were not yet available when each run completed.

### Step 6 — Report

Writes a self-contained HTML report next to the run log, combining both the static analysis charts and the dynamic token breakdown. Always written, even on partial failure, as long as at least one run-log entry exists.

## Config restore and crash recovery

The runner captures the current server config via `GET /api/status` before the mode loop starts. At the end of the run — whether successful or not — it re-applies that saved config via `POST /api/start`. This returns the server to its pre-benchmark compression mode, response format, and other settings.

The restore runs in a `finally` block and always executes on normal exit and on uncaught exceptions. Errors during restore are caught and logged as warnings so they do not mask the original run error.

**If the benchmark process is killed mid-run** (Ctrl+C during an `applyConfig` network call, SIGKILL, OOM kill) the finally block cannot execute and the server may be left in a test compression mode. To recover:

```bash
# Option 1 — Reload via the HTTP API with your original config
curl -X POST http://localhost:8000/api/start \
  -H "Content-Type: application/json" \
  -d @your-original-config.json

# Option 2 — Restart zelosMCP
# (it will reload its config from disk on startup)
```

The zelosMCP dashboard at `http://localhost:8000` also shows the current active config under the **Connections** tab.

## Output files

| File | Contents |
|---|---|
| `results/run-log.json` | Array of `RunLogEntry` — one entry per prompt × mode × adapter |
| `results/static-results.json` | Tool-definition byte and token counts per compression mode |
| `results/run-log.html` | Self-contained HTML with Chart.js charts and raw data table |
| `results/transcripts/<ide>-<mode>-<promptId>.json` | Per-prompt conversation transcript (`--log-transcripts` only) |

Each `RunLogEntry` in the run log contains:

| Field | Description |
|---|---|
| `ide` | Adapter that produced this entry (`cursor` or `copilot`) |
| `mode` | Compression mode (`null` / `low` / `medium` / `high` / `max`) |
| `promptId` | Prompt ID from `prompts/suite.json` |
| `promptText` | Full prompt text sent to the agent |
| `model` | Model ID used for this run |
| `startTime` / `endTime` | ISO 8601 wall-clock timestamps |
| `status` | `ok` or `error` |
| `inputTokens` | Non-cached input tokens |
| `outputTokens` | Output tokens |
| `cacheWriteTokens` | Tokens written to the prompt cache |
| `cacheReadTokens` | Tokens served from the prompt cache |
| `totalTokens` | Sum of all four token fields |

Token fields are populated by the Cursor usage API for Cursor runs; they are not available for Copilot runs (Copilot does not expose a per-run usage API).

## Transcripts

When `--log-transcripts` is enabled, the runner saves a structured JSON transcript for every prompt run at `results/transcripts/<ide>-<mode>-<promptId>.json`.

Each transcript contains a sequence of events:

| Event type | Description |
|---|---|
| `text` | Assistant dialog or user message |
| `thought` | Model reasoning / chain-of-thought |
| `tool_call` | Tool invocation — includes `toolName` and `toolInput` |
| `tool_result` | Tool output — includes `toolName` and `toolOutput` |
| `sub_agent_start` / `sub_agent_end` | Sub-agent lifecycle events |

**Copilot**: the adapter passes `--output-format json` to the CLI, captures the JSONL event stream, and parses it in a two-pass process: first building a `toolCallId → toolName` correlation map from `tool.execution_start` events, then emitting structured events for each line.

**Cursor**: the adapter calls `run.conversation()` on the `@cursor/sdk` to get structured `ConversationTurn[]` objects and walks them to extract steps of each type.

Tool inputs and outputs are truncated at 32 KB to keep transcript files manageable.

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

## How token data is collected (Cursor)

After each Cursor run, the runner reads your Cursor session token from the local SQLite database (`~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` on macOS; equivalent paths on Windows and Linux) and calls:

```
POST https://cursor.com/api/dashboard/get-filtered-usage-events
```

with the run's `startTime` and `endTime` as the query window. All usage events within that window are summed to produce the per-run token totals. A final refetch pass at the end of the suite gives earlier entries more time for events to propagate.

If the session token cannot be read from SQLite, set `CURSOR_SESSION_TOKEN` to your `WorkosCursorSessionToken` cookie value.

> **Attribution accuracy:** Token counts are attributed by time window only — there is no per-run ID in the Cursor usage API. Any concurrent Cursor activity (chat, completions) whose events fall in the same window will be included. For cleanest results, avoid using Cursor for other work while the benchmark is running.

## Copilot MCP configuration

Before each Copilot run the adapter upserts an entry in `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "zelosmcp": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "tools": ["*"]
    }
  }
}
```

If an entry for `zelosmcp` already exists with the correct `type`, `url`, and `tools`, the file is left unchanged. The adapter does not modify or remove other entries in the file.

## End-to-end workflow

```bash
# Cursor — full run (static + all prompts + report)
export CURSOR_API_KEY="crsr_..."
npm run bench -- run

# Copilot — full run
npm run bench -- run --ide copilot

# Both adapters, limited modes (saves time during evaluation)
npm run bench -- run --ide all --mode null,medium,max

# Fast debugging run: one mode, copilot adapter, with transcripts
npm run bench -- run --ide copilot --mode null --log-transcripts

# Static analysis only (no model, no API key)
npm run bench -- static

# Re-query token data if Cursor counts look low after the fact
npm run bench -- refetch --run-log results/run-log.json

# Regenerate HTML report from existing data
npm run bench -- report --run-log results/run-log.json
```

## See also

- [compression.md](compression.md) — how the five compression levels work and the wrapper-tool pattern
- [configuration.md](configuration.md) — the `mcpServers` schema including the `compress` block
- [http-api.md](http-api.md) — the `/api/start` and `/api/status` endpoints the benchmark drives
- [benchmarks/README.md](../benchmarks/README.md) — quick-start guide for the `benchmarks/` directory
