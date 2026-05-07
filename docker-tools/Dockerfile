##### GLOBAL BUILD ARGUMENTS
# These are used in the FROM commands.
# All other ARGS have to be declared within a stage.
ARG PROJECT_BASE_IMAGE
# pincherMCP source — defaults to kmechlin's fork branch with the
# --basepath / --trust-proxy flags that configs/default-localmcp.json
# relies on. Switch back to upstream after the PR merges by overriding
# both args at build time (see Makefile's PINCHER_REPO/PINCHER_REF).
ARG PINCHER_REPO=https://github.com/kmechlin/pincherMCP.git
ARG PINCHER_REF=feat/reverse-proxy-basepath

# Base OS layer
# OS packages are installed here.
FROM ${PROJECT_BASE_IMAGE} AS base-os
  RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    git \
    gcc \
    build-essential \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    openssh-client
  # cleanup
  RUN apt-get clean && \
    apt-get autoremove && \
    rm -rf /var/cache/apk/* /tmp/.pipenv && \
    rm -rf /var/lib/apt/lists/*
  SHELL ["/bin/bash", "-c"]

# Extra OS layer
# Add the corporate root authority cert (for TLS-intercepting proxy, e.g. Palo Alto)
# so apt/pip/npm/uvx can clear the proxy in subsequent stages.
FROM base-os AS extra-os
  ARG CERT
  COPY ${CERT} /usr/local/share/ca-certificates/cert.crt
  RUN update-ca-certificates


# Pincher build layer
# pincherMCP — codebase intelligence MCP server, single Go binary. Built
# once and COPY'd into the localmcp layer. Uses Debian-based golang image
# so we can install the corp cert (Alpine variant doesn't share the Debian
# extra-os stage's update-ca-certificates plumbing). Source is controlled
# by the PINCHER_REPO / PINCHER_REF global ARGs.
FROM golang:1.24-bookworm AS pincher-build
  ARG CERT
  ARG PINCHER_REPO
  ARG PINCHER_REF
  COPY ${CERT} /usr/local/share/ca-certificates/cert.crt
  RUN update-ca-certificates && \
      git clone --depth 1 --branch ${PINCHER_REF} ${PINCHER_REPO} /src && \
      cd /src && \
      go build -trimpath -ldflags="-s -w" -o /pincher ./cmd/pinch/


# LocalMCP layer
# The Cursor-blessed MCP proxy. Inherits the corp cert + git/curl from
# `extra-os` so apt/pip/npm/uvx can clear the corporate proxy when
# downloading runtimes and MCP backends at build time.
FROM extra-os AS localmcp
  # Node 22 (for npx-launched MCP backends like kubernetes-mcp-server,
  # @modelcontextprotocol/server-filesystem). Some npm packages compile
  # native deps - build-essential is already in base-os.
  RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        nodejs && \
      apt-get clean && \
      rm -rf /var/lib/apt/lists/*

  # uv / uvx / pipx (for uvx-launched MCP backends)
  RUN pip install --no-cache-dir uv pipx && pipx ensurepath
  ENV PATH="/root/.local/bin:${PATH}"

  # Pre-built pincher binary (codebase intelligence MCP). Reachable on
  # PATH by stdio-launched MCP backends as `pincher`.
  COPY --from=pincher-build /pincher /usr/local/bin/pincher

  # COPY localmcp source from the build context (= repo root).
  # `.dockerignore` excludes .venv, .git, .pytest_cache, etc.
  COPY pyproject.toml README.md /opt/localmcp/
  # docs/*.md powers the in-app Documentation view (/api/docs[/{slug}]).
  # `.dockerignore` re-includes docs/**/*.md so the markdown files
  # actually ship in the image.
  COPY docs /opt/localmcp/docs
  COPY src /opt/localmcp/src
  # Mandatory MCP set merged into every /api/start payload by ProxyManager.
  # See src/localmcp/manager.py:_merge_mandatory and docs/default-mcps.md.
  COPY configs/mandatory-localmcp.json /opt/localmcp/configs/mandatory-localmcp.json

  RUN pip install --no-cache-dir -e /opt/localmcp

  # Pincher (kmechlin fork) auto-indexes its CWD shortly after spawn.
  # Point CWD at /user_data_ro so the mounted user source tree gets
  # indexed in the background — no manual `make index-full` needed.
  # localmcp itself was installed editable above and is importable
  # from any CWD, so this doesn't affect uvicorn startup.
  # /user_data_ro is created here so spawned subprocesses have a valid
  # CWD even when no volume is mounted (in that case pincher's
  # auto-scan no-ops on the empty in-image dir).
  RUN mkdir -p /user_data_ro
  WORKDIR /user_data_ro
  EXPOSE 8000
  CMD ["sh", "-c", "uvicorn localmcp.app:app --host 0.0.0.0 --port 8000"]
