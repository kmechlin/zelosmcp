# LocalMCP — multi-MCP proxy with Cursor-compatible config.
#
# This image bundles the runtimes that stdio MCP servers most commonly need:
#   - Python 3.12 (host runtime + pip + uv/uvx + pipx)
#   - Node.js 22  (provides `npx` for `@modelcontextprotocol/...` packages)
#   - pincher     (Go binary baked in via the pincher-build stage below;
#                  used by the default `pincher` MCP backend)
#   - git, curl, build tools (some npm packages compile native deps)
#
# Build:    docker build -t localmcp .
# Run:      docker run --rm -p 8000:8000 localmcp
# Mount fs: docker run --rm -p 8000:8000 \
#               -v "$HOME:/user_data_rw" -v "$HOME:/user_data_ro:ro" localmcp

# ── Stage 1: build the pincherMCP Go binary ───────────────────────────
# pincherMCP — codebase intelligence MCP server. Single Go binary spoken
# over stdio.
#
# Defaults to kmechlin's fork branch which adds --basepath / --trust-proxy
# (required by configs/default-localmcp.json's reverseProxy entry).
# Switch back to upstream once that PR merges:
#   docker build \
#     --build-arg PINCHER_REPO=https://github.com/kwad77/pincherMCP.git \
#     --build-arg PINCHER_REF=v0.3.0  -t localmcp .
FROM golang:1.24-alpine AS pincher-build
ARG PINCHER_REPO=https://github.com/kmechlin/pincherMCP.git
ARG PINCHER_REF=feat/reverse-proxy-basepath
RUN apk add --no-cache git ca-certificates
RUN git clone --depth 1 --branch ${PINCHER_REF} ${PINCHER_REPO} /src \
 && cd /src \
 && go build -trimpath -ldflags="-s -w" -o /pincher ./cmd/pinch/

# ── Stage 2: localmcp runtime ─────────────────────────────────────────
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0

# System packages + Node 22 (for npx-launched MCP servers)
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl git build-essential gnupg; \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Python tooling commonly invoked by MCP server configs
RUN pip install --no-cache-dir uv pipx \
 && pipx ensurepath
ENV PATH="/root/.local/bin:${PATH}"

# Pre-built pincher binary (codebase intelligence MCP). Reachable on PATH
# by stdio-launched MCP backends as `pincher`.
COPY --from=pincher-build /pincher /usr/local/bin/pincher

WORKDIR /app

COPY pyproject.toml README.md ./
COPY docs ./docs
COPY src ./src
# Mandatory MCP set merged into every /api/start payload by ProxyManager.
# See src/localmcp/manager.py:_merge_mandatory and docs/default-mcps.md.
COPY configs/mandatory-localmcp.json /app/configs/mandatory-localmcp.json

RUN pip install --no-cache-dir -e .

# Pincher (kmechlin fork) auto-indexes its CWD shortly after spawn. Point
# CWD at /user_data_ro so the mounted user source tree gets indexed in
# the background — no manual `make index-full` needed. localmcp itself
# was installed editable above and is importable from any CWD, so this
# doesn't affect uvicorn startup. /user_data_ro is created here so
# spawned subprocesses have a valid CWD even when no volume is mounted
# (in that case pincher's auto-scan no-ops on the empty in-image dir).
RUN mkdir -p /user_data_ro
WORKDIR /user_data_ro

EXPOSE 8000

CMD ["sh", "-c", "uvicorn localmcp.app:app --host ${HOST} --port ${PORT}"]
