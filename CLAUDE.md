# CLAUDE.md — project memory for Claude sessions

## Gitflow

- Integration branch is `develop`. Production releases ship from `main`.
- All feature work happens on `feature/<short-slug>` branches cut from `origin/develop`. Never commit directly to `develop` or `main`. The active multi-phase refactor lives on `feature/refactor-code`.
- Rebase on `origin/develop` before pushing if it has moved. Don't force-push to `develop` or `main`.

## Commits

- Conventional Commits style: `refactor(scope): …`, `chore: …`, `fix(scope): …`, `feat(scope): …`, `test(scope): …`, `docs(scope): …`.
- One logical change per commit. Each commit must pass `pytest tests/ -q` (or document the pre-existing failures it inherited).
- When work is broken into phases, land one commit per phase on the same feature branch, in the declared order. The plan file under `/root/.claude/plans/` is the source of truth for phase order.

## PRs

- Open against `develop` only. Title mirrors the lead commit. Body lists what changed, what tests cover it, and any follow-ups.
- Use the `mcp__github__create_pull_request` MCP tool — there is no `gh` CLI in this environment.
- Don't merge your own PRs; leave them for reviewer approval.

## Local checks (run before pushing)

- `make test` — pytest suite (`PYTHONPATH=src .venv/bin/pytest tests/ -q`).
- `make lint` — ruff.
- `make typecheck` — mypy.
- Coverage gate is 90% (`[tool.coverage.report] fail_under = 90` in `pyproject.toml`). Don't bypass it.

## Layout pointers

- Entry point: `zelosmcp.app:main` (Starlette app + uvicorn).
- Routes: `src/zelosmcp/routes/<feature>.py` — each module exposes a `register(router, manager)` callable consumed by `app.create_app()`.
- Persistent stores: `src/zelosmcp/framework/{assetstore,authstore,savingsstore}/sqlite.py` — all extend `framework/sqlite_base.py:BaseSQLiteStore`.
- Shared constants: `src/zelosmcp/constants.py` (separator, reserved names, table names, well-known HTTP paths).
- Built-in MCP + rule generation: `BuiltinServer` in `src/zelosmcp/builtin.py`; rule rendering in `src/zelosmcp/rule_generator.py`; tool schemas in `src/zelosmcp/builtin_tools.py`.
- UI: HTML/CSS/JS in `src/zelosmcp/static/`; `src/zelosmcp/ui.py` is just the loader + template-variable substitution.
- Update this section when modules move.

## Don't

- Don't push to `main`.
- Don't force-push to `develop`.
- Don't skip pre-commit hooks (`--no-verify`) or bypass signing.
- Don't bypass the 90% coverage gate.
- Don't add new top-level Python modules without a corresponding entry in the Layout pointers above.
