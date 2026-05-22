# CLAUDE.md

> **Note for Claude sessions:** this file follows the Zelos suite-wide template
> at [zelosai/docs/template/CLAUDE.md.tmpl](https://github.com/ZelosAI/zelosai/blob/main/docs/template/CLAUDE.md.tmpl).
> The deep internal-mechanics docs for zelosmcp live in
> [README.md](./README.md), [deploy/README.md](./deploy/README.md), the
> [docs/](./docs/) directory, and [`.github/copilot-instructions.md`](./.github/copilot-instructions.md).
> This file deliberately stays thin and points there for component-internal detail.

## Repository

- **Repo:** `ZelosAI/zelosmcp`
- **Image:** `ghcr.io/zelosai/zelosmcp`
- **Purpose:** MCP aggregator + reverse proxy. Wraps any number of MCP backends
  (stdio / SSE / Streamable-HTTP) behind a single stable URL (`/<name>/mcp`
  passthrough, `/mcp` aggregated, `/zelosmcp/mcp` built-in self-introspection).
  Compresses tool descriptions to save subscription-LLM tokens, and pushes IDE
  assets (Cursor `.mdc` rules, Copilot `copilot-instructions.md`, skills,
  agents, hooks). Architecture context:
  [zelosai/docs/architecture/04-components/zelosmcp.md](https://github.com/ZelosAI/zelosai/blob/main/docs/architecture/04-components/zelosmcp.md).
- **State:** Mature. Production-shape Kubernetes manifest in
  [deploy/kubernetes/zelosmcp.yaml](./deploy/kubernetes/zelosmcp.yaml).
  Multi-stage Docker build. Full Makefile lifecycle.

## Active Branch

- Work on: `claude/<session-slug>` (or whatever the harness sets).

## Layout

See [README.md "Project structure"](./README.md#project-structure) for the
authoritative tree. Key entry points (post-`feature/refactor-code` reshape):

- `src/zelosmcp/app.py` — Starlette ASGI app + dispatcher; `create_app()`
  wires per-feature route modules from `routes/`.
- `src/zelosmcp/routes/<feature>.py` — one module per route group
  (`assets`, `auth`, `docs_view`, `pages`, `repos`, `servers`, `streaming`).
  Each exposes a `register(...)` callable consumed by `app.create_app()`.
- `src/zelosmcp/aggregator.py` — `/mcp` aggregator (union of tools across backends).
- `src/zelosmcp/builtin.py` — `/zelosmcp/mcp` built-in MCP + rule generator.
- `src/zelosmcp/compression.py` — `get_tool_schema` / `invoke_tool` compression wrappers.
- `src/zelosmcp/openapi.py` — extracted OpenAPI / CallResult helpers.
- `src/zelosmcp/constants.py` — shared constants (separator, reserved names,
  table names, well-known HTTP paths).
- `src/zelosmcp/util.py` — shared helpers.
- `src/zelosmcp/ui.py` — loader + template-variable substitution; HTML/CSS/JS
  lives in `src/zelosmcp/static/` (`index.{html,css,js}`, `catalog.{html,css,js}`).
- `src/zelosmcp/framework/{assetstore,authstore,savingsstore}/sqlite.py` —
  persistent stores (SQLite-backed).
- `Dockerfile` (upstream community) and `docker-tools/Dockerfile` (corp-cert-aware).
- `Makefile` — `init-env`, `up`, `down`, `restart`, `load`, `index`, `rule`, `clean`, `nuke`,
  plus `make test` / `make lint` / `make typecheck` for the dev loop.
- `deploy/kubernetes/zelosmcp.yaml` — production Kubernetes manifest.

When modules move, update this section.

## How to run it / How to build it

See the [README quickstart](./README.md#quickstart) for the canonical path. Short:

```bash
make init-env   # one-time .env wizard
make up         # build (if missing) + run + load default backends
```

For Kubernetes deployment see [deploy/README.md](./deploy/README.md).

## What has been verified / What has NOT been verified

See [README.md](./README.md) and the in-repo `docs/` tree — zelosmcp's
verification state is documented there in component-specific detail.

## Configuration surface

See [.env.example](./.env.example) and [configs/default-zelosmcp.json](./configs/default-zelosmcp.json).
Container-side mount conventions documented in
[docs/configuration.md](./docs/configuration.md) and [docs/makefile.md](./docs/makefile.md).

## Git / Workflow

- **Branching policy:** `main` is the protected release line. Feature branches
  (the `claude/*` session branches and any other topic branches) MUST be PR'd
  into `develop` first. Promotion from `develop` to `main` is a separate PR
  cut from `develop` once a set of features has been integrated and validated.
  Never open a PR from a feature branch directly against `main`. If `develop`
  does not yet exist on the remote, create it from `main` before opening the
  first feature PR.
- **Commits:** clear, descriptive messages. Co-author with Claude where applicable.
- **Tagging:** semver — `v0.1.0`, `v0.2.0`, … Tag from `main` only.
- **Container builds:** `.github/workflows/release.yml` builds and pushes
  multi-arch images (`linux/amd64` + `linux/arm64`) to
  `ghcr.io/zelosai/zelosmcp` on every push to `develop`, every push to `main`,
  and every `v*` tag push. The version is read from `pyproject.toml`.
  Tags applied:
  - **develop push** → `:v<X.Y.Z>-dev` · `:latest` · `:sha-<short>`
  - **main push** → `:v<X.Y.Z>` · `:latest` · `:stable` · `:sha-<short>`
  - **`v<X.Y.Z>` git tag push** → same as main push, plus validates that the
    tag name matches the in-repo version.

  `:latest` follows the most recent build of any kind; `:stable` tracks `main` only.
  Build context is the repo root and the default `Dockerfile` is used (the
  upstream community variant; the corp cert-aware `docker-tools/Dockerfile`
  is for local builds behind a TLS-intercepting proxy and not used in CI).
- **PRs:** do not create unless explicitly asked.

## Issue tracking & releases

All features, bugs, and chores in the Zelos suite are tracked in the org-level
GitHub Project [**Zelos Platform Tracker**](https://github.com/orgs/ZelosAI/projects/2).
Every issue opened in any ZelosAI repo auto-adds to the project via
`.github/workflows/add-to-project.yml` (uses the `ADD_TO_PROJECT_PAT` org secret).

**File issues in the repo they belong to**, not in `zelosai`, unless the work
genuinely spans multiple repos.

**Project fields to set on each item:**

- **Work type** — `Feature` / `Bug` / `Chore`.
- **Priority** — `P0` (drop everything) / `P1` (this sprint) / `P2` (this
  release) / `P3` (someday).
- **Status** — `Todo` / `In Progress` / `Ready for QA` / `Done` / `Blocked`.
  Transitions: `Todo` → `In Progress` is set manually when you start work.
  `In Progress` → `Ready for QA` fires **automatically** when the feature →
  develop PR merges and the `release` workflow's dev container build
  succeeds (see `.github/workflows/tracker-ready-for-qa.yml`).
  `Ready for QA` → `Done` fires **automatically** via the project's
  "Item closed" workflow when the linked issue is auto-closed on the
  develop → main promotion (per `Closes #N` in the feature PR body).
  Use `Blocked` (side-state, any phase) when you can't make forward
  progress; note the blocker in the issue.
- **Release** — cross-repo target: `v0.1`, `v0.2`, `v0.3`, `v1.0`, or
  `Backlog`.
- **Milestone** — matching repo-level milestone (same names exist in every
  repo). Keep Milestone and Release in sync so repo-native views match the
  project.

**When to file vs just fix:** if it's a self-contained change you're about to
ship this session, the PR is the record — no issue needed. File an issue for
work that won't ship this session, anything cross-repo, anything the user
asks to track, or follow-ups you discover but won't do now.

**Linking PRs:** PRs that resolve an issue must include `Closes #N` (or
`Fixes #N`) in the description so GitHub auto-closes the issue on merge and
the project's "Item closed" workflow moves it to `Done`.

## Planning and execution loop

This repo follows a structured planning ↔ execution flow with Claude. Three
artifacts stay in lockstep: the [Zelos Platform Tracker](https://github.com/orgs/ZelosAI/projects/2)
(structured state), this repo's `ROADMAP.md` (human-readable view of THIS
component), and the suite-wide [`zelosai/ROADMAP.md`](https://github.com/ZelosAI/zelosai/blob/main/ROADMAP.md)
(cross-component view).

### When a plan is accepted (planning → backlog)

The moment `ExitPlanMode` returns user approval, Claude must convert the
accepted plan into trackable work BEFORE starting any implementation:

1. **Identify feature boundaries.** Each implementable slice from the plan
   becomes one issue in the canonical repo for that work. Cross-repo slices
   get one canonical issue plus follow-up references in companion repos.
2. **File one issue per slice.** Title `Feature: <slice headline>` (or
   `Bug:` / `Chore:` if more accurate). Body carries the slice's **Why**,
   **Files to change**, **Verification**, and any decisions made during
   planning. Don't summarize — paste the slice content so future sessions
   can execute from the issue alone without re-reading the plan file.
3. **Apply project fields.** `Work type`, `Priority` (P0–P3),
   `Status=Todo`, `Release` (v0.x or `Backlog`). Field + option IDs change
   when the project schema is edited; re-fetch them with
   `gh project field-list 2 --owner zelosai --format json` instead of
   hardcoding.
4. **Apply the repo milestone.** Match `Release`.
   `gh issue create … --milestone v0.x`.
5. **Update this repo's `ROADMAP.md`.** Every filed feature lands in a lane:
   `In flight` (Status=In Progress), `Next` (Status=Todo with a v0.x
   release), `Backlog` (Release=Backlog), or `Recently shipped` (Status=Done,
   closed in the last release). Link by issue URL with the title + priority
   + release tags.
6. **Update `zelosai/ROADMAP.md`** as well if the feature matters at the
   suite level — anything in a v0.x release lane (in-flight / next /
   following) always goes in the suite roadmap; pure component-local backlog
   items can stay component-only.
7. **Update suite-architecture memory** if the plan introduces a new
   component or reshapes how existing ones interact.

This applies to plans of any size. Trivial single-file fixes the user asked
to be done in-session still skip the issue step (per "When to file vs just
fix" above) — but anything that came through `ExitPlanMode` is, by
definition, planned work and gets tracked.

### When given an issue to execute (backlog → implementation)

If the user references an issue by number or URL, Claude:

1. **Fetch the issue.** `gh issue view <N> -R zelosai/<repo> --json
   title,body,labels,milestone,assignees,projectItems`. Read end-to-end
   before touching code.
2. **Move the project item to `Status=In Progress`** and **move the entry
   in `ROADMAP.md` from `Next` (or `Backlog`) to `In flight`**. Same for
   the suite roadmap if the item lives there. Both happen in a single
   commit on the feature branch, before any implementation commits.
3. **Branch off `develop`.** Name: `claude/<short-slug-from-title>`.
4. **Implement** per the issue body's "Files to change" and "Verification"
   sections. Surface deviations to the user before pushing.
5. **PR feature → develop** with `Closes #<N>` in the body. Merge with
   `gh pr merge <PR> --squash --delete-branch --admin`. After merge: the
   `release` workflow builds and pushes the dev container; the
   `tracker-ready-for-qa` workflow then auto-moves the project item to
   `Status=Ready for QA`. Manually move the `ROADMAP.md` entry from
   `In flight` to `Ready for QA`.
6. **Promote develop → main** via a separate PR (`gh pr merge <PR> --merge
   --admin` to preserve commits). Every repo in the org defaults to `main`,
   so this is the merge that fires GitHub's `Closes #N` auto-close.
7. **Back-merge `main → develop`** to absorb the promotion's merge commit.
8. **Move the ROADMAP entries.** `Ready for QA` → `Recently shipped` in
   this repo's `ROADMAP.md` (and in `zelosai/ROADMAP.md` if it's there too).
   This can be folded into the back-merge PR or a tiny follow-up commit.
9. **Confirm.** The project's "Item closed" workflow moves Status to `Done`
   automatically; verify with `gh issue view <N>` and the project view.

If an issue turns out to be too coarse to execute as a single PR, propose
splitting it (in plan mode) before starting any code.

## Relation to the Zelos suite

zelosmcp is the MCP-side of the async path: the IDE connects to it via
[zelosgateway](https://github.com/ZelosAI/zelosgateway), and zelosmcp fans the
request out across many MCP backends. Its tool-description compression and
IDE asset push are two of the largest subscription-token-savings levers in the
suite. See
[zelosai/docs/architecture/01-async-path.md](https://github.com/ZelosAI/zelosai/blob/main/docs/architecture/01-async-path.md)
and [zelosai/docs/architecture/00-overview.md](https://github.com/ZelosAI/zelosai/blob/main/docs/architecture/00-overview.md).

## Notes / Blockers

- The repo's existing internal documentation (README, copilot-instructions.md,
  deploy/README, docs/) is the source of truth for component mechanics —
  this CLAUDE.md should remain a thin pointer to keep duplication out of the
  way as zelosmcp evolves.
- The `Repository` URL in `pyproject.toml` previously pointed at a prior
  organization; suite alignment moved zelosmcp under `ZelosAI/*`. Image
  publishing should target `ghcr.io/zelosai/zelosmcp` (suite-standard);
  any prior image-publish target should be retired.
