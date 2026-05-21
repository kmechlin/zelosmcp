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
