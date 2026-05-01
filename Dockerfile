# LocalMCP — multi-MCP proxy with Cursor-compatible config.
#
# This image bundles the runtimes that stdio MCP servers most commonly need:
#   - Python 3.12 (host runtime + pip + uv/uvx + pipx)
#   - Node.js 20  (provides `npx` for `@modelcontextprotocol/...` packages)
#   - git, curl, build tools (some npm packages compile native deps)
#
# Build:    docker build -t localmcp .
# Run:      docker run --rm -p 8000:8000 localmcp
# Mount fs: docker run --rm -p 8000:8000 -v "$PWD:/workspace" localmcp
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0

# System packages + Node 20 (for npx-launched MCP servers)
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl git build-essential gnupg; \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Python tooling commonly invoked by MCP server configs
RUN pip install --no-cache-dir uv pipx \
 && pipx ensurepath
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn localmcp.app:app --host ${HOST} --port ${PORT}"]
