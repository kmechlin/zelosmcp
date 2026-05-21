## What

<one-paragraph summary of the change>

## Why

<link to issue, ticket, or doc; if architectural, link to the relevant page in
[zelosai/docs/architecture](https://github.com/ZelosAI/zelosai/tree/main/docs/architecture)>

## Test plan

- [ ] `make lint` passes
- [ ] `make test` passes
- [ ] If container changed: `make image` succeeds
- [ ] If runtime behavior changed: <describe manual verification>

## Gitflow check

- [ ] Targeting `develop` (NOT `main`). See [05-gitflow.md](https://github.com/ZelosAI/zelosai/blob/main/docs/architecture/05-gitflow.md).
- [ ] Branch is `claude/<slug>` or topic branch off `develop`.
- [ ] No semver tag in this PR (tags are cut from `main` only).
