---
name: zelosmcp-pre-commit
description: Pre-commit blast radius analysis. Use when committing, pushing, or asking about change impact.
---
# Pre-commit Analysis

Run `pincher__changes` (or `invoke_tool(tool_name="changes")` if compressed)
before committing. Returns:
- Changed symbols from `git diff`
- Impacted callers with risk labels (CRITICAL/HIGH/MEDIUM/LOW)
- Tests to re-run (ranked by overlap)

## Scopes

- `scope=unstaged` (default) — working-tree changes
- `scope=staged` — staged changes only
- `scope=all` — includes untracked files
- `scope=base:<branch>` — diff vs branch's merge-base (PR preview)
