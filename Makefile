PROJECT_NAME ?= $(shell basename $$(git rev-parse --show-toplevel))
PROJECT_BASE_IMAGE ?= python:3.12-slim-bookworm
PYTHON_VERSION ?= 3.12
SHELL := /bin/bash
DOCKER_TOOLS_PATH ?= docker-tools
DOCKERFILE ?= ${DOCKER_TOOLS_PATH}/Dockerfile
GIT_BRANCH_NAME := $(shell git rev-parse --abbrev-ref HEAD | awk '{print A[split($$0,A,"/")]}')
BUILDX_BUILDER_NAME ?= localmcp-builder
BUILDX_IMAGE_NAME ?= localmcp-buildx
PLATFORM ?= linux/arm64
SSH_KEY_FILE ?= $(HOME)/.ssh/id_rsa
KUBERNETES_CONFIG_FILE ?= $(HOME)/.kube/config
# Host docker socket bind-mounted into the container so mcp-server-docker can
# drive whichever daemon `docker run` is going through. /var/run/docker.sock is
# the path natively exposed inside the Docker-Desktop and Rancher-Desktop VMs
# on macOS, so it works with both as long as `docker run` reaches the same
# daemon (i.e. the active `docker context` matches the daemon you want
# mcp-server-docker to talk to). For Rancher Desktop in its no-admin mode,
# first run `docker context use rancher-desktop` and then override the path:
#   make localmcp-up DOCKER_SOCK_FILE=$(HOME)/.rd/docker.sock
DOCKER_SOCK_FILE ?= /var/run/docker.sock

# when using local registry, host.docker.internal is how you reach
# the host from inside the buildx container
DOCKER_REGISTRY ?= host.docker.internal:5001

# Common Name (CN) of the corporate root CA certificate to export from the
# macOS keychain. Override if the corporate cert ever changes (e.g. via
# `.env` or a one-off `make ... CORP_ROOT_AUTHORITY_CERT_NAME='New CA Name'`).
CORP_ROOT_AUTHORITY_CERT_NAME ?= Nike Root Authority NG

# LocalMCP runtime variables. The Cursor-blessed Streamable HTTP proxy listens
# on $(LOCALMCP_PORT) and exposes its aggregator at /mcp; consumer projects
# (AAL, etc.) load their own backend configs into the running container via
# `make localmcp-load LOCALMCP_CONFIG=...`.
#
# USER_DATA_ROOT is the host directory exposed to MCP backends. It is
# bind-mounted twice (configs/default-volumes.conf): once at
# /user_data_rw (read-write, filesystem MCP root) and once at
# /user_data_ro (kernel-enforced read-only, pincher index target).
# Default $HOME so cross-repo browsing and full-tree warm-indexing work
# without configuration. Override per-invocation when you want a tighter
# scope:
#   make localmcp-up USER_DATA_ROOT=$(HOME)/code
USER_DATA_ROOT      ?= $(HOME)/workspace
LOCALMCP_PORT       ?= 8000
# Host bind address for the published $(LOCALMCP_PORT). 127.0.0.1 means
# only the Mac itself can reach :8000 (tightest, matches the network-
# isolation goal of [docs/reverse-proxy.md](docs/reverse-proxy.md));
# 0.0.0.0 exposes :8000 on every interface so peer devices on the LAN
# can hit it. Override per-invocation:
#   make localmcp-up LOCALMCP_BIND_ADDR=0.0.0.0
LOCALMCP_BIND_ADDR  ?= 127.0.0.1
LOCALMCP_IMAGE_TAG  ?= localmcp:dev
LOCALMCP_CONTAINER  ?= rancher-localmcp
# Default config file pushed into localmcp via `make localmcp-load`.
# Override with: make localmcp-load LOCALMCP_CONFIG=path/to/other.json
LOCALMCP_CONFIG     ?= $(shell pwd)/configs/default-localmcp.json
# Persistent volume mount list applied at `make localmcp-up`. One docker `-v`
# spec per line, with `$HOME` / `$USER_DATA_ROOT` / `$KUBERNETES_CONFIG_FILE`
# expanded. See the file header for the full format.
# Override with: make localmcp-up LOCALMCP_VOLUMES_FILE=path/to/your-volumes.conf
LOCALMCP_VOLUMES_FILE ?= $(shell pwd)/configs/default-volumes.conf

# Pincher warm-index targets read paths under /user_data_ro (the
# kernel-enforced read-only mount of $(USER_DATA_ROOT)). The per-repo
# target derives the relative path from the current git toplevel to
# $(USER_DATA_ROOT) so `/user_data_ro/<rel>` lines up with the repo's
# in-container view; falls back to the bare $(PROJECT_NAME) when git
# isn't available. Override either piece for repos that live outside
# $(USER_DATA_ROOT):
#   make localmcp-warm-index LOCALMCP_PROJECT_PATH=/user_data_ro/code/myrepo
LOCALMCP_PROJECT_NAME ?= $(PROJECT_NAME)
LOCALMCP_PROJECT_REL  ?= $(shell python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$$(git rev-parse --show-toplevel 2>/dev/null)" "$(USER_DATA_ROOT)" 2>/dev/null || echo "$(LOCALMCP_PROJECT_NAME)")
LOCALMCP_PROJECT_PATH ?= /user_data_ro/$(LOCALMCP_PROJECT_REL)

# When set to 1 (default), `localmcp-load` chains into `localmcp-warm-index`
# after the backends start so pincher's index for the current repo is
# ready by the first agent call. Hooked on `load` (not `up`) because
# pincher is only brought online by /api/start, which `load` triggers.
# Idempotent (xxh3-skipped on re-run). Set to 0 for fast loads (CI):
#   make localmcp-load LOCALMCP_WARM_ON_LOAD=0
LOCALMCP_WARM_ON_LOAD ?= 1

# pincherMCP source baked into the localmcp image at build time.
# Defaults to kmechlin's fork branch which adds --basepath / --trust-proxy
# (used by configs/default-localmcp.json's reverseProxy entry). Once the
# upstream PR merges, override at the command line to pin a release:
#   make localmcp-image-rebuild \
#     PINCHER_REPO=https://github.com/kwad77/pincherMCP.git \
#     PINCHER_REF=v0.3.0
PINCHER_REPO ?= https://github.com/kmechlin/pincherMCP.git
PINCHER_REF  ?= feat/reverse-proxy-basepath

# .env file is a good place to define ssh and token vars
-include .env

# Define all recipes as PHONY so they always run
# https://www.gnu.org/software/make/manual/html_node/Phony-Targets.html
.PHONY: test-vars \
	get-corp-root-authority-cert \
	build-buildx-image \
	setup-buildx \
	localmcp-image-build \
	localmcp-image-rebuild \
	localmcp-kubeconfig \
	localmcp-clean-kubeconfig \
	localmcp-up \
	localmcp-down \
	localmcp-restart \
	localmcp-logs \
	localmcp-shell \
	localmcp-status \
	localmcp-ui \
	localmcp-load \
	localmcp-stop-all \
	localmcp-warm-index \
	localmcp-warm-index-full \
	localmcp-list-tools \
	localmcp-rule-refresh \
	clean \
	nuke

# Print variable values, @: is a no-op and prevents this warning message:
# "make: Nothing to be done for `test-vars'."
test-vars:
	@:
	$(info PROJECT_NAME=$(PROJECT_NAME))
	$(info PROJECT_BASE_IMAGE=$(PROJECT_BASE_IMAGE))
	$(info PYTHON_VERSION=$(PYTHON_VERSION))
	$(info SHELL=$(SHELL))
	$(info DOCKER_TOOLS_PATH=$(DOCKER_TOOLS_PATH))
	$(info DOCKERFILE=$(DOCKERFILE))
	$(info GIT_BRANCH_NAME=$(GIT_BRANCH_NAME))
	$(info BUILDX_BUILDER_NAME=$(BUILDX_BUILDER_NAME))
	$(info BUILDX_IMAGE_NAME=$(BUILDX_IMAGE_NAME))
	$(info PLATFORM=$(PLATFORM))
	$(info DOCKER_REGISTRY=$(DOCKER_REGISTRY))
	$(info SSH_KEY_FILE=$(SSH_KEY_FILE))
	$(info KUBERNETES_CONFIG_FILE=$(KUBERNETES_CONFIG_FILE))
	$(info DOCKER_SOCK_FILE=$(DOCKER_SOCK_FILE))
	$(info CORP_ROOT_AUTHORITY_CERT_NAME=$(CORP_ROOT_AUTHORITY_CERT_NAME))
	$(info USER_DATA_ROOT=$(USER_DATA_ROOT))
	$(info LOCALMCP_PORT=$(LOCALMCP_PORT))
	$(info LOCALMCP_BIND_ADDR=$(LOCALMCP_BIND_ADDR))
	$(info LOCALMCP_IMAGE_TAG=$(LOCALMCP_IMAGE_TAG))
	$(info LOCALMCP_CONTAINER=$(LOCALMCP_CONTAINER))
	$(info LOCALMCP_CONFIG=$(LOCALMCP_CONFIG))
	$(info LOCALMCP_VOLUMES_FILE=$(LOCALMCP_VOLUMES_FILE))
	$(info LOCALMCP_PROJECT_NAME=$(LOCALMCP_PROJECT_NAME))
	$(info LOCALMCP_PROJECT_REL=$(LOCALMCP_PROJECT_REL))
	$(info LOCALMCP_PROJECT_PATH=$(LOCALMCP_PROJECT_PATH))
	$(info LOCALMCP_WARM_ON_LOAD=$(LOCALMCP_WARM_ON_LOAD))

# Export the corporate root authority certificate from the macOS keychain.
# Required for builds behind a TLS-intercepting corporate proxy (Palo Alto).
# The Common Name is parameterized via CORP_ROOT_AUTHORITY_CERT_NAME so it
# can be overridden if the corporate cert ever changes.
get-corp-root-authority-cert:
	security find-certificate -c "$(CORP_ROOT_AUTHORITY_CERT_NAME)" -p > ${DOCKER_TOOLS_PATH}/cert.pem

# build custom buildx image with corporate root authority cert
build-buildx-image: get-corp-root-authority-cert
	docker build \
		--build-arg CERT=${DOCKER_TOOLS_PATH}/cert.pem \
		--build-arg PROJECT_BASE_IMAGE=${PROJECT_BASE_IMAGE} \
		--build-arg PYTHON_VERSION=${PYTHON_VERSION} \
		--target buildx \
		-t ${BUILDX_IMAGE_NAME} \
		-f ${DOCKER_TOOLS_PATH}/buildx.Dockerfile \
		.

# create buildx builder using custom buildx image
setup-buildx: get-corp-root-authority-cert build-buildx-image
	if ! docker buildx inspect ${BUILDX_BUILDER_NAME} ; then \
		echo '[registry."$(DOCKER_REGISTRY)"]' > buildkitd.toml; \
		echo '  http = true' >> buildkitd.toml; \
		echo '  insecure = true' >> buildkitd.toml; \
		docker buildx create \
			--name ${BUILDX_BUILDER_NAME}  \
			--config buildkitd.toml \
			--driver docker-container \
			--driver-opt image=${BUILDX_IMAGE_NAME}:latest \
			--bootstrap --use; \
	fi

# ============================================================================
# LocalMCP image build + lifecycle
#
# The container talks to whatever cluster is in $(KUBERNETES_CONFIG_FILE),
# bind-mounts $(USER_DATA_ROOT) twice (rw at /user_data_rw, ro at
# /user_data_ro) so spawned MCP backends can see your code with the
# right access contract, and publishes the aggregator on
# $(LOCALMCP_BIND_ADDR):$(LOCALMCP_PORT). Bridge networking keeps every
# backend's bind inside the container's network namespace, so only the
# explicitly published port is reachable from the host.
#
# To talk to Rancher Desktop / Docker Desktop's K8s API from a bridge-
# networked container, `make localmcp-up` first runs `localmcp-kubeconfig`
# to add a `localmcp` cluster + context to $(KUBERNETES_CONFIG_FILE) that
# points at host.docker.internal:6443. The agent uses `context: "localmcp"`
# in kubernetes-mcp-server tool calls to route to the local cluster.
#
# Typical usage:
#   make localmcp-image-build  # one-time per dep change: build the image
#   make localmcp-up           # start the container (per machine boot)
#   make localmcp-load         # POST configs/default-localmcp.json (or override)
#   make localmcp-status       # confirm container is up + status of all backends
#   make localmcp-shell        # bash inside the running container
#   make localmcp-logs         # tail container logs
#   make localmcp-restart      # bounce the container
#   make localmcp-down         # stop and remove the container
#   make localmcp-image-rebuild # force fresh build (--no-cache)
#
# `make clean` also calls `localmcp-down`.
# ============================================================================

# Build the localmcp image from the `localmcp` target of $(DOCKERFILE).
# COPYs the localmcp source from this repo's working tree (build context = .).
# Loads the result into the local Docker daemon (`--load`) — no registry
# push, since this is a developer-machine image.
localmcp-image-build: get-corp-root-authority-cert setup-buildx
	docker buildx build --load \
		--builder $(BUILDX_BUILDER_NAME) \
		--progress plain \
		--target localmcp \
		--platform $(PLATFORM) \
		--build-arg PROJECT_BASE_IMAGE=$(PROJECT_BASE_IMAGE) \
		--build-arg CERT=$(DOCKER_TOOLS_PATH)/cert.pem \
		--build-arg PINCHER_REPO=$(PINCHER_REPO) \
		--build-arg PINCHER_REF=$(PINCHER_REF) \
		--tag $(LOCALMCP_IMAGE_TAG) \
		-f $(DOCKERFILE) \
		.

# Force a fresh rebuild (e.g. after a `pip` dependency change in pyproject.toml
# or to pick up a new commit). Equivalent to `--no-cache` build.
localmcp-image-rebuild: get-corp-root-authority-cert setup-buildx
	docker buildx build --load --no-cache \
		--builder $(BUILDX_BUILDER_NAME) \
		--progress plain \
		--target localmcp \
		--platform $(PLATFORM) \
		--build-arg PROJECT_BASE_IMAGE=$(PROJECT_BASE_IMAGE) \
		--build-arg CERT=$(DOCKER_TOOLS_PATH)/cert.pem \
		--build-arg PINCHER_REPO=$(PINCHER_REPO) \
		--build-arg PINCHER_REF=$(PINCHER_REF) \
		--tag $(LOCALMCP_IMAGE_TAG) \
		-f $(DOCKERFILE) \
		.

# Add a `localmcp` cluster + context to $(KUBERNETES_CONFIG_FILE) so the
# bridge-networked container can reach the host's K8s API via
# host.docker.internal. Idempotent — re-running is safe. Skipped (with a
# warning) when kubectl is missing or the kubeconfig file doesn't exist,
# so users without K8s tooling aren't blocked from running localmcp-up.
#
# The new entries are: a `localmcp` cluster pointing at
# https://host.docker.internal:6443 (with --insecure-skip-tls-verify=true,
# since the Rancher/Docker-Desktop API cert's SANs don't include
# host.docker.internal), and a `localmcp` context that pairs the cluster
# with whichever user your kubeconfig's current-context uses. Your
# current-context is left unchanged; agents pick the localmcp context per
# call via `kubernetes-mcp-server`'s built-in multi-cluster `context` arg.
#
# Verify from the host:  kubectl --context localmcp get nodes
# Remove later:          make localmcp-clean-kubeconfig
localmcp-kubeconfig:
	@if ! command -v kubectl >/dev/null 2>&1; then \
		echo "(skip) localmcp-kubeconfig: kubectl not found on PATH"; exit 0; \
	fi; \
	if [ ! -f "$(KUBERNETES_CONFIG_FILE)" ]; then \
		echo "(skip) localmcp-kubeconfig: $(KUBERNETES_CONFIG_FILE) not found"; exit 0; \
	fi; \
	CURRENT_CTX=$$(kubectl config current-context --kubeconfig="$(KUBERNETES_CONFIG_FILE)" 2>/dev/null || true); \
	if [ -z "$$CURRENT_CTX" ]; then \
		echo "(skip) localmcp-kubeconfig: no current-context set in $(KUBERNETES_CONFIG_FILE)"; exit 0; \
	fi; \
	CURRENT_USER=$$(kubectl config view --kubeconfig="$(KUBERNETES_CONFIG_FILE)" \
		-o jsonpath="{.contexts[?(@.name==\"$$CURRENT_CTX\")].context.user}"); \
	if [ -z "$$CURRENT_USER" ]; then \
		echo "(skip) localmcp-kubeconfig: could not resolve user for context '$$CURRENT_CTX'"; exit 0; \
	fi; \
	kubectl config set-cluster localmcp \
		--kubeconfig="$(KUBERNETES_CONFIG_FILE)" \
		--server=https://host.docker.internal:6443 \
		--insecure-skip-tls-verify=true >/dev/null; \
	kubectl config set-context localmcp \
		--kubeconfig="$(KUBERNETES_CONFIG_FILE)" \
		--cluster=localmcp \
		--user="$$CURRENT_USER" >/dev/null; \
	echo "==> kubeconfig: 'localmcp' context added (cluster=localmcp -> https://host.docker.internal:6443, user=$$CURRENT_USER)"

# Remove the entries `localmcp-kubeconfig` added. Safe to run even when no
# entries exist (the kubectl errors are swallowed).
localmcp-clean-kubeconfig:
	@kubectl config delete-context localmcp --kubeconfig="$(KUBERNETES_CONFIG_FILE)" 2>/dev/null || true
	@kubectl config delete-cluster localmcp --kubeconfig="$(KUBERNETES_CONFIG_FILE)" 2>/dev/null || true
	@echo "==> kubeconfig: 'localmcp' context + cluster removed (if present)"

# Run the localmcp container detached. Rebuilds the image first only if it
# doesn't already exist locally; use localmcp-image-rebuild to force.
#
# Volume mounts come from $(LOCALMCP_VOLUMES_FILE) (see
# configs/default-volumes.conf for the format). Defaults wire up:
#   - $(KUBERNETES_CONFIG_FILE)  -> /root/.kube/config       (read-only)
#   - $(USER_DATA_ROOT)          -> /user_data_rw            (filesystem MCP root)
#   - $(USER_DATA_ROOT)          -> /user_data_ro (read-only) (pincher index)
#   - $(DOCKER_SOCK_FILE)        -> /var/run/docker.sock     (mcp-server-docker)
#   - localmcp-npm               -> /root/.npm               (npx cache)
#   - localmcp-cache             -> /root/.cache             (uv/pip cache)
#   - localmcp-pincher           -> /tmp/pincher             (pincher SQLite DB)
#   - localmcp-savings           -> /root/.localmcp          (savings SQLite DB)
# We expose USER_DATA_ROOT through both an rw and a ro mount so writes go
# through the rw mount and pincher indexes via the kernel-enforced ro
# mount. `localmcp-warm-index` (auto-chained from `localmcp-load` when
# LOCALMCP_WARM_ON_LOAD=1) targets /user_data_ro/<rel-to-USER_DATA_ROOT>
# for the current repo only; `localmcp-warm-index-full` indexes the
# whole /user_data_ro mount.
# Named volumes survive `docker rm` so caches/indexes persist across restarts.
# Edit the conf file (or point LOCALMCP_VOLUMES_FILE at your own) to change.
localmcp-up: localmcp-kubeconfig
	@if [ ! -f "$(LOCALMCP_VOLUMES_FILE)" ]; then \
		echo "ERROR: volumes file not found: $(LOCALMCP_VOLUMES_FILE)"; \
		echo "       Override with LOCALMCP_VOLUMES_FILE=path/to/your.conf"; \
		exit 2; \
	fi
	@if ! docker image inspect $(LOCALMCP_IMAGE_TAG) >/dev/null 2>&1; then \
		echo "==> image $(LOCALMCP_IMAGE_TAG) not found locally; building it"; \
		$(MAKE) localmcp-image-build; \
	fi
	-docker rm -f $(LOCALMCP_CONTAINER) 2>/dev/null
	@echo "==> mounting volumes from $(LOCALMCP_VOLUMES_FILE)"
	@export HOME='$(HOME)' \
	        USER_DATA_ROOT='$(USER_DATA_ROOT)' \
	        KUBERNETES_CONFIG_FILE='$(KUBERNETES_CONFIG_FILE)' \
	        DOCKER_SOCK_FILE='$(DOCKER_SOCK_FILE)' ; \
	VOLUME_ARGS="" ; \
	while IFS= read -r line; do \
		eval "spec=\"$$line\"" ; \
		case "$$spec" in "~/"*) spec="$$HOME/$${spec#\~/}" ;; esac ; \
		echo "    -v $$spec" ; \
		VOLUME_ARGS="$$VOLUME_ARGS -v $$spec" ; \
	done < <(sed -e 's/[[:space:]]*\#.*$$//' -e '/^[[:space:]]*$$/d' \
		"$(LOCALMCP_VOLUMES_FILE)") ; \
	docker run -d \
		--name $(LOCALMCP_CONTAINER) \
		--restart unless-stopped \
		--add-host host.docker.internal:host-gateway \
		-p $(LOCALMCP_BIND_ADDR):$(LOCALMCP_PORT):$(LOCALMCP_PORT) \
		$$VOLUME_ARGS \
		$(LOCALMCP_IMAGE_TAG)
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do \
		if curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status 2>/dev/null; then \
			echo ""; \
			echo "localmcp is up on http://localhost:$(LOCALMCP_PORT)"; \
			echo "Cursor connects via:     http://localhost:$(LOCALMCP_PORT)/mcp"; \
			echo "Configure backends:      make localmcp-load   (POSTs $(LOCALMCP_CONFIG))"; \
			echo "Web UI:                  make localmcp-ui"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "localmcp did not become ready within 15s; check 'make localmcp-logs'"; \
	exit 1

localmcp-down:
	-docker rm -f $(LOCALMCP_CONTAINER)

localmcp-restart:
	$(MAKE) localmcp-down
	$(MAKE) localmcp-up

localmcp-logs:
	@docker logs -f --tail=200 $(LOCALMCP_CONTAINER)

# Drop into a shell inside the running container (useful for debugging
# spawned MCP backends — checking npx/uvx output, paths, kubeconfig, etc.).
localmcp-shell:
	@if ! docker ps --filter name=^/$(LOCALMCP_CONTAINER)$$ --format '{{.Names}}' | grep -q .; then \
		echo "Container $(LOCALMCP_CONTAINER) is not running."; \
		echo "Start it first with: make localmcp-up"; \
		exit 1; \
	fi
	docker exec -ti $(LOCALMCP_CONTAINER) bash

localmcp-status:
	@if docker ps --filter name=^/$(LOCALMCP_CONTAINER)$$ --format '{{.Names}}' | grep -q .; then \
		docker ps --filter name=^/$(LOCALMCP_CONTAINER)$$ \
			--format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'; \
	else \
		echo "Container $(LOCALMCP_CONTAINER) is NOT running."; \
		exit 1; \
	fi
	@printf "ui:          " && curl -sS -o /dev/null -w "HTTP %{http_code}\n" \
		http://localhost:$(LOCALMCP_PORT)/ || echo "(unreachable)"
	@printf "api/status:  " && curl -sS http://localhost:$(LOCALMCP_PORT)/api/status \
		| python3 -m json.tool 2>/dev/null || echo "(unreachable)"
	@printf "/mcp:        " && curl -sS -o /dev/null -w "HTTP %{http_code} (503 no backend / 406 backend running on GET / 200 valid POST)\n" \
		http://localhost:$(LOCALMCP_PORT)/mcp || echo "(unreachable)"

# Open the localmcp web UI in the default browser.
localmcp-ui:
	@open http://localhost:$(LOCALMCP_PORT) 2>/dev/null \
		|| echo "Open this URL manually:  http://localhost:$(LOCALMCP_PORT)"

# Push $(LOCALMCP_CONFIG) into localmcp via its /api/start endpoint.
# Replaces any currently running server set with the contents of the file.
# Usage:
#   make localmcp-load                              # uses configs/default-localmcp.json
#   make localmcp-load LOCALMCP_CONFIG=other.json   # uses a different file
localmcp-load:
	@if [ ! -f "$(LOCALMCP_CONFIG)" ]; then \
		echo "ERROR: config file not found: $(LOCALMCP_CONFIG)"; \
		exit 2; \
	fi
	@echo "==> validating $(LOCALMCP_CONFIG)"
	@python3 -m json.tool "$(LOCALMCP_CONFIG)" > /dev/null
	@if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status; then \
		echo "ERROR: localmcp not reachable on port $(LOCALMCP_PORT)."; \
		echo "       Run 'make localmcp-up' first."; \
		exit 1; \
	fi
	@echo "==> POSTing config to http://localhost:$(LOCALMCP_PORT)/api/start"
	@RESP=$$(curl -sS -X POST -H "Content-Type: application/json" \
		--data-binary @"$(LOCALMCP_CONFIG)" \
		http://localhost:$(LOCALMCP_PORT)/api/start); \
	echo "$$RESP" | python3 -m json.tool 2>/dev/null || echo "$$RESP"
	@echo ""
	@echo "==> resulting status"
	@curl -sS http://localhost:$(LOCALMCP_PORT)/api/status | python3 -m json.tool
	@if [ "$(LOCALMCP_WARM_ON_LOAD)" = "1" ]; then \
		echo ""; \
		$(MAKE) --no-print-directory localmcp-warm-index || \
			echo "(warm-index failed; run 'make localmcp-warm-index' manually once pincher is up)"; \
	fi

# Stop all backend servers in localmcp (without stopping localmcp itself).
localmcp-stop-all:
	@curl -sS -X POST http://localhost:$(LOCALMCP_PORT)/api/stop \
		| python3 -m json.tool 2>/dev/null \
		|| echo "(localmcp not reachable on port $(LOCALMCP_PORT))"

# Drive pincher's `index` MCP tool against $(LOCALMCP_PROJECT_PATH) (the
# current git repo viewed through /user_data_ro) so the codebase
# intelligence DB (symbols, edges, FTS5) is populated for the first
# agent interaction. Pincher uses xxh3 content hashing so re-runs are
# fast (skipped files cost nothing). Idempotent. Override the path
# explicitly when warming a sibling repo:
#   make localmcp-warm-index LOCALMCP_PROJECT_PATH=/user_data_ro/code/myrepo
localmcp-warm-index:
	@if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status 2>/dev/null; then \
		echo "ERROR: localmcp not reachable on port $(LOCALMCP_PORT)."; \
		echo "       Run 'make localmcp-up' first."; \
		exit 1; \
	fi
	@if ! docker exec $(LOCALMCP_CONTAINER) test -d "$(LOCALMCP_PROJECT_PATH)" 2>/dev/null; then \
		echo "ERROR: $(LOCALMCP_PROJECT_PATH) does not exist inside $(LOCALMCP_CONTAINER)."; \
		echo "       Verify your repo is under $(USER_DATA_ROOT) on the host"; \
		echo "       (LOCALMCP_PROJECT_NAME=$(LOCALMCP_PROJECT_NAME),"; \
		echo "        LOCALMCP_PROJECT_REL=$(LOCALMCP_PROJECT_REL))."; \
		exit 1; \
	fi
	@curl -sS -X POST http://localhost:$(LOCALMCP_PORT)/mcp \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"makefile","version":"1"}}}' \
		>/dev/null
	@echo "==> pincher__index path=$(LOCALMCP_PROJECT_PATH) (AST extraction; first run may take a minute)"
	@curl -sS -X POST http://localhost:$(LOCALMCP_PORT)/mcp \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"pincher__index","arguments":{"path":"$(LOCALMCP_PROJECT_PATH)"}}}' \
		| python3 -c 'import json,sys; d=json.load(sys.stdin); r=d.get("result",{}); c=r.get("content",[]); print(c[0]["text"][:500]) if c else print("(no content)")' 2>/dev/null \
		|| echo "(call failed — pincher backend may not be running)"

# Warm pincher's index for EVERYTHING under $(USER_DATA_ROOT) by indexing
# /user_data_ro. Slow on first run (every file under $(USER_DATA_ROOT)
# gets parsed); subsequent runs are cheap (xxh3-skipped). Use when you
# want cross-repo search/symbol coverage; otherwise prefer the per-repo
# `localmcp-warm-index` for tighter scope.
localmcp-warm-index-full:
	@if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status 2>/dev/null; then \
		echo "ERROR: localmcp not reachable on port $(LOCALMCP_PORT)."; \
		echo "       Run 'make localmcp-up' first."; \
		exit 1; \
	fi
	@curl -sS -X POST http://localhost:$(LOCALMCP_PORT)/mcp \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"makefile","version":"1"}}}' \
		>/dev/null
	@echo "==> pincher__index path=/user_data_ro (full $(USER_DATA_ROOT) — may take several minutes)"
	@curl -sS -X POST http://localhost:$(LOCALMCP_PORT)/mcp \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"pincher__index","arguments":{"path":"/user_data_ro"}}}' \
		| python3 -c 'import json,sys; d=json.load(sys.stdin); r=d.get("result",{}); c=r.get("content",[]); print(c[0]["text"][:500]) if c else print("(no content)")' 2>/dev/null \
		|| echo "(call failed — pincher backend may not be running)"

# Print the names of every tool currently exposed by the localmcp aggregator,
# grouped by backend prefix. Handy after `localmcp-load` to confirm what's available.
localmcp-list-tools:
	@if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status 2>/dev/null; then \
		echo "ERROR: localmcp not reachable on port $(LOCALMCP_PORT)."; \
		echo "       Run 'make localmcp-up' first."; \
		exit 1; \
	fi
	@curl -sS -X POST http://localhost:$(LOCALMCP_PORT)/mcp \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"makefile","version":"1"}}}' \
		>/dev/null
	@curl -sS -X POST http://localhost:$(LOCALMCP_PORT)/mcp \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
		| python3 -c 'import json,sys; d=json.load(sys.stdin); names=[t["name"] for t in d["result"]["tools"]]; \
from collections import defaultdict; groups=defaultdict(list); \
[groups[(n.split("__",1)+[""])[0] if "__" in n else "(unprefixed)"].append((n.split("__",1)+[""])[1]) for n in names]; \
[print(f"\n[{k}] ({len(v)} tools)") or [print(f"  {t}") for t in v] for k,v in sorted(groups.items())]'

# Refresh the project-scoped Cursor rule .mdc to match whatever backend set
# is currently loaded. Run after `make localmcp-load` (or any reload that
# changes the running backends) so the agent sees the new tool catalog.
#
# Knobs: ACCESS=read-only|read-write (default read-write — allows mutating
# tools with confirmation; switch to read-only for stricter sessions).
LOCALMCP_RULE_FILE   ?= .cursor/rules/localmcp.mdc
LOCALMCP_RULE_ACCESS ?= read-write
localmcp-rule-refresh:
	@if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status 2>/dev/null; then \
		echo "ERROR: localmcp not reachable on port $(LOCALMCP_PORT)."; \
		echo "       Run 'make localmcp-up' first."; \
		exit 1; \
	fi
	@mkdir -p "$$(dirname $(LOCALMCP_RULE_FILE))"
	@curl -fsSL "http://localhost:$(LOCALMCP_PORT)/api/cursor-rule?access=$(LOCALMCP_RULE_ACCESS)" \
		-o $(LOCALMCP_RULE_FILE)
	@echo "==> wrote $(LOCALMCP_RULE_FILE) ($(LOCALMCP_RULE_ACCESS) mode, $$(wc -l < $(LOCALMCP_RULE_FILE)) lines)"
	@echo "    Restart Cursor (Cmd+Q) for the new rule to load."

# clean everything: container, image, builder, registry config helper
clean: localmcp-down
	-docker buildx rm ${BUILDX_BUILDER_NAME}
	-docker image rm ${BUILDX_IMAGE_NAME}
	-docker image rm ${LOCALMCP_IMAGE_TAG}
	-rm -f buildkitd.toml

# nuke: everything `clean` removes PLUS the persistent Docker volumes
# LocalMCP creates (npx/uv/pincher caches, pincher SQLite index DB, and
# any leftover buildx state for $(BUILDX_BUILDER_NAME)). DESTRUCTIVE —
# the pincher index has to be rebuilt from scratch on the next
# `make localmcp-up && make localmcp-load` (which can take several
# minutes if you also call `localmcp-warm-index-full`). Use
# `make clean` instead when you just want to tear down the container
# and image and keep caches.
#
# Volumes removed: every Docker volume whose name contains
# `localmcp-`. That covers the named volumes pinned in
# configs/default-volumes.conf (`localmcp-npm`, `localmcp-cache`,
# `localmcp-pincher`, `localmcp-savings`) plus any code-index / buildx
# state volumes (`localmcp-code-index`, `buildx_buildkit_localmcp-*_state`).
nuke: clean
	@echo "==> stopping any leftover buildx containers for $(BUILDX_BUILDER_NAME)"
	-@cids=$$(docker ps -aq -f name=buildx_buildkit_$(BUILDX_BUILDER_NAME) 2>/dev/null); \
	if [ -n "$$cids" ]; then \
		echo "$$cids" | xargs docker rm -f; \
	else \
		echo "    (none)"; \
	fi
	@echo "==> removing volumes whose name contains 'localmcp-'"
	-@volumes=$$(docker volume ls -q -f name=localmcp- 2>/dev/null); \
	if [ -n "$$volumes" ]; then \
		echo "$$volumes" | sed 's/^/    rm: /'; \
		echo "$$volumes" | xargs docker volume rm; \
	else \
		echo "    (none)"; \
	fi
	@echo "==> nuke complete; next 'make localmcp-up' will build a fresh image and rehydrate caches"

