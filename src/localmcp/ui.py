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
    display: flex;
    align-items: center;
    gap: 10px;
  }

  /* "Recommended" pill rendered inline with a section label. */
  .recommended-pill {
    background: var(--accent);
    color: var(--white);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 3px 8px;
    border-radius: 999px;
    line-height: 1;
  }

  /* Inline `Access: [select]` control next to the Cursor rule section label. */
  .rule-access-control {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    text-transform: none;
    letter-spacing: 0;
    font-size: 12px;
    font-weight: 500;
    color: var(--mid);
  }
  .rule-access-control select {
    font-family: var(--mono);
    font-size: 12px;
    padding: 4px 8px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--white);
    color: var(--black);
    cursor: pointer;
  }
  .rule-access-control select:focus {
    outline: none;
    border-color: var(--black);
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

  .server-entry {
    display: flex;
    flex-direction: column;
    gap: 0;
  }

  .server-row {
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--white);
    border-radius: 12px;
    padding: 12px 16px;
    border: 1px solid var(--border);
    cursor: pointer;
    transition: background 0.1s ease;
    user-select: none;
  }
  .server-row:hover {
    background: var(--surface);
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

  /* Caret indicating click-to-expand. Rotates 90deg when the row is open. */
  .caret {
    display: inline-block;
    width: 12px;
    text-align: center;
    color: var(--mid);
    font-size: 10px;
    transition: transform 0.15s ease;
  }
  .caret.open { transform: rotate(90deg); }

  /* Inline catalog block under an expanded server row. */
  .server-catalog {
    background: var(--surface);
    border: 1px solid var(--border);
    border-top: none;
    border-radius: 0 0 12px 12px;
    margin: -8px 0 0 0;
    padding: 16px 20px 16px 36px;
  }
  .server-catalog .cat-group { margin-bottom: 14px; }
  .server-catalog .cat-group:last-child { margin-bottom: 0; }
  .server-catalog .cat-label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--mid);
    margin-bottom: 8px;
  }
  .server-catalog ul { list-style: none; }
  .server-catalog li {
    padding: 4px 0;
    font-family: var(--mono);
    font-size: 12px;
    line-height: 1.5;
    border-left: 2px solid var(--border);
    padding-left: 10px;
    margin-bottom: 6px;
  }
  .server-catalog li code { font-weight: 700; }
  .server-catalog li .desc {
    font-family: var(--font);
    color: var(--mid);
    font-size: 12px;
    margin-top: 2px;
    white-space: pre-wrap;
  }
  .server-catalog li details { margin-top: 4px; }
  .server-catalog li details summary {
    cursor: pointer;
    color: var(--mid);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .server-catalog li pre {
    background: var(--white);
    border-radius: 6px;
    padding: 10px;
    font-size: 11px;
    line-height: 1.45;
    margin-top: 6px;
    overflow-x: auto;
  }
  .server-catalog .empty {
    color: var(--mid);
    font-style: italic;
    font-size: 12px;
  }

  /* "Full catalog" link in the Servers card header. */
  .servers-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
  }
  .snippet-link {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--black);
    text-decoration: none;
    padding: 4px 12px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--white);
  }
  .snippet-link:hover { background: var(--surface); }

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

  <p class="intro">Paste a Cursor <code>mcp.json</code>-style config below. Each server is mounted at <code>localhost:8000/&lt;name&gt;/mcp</code> (raw passthrough), and the aggregate endpoint at <code>localhost:8000/mcp</code> exposes every running server's tools, prompts, and resources under the <code>&lt;server&gt;__&lt;name&gt;</code> namespace (resource URIs keep their original form). The aggregate is the recommended way to wire Cursor &mdash; one entry, every backend.</p>

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
    "pincher": {
      "command": "pincher",
      "args": ["--data-dir", "/tmp/pincher"]
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
    <div class="servers-header">
      <div class="section-label" style="margin: 0;">Servers</div>
      <a class="snippet-link" href="/catalog" target="_blank" rel="noopener">Full catalog</a>
    </div>
    <div class="card">
      <p class="intro" style="margin: 0 0 12px 0;">
        Click any row to inspect that backend's tools, prompts, and resources inline. The
        <a href="/catalog" target="_blank" rel="noopener">full catalog</a> opens a searchable, print-friendly documentation page.
      </p>
      <div class="server-list" id="server-list"></div>
    </div>
  </div>

  <!-- Cursor mcp.json — aggregated (recommended) -->
  <div class="section">
    <div class="section-label">
      <span>Cursor mcp.json (aggregated)</span>
      <span class="recommended-pill">Recommended</span>
    </div>
    <div class="card">
      <p class="intro" style="margin: 0 0 12px 0;">
        One Cursor entry, every running backend's tools and prompts. Names are
        namespaced as <code>&lt;server&gt;__&lt;tool&gt;</code> so they don't collide.
        Use this unless you have a specific reason to talk to a single backend
        directly.
      </p>
      <div class="snippet">
        <button class="snippet-copy" id="copy-aggregate-btn" onclick="copyAggregateSnippet()">Copy</button>
        <pre id="mcp-snippet-aggregate">{
  "mcpServers": {
    "localmcp-aggregate": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}</pre>
      </div>
    </div>
  </div>

  <!-- Cursor mcp.json — full (per-backend passthrough + aggregate) -->
  <div class="section">
    <div class="section-label">Cursor full mcp.json</div>
    <div class="card">
      <p class="intro" style="margin: 0 0 12px 0;">
        One entry per running backend at <code>/&lt;name&gt;/mcp</code> (raw
        passthrough, original tool names) plus the aggregate. Use when you need
        a backend's tools to keep their unprefixed names, or to wire a single
        backend into a separate Cursor profile.
      </p>
      <div class="snippet">
        <button class="snippet-copy" id="copy-btn" onclick="copySnippet()">Copy</button>
        <pre id="mcp-snippet">{
  "mcpServers": {
    "localmcp-aggregate": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}</pre>
      </div>
    </div>
  </div>

  <!-- Cursor rule -->
  <div class="section">
    <div class="section-label">
      <span>Cursor rule (.mdc)</span>
      <span class="rule-access-control">
        <label for="rule-access">Access:</label>
        <select id="rule-access" onchange="onRuleAccessChange()">
          <option value="read-only" selected>Read-only (safe)</option>
          <option value="read-write">Read-write (allows mutation)</option>
        </select>
      </span>
    </div>
    <div class="card">
      <p class="intro" style="margin: 0 0 12px 0;">
        Comprehensive rule listing every tool from every currently-loaded backend, with descriptions, arg summaries,
        and a <code>[readonly]</code>/<code>[mutates]</code>/<code>[destructive]</code>/<code>[?]</code> mutability marker.
        <strong>Read-only</strong> mode forbids the agent from calling mutating tools &mdash; safe default for
        inspection-style projects (code review, demos). Switch to <strong>Read-write</strong> when the agent needs
        to make changes through the MCPs. Save the body below as <code>.cursor/rules/localmcp.mdc</code> in any
        workspace (or <code>~/.cursor/rules/</code> for global).
      </p>
      <div class="snippet">
        <button class="snippet-copy" id="copy-rule-btn" onclick="copyRule()">Copy</button>
        <pre id="cursor-rule">Loading...</pre>
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
  const mcpSnippetAggregate = document.getElementById("mcp-snippet-aggregate");
  const cursorRule = document.getElementById("cursor-rule");
  let lastRuleSig = null;
  const serversSection = document.getElementById("servers-section");
  const serverList = document.getElementById("server-list");

  let running = false;
  let loading = false;
  let currentStatus = { servers: [], primary: null, running: false };
  // Inline-catalog state. Survives refreshStatus polls so a row the
  // user expanded earlier stays open when status updates re-render.
  // Resets on page reload (no localStorage by design).
  const expandedRows = new Set();
  let currentCatalog = {};
  let lastCatalogSig = null;
  let catalogInFlight = false;

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

  // Cursor's `mcpServers` keys are display labels, not routing identifiers.
  // Prefix them with `localmcp-` so it's obvious in Cursor's UI which entries
  // come from this proxy and so they don't collide with backend names a user
  // already has configured directly in their `mcp.json`.
  const SNIPPET_PREFIX = "localmcp-";

  function buildSnippet(status) {
    const entries = {};
    const servers = status.servers || [];
    const anyRunning = servers.some(s => s.running);
    for (const s of servers) {
      entries[SNIPPET_PREFIX + s.name] = {
        type: "streamable-http",
        url: makeUrl(s.name, false),
      };
    }
    if (anyRunning) {
      entries[SNIPPET_PREFIX + "aggregate"] = {
        type: "streamable-http",
        url: makeUrl(null, true),
      };
    }
    if (Object.keys(entries).length === 0) {
      return JSON.stringify({
        mcpServers: {
          [SNIPPET_PREFIX + "aggregate"]: { type: "streamable-http", url: makeUrl(null, true) }
        }
      }, null, 2);
    }
    return JSON.stringify({ mcpServers: entries }, null, 2);
  }

  // The aggregated snippet — single Cursor entry pointing at /mcp. The
  // builtin's tools (`localmcp__*`) are always available here regardless
  // of whether any user backend is running, so this snippet is stable.
  function buildAggregateSnippet(status) {
    return JSON.stringify({
      mcpServers: {
        [SNIPPET_PREFIX + "aggregate"]: {
          type: "streamable-http",
          url: makeUrl(null, true),
        },
      },
    }, null, 2);
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
      const entry = document.createElement("div");
      entry.className = "server-entry";
      entry.dataset.name = s.name;

      const row = document.createElement("div");
      row.className = "server-row";

      const isOpen = expandedRows.has(s.name);

      const caret = document.createElement("span");
      caret.className = "caret" + (isOpen ? " open" : "");
      caret.textContent = "\\u25B6"; // right-pointing triangle
      row.appendChild(caret);

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

      const meta = document.createElement("div");
      meta.className = "server-meta grow";
      meta.textContent = makeUrl(s.name, false);
      // Reverse-proxy mount (when configured) on the same line as the
      // MCP URL. Linkified to the live mount when the backend is up.
      const rp = s.spec && s.spec.reverseProxy;
      if (rp && rp.mount && rp.upstream) {
        meta.appendChild(document.createTextNode(" \\u00B7 Proxy: "));
        const mountLabel = rp.mount + "/*";
        if (s.running) {
          const link = document.createElement("a");
          link.href = rp.mount + "/";
          link.target = "_blank";
          link.rel = "noopener";
          link.textContent = mountLabel;
          // Don't trigger row expansion when the user clicks the link.
          link.onclick = (ev) => ev.stopPropagation();
          meta.appendChild(link);
        } else {
          meta.appendChild(document.createTextNode(mountLabel));
        }
        meta.appendChild(document.createTextNode(" \\u2192 " + rp.upstream));
      }
      row.appendChild(meta);

      // The always-on builtin can't be started/stopped from the UI; show a
      // disabled marker pill instead of a toggle button.
      if (s.builtin) {
        const pill = document.createElement("span");
        pill.className = "pill";
        pill.textContent = "always-on";
        row.appendChild(pill);
      } else {
        const btn = document.createElement("button");
        btn.className = "btn btn-mini " + (s.running ? "btn-outline" : "btn-primary");
        btn.textContent = s.running ? "Stop" : "Start";
        btn.onclick = (ev) => {
          ev.stopPropagation();
          toggleServer(s.name, s.running);
        };
        row.appendChild(btn);
      }

      // Click anywhere on the row toggles the inline catalog block.
      row.onclick = () => toggleServerCatalog(s.name);
      entry.appendChild(row);

      const catalogBox = document.createElement("div");
      catalogBox.className = "server-catalog" + (isOpen ? "" : " hidden");
      catalogBox.id = "catalog-" + cssEscape(s.name);
      entry.appendChild(catalogBox);
      serverList.appendChild(entry);

      // Populate the catalog block immediately if we already have data
      // for this backend (cheap; the box is hidden when not expanded).
      // getElementById requires the entry to already be in the document.
      if (currentCatalog[s.name]) renderServerCatalog(s.name);
      else if (isOpen) catalogBox.textContent = "Loading...";
    }
  }

  // ID-safe version of a server name for HTML id attributes. Server
  // names already match `[A-Za-z0-9._-]+` per config.py, so this is
  // belt-and-braces.
  function cssEscape(s) {
    return String(s).replace(/[^A-Za-z0-9_-]/g, "_");
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
      mcpSnippetAggregate.textContent = buildAggregateSnippet(data);
      refreshCursorRule(data);
      refreshCatalog(data);
      syncConfigInput(data);
      updateUI();
    } catch (err) {
      addLog("ERROR: " + err.message);
    }
  }

  // While the system is running, reflect the actually-loaded config in the
  // (disabled) textarea so the user can see exactly what's live. When
  // stopped, leave the textarea alone so any edits-in-progress survive.
  function syncConfigInput(status) {
    if (!status.running) return;
    const cfg = buildConfigFromStatus(status);
    if (!cfg) return;
    const text = JSON.stringify(cfg, null, 2);
    if (configInput.value !== text) {
      configInput.value = text;
    }
  }

  // Reconstruct a Cursor mcp.json-shape config from /api/status. Skips the
  // always-on builtin (it isn't user-configured) and emits the same field
  // shape that POST /api/start accepts:
  //   stdio   -> { command, args?, env?, cwd? }    (no `type`)
  //   sse     -> { type: "sse", url, headers? }
  //   http    -> { type: "streamable-http", url, headers? }
  function buildConfigFromStatus(status) {
    const out = { mcpServers: {} };
    for (const s of status.servers || []) {
      if (s.builtin) continue;
      const spec = s.spec || {};
      let entry;
      if (spec.command) {
        entry = { command: spec.command };
        if (Array.isArray(spec.args) && spec.args.length) entry.args = spec.args.slice();
        if (spec.env && Object.keys(spec.env).length) entry.env = { ...spec.env };
        if (spec.cwd) entry.cwd = spec.cwd;
      } else if (spec.url) {
        entry = {
          type: spec.transport === "sse" ? "sse" : "streamable-http",
          url: spec.url,
        };
        if (spec.headers && Object.keys(spec.headers).length) entry.headers = { ...spec.headers };
      } else {
        continue;
      }
      out.mcpServers[s.name] = entry;
    }
    return Object.keys(out.mcpServers).length ? out : null;
  }

  // Refetch the generated Cursor rule from /api/cursor-rule whenever the
  // running-backends set OR the access-mode selector changes. Signature
  // includes `access:` so toggling the dropdown forces a refetch.
  async function refreshCursorRule(status) {
    const access = ruleAccessValue();
    const sig =
      "access:" + access + "|" +
      (status.servers || [])
        .map((s) => s.name + ":" + (s.running ? "1" : "0"))
        .join(",");
    if (sig === lastRuleSig) return;
    lastRuleSig = sig;
    try {
      const params = new URLSearchParams({ access });
      const r = await fetch("/api/cursor-rule?" + params.toString());
      cursorRule.textContent = await r.text();
    } catch (err) {
      cursorRule.textContent = "(failed to fetch /api/cursor-rule: " + err.message + ")";
    }
  }

  function ruleAccessValue() {
    const sel = document.getElementById("rule-access");
    return sel && sel.value === "read-write" ? "read-write" : "read-only";
  }

  // Triggered by the `<select id="rule-access">`. Forces a re-fetch by
  // clearing the cached signature, then runs refreshCursorRule against
  // the latest status snapshot.
  function onRuleAccessChange() {
    lastRuleSig = null;
    cursorRule.textContent = "Loading...";
    refreshCursorRule(currentStatus);
  }

  // Refetch /api/catalog whenever the running-backends set changes (same
  // signature pattern as refreshCursorRule). On success, re-render any
  // currently-expanded inline catalog blocks.
  async function refreshCatalog(status) {
    const sig = (status.servers || [])
      .map((s) => s.name + ":" + (s.running ? "1" : "0"))
      .join(",");
    if (sig === lastCatalogSig) return;
    if (catalogInFlight) return;
    lastCatalogSig = sig;
    catalogInFlight = true;
    try {
      const r = await fetch("/api/catalog");
      currentCatalog = await r.json();
      for (const name of expandedRows) renderServerCatalog(name);
    } catch (err) {
      // Don't blank existing data on a transient failure; just log.
      addLog("ERROR: /api/catalog: " + err.message);
    } finally {
      catalogInFlight = false;
    }
  }

  function toggleServerCatalog(name) {
    const box = document.getElementById("catalog-" + cssEscape(name));
    if (!box) return;
    const opening = !expandedRows.has(name);
    if (opening) expandedRows.add(name);
    else expandedRows.delete(name);
    box.classList.toggle("hidden", !opening);
    // Rotate the caret. The row is the previous sibling.
    const row = box.previousElementSibling;
    const caret = row ? row.querySelector(".caret") : null;
    if (caret) caret.classList.toggle("open", opening);
    if (opening) {
      if (currentCatalog[name]) renderServerCatalog(name);
      else box.textContent = "Loading...";
    }
  }

  // Build the inline catalog block for one backend from currentCatalog.
  function renderServerCatalog(name) {
    const box = document.getElementById("catalog-" + cssEscape(name));
    if (!box) return;
    const data = currentCatalog[name];
    box.innerHTML = "";
    if (!data) {
      box.textContent = "No catalog data available.";
      return;
    }
    const KINDS = [
      ["tools", "Tools"],
      ["prompts", "Prompts"],
      ["resources", "Resources"],
      ["resourceTemplates", "Resource templates"],
    ];
    let any = false;
    for (const [kind, label] of KINDS) {
      const raw = data[kind];
      const group = document.createElement("div");
      group.className = "cat-group";
      const head = document.createElement("div");
      head.className = "cat-label";
      if (Array.isArray(raw)) {
        head.textContent = `${label} (${raw.length})`;
        group.appendChild(head);
        if (raw.length === 0) {
          const p = document.createElement("p");
          p.className = "empty";
          p.textContent = "(none)";
          group.appendChild(p);
        } else {
          const ul = document.createElement("ul");
          for (const item of raw) ul.appendChild(buildCatalogItem(kind, item));
          group.appendChild(ul);
          any = true;
        }
      } else if (raw && raw.error) {
        head.textContent = label;
        group.appendChild(head);
        const p = document.createElement("p");
        p.className = "empty";
        p.textContent = `Error: ${raw.error}`;
        group.appendChild(p);
      } else {
        continue;
      }
      box.appendChild(group);
    }
    if (!any && box.childElementCount === 0) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "Backend advertised no tools, prompts, or resources.";
      box.appendChild(p);
    }
  }

  function buildCatalogItem(kind, item) {
    const li = document.createElement("li");
    const code = document.createElement("code");
    code.textContent = item.name || item.uri || "(unnamed)";
    li.appendChild(code);
    if (item.description) {
      const d = document.createElement("div");
      d.className = "desc";
      d.textContent = item.description;
      li.appendChild(d);
    }
    if (kind === "tools" && item.inputSchema) {
      const det = document.createElement("details");
      const sum = document.createElement("summary");
      sum.textContent = "schema";
      det.appendChild(sum);
      const pre = document.createElement("pre");
      pre.textContent = JSON.stringify(item.inputSchema, null, 2);
      det.appendChild(pre);
      li.appendChild(det);
    }
    if (kind === "prompts" && Array.isArray(item.arguments) && item.arguments.length) {
      const args = document.createElement("div");
      args.className = "desc";
      args.textContent = "args: " + item.arguments.map(a =>
        a.name + (a.required ? "" : "?") + (a.description ? `: ${a.description}` : "")
      ).join(" • ");
      li.appendChild(args);
    }
    if ((kind === "resources" || kind === "resourceTemplates")) {
      const meta = [];
      if (item.mimeType) meta.push(`mime: ${item.mimeType}`);
      if (item.uriTemplate) meta.push(`template: ${item.uriTemplate}`);
      if (meta.length) {
        const d = document.createElement("div");
        d.className = "desc";
        d.textContent = meta.join(" • ");
        li.appendChild(d);
      }
    }
    return li;
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

  function copyAggregateSnippet() {
    const text = mcpSnippetAggregate.textContent;
    const btn = document.getElementById("copy-aggregate-btn");
    navigator.clipboard.writeText(text).then(() => {
      btn.textContent = "Copied";
      btn.classList.add("copied");
      setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 2000);
    });
  }

  function copyRule() {
    const text = cursorRule.textContent;
    const btn = document.getElementById("copy-rule-btn");
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


# ── Standalone catalog page (served at /catalog) ──────────────────────

CATALOG_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LOCALMCP — Tool catalog</title>
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

  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: var(--font);
    font-size: 16px;
    line-height: 1.5;
    color: var(--black);
    background: var(--white);
    -webkit-font-smoothing: antialiased;
  }

  .container {
    max-width: 1080px;
    margin: 0 auto;
    padding: 48px 24px;
  }

  .header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
  }

  .wordmark {
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .header-spacer { flex: 1; }

  .docs-link {
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--black);
    text-decoration: none;
    padding: 6px 12px;
    border: 1px solid var(--border);
    border-radius: 999px;
  }
  .docs-link:hover { background: var(--surface); }

  .subtitle {
    color: var(--mid);
    margin-bottom: 24px;
    font-size: 15px;
  }

  .filter {
    width: 100%;
    padding: 12px 16px;
    border: 1px solid var(--border);
    border-radius: 12px;
    font-family: var(--mono);
    font-size: 14px;
    margin-bottom: 32px;
    background: var(--white);
  }
  .filter:focus {
    outline: none;
    border-color: var(--black);
  }

  .server-block {
    margin-bottom: 40px;
    padding: 20px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--white);
  }
  .server-head {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
  }
  .server-head h2 {
    font-family: var(--mono);
    font-size: 18px;
    font-weight: 700;
  }
  .server-head .meta {
    color: var(--mid);
    font-size: 13px;
  }

  .group {
    margin-top: 20px;
  }
  .group h3 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--mid);
    margin-bottom: 10px;
  }
  .group ul {
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .item {
    border-left: 2px solid var(--border);
    padding-left: 12px;
  }
  .item code {
    font-family: var(--mono);
    font-size: 14px;
    font-weight: 700;
    color: var(--black);
  }
  .item .desc {
    color: var(--mid);
    font-size: 14px;
    margin-top: 2px;
    white-space: pre-wrap;
  }
  .item pre {
    margin-top: 8px;
    background: var(--surface);
    padding: 12px;
    border-radius: 8px;
    font-family: var(--mono);
    font-size: 12px;
    line-height: 1.45;
    overflow-x: auto;
  }
  .item .args {
    margin-top: 4px;
    color: var(--mid);
    font-family: var(--mono);
    font-size: 12px;
  }

  .empty {
    color: var(--mid);
    font-size: 13px;
    font-style: italic;
  }

  .error-banner {
    background: var(--error);
    color: var(--white);
    padding: 16px;
    border-radius: 8px;
    margin-bottom: 24px;
  }

  /* Print friendliness — strip the colored bits and decoration. */
  @media print {
    .filter, .docs-link { display: none; }
    .server-block { break-inside: avoid; border: none; padding: 0; }
    .item pre { background: transparent; border: 1px solid #ccc; }
  }

  .hidden { display: none !important; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <span class="wordmark">LOCALMCP</span>
    <span class="header-spacer"></span>
    <a class="docs-link" href="/" target="_blank" rel="noopener">Web UI</a>
    <a class="docs-link" href="/docs" target="_blank" rel="noopener">API Docs</a>
  </div>
  <p class="subtitle">Tool catalog &mdash; live, read-only documentation of every running backend.</p>

  <input
    type="text"
    class="filter"
    id="filter"
    placeholder="Filter by tool / prompt / resource name (case-insensitive)"
    autocomplete="off"
    spellcheck="false"
  />

  <div id="root">Loading...</div>
</div>

<script>
  const filterInput = document.getElementById("filter");
  const root = document.getElementById("root");
  let catalog = {};

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    if (props) for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else node.setAttribute(k, v);
    }
    for (const c of children) {
      if (c == null) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  }

  function renderItem(kind, item) {
    const li = el("li", { class: "item", "data-name": (item.name || item.uri || "") });
    const head = el("div", null, el("code", null, item.name || item.uri || "(unnamed)"));
    li.appendChild(head);
    if (item.description) li.appendChild(el("div", { class: "desc" }, item.description));
    if (kind === "tools" && item.inputSchema) {
      const det = el("details");
      det.appendChild(el("summary", null, "schema"));
      det.appendChild(el("pre", null, JSON.stringify(item.inputSchema, null, 2)));
      li.appendChild(det);
    }
    if (kind === "prompts" && Array.isArray(item.arguments) && item.arguments.length) {
      const args = item.arguments.map(a =>
        a.name + (a.required ? "" : "?") + (a.description ? `: ${a.description}` : "")
      ).join(" • ");
      li.appendChild(el("div", { class: "args" }, args));
    }
    if (kind === "resources" || kind === "resourceTemplates") {
      const meta = [];
      if (item.mimeType) meta.push(`mime: ${item.mimeType}`);
      if (item.uriTemplate) meta.push(`template: ${item.uriTemplate}`);
      if (meta.length) li.appendChild(el("div", { class: "args" }, meta.join(" • ")));
    }
    return li;
  }

  const KINDS = [
    ["tools", "Tools"],
    ["prompts", "Prompts"],
    ["resources", "Resources"],
    ["resourceTemplates", "Resource templates"],
  ];

  function renderCatalog(filterText) {
    root.innerHTML = "";
    const needle = (filterText || "").trim().toLowerCase();
    const names = Object.keys(catalog);
    if (names.length === 0) {
      root.appendChild(el("p", { class: "empty" }, "No backends are currently running."));
      return;
    }
    let totalShown = 0;
    for (const name of names) {
      const block = el("div", { class: "server-block" });
      const transport = catalog[name].transport || "unknown";
      block.appendChild(el("div", { class: "server-head" },
        el("h2", null, name),
        el("span", { class: "meta" }, `(${transport})`),
      ));
      let blockShown = 0;
      for (const [kind, label] of KINDS) {
        const raw = catalog[name][kind];
        if (Array.isArray(raw)) {
          const items = needle
            ? raw.filter(it => ((it.name || it.uri || "") + " " + (it.description || ""))
                .toLowerCase().includes(needle))
            : raw;
          if (raw.length === 0) {
            const group = el("div", { class: "group" },
              el("h3", null, `${label} (0)`),
              el("p", { class: "empty" }, "(none)"),
            );
            if (!needle) block.appendChild(group);
          } else if (items.length > 0) {
            const ul = el("ul");
            for (const item of items) ul.appendChild(renderItem(kind, item));
            block.appendChild(el("div", { class: "group" },
              el("h3", null, `${label} (${items.length}${items.length !== raw.length ? ` of ${raw.length}` : ""})`),
              ul,
            ));
            blockShown += items.length;
          }
        } else if (raw && raw.error) {
          block.appendChild(el("div", { class: "group" },
            el("h3", null, label),
            el("p", { class: "empty" }, `Error: ${raw.error}`),
          ));
        }
      }
      // Hide whole block if filter matched nothing in it.
      if (!needle || blockShown > 0) {
        root.appendChild(block);
        totalShown += blockShown;
      }
    }
    if (needle && totalShown === 0) {
      root.appendChild(el("p", { class: "empty" }, `No matches for "${filterText}".`));
    }
  }

  filterInput.addEventListener("input", () => renderCatalog(filterInput.value));

  fetch("/api/catalog")
    .then(r => r.json())
    .then(data => { catalog = data; renderCatalog(""); })
    .catch(err => {
      root.innerHTML = "";
      root.appendChild(el("div", { class: "error-banner" }, `Failed to load /api/catalog: ${err.message}`));
    });
</script>
</body>
</html>
"""
