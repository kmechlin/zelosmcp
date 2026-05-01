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
    --success: #2BA44E;
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
    max-width: 720px;
    margin: 0 auto;
    padding: 48px 24px;
  }

  /* ── Header ── */
  .header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 24px;
  }

  .wordmark {
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--black);
  }

  .header-spacer { flex: 1; }

  .header a.docs-link {
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--mid);
    text-decoration: none;
    border: 1px solid var(--border);
    padding: 6px 12px;
    border-radius: 999px;
  }
  .header a.docs-link:hover { color: var(--black); border-color: var(--black); }

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
    min-height: 240px;
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

  .btn-mini {
    padding: 6px 14px;
    font-size: 11px;
    width: auto;
    border-radius: 999px;
  }

  /* ── Server list ── */
  .server-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin-bottom: 16px;
  }

  .server-row {
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--white);
    border-radius: 12px;
    padding: 12px 16px;
    border: 1px solid var(--border);
  }

  .server-name {
    font-family: var(--mono);
    font-size: 14px;
    font-weight: 700;
    color: var(--black);
  }

  .server-meta {
    font-size: 12px;
    color: var(--mid);
    font-family: var(--mono);
  }

  .server-row .grow { flex: 1; }

  .pill {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    padding: 3px 8px;
    border-radius: 999px;
    line-height: 1;
  }

  .pill.transport { background: var(--surface); color: var(--mid); }
  .pill.primary { background: var(--accent); color: var(--white); }
  .pill.state-running { background: var(--success); color: var(--white); }
  .pill.state-error { background: var(--error); color: var(--white); }
  .pill.state-stopped { background: var(--border); color: var(--mid); }

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
    <span class="header-spacer"></span>
    <a class="docs-link" href="/docs" target="_blank" rel="noopener">API Docs</a>
  </div>

  <p class="intro">Paste a Cursor <code>mcp.json</code>-style config below. Each server is mounted at <code>localhost:8000/&lt;name&gt;/mcp</code>; set <code>primaryMCP</code> to also mirror one of them at <code>localhost:8000/mcp</code>.</p>

  <!-- Config -->
  <div class="section">
    <div class="section-label">Configuration</div>
    <div class="card">
      <textarea
        class="config-textarea"
        id="config-input"
        spellcheck="false"
      >{
  "primaryMCP": "code-index",
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

  <!-- Running servers -->
  <div class="section hidden" id="servers-section">
    <div class="section-label">Servers</div>
    <div class="card">
      <div class="server-list" id="server-list"></div>
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
  const serversSection = document.getElementById("servers-section");
  const serverList = document.getElementById("server-list");

  let running = false;
  let loading = false;
  let currentStatus = { servers: [], primary: null, running: false };

  // Loose client-side validation. Server is the source of truth.
  function parseConfig(raw) {
    let obj;
    try {
      obj = JSON.parse(raw);
    } catch (e) {
      throw new Error("Invalid JSON: " + e.message);
    }
    if (!obj || typeof obj !== "object") {
      throw new Error("Config must be a JSON object");
    }
    if (!obj.mcpServers || typeof obj.mcpServers !== "object") {
      throw new Error("Config must contain 'mcpServers'");
    }
    if (Object.keys(obj.mcpServers).length === 0) {
      throw new Error("'mcpServers' must contain at least one server");
    }
    return obj;
  }

  function makeUrl(name, isPrimary) {
    const base = window.location.origin;
    if (isPrimary) return base + "/mcp";
    return base + "/" + name + "/mcp";
  }

  function buildSnippet(status) {
    const entries = {};
    const servers = status.servers || [];
    for (const s of servers) {
      entries[s.name] = {
        type: "streamable-http",
        url: makeUrl(s.name, false),
      };
    }
    if (status.primary) {
      entries[status.primary + "-primary"] = {
        type: "streamable-http",
        url: makeUrl(status.primary, true),
      };
    }
    if (Object.keys(entries).length === 0) {
      return JSON.stringify({
        mcpServers: {
          "my-mcp": { type: "streamable-http", url: makeUrl("my-mcp", true) }
        }
      }, null, 2);
    }
    return JSON.stringify({ mcpServers: entries }, null, 2);
  }

  function renderServers(status) {
    const servers = status.servers || [];
    if (servers.length === 0) {
      serversSection.classList.add("hidden");
      serverList.innerHTML = "";
      return;
    }
    serversSection.classList.remove("hidden");
    serverList.innerHTML = "";
    for (const s of servers) {
      const row = document.createElement("div");
      row.className = "server-row";

      const name = document.createElement("div");
      name.className = "server-name";
      name.textContent = s.name;
      row.appendChild(name);

      if (s.transport) {
        const t = document.createElement("span");
        t.className = "pill transport";
        t.textContent = s.transport;
        row.appendChild(t);
      }

      const state = document.createElement("span");
      if (s.error) {
        state.className = "pill state-error";
        state.textContent = "error";
      } else if (s.running) {
        state.className = "pill state-running";
        state.textContent = "running";
      } else {
        state.className = "pill state-stopped";
        state.textContent = "stopped";
      }
      row.appendChild(state);

      if (s.primary) {
        const p = document.createElement("span");
        p.className = "pill primary";
        p.textContent = "primary";
        row.appendChild(p);
      }

      const meta = document.createElement("div");
      meta.className = "server-meta grow";
      meta.textContent = makeUrl(s.name, false);
      row.appendChild(meta);

      const btn = document.createElement("button");
      btn.className = "btn btn-mini " + (s.running ? "btn-outline" : "btn-primary");
      btn.textContent = s.running ? "Stop" : "Start";
      btn.onclick = () => toggleServer(s.name, s.running);
      row.appendChild(btn);

      serverList.appendChild(row);
    }
  }

  async function toggleServer(name, isRunning) {
    const action = isRunning ? "stop" : "start";
    const res = await fetch(`/api/servers/${encodeURIComponent(name)}/${action}`, { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      addLog("ERROR: " + (data.error || "Server toggle failed"));
    }
    await refreshStatus();
  }

  async function refreshStatus() {
    try {
      const r = await fetch("/api/status");
      const data = await r.json();
      currentStatus = data;
      running = !!data.running;
      renderServers(data);
      mcpSnippet.textContent = buildSnippet(data);
      updateUI();
    } catch (err) {
      addLog("ERROR: " + err.message);
    }
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
      actionBtn.textContent = "STOP ALL";
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
        await res.json();
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
        const res = await fetch("/api/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!data.ok) {
          addLog("ERROR: " + (data.error || "Failed to start"));
        } else if (data.servers) {
          for (const [name, r] of Object.entries(data.servers)) {
            if (!r.ok) addLog(`ERROR: ${name}: ${r.error}`);
          }
        }
      }
    } catch (err) {
      addLog("ERROR: " + err.message);
    }

    await refreshStatus();
    setLoading(false);
    updateUI();
  }

  function copySnippet() {
    const text = mcpSnippet.textContent;
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

  // Initial status
  refreshStatus();
</script>
</body>
</html>
"""
