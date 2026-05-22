# zelosmcp roadmap

MCP aggregator + reverse proxy. Tool-description compression, IDE asset push, sync/async tool surfaces.

Forward-looking view of issues filed against this repo, sourced from the
[Zelos Platform Tracker](https://github.com/orgs/ZelosAI/projects/2). The
suite-wide view is at [zelosai/ROADMAP.md](https://github.com/ZelosAI/zelosai/blob/main/ROADMAP.md).

Each entry: `[#N title](url) — Work type · Priority · Release`.

## In flight

_(empty)_

## Ready for QA

_(empty)_

## Next (v0.2)

- [#20 Chore: Bump pyproject.toml 0.1.0 → 0.2.0](https://github.com/ZelosAI/zelosmcp/issues/20) — Chore · P1 · v0.2

## Next (v0.3) — sync + async tool surfaces (EA-readiness gap A)

Filed 2026-05-22 as part of the EA-readiness audit. zelosmcp was previously
scope-empty; these five issues are the feature plumbing zelosbroker#11 and
the v0.3 gateway issues assume.

- [#23 Feature: Broker HTTP + WebSocket client (share lifecycle + sync-channel frame schema)](https://github.com/ZelosAI/zelosmcp/issues/23) — Feature · P1 · v0.3 (gates #24 + #25)
- [#24 Feature: Sync-subagent MCP tools (expose subagents as tools; stream turns over broker WS)](https://github.com/ZelosAI/zelosmcp/issues/24) — Feature · P1 · v0.3
- [#25 Feature: Backplane NATS publisher for async-task MCP tools](https://github.com/ZelosAI/zelosmcp/issues/25) — Feature · P1 · v0.3
- [#26 Feature: Bearer-token issuance to broker + backplane on tool invoke](https://github.com/ZelosAI/zelosmcp/issues/26) — Feature · P1 · v0.3

## Following (v0.4)

- [#27 Feature: Subagent artifact loader (skills + hooks bundle at invoke time)](https://github.com/ZelosAI/zelosmcp/issues/27) — Feature · P2 · v0.4
- [#28 Chore: Apply ci.yml from zelosai template (Python flavor)](https://github.com/ZelosAI/zelosmcp/issues/28) — Chore · P2 · v0.4

## Backlog

_(empty)_

## Recently shipped

- [#16 Chore: add Ready for QA status + auto-transition workflow + ROADMAP lane](https://github.com/ZelosAI/zelosmcp/issues/16) — Chore · P2 · v0.3 (closed 2026-05-22)
- [#12 Chore: add planning ↔ execution loop section to CLAUDE.md + introduce ROADMAP.md](https://github.com/ZelosAI/zelosmcp/issues/12) — Chore · P2 · v0.3 (closed 2026-05-22)
- [#9 docs: add Issue tracking & releases section to CLAUDE.md](https://github.com/ZelosAI/zelosmcp/issues/9) — Chore · P3 · Backlog (closed 2026-05-22)

## See also

- [Zelos Platform Tracker (org-level project)](https://github.com/orgs/ZelosAI/projects/2)
- [Open issues for this repo](https://github.com/ZelosAI/zelosmcp/issues)
- [Suite roadmap (cross-component)](https://github.com/ZelosAI/zelosai/blob/main/ROADMAP.md)
- This repo's [CLAUDE.md](./CLAUDE.md) — planning ↔ execution loop conventions.
