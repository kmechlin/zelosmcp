# Plan: Replace subagent architecture with 4 flat agents

## TL;DR

Replace the current zelos orchestration + 7 subagent architecture with 4 self-sufficient, user-selectable agents (`zelos-plan`, `zelos-ask`, `zelos-agent`, `zelos-mcp-only`) across both VS Code and Cursor. Each agent gets explicit memory usage instructions and direct access to zelosmcp tools — no delegation overhead.

## Scope

- **In scope:** Rewrite `configs/assets/global.yaml` agents section for both VS Code and Cursor targets
- **Out of scope:** Skills, rules, hooks, extensions, agent.py rendering code (no changes needed)

## Changes to `configs/assets/global.yaml`

### Disable old agents (set `targets: []` — preserved but not pushed)

| Agent key | Current targets | New targets |
|-----------|----------------|-------------|
| `zelos-cursor` | `[cursor]` | `[]` |
| `zelos-vscode` | `[vscode]` | `[]` |
| `zelos-ask-vscode` | `[vscode]` | `[]` |
| `explorer-cursor` | `[cursor]` | `[]` |
| `explorer-vscode` | `[vscode]` | `[]` |
| `researcher-cursor` | `[cursor]` | `[]` |
| `researcher-vscode` | `[vscode]` | `[]` |
| `developer-cursor` | `[cursor]` | `[]` |
| `developer-vscode` | `[vscode]` | `[]` |
| `documenter-cursor` | `[cursor]` | `[]` |
| `documenter-vscode` | `[vscode]` | `[]` |
| `reviewer-cursor` | `[cursor]` | `[]` |
| `reviewer-vscode` | `[vscode]` | `[]` |
| `devops-cursor` | `[cursor]` | `[]` |
| `devops-vscode` | `[vscode]` | `[]` |

These agents remain in `global.yaml` for future reuse but produce no output files.

### Add (8 agents — 4 per target)

#### 1. `zelos-plan-vscode` (VS Code)
- **name:** zelos-plan
- **description:** Planning agent. Researches codebase, clarifies requirements, and builds actionable implementation plans. Read-only — never edits files.
- **model:** GPT-4.5 (copilot)
- **tools:** web, vscode/memory, vscode/askQuestions, pincher (compressed), filesystem (compressed)
- **handoffs:** to zelos-agent ("Start Implementation"), to zelos-ask ("Ask a Question")
- **body:** Planning workflow (discover → align → plan → refine) with explicit memory instructions:
  - On start: read `/memories/repo/` for conventions and `/memories/session/` for prior context
  - Persist plans to `/memories/session/plan.md`
  - Update session memory as decisions are made
  - Never edit source files

#### 2. `zelos-ask-vscode` (VS Code)
- **name:** zelos-ask
- **description:** Read-only Q&A agent. Answers questions about code, architecture, and project patterns. Never modifies files.
- **model:** GPT-4.5 (copilot)
- **tools:** web, vscode/memory, vscode/askQuestions, pincher (compressed), filesystem (compressed)
- **handoffs:** to zelos-plan ("Create a Plan"), to zelos-agent ("Start Implementation")
- **body:** Q&A workflow with memory instructions:
  - On start: check `/memories/repo/` for conventions
  - Consult session memory for running context
  - Store significant findings in session memory when they'll inform later work
  - Never edit files

#### 3. `zelos-agent-vscode` (VS Code)
- **name:** zelos-agent
- **description:** Full implementation agent. Reads, edits, validates, and documents code. Uses memory to track progress and learn from past work.
- **model:** GPT-4.5 (copilot)
- **tools:** agent, browser, todo, web, edit, vscode/memory, vscode/askQuestions, pincher (compressed), filesystem (compressed)
- **handoffs:** to zelos-plan ("Plan First"), to zelos-ask ("Ask a Question")
- **body:** Implementation workflow with full memory lifecycle:
  - On start: read `/memories/session/plan.md` for the active plan, `/memories/repo/` for conventions
  - During work: update session memory with progress
  - After key learnings: store reusable patterns in `/memories/` (user memory) or `/memories/repo/`
  - Match existing patterns, validate changes, report what changed

#### 4. `zelos-mcp-only-vscode` (VS Code)
- **name:** zelos-mcp-only
- **description:** Pure MCP agent. Uses only zelosmcp backends (pincher, filesystem) for all analysis, file reading, and file writing. No VS Code edit tool — all file operations go through filesystem compressed wrappers.
- **model:** GPT-4.5 (copilot)
- **tools:** vscode/memory, todo, browser, pincher (compressed), filesystem (compressed) — NO `edit`, NO `web`, NO `agent`
- **handoffs:** to zelos-plan ("Plan First"), to zelos-ask ("Ask a Question")
- **body:** MCP-native workflow with memory instructions:
  - On start: read `/memories/repo/` for conventions and `/memories/session/` for prior context
  - Use pincher tools (search, context, trace, architecture, changes) for all code analysis
  - Use filesystem tools (read_text_file, edit_file, search_files, directory_tree) for all file operations
  - Apply path translation: host paths → `/user_data_ro/<repo>` (reads) or `/user_data_rw/<repo>` (writes)
  - Update session memory with progress
  - Store reusable patterns in `/memories/repo/` when verified

#### 5. `zelos-plan-cursor` (Cursor)
- **name:** zelos-plan
- **description:** Planning agent. Researches codebase, clarifies requirements, builds actionable plans. Read-only.
- **model:** composer-2[]
- **readonly:** true
- **body:** Same planning workflow adapted for Cursor (no vscode/memory references; use pincher adr for persistence)

#### 6. `zelos-ask-cursor` (Cursor)
- **name:** zelos-ask
- **description:** Read-only Q&A agent. Answers questions about code, architecture, and project patterns.
- **model:** composer-2[]
- **readonly:** true
- **body:** Same Q&A workflow adapted for Cursor

#### 7. `zelos-agent-cursor` (Cursor)
- **name:** zelos-agent
- **description:** Full implementation agent. Reads, edits, validates, and documents code.
- **model:** composer-2[]
- **body:** Same implementation workflow adapted for Cursor

#### 8. `zelos-mcp-only-cursor` (Cursor)
- **name:** zelos-mcp-only
- **description:** Pure MCP agent. Uses only zelosmcp backends for all analysis and file operations.
- **model:** composer-2[]
- **body:** Same MCP-native workflow adapted for Cursor (path translation, pincher + filesystem only)

## Memory usage pattern (VS Code agents)

All four agents share this memory discipline:

```
/memories/repo/    → Read on start. Codebase conventions, build commands, verified patterns.
/memories/session/ → Active task context. Plans, decisions, progress tracking.
/memories/         → User preferences and cross-project insights. Write sparingly.
```

Each agent body includes a `## Memory` section with specific read/write instructions for that agent's role.

## Handoff flow

```
zelos-plan ←→ zelos-ask ←→ zelos-agent
     ↓            ↑              ↑
     └────────────┼──────────────┘
                  └── zelos-mcp-only
```

All four agents can hand off to each other, enabling the user to switch modes without losing context (session memory carries state between agents).

## Relevant files

- `configs/assets/global.yaml` — The ONLY file to edit. Replace the `agents:` section. Leave rules, hooks, skills, extensions untouched.
- `src/zelosmcp/framework/assetstore/kinds/agent.py` — Reference only. Rendering code handles all frontmatter fields we need (handoffs, tools, model, etc.). No changes needed.

## Verification

1. Run `uv run pytest tests/framework/test_kind_render_targets.py` — verify agent rendering still works (may need test updates for new agent names)
2. Push agents via the API or CLI and verify generated `.github/agents/*.agent.md` and `.cursor/agents/*.md` files have correct frontmatter
3. Manually verify in VS Code that all 4 agents appear in the agent picker and handoff buttons work
4. Verify pincher/filesystem compressed tools work in each agent
5. Verify `vscode/memory` reads/writes work in each agent

## Decisions

- Old subagents preserved with `targets: []` — no hidden delegation layer, but definitions kept for future reuse
- All 4 VS Code agents are user-invocable (default behavior, no `user_invocable: false`)
- Cursor agents mirror the same 4-agent structure with readonly flags where appropriate
- Session memory is the handoff mechanism between agents (plan.md, progress, decisions)
- Skills (code-review, file-operations, codebase-explore, architecture-map, change-blast-radius) remain available — agents reference them in body text as workflow guidance
- `zelos-mcp-only` deliberately excludes `edit` and `web` — forces all file I/O through filesystem compressed wrappers with path translation
- seed_version should be bumped from 6 to 7
