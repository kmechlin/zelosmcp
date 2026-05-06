# Makefile reference

The [`Makefile`](../Makefile) wraps every container-lifecycle action LocalMCP needs and adds an enterprise-friendly cert-aware build path on top. This page covers every target, how the volumes config works, and how the Make variables interact.

## At a glance

```bash
make localmcp-up              # start the container (auto-builds image if missing)
make localmcp-load            # POST configs/default-localmcp.json to /api/start
make localmcp-status          # is it up? + curl probes
make localmcp-list-tools      # tools each backend exposes (uses /mcp aggregator)
make localmcp-warm-index      # pre-build pincher's symbol DB for the current repo
make localmcp-warm-index-full # pre-build it for the entire /user_data_ro mount
make localmcp-shell           # bash inside the container
make localmcp-logs            # tail container logs
make localmcp-restart         # bounce the container
make localmcp-down            # stop + remove
make clean                    # tear down container, builder image, buildx instance
```

## Make variables (the things you'll override)

Every target takes Make variable overrides on the command line, e.g. `make localmcp-up KUBERNETES_CONFIG_FILE=/some/other/path`.

| Variable | Default | Purpose |
|---|---|---|
| `LOCALMCP_PORT` | `8000` | Container-internal port LocalMCP listens on. Published to the host via `-p $(LOCALMCP_BIND_ADDR):$(LOCALMCP_PORT):$(LOCALMCP_PORT)`. |
| `LOCALMCP_BIND_ADDR` | `127.0.0.1` | Host address the published port binds to. `127.0.0.1` (default) means only this Mac can reach `:8000`; `0.0.0.0` exposes it on the LAN. |
| `LOCALMCP_IMAGE_TAG` | `localmcp:dev` | Image tag the build produces and `docker run` uses. |
| `LOCALMCP_CONTAINER` | `rancher-localmcp` | Container name `make localmcp-up` creates. |
| `LOCALMCP_CONFIG` | `$(pwd)/configs/default-localmcp.json` | JSON config file `make localmcp-load` POSTs into the running container. |
| `LOCALMCP_VOLUMES_FILE` | `$(pwd)/configs/default-volumes.conf` | List of `docker run -v` specs. See "Volume mounts" below. |
| `USER_DATA_ROOT` | `$HOME` | Host directory bind-mounted twice: read-write at `/user_data_rw` (filesystem MCP root) and read-only at `/user_data_ro` (pincher index target). |
| `LOCALMCP_PROJECT_NAME` | `$(PROJECT_NAME)` (current git repo basename) | Used to derive the per-repo warm-index path. |
| `LOCALMCP_PROJECT_REL` | computed via `python3 os.path.relpath` | Relative path from `$(USER_DATA_ROOT)` to the current git toplevel. Falls back to `$(LOCALMCP_PROJECT_NAME)` when git isn't available. |
| `LOCALMCP_PROJECT_PATH` | `/user_data_ro/$(LOCALMCP_PROJECT_REL)` | In-container path that `localmcp-warm-index` indexes. Override directly for repos outside `$(USER_DATA_ROOT)`. |
| `LOCALMCP_WARM_ON_LOAD` | `1` | When `1`, `make localmcp-load` chains into `make localmcp-warm-index` after backends start. Set to `0` for fast loads. |
| `KUBERNETES_CONFIG_FILE` | `$HOME/.kube/config` | Host kubeconfig bind-mounted read-only to `/root/.kube/config` (the `kubernetes` backend reads this). `make localmcp-up` adds a `localmcp` cluster + context to it via `kubectl config set-cluster/set-context` so the bridge-networked container can reach the host's K8s API at `host.docker.internal:6443`. See [setup-rancher-desktop.md](setup-rancher-desktop.md#bridge-networking--the-localmcp-context) for the full flow. |
| `DOCKER_SOCK_FILE` | `/var/run/docker.sock` | Host Docker socket bind-mounted at `/var/run/docker.sock` (mcp-server-docker uses this). See [setup-rancher-desktop.md](setup-rancher-desktop.md) for when to override. |
| `PINCHER_REPO` | kmechlin fork URL | Git URL for the pincherMCP source baked into the image. |
| `PINCHER_REF` | `feat/reverse-proxy-basepath` | Branch/tag in `PINCHER_REPO` to build. Override both to switch back to upstream once the PR merges. |
| `PLATFORM` | `linux/arm64` | Target platform for the buildx build (Apple Silicon default). Set `linux/amd64` on Intel. |

Run `make test-vars` to print every effective value.

## Lifecycle targets

### `make localmcp-up`

Starts the container detached. Behavior:

1. Runs `localmcp-kubeconfig` first to add a `localmcp` cluster + context to your kubeconfig (see below).
2. Validates `LOCALMCP_VOLUMES_FILE` exists.
3. If `LOCALMCP_IMAGE_TAG` isn't in your local Docker daemon, builds it (calls `localmcp-image-build`).
4. Tears down any existing container with the same name (`docker rm -f` — silent).
5. Reads the volumes file, expands env-var references, builds a `-v <spec>` list.
6. `docker run -d` with `--add-host host.docker.internal:host-gateway`, `-p $(LOCALMCP_BIND_ADDR):$(LOCALMCP_PORT):$(LOCALMCP_PORT)`, and the assembled `-v` flags. Bridge networking — only the published port is reachable from the host.
7. Polls `http://localhost:$(LOCALMCP_PORT)/api/status` for up to 15 seconds.

### `make localmcp-down`

`docker rm -f $(LOCALMCP_CONTAINER)`. The image and named volumes survive.

### `make localmcp-restart`

Down + up. Image, builder, and named volumes survive. Use this after editing `default-localmcp.json` or the volumes file (the new file gets re-read on `up`).

### `make localmcp-logs`

`docker logs -f --tail=200 $(LOCALMCP_CONTAINER)`. Streams Starlette + uvicorn + per-backend `[name]`-tagged log lines.

### `make localmcp-shell`

`docker exec -ti $(LOCALMCP_CONTAINER) bash`. Useful for debugging spawned MCP backends — verifying npx packages, checking kubeconfig, etc.

### `make localmcp-status`

Three probes:

1. `docker ps` row for the container (or "not running" message + non-zero exit).
2. `curl http://localhost:$(LOCALMCP_PORT)/api/status | python -m json.tool`.
3. `curl http://localhost:$(LOCALMCP_PORT)/mcp` (HEAD) — expects 503 (no backends running) / 406 (backends running, GET not allowed) / 200 (valid POST). Quick smoke test that the dispatcher is alive.

### `make localmcp-load LOCALMCP_CONFIG=...`

POSTs `LOCALMCP_CONFIG` to `/api/start`. Validates it's a JSON file before sending; checks the server is reachable; pretty-prints the `/api/status` afterward. The default config in `configs/default-localmcp.json` boots the four [default backends](default-mcps.md).

### `make localmcp-kubeconfig`

Adds a `localmcp` cluster + context to `$(KUBERNETES_CONFIG_FILE)` so the bridge-networked container can reach Rancher / Docker Desktop's K8s API:

- Cluster: `server=https://host.docker.internal:6443`, `insecure-skip-tls-verify=true`.
- Context: paired with whichever user your kubeconfig's `current-context` is using.

Idempotent — re-running is safe. Skipped (with a warning) when `kubectl` isn't installed or the kubeconfig file doesn't exist, so users without K8s tooling aren't blocked. Runs automatically as a prerequisite of `make localmcp-up`. See [setup-rancher-desktop.md](setup-rancher-desktop.md#bridge-networking--the-localmcp-context) for the full rationale.

### `make localmcp-clean-kubeconfig`

The inverse — removes the `localmcp` context and cluster from `$(KUBERNETES_CONFIG_FILE)`. Safe to run even when the entries don't exist (kubectl errors are swallowed).

### `make localmcp-stop-all`

POSTs `/api/stop`. Tears down user backends; the always-on built-in MCP at `/localmcp/mcp` survives.

### `make localmcp-warm-index`

Pre-populates `pincher`'s codebase intelligence DB (symbols + edges + FTS5) by indexing the **current git repo** at `$(LOCALMCP_PROJECT_PATH)` (default `/user_data_ro/<rel-from-USER_DATA_ROOT-to-git-toplevel>`). Auto-chained from `make localmcp-load` when `LOCALMCP_WARM_ON_LOAD=1` (default), so the typical flow is just:

```bash
make localmcp-up && make localmcp-load
```

Calls `pincher__index` via the aggregator's `/mcp` endpoint. xxh3 content-hashing makes re-runs cheap (skipped files cost nothing), so it's fine to re-run after big edits. Override the path explicitly when warming a sibling repo:

```bash
make localmcp-warm-index LOCALMCP_PROJECT_PATH=/user_data_ro/code/myrepo
```

### `make localmcp-warm-index-full`

Same idea but indexes the **entire `/user_data_ro` mount** (the whole `$(USER_DATA_ROOT)` host tree). Slow on first run; useful when you want cross-repo search/symbol coverage. Subsequent runs are cheap (xxh3-skipped):

```bash
make localmcp-warm-index-full
```

### `make localmcp-list-tools`

POSTs `tools/list` to `/mcp` and prints the result grouped by backend prefix. Equivalent to opening `/catalog` in your browser. Handy for confirming what's available after a `localmcp-load`.

## Build targets

### `make localmcp-image-build`

Runs `docker buildx build --load --target localmcp` against [`docker-tools/Dockerfile`](../docker-tools/Dockerfile). Loads the result into your local Docker daemon as `$(LOCALMCP_IMAGE_TAG)`. Cached layers are reused; only the `COPY src` layer downstream re-runs when source changes.

Depends on `setup-buildx` which depends on `build-buildx-image` which depends on `get-corp-root-authority-cert`. The whole chain runs on first invocation; subsequent runs short-circuit if the cert file is recent and the buildx builder exists.

### `make localmcp-image-rebuild`

Same as `localmcp-image-build` but with `--no-cache`. Use after `pyproject.toml` dep changes or to pick up upstream base-image security updates. ~3-5 minutes on a fresh cache.

### `make get-corp-root-authority-cert`

Exports the corporate root CA cert from the macOS keychain to `docker-tools/cert.pem`. CN parameterized via `CORP_ROOT_AUTHORITY_CERT_NAME` (default `Nike Root Authority NG`). The cert is then `COPY`-ed into the `extra-os` build stage and trusted via `update-ca-certificates`, so `apt`, `pip`, `npm`, and `uvx` can all clear the corporate TLS-intercepting proxy at build time. Skipped if you're not behind such a proxy — the upstream `Dockerfile` at the repo root works fine without certs.

### `make build-buildx-image` / `make setup-buildx`

Build / register a custom buildx builder image (`localmcp-buildx`) preloaded with the corp cert so the buildkit daemon can pull base images through the proxy. Used as the buildkit builder for the localmcp image build. See [docker-tools/README.md](../docker-tools/README.md).

### `make clean`

Tear down everything Make creates:

- The container (`docker rm -f`)
- The localmcp image (`docker image rm`)
- The buildx builder (`docker buildx rm`)
- The buildx-image (`docker image rm`)
- `buildkitd.toml` (registry-config helper file)

Named volumes (the npx/uv/pincher caches) **survive** by design — re-running `make localmcp-up` after `make clean` rebuilds the image but reuses the persistent caches, so first call into pincher doesn't pay the full reindex cost again.

## Volume mounts (`LOCALMCP_VOLUMES_FILE`)

`make localmcp-up` reads its `docker run -v` list from `$(LOCALMCP_VOLUMES_FILE)` (default [`configs/default-volumes.conf`](../configs/default-volumes.conf)). One mount per line. Comments allowed (`#…` to end of line). Blank lines ignored. Shell variables are expanded.

### Default contents

```
$KUBERNETES_CONFIG_FILE:/root/.kube/config:ro    # kubeconfig (read-only)
$USER_DATA_ROOT:/user_data_rw                     # source tree (read/write — filesystem MCP)
$USER_DATA_ROOT:/user_data_ro:ro                  # source tree (read-only — pincher index)
$DOCKER_SOCK_FILE:/var/run/docker.sock            # docker daemon socket
localmcp-npm:/root/.npm                           # npx cache (named volume)
localmcp-cache:/root/.cache                       # uv/pip cache (named volume)
localmcp-pincher:/tmp/pincher                     # pincher SQLite index DB (named volume)
localmcp-savings:/root/.localmcp                  # savings SQLite store (named volume)
```

### Variables expanded at runtime

These four shell variables are substituted by the Makefile into the volumes file before `docker run`:

| Var | Default | Source |
|---|---|---|
| `$HOME` | (your home dir) | shell env |
| `$USER_DATA_ROOT` | `$HOME` | Make variable, exported |
| `$KUBERNETES_CONFIG_FILE` | `$HOME/.kube/config` | Make variable, exported |
| `$DOCKER_SOCK_FILE` | `/var/run/docker.sock` | Make variable, exported |

Override any of them on the command line:

```bash
make localmcp-up \
  USER_DATA_ROOT=/Users/me/code \
  KUBERNETES_CONFIG_FILE=/Users/me/.kube/staging-config
```

Tilde paths (e.g. `~/.kube/config`) are also handled — the Makefile expands a leading `~/` to `$HOME/` after env-var substitution.

### Add a custom mount

Easiest: edit `configs/default-volumes.conf` directly.

Cleaner if you want to keep the default file untouched: copy it, edit, and point the Makefile at your version:

```bash
cp configs/default-volumes.conf ~/.config/localmcp-volumes.conf
# edit ~/.config/localmcp-volumes.conf — add lines like:
#   /Users/me/secrets/.aws:/root/.aws:ro
#   $HOME/.gitconfig:/root/.gitconfig:ro
#   localmcp-models:/opt/models
make localmcp-up LOCALMCP_VOLUMES_FILE=~/.config/localmcp-volumes.conf
```

The format mirrors `docker run -v` exactly: `<host-path-or-named-volume>:<container-path>[:options]`. Named volumes (entries with no leading `/`) are managed by Docker and survive `docker rm`, so caches/indexes don't have to be rebuilt on every restart.

### Security note on `$DOCKER_SOCK_FILE`

Mounting the Docker socket is effectively root-on-host — anyone who can call the `docker` MCP tools can spawn / kill / image-pull on your machine. Only mount on dev machines. To opt out:

1. Comment the `$DOCKER_SOCK_FILE:/var/run/docker.sock` line in your volumes conf.
2. Remove the `docker` backend from `configs/default-localmcp.json` (or your own config).

## Enterprise extras

The Makefile also wires up a few helpers specific to corporate / Rancher Desktop environments:

| Variable | Default | Purpose |
|---|---|---|
| `CORP_ROOT_AUTHORITY_CERT_NAME` | `Nike Root Authority NG` | CN of the corporate root CA cert in macOS keychain. Override if your CA is named differently. |
| `DOCKER_REGISTRY` | `host.docker.internal:5001` | Insecure-registry the buildkit daemon will trust (used when running a local registry alongside buildx). |
| `BUILDX_BUILDER_NAME` | `localmcp-builder` | Name of the buildx builder instance. |
| `BUILDX_IMAGE_NAME` | `localmcp-buildx` | Tag of the cert-aware buildkit builder image. |

If you're not on a corporate network, none of these matter — the standard buildx machinery the Makefile invokes works the same way.

See [docker-tools/README.md](../docker-tools/README.md) for the cert-aware build flow rationale.

## See also

- [setup-rancher-desktop.md](setup-rancher-desktop.md) — choosing a Docker daemon and how `DOCKER_SOCK_FILE` interacts.
- [configuration.md](configuration.md) — the `mcpServers` shape `make localmcp-load` expects.
- [http-api.md](http-api.md) — the API endpoints the Makefile targets call internally.
