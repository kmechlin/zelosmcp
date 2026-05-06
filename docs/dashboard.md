# Token-savings dashboard

LocalMCP ships with a savings dashboard that quantifies the token cost
the proxy layer is removing from the agent's bill. It lives at the
**Savings** tab on the home page (`http://localhost:8000/`) and is
backed by a JSON endpoint at `/api/savings` plus an SSE stream at
`/api/savings/stream`.

## What gets measured

Three independent sources of savings are recorded:

1. **Tool-list compression** — every time a client calls `tools/list`
   against `/mcp`, the aggregator already builds two views of each
   compressed backend (full prefixed catalog vs. the wrapper-pair
   surface from [`docs/compression.md`](compression.md)). The recorder
   serializes both shapes, counts tokens against each, and stores one
   row per backend in `compression_snapshot`. Replaced on each
   `tools/list`.
2. **Per-call token accounting** — every `call_tool` (raw or compressed
   wrapper) routes through `localmcp.savings.measure_call`. Inputs
   (request arguments) and outputs (`content` + `structuredContent`)
   are token-counted, latency is measured, and a `call_events` row is
   appended.
3. **Pincher self-reported BPE savings** — pincher emits `_meta` blocks
   on every response with real BPE counts (`tokens_used`,
   `tokens_saved`, `cost_avoided`). The recorder probes three carrier
   locations (`CallToolResult.meta`, `structuredContent._meta`,
   `content[i].annotations`) and stores whichever it finds. A periodic
   poller also calls `pincher__stats` and stores the formatted summary
   verbatim.

The aggregator excludes the always-on `localmcp__*` builtin from the
totals so the dashboard's own queries don't pollute their own metrics.

## Token counting

By default LocalMCP uses [tiktoken](https://pypi.org/project/tiktoken/)
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
at `~/.localmcp/savings.sqlite`. Override with the
`LOCALMCP_SAVINGS_DB` environment variable; pass `:memory:` to keep it
process-local (counters reset on every restart). If the home directory
isn't writable, the proxy falls back to in-memory automatically — the
dashboard still works, but won't carry totals across restarts.

When LocalMCP runs in Docker (`make localmcp-up`), `~/.localmcp`
resolves to `/root/.localmcp` inside the container. That directory is
backed by the named volume `localmcp-savings` (see
[`configs/default-volumes.conf`](../configs/default-volumes.conf)), so
the SQLite store survives `docker rm` and image rebuilds the same way
the pincher index does. `make nuke` removes both — `make clean`
keeps them.

| Env var | Default | Purpose |
|---|---|---|
| `LOCALMCP_SAVINGS_DB` | `~/.localmcp/savings.sqlite` | SQLite path. `:memory:` disables persistence. |
| `LOCALMCP_PINCHER_POLL_SECS` | `60` | Pincher `stats` snapshot interval. `0` disables. |

## Endpoints

- `GET /api/savings` — JSON snapshot. Returns 503 with
  `{"error": "savings store not initialised"}` if the lifespan hook
  hasn't started the recorder yet.
- `GET /api/savings/stream` — SSE stream. Each frame is a JSON object
  with at least an `event` key (`call`, `compression`, or
  `pincher_stats`). Useful for clients that want push-style cache
  invalidation.

## Schema

Four tables (see `localmcp/savings_db.py`):

- `compression_snapshot` (one row per backend) — last
  `tools/list` raw vs. compressed bytes/tokens.
- `call_events` (append-only) — every per-call measurement with
  qualified name, compressed flag, input/output tokens, latency, and
  error flag.
- `pincher_meta` (append-only) — parsed `_meta` envelopes per pincher
  call, plus the original payload as a JSON blob for fidelity.
- `pincher_stats_snapshot` (replaced on each poll) — verbatim
  `pincher__stats` output.

## Reading the dashboard

- **Tokens saved (compression)** — sum of `raw_tokens - compressed_tokens`
  over every backend's last snapshot. Represents the per-`tools/list`
  saving the agent sees on every refresh.
- **Tokens saved (pincher)** — sum of `tokens_saved` from pincher's own
  `_meta` envelope. Represents real BPE savings pincher claims to have
  produced (e.g. by returning a 200-token symbol context instead of a
  3000-token full-file read).
- **Calls recorded** — total `call_tool` invocations excluding the
  built-in introspection backend.
- **Cost avoided (pincher)** — sum of `cost_avoided` from pincher's
  `_meta` envelope (USD; precision varies by pincher version).
- **Compression by backend** — per-backend table with the level setting
  and percentage saved.
- **Top tools by token volume** — most-used tools by combined
  input+output token count.
- **Per-backend activity** — bar chart of call counts per backend.
- **Pincher session stats** — formatted output of the most recent
  `pincher__stats` snapshot.

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
