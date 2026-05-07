# ============================================================================
# LocalMCP — Docker container lifecycle for the MCP proxy + aggregator.
#
# Quickstart:
#   make init-env   # optional one-time wizard: writes .env (USER_DATA_ROOT, ports, etc.)
#   make up         # build image (if missing) + start container + load + index + rule
#
# `make help` lists every public verb.
# `.env` overrides every variable below (auto-loaded via `-include .env`).
# Hand-edit `.env` directly, or run `make init-env` to walk through prompts.
# ============================================================================

PROJECT_NAME ?= $(shell basename $$(git rev-parse --show-toplevel))
PROJECT_BASE_IMAGE ?= python:3.12-slim-bookworm
PYTHON_VERSION ?= 3.12
SHELL := /bin/bash
GIT_BRANCH_NAME := $(shell git rev-parse --abbrev-ref HEAD | awk '{print A[split($$0,A,"/")]}')

# ----- Build / image identity -----
DOCKER_TOOLS_PATH ?= docker-tools
DOCKERFILE ?= ${DOCKER_TOOLS_PATH}/Dockerfile
PLATFORM ?= linux/arm64
BUILDX_BUILDER_NAME ?= localmcp-builder
BUILDX_IMAGE_NAME ?= localmcp-buildx
DOCKER_REGISTRY ?= host.docker.internal:5001
SSH_KEY_FILE ?= $(HOME)/.ssh/id_rsa

# Corporate root CA cert (used by `make cert`, called from `make setup`).
# Only relevant behind a TLS-intercepting proxy. Override CN if your CA differs;
# users without a corporate cert can switch to the simpler upstream image via
# DOCKERFILE=Dockerfile in `.env`.
CORP_ROOT_AUTHORITY_CERT_NAME ?= Nike Root Authority NG

# ----- Pincher source pinning -----
# kmechlin's fork adds --basepath / --trust-proxy used by the default config's
# reverseProxy entry. Override both to switch to upstream once that PR merges.
PINCHER_REPO ?= https://github.com/kmechlin/pincherMCP.git
PINCHER_REF  ?= feat/reverse-proxy-basepath

# ============================================================================
# Runtime variables (.env overrides these — `make init-env` writes a .env)
# ============================================================================

# Source tree exposed to MCP backends. Bind-mounted twice into the container:
# rw at /user_data_rw (filesystem MCP root) and ro at /user_data_ro (pincher
# index target / WORKDIR). Default $HOME/workspace so cross-repo browsing
# and pincher's auto-scan work without configuration.
USER_DATA_ROOT      ?= $(HOME)/workspace

# HTTP listen surface. 127.0.0.1 means only this Mac can reach :8000;
# 0.0.0.0 exposes it on the LAN.
LOCALMCP_PORT       ?= 8000
LOCALMCP_BIND_ADDR  ?= 127.0.0.1

# Image + container identity.
LOCALMCP_IMAGE_TAG  ?= localmcp:dev
LOCALMCP_CONTAINER  ?= localmcp

# Default config posted by `make load`. Override (e.g. via .env) to point
# at configs/user-localmcp.json that `make init-env` writes when the user
# opts out of any default backend.
LOCALMCP_CONFIG     ?= $(shell pwd)/configs/default-localmcp.json
LOCALMCP_VOLUMES_FILE ?= $(shell pwd)/configs/default-volumes.conf

# Host paths bind-mounted in.
KUBERNETES_CONFIG_FILE ?= $(HOME)/.kube/config
DOCKER_SOCK_FILE       ?= /var/run/docker.sock

# Cursor `.mdc` rule file: where it lands and what access mode it carries.
# Default: per-project. Set to $(HOME)/.cursor/rules/localmcp.mdc for global.
LOCALMCP_RULE_FILE   ?= .cursor/rules/localmcp.mdc
LOCALMCP_RULE_ACCESS ?= read-write

# Auto-chain toggles for `make load`. Set to 0 in .env for fast CI loads.
# LOCALMCP_WARM_ON_LOAD=1: load → index   (warms pincher's index for current repo)
# LOCALMCP_RULE_ON_LOAD=1: load → rule    (regenerates Cursor .mdc to match loaded set)
LOCALMCP_WARM_ON_LOAD ?= 1
LOCALMCP_RULE_ON_LOAD ?= 1

# Pincher per-repo `make index` target (advanced — usually auto-derived).
LOCALMCP_PROJECT_NAME ?= $(PROJECT_NAME)
LOCALMCP_PROJECT_REL  ?= $(shell python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$$(git rev-parse --show-toplevel 2>/dev/null)" "$(USER_DATA_ROOT)" 2>/dev/null || echo "$(LOCALMCP_PROJECT_NAME)")
LOCALMCP_PROJECT_PATH ?= /user_data_ro/$(LOCALMCP_PROJECT_REL)

-include .env

.DEFAULT_GOAL := help

.PHONY: help vars init-env \
	cert build-buildx-image setup-buildx setup build rebuild \
	kubeconfig clean-kubeconfig \
	up down restart load status logs shell ui tools rule index index-full \
	clean nuke

# ============================================================================
# Discovery
# ============================================================================

# Verb categorization for `make help`. Add a verb to one of these lists when
# you add a new public target; the help output is generated from them.
HELP_CONTROL := up down restart clean nuke
HELP_SERVICE := load status tools rule index index-full ui kubeconfig clean-kubeconfig
HELP_DEV     := init-env setup build rebuild cert logs shell vars

help: ## Print every public verb grouped by section
	@printf '\nLocalMCP — Docker container lifecycle for the MCP proxy + aggregator.\n\n'
	@printf 'Quickstart:\n'
	@printf '  make init-env       optional: interactive wizard, writes .env\n'
	@printf '  make up             build (if missing) + start + load + index + rule\n\n'
	@printf 'Control (lifecycle):\n'
	@for v in $(HELP_CONTROL); do \
		desc=$$(awk -v t="$$v" 'BEGIN{FS=":.*?## "} $$1==t {sub(/^[^#]*## */,""); print; exit}' $(MAKEFILE_LIST)); \
		printf "  %-18s %s\n" "$$v" "$$desc"; \
	done
	@printf '\nService (interact with the running container):\n'
	@for v in $(HELP_SERVICE); do \
		desc=$$(awk -v t="$$v" 'BEGIN{FS=":.*?## "} $$1==t {sub(/^[^#]*## */,""); print; exit}' $(MAKEFILE_LIST)); \
		printf "  %-18s %s\n" "$$v" "$$desc"; \
	done
	@printf '\nDevelopment / debugging:\n'
	@for v in $(HELP_DEV); do \
		desc=$$(awk -v t="$$v" 'BEGIN{FS=":.*?## "} $$1==t {sub(/^[^#]*## */,""); print; exit}' $(MAKEFILE_LIST)); \
		printf "  %-18s %s\n" "$$v" "$$desc"; \
	done
	@printf '\n.env overrides every variable. Print effective values: make vars\n\n'

vars: ## Print effective Make variable values
	@:
	$(info PROJECT_NAME=$(PROJECT_NAME))
	$(info PLATFORM=$(PLATFORM))
	$(info DOCKERFILE=$(DOCKERFILE))
	$(info USER_DATA_ROOT=$(USER_DATA_ROOT))
	$(info KUBERNETES_CONFIG_FILE=$(KUBERNETES_CONFIG_FILE))
	$(info DOCKER_SOCK_FILE=$(DOCKER_SOCK_FILE))
	$(info LOCALMCP_PORT=$(LOCALMCP_PORT))
	$(info LOCALMCP_BIND_ADDR=$(LOCALMCP_BIND_ADDR))
	$(info LOCALMCP_IMAGE_TAG=$(LOCALMCP_IMAGE_TAG))
	$(info LOCALMCP_CONTAINER=$(LOCALMCP_CONTAINER))
	$(info LOCALMCP_CONFIG=$(LOCALMCP_CONFIG))
	$(info LOCALMCP_VOLUMES_FILE=$(LOCALMCP_VOLUMES_FILE))
	$(info LOCALMCP_RULE_FILE=$(LOCALMCP_RULE_FILE))
	$(info LOCALMCP_RULE_ACCESS=$(LOCALMCP_RULE_ACCESS))
	$(info LOCALMCP_PROJECT_PATH=$(LOCALMCP_PROJECT_PATH))
	$(info LOCALMCP_WARM_ON_LOAD=$(LOCALMCP_WARM_ON_LOAD))
	$(info LOCALMCP_RULE_ON_LOAD=$(LOCALMCP_RULE_ON_LOAD))

init-env: ## Interactive wizard: walk through common config and write .env
	@python3 scripts/init_env.py $(if $(FORCE),--force,)

# ============================================================================
# Build (cert + buildx + image)
# ============================================================================

cert: ## Export the corporate root CA cert from the macOS keychain (corp builds only)
	security find-certificate -c "$(CORP_ROOT_AUTHORITY_CERT_NAME)" -p > ${DOCKER_TOOLS_PATH}/cert.pem

# Internal: cert-aware buildx builder image used by setup-buildx.
build-buildx-image: cert
	docker build \
		--build-arg CERT=${DOCKER_TOOLS_PATH}/cert.pem \
		--build-arg PROJECT_BASE_IMAGE=${PROJECT_BASE_IMAGE} \
		--build-arg PYTHON_VERSION=${PYTHON_VERSION} \
		--target buildx \
		-t ${BUILDX_IMAGE_NAME} \
		-f ${DOCKER_TOOLS_PATH}/buildx.Dockerfile \
		.

# Internal: register the cert-aware buildx builder. Idempotent.
setup-buildx: cert build-buildx-image
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

setup: cert setup-buildx build ## One-shot first-time prep: cert + buildx + image build

build: cert setup-buildx ## Build the LocalMCP image (incremental)
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

rebuild: cert setup-buildx ## Force-rebuild the LocalMCP image (--no-cache)
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

# ============================================================================
# Kubeconfig (auto-called by `up`; exposed for manual re-add)
# ============================================================================

# Add a `localmcp` cluster + context to $(KUBERNETES_CONFIG_FILE) so the
# bridge-networked container can reach the host's K8s API via
# host.docker.internal:6443. Idempotent — safe to re-run. Skipped (with a
# warning) when kubectl is missing or the kubeconfig file doesn't exist.
# Agents pick the localmcp context per call via `kubernetes-mcp-server`'s
# multi-cluster `context` arg. See docs/setup-rancher-desktop.md.
kubeconfig: ## Add `localmcp` cluster + context to $(KUBERNETES_CONFIG_FILE)
	@if ! command -v kubectl >/dev/null 2>&1; then \
		echo "(skip) kubeconfig: kubectl not found on PATH"; exit 0; \
	fi; \
	if [ ! -f "$(KUBERNETES_CONFIG_FILE)" ]; then \
		echo "(skip) kubeconfig: $(KUBERNETES_CONFIG_FILE) not found"; exit 0; \
	fi; \
	CURRENT_CTX=$$(kubectl config current-context --kubeconfig="$(KUBERNETES_CONFIG_FILE)" 2>/dev/null || true); \
	if [ -z "$$CURRENT_CTX" ]; then \
		echo "(skip) kubeconfig: no current-context set in $(KUBERNETES_CONFIG_FILE)"; exit 0; \
	fi; \
	CURRENT_USER=$$(kubectl config view --kubeconfig="$(KUBERNETES_CONFIG_FILE)" \
		-o jsonpath="{.contexts[?(@.name==\"$$CURRENT_CTX\")].context.user}"); \
	if [ -z "$$CURRENT_USER" ]; then \
		echo "(skip) kubeconfig: could not resolve user for context '$$CURRENT_CTX'"; exit 0; \
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

clean-kubeconfig: ## Remove the `localmcp` context + cluster from $(KUBERNETES_CONFIG_FILE)
	@kubectl config delete-context localmcp --kubeconfig="$(KUBERNETES_CONFIG_FILE)" 2>/dev/null || true
	@kubectl config delete-cluster localmcp --kubeconfig="$(KUBERNETES_CONFIG_FILE)" 2>/dev/null || true
	@echo "==> kubeconfig: 'localmcp' context + cluster removed (if present)"

# ============================================================================
# Container lifecycle
# ============================================================================

# Start the LocalMCP container. Volume mounts come from $(LOCALMCP_VOLUMES_FILE).
# Auto-builds the image if it isn't loaded yet. After the container is healthy,
# chains into `load` (which itself chains `index` and `rule`).
up: kubeconfig ## Build (if missing) + start container + load default backends
	@if [ ! -f "$(LOCALMCP_VOLUMES_FILE)" ]; then \
		echo "ERROR: volumes file not found: $(LOCALMCP_VOLUMES_FILE)"; \
		echo "       Override with LOCALMCP_VOLUMES_FILE=path/to/your.conf"; \
		exit 2; \
	fi
	@if ! docker image inspect $(LOCALMCP_IMAGE_TAG) >/dev/null 2>&1; then \
		echo "==> image $(LOCALMCP_IMAGE_TAG) not found locally; building it"; \
		$(MAKE) build; \
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
			break; \
		fi; \
		sleep 1; \
	done; \
	if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status 2>/dev/null; then \
		echo "localmcp did not become ready within 15s; check 'make logs'"; \
		exit 1; \
	fi
	@$(MAKE) --no-print-directory load

down: ## Stop and remove the LocalMCP container
	-docker rm -f $(LOCALMCP_CONTAINER)

restart: ## Bounce the container (down + up)
	$(MAKE) down
	$(MAKE) up

logs: ## Tail the container logs (-f, last 200 lines)
	@docker logs -f --tail=200 $(LOCALMCP_CONTAINER)

shell: ## Open a bash shell inside the running container
	@if ! docker ps --filter name=^/$(LOCALMCP_CONTAINER)$$ --format '{{.Names}}' | grep -q .; then \
		echo "Container $(LOCALMCP_CONTAINER) is not running."; \
		echo "Start it first with: make up"; \
		exit 1; \
	fi
	docker exec -ti $(LOCALMCP_CONTAINER) bash

status: ## Print container + HTTP probe state
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

ui: ## Open the web UI in your default browser
	@open http://localhost:$(LOCALMCP_PORT) 2>/dev/null \
		|| echo "Open this URL manually:  http://localhost:$(LOCALMCP_PORT)"

# ============================================================================
# Backend lifecycle (load + chained warm-up)
# ============================================================================

# POST $(LOCALMCP_CONFIG) to /api/start. After backends start, chains:
#   - `index` (when LOCALMCP_WARM_ON_LOAD=1) — warms pincher's index for the
#     current repo so it's hot in seconds. Pincher's WORKDIR auto-scan
#     covers the rest of /user_data_ro in the background.
#   - `rule`  (when LOCALMCP_RULE_ON_LOAD=1) — regenerates the Cursor .mdc
#     file at $(LOCALMCP_RULE_FILE) so it reflects the loaded backend set.
load: ## POST $(LOCALMCP_CONFIG); chains `index` and `rule`
	@if [ ! -f "$(LOCALMCP_CONFIG)" ]; then \
		echo "ERROR: config file not found: $(LOCALMCP_CONFIG)"; \
		exit 2; \
	fi
	@echo "==> validating $(LOCALMCP_CONFIG)"
	@python3 -m json.tool "$(LOCALMCP_CONFIG)" > /dev/null
	@if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status; then \
		echo "ERROR: localmcp not reachable on port $(LOCALMCP_PORT)."; \
		echo "       Run 'make up' first."; \
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
		$(MAKE) --no-print-directory index || \
			echo "(index failed; run 'make index' manually once pincher is up)"; \
	fi
	@if [ "$(LOCALMCP_RULE_ON_LOAD)" = "1" ]; then \
		echo ""; \
		$(MAKE) --no-print-directory rule || \
			echo "(rule generation failed; run 'make rule' manually)"; \
	fi

# Force re-index of the current repo via pincher's `index` MCP tool. Auto-
# chained from `load`. xxh3 content-hashing makes re-runs cheap. Override
# the path explicitly when warming a sibling repo:
#   make index LOCALMCP_PROJECT_PATH=/user_data_ro/code/myrepo
index: ## Force pincher index of the current repo (auto-chained from `load`)
	@if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status 2>/dev/null; then \
		echo "ERROR: localmcp not reachable on port $(LOCALMCP_PORT)."; \
		echo "       Run 'make up' first."; \
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
	@echo "==> pincher__index path=$(LOCALMCP_PROJECT_PATH) (force re-index of current repo)"
	@curl -sS -X POST http://localhost:$(LOCALMCP_PORT)/mcp \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"pincher__index","arguments":{"path":"$(LOCALMCP_PROJECT_PATH)"}}}' \
		| python3 -c 'import json,sys; d=json.load(sys.stdin); r=d.get("result",{}); c=r.get("content",[]); print(c[0]["text"][:500]) if c else print("(no content)")' 2>/dev/null \
		|| echo "(call failed — pincher backend may not be running)"

# Force re-index the entire /user_data_ro mount. NOT chained from anywhere —
# pincher's WORKDIR-driven background auto-scan already covers eventual
# full-tree indexing. Use this verb when you need to re-do it now (after a
# schema bump, after `make nuke`, or to confirm auto-scan completeness).
index-full: ## Force pincher index of the entire /user_data_ro mount (on demand)
	@if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status 2>/dev/null; then \
		echo "ERROR: localmcp not reachable on port $(LOCALMCP_PORT)."; \
		echo "       Run 'make up' first."; \
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

# Print every aggregator tool grouped by backend. Same data the /catalog
# page renders; handy after `make load` to confirm what's available.
tools: ## Print aggregator tools grouped by backend prefix
	@if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status 2>/dev/null; then \
		echo "ERROR: localmcp not reachable on port $(LOCALMCP_PORT)."; \
		echo "       Run 'make up' first."; \
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

# Refresh the Cursor .mdc rule file. Auto-chained from `load`. Use
# LOCALMCP_RULE_FILE / LOCALMCP_RULE_ACCESS to control where it lands and
# whether mutating tools are permitted.
rule: ## Regenerate $(LOCALMCP_RULE_FILE) (auto-chained from `load`)
	@if ! curl -sS -o /dev/null http://localhost:$(LOCALMCP_PORT)/api/status 2>/dev/null; then \
		echo "ERROR: localmcp not reachable on port $(LOCALMCP_PORT)."; \
		echo "       Run 'make up' first."; \
		exit 1; \
	fi
	@mkdir -p "$$(dirname $(LOCALMCP_RULE_FILE))"
	@curl -fsSL "http://localhost:$(LOCALMCP_PORT)/api/cursor-rule?access=$(LOCALMCP_RULE_ACCESS)" \
		-o $(LOCALMCP_RULE_FILE)
	@echo "==> wrote $(LOCALMCP_RULE_FILE) ($(LOCALMCP_RULE_ACCESS) mode, $$(wc -l < $(LOCALMCP_RULE_FILE)) lines)"
	@echo "    Restart Cursor (Cmd+Q) for the new rule to load."

# ============================================================================
# Teardown
# ============================================================================

clean: down ## Tear down container, image, builder, and registry-config helper
	-docker buildx rm ${BUILDX_BUILDER_NAME}
	-docker image rm ${BUILDX_IMAGE_NAME}
	-docker image rm ${LOCALMCP_IMAGE_TAG}
	-rm -f buildkitd.toml

# `nuke` removes everything `clean` does PLUS persistent volumes (the
# pincher SQLite index, savings DB, npx/uv caches, buildx state). Pincher
# has to re-index from scratch on the next `make up`. Use `make clean`
# when you want to keep caches.
nuke: clean ## clean + remove every persistent localmcp-* Docker volume
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
	@echo "==> nuke complete; next 'make up' will build a fresh image and rehydrate caches"
