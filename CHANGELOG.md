# Changelog

## [Unreleased]

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
