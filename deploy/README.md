# Deploy artifacts

Sample manifests for running zelosMCP outside the local-dev `make up`
flow. Two orchestrators covered:

- [`kubernetes/zelosmcp.yaml`](kubernetes/zelosmcp.yaml) — Deployment +
  Service + ConfigMap (mcpServers catalog) + Secret (auth-providers +
  encryption key) + PersistentVolumeClaim (per-user OAuth token store).
- [`swarm/docker-compose.yml`](swarm/docker-compose.yml) — Docker
  Swarm stack with the analogous configs/secrets primitives.

Both follow the same architecture: the **mcpServers catalog** (URLs,
command lines, low-sensitivity) ships as a ConfigMap-equivalent, and
the **auth-providers config** (env-resolved client_ids, the AES key
for the encrypted token store) ships as a Secret-equivalent. zelosMCP
auto-loads both at container startup; you can also POST live edits via
`/api/auth/providers/config` and `/api/start`.

## Which to use

| | Kubernetes | Docker Swarm | Local `make up` |
|---|---|---|---|
| Single-host dev | overkill | overkill | use this |
| Single-host shared (multi-user) | works | simpler | possible but no isolation |
| Multi-host production | use this | works for small fleets | no |
| Per-user encryption-key isolation | yes (per-Pod Secret mount) | yes (per-task secret mount) | one user, one host |

For Nike-internal deployments behind the standard ingress + Okta SSO,
Kubernetes is the documented path.

## Prerequisites

Before applying either manifest:

1. **Pre-build the image** to a registry your cluster can pull from. The
   default `ZELOSMCP_IMAGE_TAG=zelosmcp:dev` is the local-only image
   `make build` produces; replace with a tagged remote image
   (`ghcr.io/yourorg/zelosmcp:vX.Y.Z`).
2. **Generate the AES key for the auth store** ONCE — never auto-
   generate per-Pod or restarts will lose every user's tokens:

   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   # copy the output into your Secret manifest as auth.key (chmod 600)
   ```

3. **Provider client IDs**: register the GitHub OAuth App + Okta App
   per [docs/oauth-passthrough.md](../docs/oauth-passthrough.md), then
   put the resulting `client_id` values into the Secret. These are
   public identifiers; the secrecy isn't about the value, it's about
   keeping the deployment artifact uniform with truly-secret material
   like the AES key.

## Deploying to Kubernetes

```bash
# 1) Create the namespace
kubectl create namespace zelosmcp

# 2) Edit deploy/kubernetes/zelosmcp.yaml:
#    - replace IMAGE_TAG_HERE with your registry path
#    - paste the AES key into the Secret
#    - paste your provider client_ids into the Secret

# 3) Apply
kubectl apply -f deploy/kubernetes/zelosmcp.yaml -n zelosmcp

# 4) Verify
kubectl get pods,svc,configmap,secret,pvc -n zelosmcp
kubectl logs -l app=zelosmcp -n zelosmcp
```

The Service exposes port 8000 internally; add an Ingress / Gateway
resource per your cluster's conventions to expose it externally.

## Deploying to Docker Swarm

```bash
# 1) Init Swarm (if not already)
docker swarm init

# 2) Create the secrets
echo 'YOUR_BASE64_FERNET_KEY' | docker secret create zelosmcp_auth_key -
docker secret create zelosmcp_auth_providers \
    deploy/swarm/auth-providers.json   # local file you fill in

# 3) Deploy the stack
docker stack deploy -c deploy/swarm/docker-compose.yml zelosmcp
```

Each task that runs zelosMCP gets the secrets mounted into
`/run/secrets/`. The container's lifespan reads them on startup and
populates the auth registry before serving any traffic.

## Common gotchas

- **Auth key rotation invalidates every stored token.** Plan a re-auth
  flow (force users back through the GUI Connections page) before you
  rotate the key. The store handles undecryptable rows gracefully
  (returns `None`, which surfaces as "not authenticated" to the
  aggregator's gating logic), but the user experience is "all your
  connections silently went away."
- **Per-Pod auth.key MUST be identical** across all replicas. Mount the
  same Secret on every Pod. If you set `replicas: > 1` with different
  key material per Pod, users will randomly get "you're not connected"
  errors depending on which Pod handles their next request.
- **Persistent token store needs a real PV** in production. The
  PVC in the manifest assumes a default StorageClass; adjust for your
  cluster.
- **Cursor-side caching** of `tools/list` means the first user to
  authenticate on a fresh Pod may need to restart Cursor before the
  newly-unlocked wrapper tools appear in their session. See
  [docs/oauth-passthrough.md](../docs/oauth-passthrough.md) for the
  cache-invalidation gotcha.

## See also

- [docs/oauth-passthrough.md](../docs/oauth-passthrough.md) — full
  reference for the broker model + the providers config schema.
- [docs/configuration.md](../docs/configuration.md) — full mcpServers
  schema reference.
- [configs/example-auth-providers.json](../configs/example-auth-providers.json) —
  full provider matrix example to crib from when filling in the
  Secret manifest.
