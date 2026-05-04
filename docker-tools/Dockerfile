##### GLOBAL BUILD ARGUMENTS
# These are used in the FROM commands.
# All other ARGS have to be declared within a stage.
ARG PROJECT_BASE_IMAGE

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

  # uv / uvx / pipx (for uvx-launched MCP backends like code-index-mcp)
  RUN pip install --no-cache-dir uv pipx && pipx ensurepath
  ENV PATH="/root/.local/bin:${PATH}"

  # COPY localmcp source from the build context (= repo root).
  # `.dockerignore` excludes .venv, .git, .pytest_cache, etc.
  COPY pyproject.toml README.md /opt/localmcp/
  COPY src /opt/localmcp/src

  RUN pip install --no-cache-dir -e /opt/localmcp

  WORKDIR /opt/localmcp
  EXPOSE 8000
  CMD ["sh", "-c", "uvicorn localmcp.app:app --host 0.0.0.0 --port 8000"]
