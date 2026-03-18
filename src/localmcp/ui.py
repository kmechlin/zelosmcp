HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LOCALMCP</title>
<style>
  :root {
    --black: #111111;
    --white: #FFFFFF;
    --surface: #F5F5F5;
    --border: #E5E5E5;
    --mid: #757575;
    --accent: #FF6600;
    --error: #D13B3B;
    --font: "Helvetica Neue", Helvetica, Arial, sans-serif;
    --mono: "SF Mono", "Cascadia Code", "Fira Code", Consolas, monospace;
  }

  *, *::before, *::after {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
  }

  body {
    font-family: var(--font);
    font-size: 16px;
    line-height: 1.5;
    color: var(--black);
    background: var(--white);
    -webkit-font-smoothing: antialiased;
  }

  .container {
    max-width: 640px;
    margin: 0 auto;
    padding: 48px 24px;
  }

  /* ── Header ── */
  .header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 40px;
  }

  .wordmark {
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--black);
  }

  .badge {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    padding: 4px 12px;
    border-radius: 999px;
    line-height: 1;
  }

  .badge.stopped {
    background: var(--border);
    color: var(--mid);
  }

  .badge.running {
    background: var(--accent);
    color: var(--white);
  }

  .badge.error {
    background: var(--error);
    color: var(--white);
  }

  /* ── Intro ── */
  .intro {
    font-size: 15px;
    color: var(--mid);
    margin-bottom: 32px;
    line-height: 1.6;
  }

  .intro code {
    font-family: var(--mono);
    font-size: 13px;
    background: var(--surface);
    padding: 2px 6px;
    border-radius: 4px;
  }

  /* ── Sections ── */
  .section {
    margin-bottom: 32px;
  }

  .section-label {
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--mid);
    margin-bottom: 12px;
  }

  /* ── Card ── */
  .card {
    background: var(--surface);
    border-radius: 16px;
    padding: 24px;
  }

  /* ── Textarea ── */
  .config-textarea {
    width: 100%;
    min-height: 180px;
    background: var(--white);
    border: 2px solid var(--border);
    border-radius: 12px;
    padding: 14px 16px;
    font-size: 13px;
    font-family: var(--mono);
    line-height: 1.6;
    color: var(--black);
    resize: vertical;
    transition: border-color 0.2s ease;
    margin-bottom: 20px;
    tab-size: 2;
  }

  .config-textarea:focus {
    border-color: var(--black);
    outline: none;
  }

  .config-textarea::placeholder {
    color: var(--mid);
  }

  /* ── Button ── */
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 999px;
    padding: 14px 32px;
    font-size: 15px;
    font-weight: 700;
    font-family: var(--font);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    cursor: pointer;
    transition: all 0.2s ease;
    border: 2px solid transparent;
    width: 100%;
  }

  .btn-primary {
    background: var(--black);
    color: var(--white);
    border-color: var(--black);
  }

  .btn-primary:hover {
    background: #333333;
    border-color: #333333;
  }

  .btn-outline {
    background: var(--white);
    color: var(--black);
    border-color: var(--black);
  }

  .btn-outline:hover {
    background: var(--black);
    color: var(--white);
  }

  .btn:disabled {
    background: var(--border);
    color: var(--mid);
    border-color: var(--border);
    cursor: not-allowed;
  }

  /* ── Log Viewer ── */
  .log-viewer {
    background: var(--white);
    border-radius: 12px;
    padding: 16px;
    max-height: 320px;
    overflow-y: auto;
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.6;
    color: var(--black);
  }

  .log-viewer:empty::before {
    content: "Logs will appear here...";
    color: var(--mid);
  }

  .log-line {
    white-space: pre-wrap;
    word-break: break-all;
  }

  .log-line.error {
    color: var(--error);
  }

  /* ── Snippet block ── */
  .snippet {
    position: relative;
    background: var(--white);
    border-radius: 12px;
    padding: 16px;
    padding-right: 80px;
  }

  .snippet pre {
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.6;
    color: var(--black);
    margin: 0;
    white-space: pre;
    overflow-x: auto;
  }

  .snippet-copy {
    position: absolute;
    top: 12px;
    right: 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 12px;
    font-family: var(--font);
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--mid);
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .snippet-copy:hover {
    background: var(--border);
    color: var(--black);
  }

  .snippet-copy.copied {
    color: var(--accent);
    border-color: var(--accent);
  }

  /* ── Utilities ── */
  .hidden { display: none !important; }
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <div class="header">
    <span class="wordmark">LOCALMCP</span>
    <span class="badge stopped" id="badge">STOPPED</span>
  </div>

  <p class="intro">Paste the <code>mcp.json</code> configuration from your MCP server's documentation below, then click <strong>Start</strong> to proxy it on <code>localhost:8000/mcp</code>.</p>

  <!-- Config -->
  <div class="section">
    <div class="section-label">Configuration</div>
    <div class="card">
      <textarea
        class="config-textarea"
        id="config-input"
        spellcheck="false"
      >{
  "mcpServers": {
    "code-index": {
      "command": "uvx",
      "args": ["code-index-mcp"]
    }
  }
}</textarea>

      <button class="btn btn-primary" id="action-btn" onclick="handleAction()">
        START
      </button>
    </div>
  </div>

  <!-- mcp.json Snippet -->
  <div class="section">
    <div class="section-label">Cursor mcp.json</div>
    <div class="card">
      <div class="snippet">
        <button class="snippet-copy" id="copy-btn" onclick="copySnippet()">Copy</button>
        <pre id="mcp-snippet">{
  "mcpServers": {
    "my-mcp": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}</pre>
      </div>
    </div>
  </div>

  <!-- Logs -->
  <div class="section">
    <div class="section-label">Activity</div>
    <div class="card">
      <div class="log-viewer" id="log-viewer"></div>
    </div>
  </div>

</div>

<script>
  const badge = document.getElementById("badge");
  const actionBtn = document.getElementById("action-btn");
  const configInput = document.getElementById("config-input");
  const logViewer = document.getElementById("log-viewer");
  const mcpSnippet = document.getElementById("mcp-snippet");

  let running = false;
  let loading = false;

  function parseConfig(raw) {
    let obj;
    try {
      obj = JSON.parse(raw);
    } catch (e) {
      throw new Error("Invalid JSON: " + e.message);
    }

    let server = obj;
    let serverName = "my-mcp";
    if (obj.mcpServers) {
      const keys = Object.keys(obj.mcpServers);
      if (keys.length === 0) throw new Error("No server entries found in mcpServers");
      serverName = keys[0];
      server = obj.mcpServers[keys[0]];
    }

    if (server.command) {
      const parts = [server.command];
      if (Array.isArray(server.args)) {
        parts.push(...server.args);
      }
      const result = { transport: "stdio", command: parts.join(" "), _name: serverName };
      if (server.env && typeof server.env === "object") {
        result.env = server.env;
      }
      return result;
    }

    if (server.type === "sse") {
      if (!server.url) throw new Error("SSE config missing 'url'");
      return { transport: "sse", url: server.url, _name: serverName };
    }

    if (server.type === "streamable-http") {
      if (!server.url) throw new Error("Streamable HTTP config missing 'url'");
      return { transport: "http", url: server.url, _name: serverName };
    }

    throw new Error("Could not determine transport. Provide 'command' (stdio) or 'type' (sse/streamable-http).");
  }

  function updateSnippet(name) {
    const snippet = JSON.stringify({
      mcpServers: {
        [name]: { type: "streamable-http", url: "http://localhost:8000/mcp" }
      }
    }, null, 2);
    mcpSnippet.textContent = snippet;
  }

  function setLoading(state) {
    loading = state;
    actionBtn.disabled = state;
    if (state) {
      actionBtn.textContent = running ? "STOPPING\\u2026" : "STARTING\\u2026";
    }
  }

  function updateUI() {
    if (running) {
      badge.textContent = "RUNNING";
      badge.className = "badge running";
      actionBtn.textContent = "STOP";
      actionBtn.className = "btn btn-outline";
      configInput.disabled = true;
    } else {
      badge.textContent = "STOPPED";
      badge.className = "badge stopped";
      actionBtn.textContent = "START";
      actionBtn.className = "btn btn-primary";
      configInput.disabled = false;
    }
    actionBtn.disabled = false;
  }

  function addLog(text) {
    const line = document.createElement("div");
    line.className = "log-line" + (text.includes("ERROR") ? " error" : "");
    line.textContent = text;
    logViewer.appendChild(line);
    logViewer.scrollTop = logViewer.scrollHeight;
  }

  async function handleAction() {
    if (loading) return;
    setLoading(true);

    try {
      if (running) {
        const res = await fetch("/api/stop", { method: "POST" });
        const data = await res.json();
        if (data.ok) running = false;
      } else {
        let body;
        try {
          body = parseConfig(configInput.value);
        } catch (e) {
          addLog("ERROR: " + e.message);
          setLoading(false);
          updateUI();
          return;
        }
        const serverName = body._name;
        delete body._name;
        const res = await fetch("/api/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.ok) {
          running = true;
          updateSnippet(serverName);
        } else {
          addLog("ERROR: " + (data.error || "Failed to start"));
        }
      }
    } catch (err) {
      addLog("ERROR: " + err.message);
    }

    setLoading(false);
    updateUI();
  }

  function copySnippet() {
    const text = document.getElementById("mcp-snippet").textContent;
    const btn = document.getElementById("copy-btn");
    navigator.clipboard.writeText(text).then(() => {
      btn.textContent = "Copied";
      btn.classList.add("copied");
      setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 2000);
    });
  }

  // SSE log stream
  const events = new EventSource("/api/logs");
  events.onmessage = (e) => addLog(e.data);

  // Initial status check
  fetch("/api/status")
    .then(r => r.json())
    .then(data => { running = data.running; updateUI(); });
</script>
</body>
</html>
"""
