# CLAUDE.md

> **Note for Claude sessions:** this file follows the Zelos suite-wide template
> at [zelosai/docs/template/CLAUDE.md.tmpl](https://github.com/ZelosAI/zelosai/blob/main/docs/template/CLAUDE.md.tmpl).
> The canonical gitflow rules every Zelos repo follows live in
> [zelosai/docs/architecture/05-gitflow.md](https://github.com/ZelosAI/zelosai/blob/main/docs/architecture/05-gitflow.md).
> The deep internal-mechanics docs for zelosmcp live in
> [README.md](./README.md), [deploy/README.md](./deploy/README.md), the
> [docs/](./docs/) tree, and [`.github/copilot-instructions.md`](./.github/copilot-instructions.md).
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
- **State:** v0.1.0 in `pyproject.toml`; v0.2.0 suite-container-contract work
  is landed in `CHANGELOG.md` under the `[0.2.0]` section but the version
  bump has not yet been cut. Treat as **pre-0.2.0 on `develop`**, version-
  bump-pending. Production-shape Kubernetes manifest in
  [deploy/kubernetes/zelosmcp.yaml](./deploy/kubernetes/zelosmcp.yaml).
  Multi-stage Docker build, full Makefile lifecycle.

## Active Branch

- Work on: `claude/claude-md-docs-0wYXt`

## Layout

```
zelosmcp/
├── CLAUDE.md  README.md  CHANGELOG.md  ROADMAP.md  LICENSE
├── pyproject.toml  uv.lock                        # Python project + lockfile
├── Dockerfile                                     # upstream community build (CI default)
├── Makefile                                       # full local lifecycle (build/up/down/load/test/lint…)
├── mcpdoc-implementation-plan.md                  # design doc: MCPDoc backend + dep auto-discovery
├── .env.example  .editorconfig  .dockerignore
├── .github/
│   ├── workflows/
│   │   ├── release.yml                            # multi-arch GHCR build on develop/main/v* tags
│   │   ├── docs.yml                               # mermaid block validation
│   │   ├── add-to-project.yml                     # auto-add issues to Zelos Platform Tracker
│   │   └── tracker-ready-for-qa.yml               # In Progress → Ready for QA on dev build success
│   ├── copilot-instructions.md                    # canonical Copilot guidance
│   ├── agents/  skills/  hooks/  prompts/         # IDE-asset templates (also pushed at runtime)
│   ├── hooks.json  mcp.json  settings.json  zelosmcp.json
│   ├── CODEOWNERS  pull_request_template.md
├── benchmarks/                                    # Node/TS subscription-token-savings harness
│   ├── package.json  vitest.config.ts  tsconfig*.json
│   ├── src/{cli,configs,report,runner,secrets,static-analyzer,tokens,types,usage-api}.ts
│   ├── src/{adapters,core}/  prompts/  tests/
├── configs/
│   ├── default-zelosmcp.json                      # minimum useful catalog (k8s+docker+github)
│   ├── mandatory-zelosmcp.json                    # baseline always-loaded entries
│   ├── example-passthrough-zelosmcp.json          # broader Nike/atlassian/etc. example
│   ├── auth-providers.json  example-auth-providers.json
│   ├── default-volumes.conf
│   └── assets/{filesystem,global,mcpdoc,pincher}.yaml
├── deploy/
│   ├── kubernetes/zelosmcp.yaml                   # suite-aligned manifest (PVC, secrets, probes)
│   ├── kubernetes/cr-sample.yaml                  # operator-driven install via ZelosMCP CR
│   ├── kubernetes/ghcr-pull-secret.example.yaml
│   └── swarm/docker-compose.yml
├── docker-tools/
│   ├── Dockerfile                                 # corp-cert-aware variant (TLS-intercepting proxy)
│   ├── buildx.Dockerfile  README.md
├── docs/
│   ├── architecture.md  quickstart.md  quickstart-no-docker.md
│   ├── configuration.md  makefile.md  http-api.md  reverse-proxy.md
│   ├── compression.md  benchmarks.md  built-in-mcp.md  default-mcps.md
│   ├── repositories.md  dashboard.md
│   ├── assets.md  assets-api.md  assets-editor.md  assets-yaml.md  asset-kinds.md
│   ├── oauth-passthrough.md  cursor-integration.md  vscode-integration.md
│   ├── setup-rancher-desktop.md
├── scripts/
│   ├── init_env.py                                # `make init-env` wizard
│   └── compare_aggregator_mcp.py
├── src/zelosmcp/
│   ├── __init__.py  __main__.py
│   ├── app.py                                     # Starlette ASGI app + create_app dispatcher
│   ├── aggregator.py                              # /mcp aggregator (union of backend tools)
│   ├── builtin.py                                 # /zelosmcp/mcp built-in MCP + rule generator
│   ├── compression.py                             # get_tool_schema / invoke_tool wrappers
│   ├── config.py                                  # config load + secret/env interpolation
│   ├── constants.py                               # separator, reserved names, well-known paths
│   ├── docs.py  llms_txt_registry.py              # docs surface + llms.txt registry (mcpdoc plan)
│   ├── manager.py                                 # backend lifecycle manager
│   ├── openapi.py                                 # OpenAPI / CallResult helpers
│   ├── passthrough_pool.py  proxy.py              # /<name>/mcp reverse-proxy + connection pool
│   ├── repos.py                                   # repository-aware tooling
│   ├── response.py                                # response formatting (compact_json etc.)
│   ├── savings.py  savings_db.py                  # token-savings tracking
│   ├── ui.py                                      # HTML template loader + var substitution
│   ├── util.py
│   ├── auth/                                      # auth providers (github, okta, passthrough, static)
│   │   └── {factory,github,okta,okta_authorization_code,passthrough,protocol,registry,static,store}.py
│   ├── framework/                                 # SQLite-backed stores + asset framework
│   │   ├── state_dir.py                           # resolve /var/lib/zelos/zelosmcp vs ~/.zelosmcp
│   │   ├── assetstore/{sqlite,defaults,kinds/,prefs,push,registry,row,runner,schema,seeder,…}.py
│   │   ├── authstore/{sqlite,protocol}.py
│   │   └── savingsstore/{sqlite,protocol}.py
│   ├── routes/                                    # one module per route group; each exposes register()
│   │   └── {assets,auth,docs_view,health,pages,repos,servers,streaming}.py
│   └── static/                                    # bundled UI assets
│       └── {index,catalog}.{html,css,js}
└── tests/                                         # pytest suite (PYTHONPATH=src)
    ├── conftest.py
    ├── test_*.py                                  # aggregator/builtin/compression/auth/manager/…
    └── framework/                                 # asset-framework unit/integration tests
        └── test_*.py
```

When modules move, update this section.

## How to run it / How to build it

```bash
# Local Docker lifecycle (canonical path)
make init-env       # one-time .env wizard
make up             # build (if missing) + start container + load default backends
make down           # stop + remove the container
make restart        # bounce (down + up)
make load           # POST $(ZELOSMCP_CONFIG); auto-chains index + rule
make logs           # tail container logs
make shell          # bash inside the container
make status         # container + HTTP probe state
make ui             # open the web UI

# Dev loop
make test           # pytest (PYTHONPATH=src tests/)
make lint           # ruff
make typecheck      # mypy
make check          # lint + typecheck + test

# Teardown
make clean          # down + remove image, builder, registry helper
make nuke           # clean + remove every persistent zelosmcp-* Docker volume
```

The `zelosmcp` console script (`pyproject.toml [project.scripts]`) launches
`zelosmcp.app:main` directly for non-Docker installs — see
[docs/quickstart-no-docker.md](./docs/quickstart-no-docker.md).

For Kubernetes deployment see [deploy/kubernetes/zelosmcp.yaml](./deploy/kubernetes/zelosmcp.yaml)
(direct apply) or [deploy/kubernetes/cr-sample.yaml](./deploy/kubernetes/cr-sample.yaml)
(operator-driven install via the `ZelosMCP` CR provisioned by `zelosai`).

## What has been verified / What has NOT been verified

- **Verified:** test suite under `tests/` (and `tests/framework/`) runs via
  `make test`; ruff lint and mypy typecheck pass via `make lint` /
  `make typecheck`; full Docker lifecycle (`make up` / `make load` /
  `make down`) is the everyday developer path. The release workflow
  pins version from `pyproject.toml`, validates `v*` tag against in-repo
  version, and publishes multi-arch (`linux/amd64` + `linux/arm64`)
  images using the root `Dockerfile`.
- **Not verified end-to-end here:** operator-driven Kubernetes install
  (the `cr-sample.yaml` path requires `zelosai`'s operator to be running
  in-cluster); `mcpdoc-implementation-plan.md` MVP/moonshot phases are
  not yet implemented; the version bump from `0.1.0` → `0.2.0` in
  `pyproject.toml` has not been cut even though the `[0.2.0]` CHANGELOG
  block is populated.
- Component-specific verification state is also documented in
  [README.md](./README.md) and the in-repo [docs/](./docs/) tree.

## Configuration surface

See [.env.example](./.env.example) (Makefile / Docker runtime knobs) and
[configs/default-zelosmcp.json](./configs/default-zelosmcp.json) (the MCP
catalog itself). The mandatory baseline and broader Nike/atlassian example
live alongside as `configs/mandatory-zelosmcp.json` and
`configs/example-passthrough-zelosmcp.json`. Auth providers are configured
via `configs/auth-providers.json` (see `configs/example-auth-providers.json`
for templates).

Container-side mount conventions and the suite container contract
(`/var/lib/zelos/zelosmcp` PVC, `/etc/zelos/secrets/*` Secret mounts,
`/etc/zelos/tls`, `*_FILE` env-var fallback) are documented in
[docs/configuration.md](./docs/configuration.md) and
[docs/makefile.md](./docs/makefile.md). State dir resolution lives in
`src/zelosmcp/framework/state_dir.py`.

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
    tag name matches the in-repo version (build fails if they diverge).

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

- **Version bump pending.** `pyproject.toml` still reads `version = "0.1.0"`
  even though the `[0.2.0] — suite container contract alignment` block in
  `CHANGELOG.md` is complete. The bump (and `v0.2.0` git tag from `main`)
  is the next release-mechanics action; the `release` workflow will fail
  the build if a `v0.2.0` tag is pushed without bumping `pyproject.toml`
  first.
- **`mcpdoc-implementation-plan.md`** at the repo root is a design document
  for adding the LangChain `mcpdoc` MCP backend (MVP) and project-dependency
  `llms.txt` auto-discovery (moonshot). The scaffolding files
  (`llms_txt_registry.py`, `configs/assets/mcpdoc.yaml`,
  `tests/test_mcpdoc_integration.py`, `tests/test_llms_txt_registry.py`)
  exist; the full plan is not yet executed.
- The repo's existing internal documentation (`README.md`,
  `.github/copilot-instructions.md`, `deploy/`, `docs/`) is the source of
  truth for component mechanics — this CLAUDE.md should remain a thin
  pointer so duplication doesn't drift as zelosmcp evolves.
