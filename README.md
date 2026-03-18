# LocalMCP

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

Wrap any MCP server and serve it on `localhost:8000/mcp` via Streamable HTTP.

LocalMCP provides a web UI where you point it at any MCP server — a stdio command, an SSE endpoint, or a Streamable HTTP URL — and it re-exposes that server at a single, fixed local address. Cursor (or any MCP client) always connects to the same endpoint regardless of what's behind it.

## Install

```
pip install -e .
```

Requires Python 3.10+.

## Run

```
localmcp
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

## Cursor Setup

Add this to your `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "my-mcp": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

This config never changes — swap the underlying MCP server any time through the web UI. When you start a server, the web UI updates the displayed snippet so the server name matches your config. Copy it from there for an exact match.

## Usage

1. Open the web UI at `http://localhost:8000`
2. Paste an MCP server config into the textarea — the same JSON format Cursor uses in `mcp.json`. The transport is auto-detected from the config shape:

   **Stdio** (has `command`):
   ```json
   {
     "mcpServers": {
       "my-server": {
         "command": "npx",
         "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
       }
     }
   }
   ```

   **SSE** (has `type: "sse"`):
   ```json
   {
     "mcpServers": {
       "my-server": {
         "type": "sse",
         "url": "http://example.com/sse"
       }
     }
   }
   ```

   **Streamable HTTP** (has `type: "streamable-http"`):
   ```json
   {
     "mcpServers": {
       "my-server": {
         "type": "streamable-http",
         "url": "http://example.com/mcp"
       }
     }
   }
   ```

3. Click **Start**
4. The **Cursor mcp.json** snippet below the config updates its server name to match your config — copy it into `.cursor/mcp.json`
5. Cursor can now use the proxied MCP server at `http://localhost:8000/mcp`

Click **Stop** to disconnect, then start a different server whenever you need to.

## How It Works

LocalMCP runs a single Starlette app on port 8000 with three responsibilities:

- `/` serves the web UI
- `/api/*` handles start/stop/status/log-streaming
- `/mcp` is the MCP Streamable HTTP endpoint that Cursor connects to

When you start a proxy, LocalMCP spawns the backend (or connects to a remote one), establishes an MCP client session, and forwards all tool calls, resource reads, and prompt requests transparently — no prefixing or transformation.

## Project Structure

```
pyproject.toml          # Package definition
src/localmcp/
  __init__.py
  __main__.py           # python -m localmcp
  app.py                # Starlette app and routes
  proxy.py              # ProxyState: backend lifecycle and MCP forwarding
  ui.py                 # Web UI (single-page HTML/CSS/JS)
```
