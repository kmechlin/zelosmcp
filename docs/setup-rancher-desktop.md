# Rancher Desktop setup

zelosMCP's container needs a Docker daemon to run on, and its `docker` MCP backend needs the Docker socket bind-mounted in. On macOS, [Rancher Desktop](https://rancherdesktop.io/) is the recommended way to provide both. This page covers install, the engine + admin-mode toggles, where the socket lives, and how it interacts with zelosMCP's `DOCKER_SOCK_FILE` Make variable.

## Why Rancher Desktop

You need three things on macOS for zelosMCP to work end-to-end:

1. A Docker daemon to run the zelosMCP container itself.
2. A Kubernetes cluster (k3s) for the `kubernetes` MCP backend to point `kubernetes-mcp-server` at.
3. A Unix socket that the `mcp-server-docker` MCP backend can bind-mount in to query containers.

Rancher Desktop bundles all three: a Lima-VM-hosted dockerd or containerd, a single-node k3s, and a `~/.rd/docker.sock` you can mount.

(You can mix and match — Docker Desktop covers #1 and #3, and you'd need a separate kubeconfig for #2 — but Rancher Desktop is the simplest single install.)

## Install

```bash
brew install --cask rancher
```

Or download the installer from [rancherdesktop.io](https://rancherdesktop.io/).

Launch Rancher Desktop and let it finish provisioning the VM (first boot takes 1-2 minutes).

## Two settings that matter

### 1. Container engine: dockerd vs containerd

Rancher Desktop → **Preferences** → **Container Engine**.

- **dockerd (moby)** — *recommended*. Same daemon and socket protocol Docker Desktop ships, fully Docker-CLI-compatible. Required for `mcp-server-docker` to work.
- **containerd** — uses `nerdctl` instead of `docker`. The `~/.rd/docker.sock` file still gets created but it's a `nerdctl` shim, not a real Docker daemon. The `mcp-server-docker` MCP backend won't work against it.

Pick **dockerd** unless you have a specific reason not to.

### 2. Administrative access

Rancher Desktop → **Preferences** → **Application** → **Administrative Access**.

This setting controls whether Rancher Desktop binds the daemon to `/var/run/docker.sock` (the canonical macOS path) or only to `~/.rd/docker.sock`.

| Admin access | Socket paths exposed | zelosMCP override needed |
|---|---|---|
| **Enabled** | `/var/run/docker.sock` (symlink → `~/.rd/docker.sock`) and `~/.rd/docker.sock` | None — `DOCKER_SOCK_FILE=/var/run/docker.sock` (the default) just works |
| **Disabled** *(default since Rancher Desktop v1.9)* | `~/.rd/docker.sock` only | Override `DOCKER_SOCK_FILE=$HOME/.rd/docker.sock` AND switch docker context (see below) |

Recommendation: **leave admin access disabled** unless you have a specific need. The override flow is one extra step and avoids the security tradeoff of letting Rancher Desktop touch system paths.

## Verify your daemon is reachable

```bash
docker context ls          # see all configured contexts
docker context show        # current active context
docker info                # talks to the active daemon
```

You should see something like:

```
NAME              DESCRIPTION                               DOCKER ENDPOINT
default *         Current DOCKER_HOST based configuration   unix:///var/run/docker.sock
desktop-linux     Docker Desktop                            unix:///Users/<you>/.docker/run/docker.sock
rancher-desktop   Rancher Desktop moby context              unix:///Users/<you>/.rd/docker.sock
```

If the active context is `default` and `/var/run/docker.sock` is a symlink to `~/.rd/docker.sock` (admin-access mode), you're set up for the canonical path.

If you have **both** Docker Desktop and Rancher Desktop installed and admin access disabled on Rancher Desktop, the `default` context's `/var/run/docker.sock` likely points at Docker Desktop. Switch contexts to make Rancher Desktop the daemon zelosMCP uses:

```bash
docker context use rancher-desktop
```

## How zelosMCP picks up your daemon

The zelosMCP container is started by `make up`. It uses two daemons in different ways:

1. **`docker run` itself** uses whichever daemon your **active `docker context`** points at. That's the daemon the zelosMCP container runs on.
2. **The `docker` MCP backend inside the container** talks to the daemon via the Unix socket bind-mounted into the container. The host path of that socket is configurable via the `DOCKER_SOCK_FILE` Make variable; container path is always `/var/run/docker.sock`.

For a coherent setup the two should point at the same daemon. The default values do this for the common Docker-Desktop case:

```make
DOCKER_SOCK_FILE ?= /var/run/docker.sock
```

For Rancher Desktop without admin access:

```bash
docker context use rancher-desktop
make up DOCKER_SOCK_FILE=$HOME/.rd/docker.sock
```

The mismatch case (running the container on Docker Desktop while bind-mounting Rancher Desktop's socket) fails with:

```
docker: Error response from daemon: error while creating mount source path
'/Users/<you>/.rd/docker.sock': mkdir /Users/<you>/.rd/docker.sock: operation not supported
```

…because Docker Desktop's VM can't see Rancher Desktop's socket file. If you hit that, either switch contexts (option above) or pick the daemon you actually want and align both sides.

See [makefile.md](makefile.md) for the full volume-mount config including `DOCKER_SOCK_FILE` and other host-path overrides.

## Kubeconfig

Rancher Desktop writes its kubeconfig to `~/.kube/config` (merging into any existing one), with `server: https://127.0.0.1:6443`. The `kubernetes` MCP backend in `default-zelosmcp.json` reads it via the `KUBERNETES_CONFIG_FILE` mount in [configs/default-volumes.conf](../configs/default-volumes.conf):

```
$KUBERNETES_CONFIG_FILE:/root/.kube/config:ro
```

Override the host path if your kubeconfig lives elsewhere:

```bash
make up KUBERNETES_CONFIG_FILE=/path/to/your/kubeconfig
```

### Bridge networking + the `zelosmcp` context

zelosMCP runs in bridge networking (only `:8000` is published to the host — see [reverse-proxy.md](reverse-proxy.md)). That means `127.0.0.1` inside the container is the container itself, not your Mac. A kubeconfig pointing at `https://127.0.0.1:6443` is unreachable from inside the container as written.

Rather than rewriting your kubeconfig destructively, `make up` runs **`make kubeconfig`** as a prerequisite. It uses host-side `kubectl` to add a single new cluster + context to your existing kubeconfig:

```bash
kubectl config set-cluster zelosmcp \
  --server=https://host.docker.internal:6443 \
  --insecure-skip-tls-verify=true
kubectl config set-context zelosmcp \
  --cluster=zelosmcp \
  --user=<the-user-from-your-current-context>
```

These commands are idempotent — running `make up` repeatedly is safe and does nothing on the second run. Your existing contexts and `current-context` are untouched.

`kubernetes-mcp-server` is multi-cluster aware by default, so every tool call accepts an optional `context` argument. The agent passes `context: "zelosmcp"` to talk to your local cluster:

```jsonc
{ "name": "kubernetes__pods_list", "arguments": { "context": "zelosmcp" } }
```

For remote clusters (EKS, AKS, GKE, …), pass that cluster's context name instead — they keep working through your kubeconfig as before.

#### Verify from the host

Before pointing the agent at it:

```bash
kubectl --context zelosmcp get nodes
```

If that succeeds, the bridge-networked container will get the same result.

#### TLS verification

The auto-added `zelosmcp` cluster sets `insecure-skip-tls-verify: true` because Rancher Desktop / Docker Desktop's K8s API certificates have `127.0.0.1` and `localhost` in their SAN list, but **not** `host.docker.internal`. Strict TLS against the new server name would fail.

For local development this is fine — the cluster is loopback-only and reachable only via the host's docker bridge. If you want strict verification, the manual override is:

```bash
# 1. Extract the CA from your existing cluster (whichever context targets the local k8s).
kubectl config view --raw \
  -o jsonpath="{.clusters[?(@.name==\"rancher-desktop\")].cluster.certificate-authority-data}" \
  | base64 -d > /tmp/rancher-ca.crt

# 2. Re-create the zelosmcp cluster with proper CA + tls-server-name override.
kubectl config set-cluster zelosmcp \
  --server=https://host.docker.internal:6443 \
  --certificate-authority=/tmp/rancher-ca.crt \
  --embed-certs=true \
  --tls-server-name=127.0.0.1
```

The `tls-server-name=127.0.0.1` makes the TLS handshake validate against `127.0.0.1` (which IS in the SANs) while the connection still goes to `host.docker.internal:6443`.

#### Cleanup

If you want to remove the auto-added entries (e.g. before uninstalling zelosMCP):

```bash
make clean-kubeconfig
```

## Common gotchas

- **`mcp-server-docker` returns nothing or 503 errors.** The Docker MCP isn't running, or the socket bind-mount didn't connect. Verify with `make status` (showing the docker backend as `running`) and that the mount in the `docker run` command points at a valid socket file on your host. See [makefile.md](makefile.md) for `make status` output details.
- **`kubectl` works on the host but `kubernetes__pods_list` fails inside the container.** Probably a kubeconfig auth that uses a credential helper or local certs the container can't reach. Check `make shell` then `cat /root/.kube/config` from inside.
- **`docker context use rancher-desktop` sticks across reboots but `make up` still uses Docker Desktop.** Make sure you're not exporting `DOCKER_HOST` in your shell rc (it overrides the active context). Also: each `make` invocation reads the active context fresh, so once `docker context use` succeeds, the next `make up` does the right thing.
- **Random `Operation not supported` on stdout from heredoc-using shell wrappers.** Cosmetic — your shell's `$TMPDIR` is missing/read-only. Doesn't affect the actual commands. Set `TMPDIR=/tmp` if it bothers you.

## See also

- [makefile.md](makefile.md) — `ZELOSMCP_VOLUMES_FILE` format, all volume-mount Make variables
- [default-mcps.md](default-mcps.md) — what each backend does and which mounts it needs
