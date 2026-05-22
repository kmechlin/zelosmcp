# Changelog

## [Unreleased]

## [0.2.0] — suite container contract alignment

### Added

- **Health-probe routes** at `src/zelosmcp/routes/health.py` exposing
  `/healthz`, `/readyz`, and `/` so the suite-standard operator probe config
  works without component-specific branching. `/api/status` still serves
  the rich introspection payload.
- **Suite container contract paths.** SQLite stores (`auth.sqlite`,
  `savings.sqlite`, `assets.sqlite`) and the Fernet key are now resolved
  via `zelosmcp.framework.state_dir.resolve_state_dir()`, which prefers
  `/var/lib/zelos/zelosmcp/` (operator-mounted PVC) over `~/.zelosmcp/`
  (legacy / local-dev). Overridable via `$ZELOSMCP_STATE_DIR`.
- **Standard secret lookup helper.** `zelosmcp.config.load_secret_env()` and
  `_interpolate_env()` now honor the `*_FILE` fallback so `${VAR}` references
  resolve from `/etc/zelos/secrets/<key>` Secret mounts.
- **`deploy/kubernetes/cr-sample.yaml`** — operator-driven install path
  via the `ZelosMCP` CR.
- **`docs.yml` CI workflow** validating Mermaid blocks.

### Changed

- **`deploy/kubernetes/zelosmcp.yaml`** realigned with the suite container
  contract: secret mounts at `/etc/zelos/secrets/{auth.key,providers.json}`,
  PVC at `/var/lib/zelos/zelosmcp/`, standard `ghcr-pull-secret` pull-secret
  name, image pinned to `ghcr.io/zelosai/zelosmcp:develop`, probes against
  `/healthz` and `/readyz`. Labels migrated to `app.kubernetes.io/*`.
- **`Dockerfile`** creates the suite-standard `/var/lib/zelos/zelosmcp`,
  `/etc/zelos/secrets`, `/etc/zelos/tls` directories at build time.

## [Unreleased pre-0.2.0]

### Added

- Suite-aligned scaffolding from the first-pass `zelosai` bootstrap:
  - Top-level `CLAUDE.md` following the canonical Zelos suite template.
  - `LICENSE` (MIT), `.editorconfig`.
  - `.github/workflows/release.yml` — publishes `ghcr.io/zelosai/zelosmcp` on
    `main` push and `v*` tag push.
  - `.github/pull_request_template.md`, `.github/CODEOWNERS`.

### Changed

- `pyproject.toml` `Repository` URL pointed at the canonical
  `github.com/ZelosAI/zelosmcp`.

## Prior development

See git history for pre-bootstrap changes. The full suite documentation lives
in [zelosai/docs/architecture](https://github.com/ZelosAI/zelosai/tree/main/docs/architecture).
