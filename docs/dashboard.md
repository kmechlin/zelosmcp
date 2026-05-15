# Token-savings dashboard

zelosMCP ships with a savings dashboard that quantifies the token cost
the proxy layer is removing from the agent's bill. It lives at the
**Savings** tab on the home page (`http://localhost:8000/`) and is
backed by a JSON endpoint at `/api/savings` plus an SSE stream at
`/api/savings/stream`. The same structured event stream also powers the
**Events** tab and the `/api/events*` endpoints.

## What gets measured

Three independent sources of savings are recorded:

1. **Tool-list compression** — every time a client calls `tools/list`
   against `/mcp`, the aggregator already builds two views of each
   compressed backend (full prefixed catalog vs. the configured wrapper
   surface from [`docs/compression.md`](compression.md)). The recorder
   serializes both shapes, counts tokens against each, and stores one
   row per backend in `compression_snapshot`. Replaced on each
   `tools/list`.
2. **Structured proxy-event accounting** — every routed MCP transaction
  now emits a `proxy_events` row. That includes `tools/call`,
  `tools/list`, `resources/read`, prompts, and the aggregator fan-out
  paths. For response-producing methods, zelosMCP records both the raw
  upstream output token count and the transformed token count returned
  to the IDE, so the dashboard can show true proxy-side transform
  savings.
3. **Pincher self-reported BPE savings** — pincher emits `_meta` blocks
   on every response with real BPE counts (`tokens_used`,
   `tokens_saved`, `cost_avoided`). The recorder probes three carrier
   locations (`CallToolResult.meta`, `structuredContent._meta`,
   `content[i].annotations`) and stores whichever it finds. A periodic
   poller also calls `pincher__stats` and stores the formatted summary
   verbatim.

The aggregator excludes the always-on `zelosmcp__*` builtin from the
totals so the dashboard's own queries don't pollute their own metrics.

## Token counting

By default zelosMCP uses [tiktoken](https://pypi.org/project/tiktoken/)
with the `cl100k_base` encoding (gpt-4 / gpt-3.5-turbo). It's close
enough to Anthropic's tokenizer for trend reporting and
relative-compression ratios. Install via the optional extra:

```bash
uv pip install -e '.[tokenizer]'
```

If `tiktoken` can't be imported (air-gapped install, missing wheel,
etc.), the counter automatically falls back to `len(text) // 4`. The
dashboard exposes which mode is active in its header — look for
`heuristic` vs `cl100k_base`.

## Persistence

Savings counters are stored in a SQLite database. By default it lives
at `~/.zelosmcp/savings.sqlite`. Override with the
`ZELOSMCP_SAVINGS_DB` environment variable; pass `:memory:` to keep it
process-local (counters reset on every restart). If the home directory
isn't writable, the proxy falls back to in-memory automatically — the
dashboard still works, but won't carry totals across restarts.

When zelosMCP runs in Docker (`make up`), `~/.zelosmcp`
resolves to `/root/.zelosmcp` inside the container. That directory is
backed by the named volume `zelosmcp-savings` (see
[`configs/default-volumes.conf`](../configs/default-volumes.conf)), so
the SQLite store survives `docker rm` and image rebuilds the same way
the pincher index does. `make nuke` removes both — `make clean`
keeps them.

| Env var | Default | Purpose |
|---|---|---|
| `ZELOSMCP_SAVINGS_DB` | `~/.zelosmcp/savings.sqlite` | SQLite path. `:memory:` disables persistence. |
| `ZELOSMCP_PINCHER_POLL_SECS` | `60` | Pincher `stats` snapshot interval. `0` disables. |
| `ZELOSMCP_EVENT_RETENTION_HOURS` | `168` | How long to retain `proxy_events` rows before pruning. |
| `ZELOSMCP_EVENT_PRUNE_INTERVAL_MINS` | `30` | Background prune interval for expired `proxy_events` rows. |

The same retention knobs are also accepted in the top-level runtime
config as `event_retention_hours` and `event_prune_interval_mins`.

## Endpoints

- `GET /api/savings` — JSON snapshot. Returns 503 with
  `{"error": "savings store not initialised"}` if the lifespan hook
  hasn't started the recorder yet. Includes compression metrics,
  pincher metrics, and event-stream-backed upstream-vs-returned token
  totals.
- `GET /api/savings/stream` — SSE stream. Each frame is a JSON object
  with at least an `event` key (`call`, `compression`, or
  `pincher_stats`) or a structured proxy-event payload. Useful for
  clients that want push-style cache invalidation.
- `GET /api/events` — paginated event history with optional backend,
  method, tool, and error filters.
- `GET /api/events/summary` — aggregate event metrics, including raw
  upstream output tokens, returned output tokens, and transform savings.
- `GET /api/events/stream` — live SSE stream of structured proxy
  events.
- `GET /api/events/retention` — current retention/prune settings plus
  the oldest/newest retained event timestamps.

## Schema

Five tables drive the dashboard today (see `zelosmcp/savings_db.py`):

- `compression_snapshot` (one row per backend) — last
  `tools/list` raw vs. compressed bytes/tokens.
- `proxy_events` (append-only) — every structured proxy transaction,
  including raw upstream output tokens, returned output tokens,
  transform type, latency, and error details.
- `call_events` (append-only) — every per-call measurement with
  qualified name, compressed flag, input/output tokens, latency, and
  error flag. Retained for legacy per-call accounting and historical
  compatibility.
- `pincher_meta` (append-only) — parsed `_meta` envelopes per pincher
  call, plus the original payload as a JSON blob for fidelity.
- `pincher_stats_snapshot` (replaced on each poll) — verbatim
  `pincher__stats` output.

## Reading the dashboard

- **Tokens saved (compression)** — sum of `raw_tokens - compressed_tokens`
  over every backend's last snapshot. Represents the per-`tools/list`
  saving the agent sees on every refresh.
- **Tokens saved (proxy transforms)** — sum of `raw_output_tokens - output_tokens`
  where zelosMCP transformed an upstream response before returning it to
  the IDE.
- **Tokens saved (pincher)** — sum of `tokens_saved` from pincher's own
  `_meta` envelope. Represents real BPE savings pincher claims to have
  produced (e.g. by returning a 200-token symbol context instead of a
  3000-token full-file read).
- **Transactions recorded** — total structured proxy events retained in
  `proxy_events`.
- **Upstream / Returned output tokens** — compares the token count from
  the raw upstream MCP payload to the token count sent back to the IDE.
- **Cost avoided (pincher)** — sum of `cost_avoided` from pincher's
  `_meta` envelope (USD; precision varies by pincher version).
- **Compression by backend** — per-backend table with the level setting
  and percentage saved.
- **Top tools by token volume** — highest-volume qualified tool and
  method targets, with separate input, upstream, returned, and saved
  columns.
- **Response transform breakdown** — grouped totals by transform type
  (`raw`, `toon`, `compact_json`, etc.).
- **Per-backend transactions** — bar chart of event counts per backend.
- **Pincher session stats** — formatted output of the most recent
  `pincher__stats` snapshot.
- **Events tab** — filtered event history and per-row detail view over
  the retained `proxy_events` stream.

## Caveats

- Per-call token counts measure the *proxy → backend* round-trip, not
  the *agent → proxy* prompt cost. The agent's true bill includes
  conversation history we can't see.
- Compressed-mode percentages compare what `tools/list` *currently*
  emits against the unfolded raw catalog of the same backend — they
  don't try to reconstruct hypothetical agent costs in a world without
  compression.
- pincher's BPE counts use its own tokenizer, not `cl100k_base`. The
  dashboard surfaces them verbatim alongside, never adds them to the
  proxy-side counters.
- The recorder swallows write failures and logs at `WARNING`; a flaky
  SQLite store will *not* break the request hot path.
