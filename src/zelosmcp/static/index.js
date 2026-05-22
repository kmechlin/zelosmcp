  const badge = document.getElementById("badge");
  const actionBtn = document.getElementById("action-btn");
  const configInput = document.getElementById("config-input");
  const logViewer = document.getElementById("log-viewer");
  const logFilter = document.getElementById("log-filter");
  let currentLogFilter = "";

  function logLineMatchesFilter(text) {
    if (!currentLogFilter) return true;
    return text.toLowerCase().indexOf(currentLogFilter) !== -1;
  }

  function applyLogFilter() {
    const lines = logViewer.querySelectorAll(".log-line");
    let lastVisible = null;
    lines.forEach((el) => {
      const match = logLineMatchesFilter(el.textContent || "");
      el.classList.toggle("hidden", !match);
      if (match) lastVisible = el;
    });
    if (lastVisible) {
      logViewer.scrollTop = logViewer.scrollHeight;
    }
  }

  if (logFilter) {
    logFilter.addEventListener("input", () => {
      currentLogFilter = logFilter.value.trim().toLowerCase();
      applyLogFilter();
    });
  }
  const mcpSnippet = document.getElementById("mcp-snippet");
  const mcpSnippetAggregate = document.getElementById("mcp-snippet-aggregate");
  const cursorRule = document.getElementById("cursor-rule");
  let lastRuleSig = null;
  const serversSection = document.getElementById("servers-section");
  const serverList = document.getElementById("server-list");

  let running = false;
  let loading = false;
  let currentStatus = { servers: [], primary: null, running: false };
  // Per-provider auth status keyed by provider name; populated from
  // /api/auth/providers. Used to colour the auth-provider pill in the
  // server list. Refreshed alongside the status poll so pills react when
  // the user connects via the Connections view.
  let currentAuthProviders = {};  // { providerName: {ready, identity, type, ...} }
  let lastAuthProvidersSig = null;
  // Inline-catalog state. Survives refreshStatus polls so a row the
  // user expanded earlier stays open when status updates re-render.
  // Resets on page reload (no localStorage by design).
  const expandedRows = new Set();
  let currentCatalog = {};
  let lastCatalogSig = null;
  let catalogInFlight = false;
  // Center-pane "Server details" selection. Cleared on page reload.
  let currentDetailsServer = null;
  // Documentation view state — index fetched lazily on first activation.
  let docsIndex = null;
  let currentDocSlug = null;
  let docsLoadInflight = false;

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
  // Prefix them with `zelosmcp-` so it's obvious in Cursor's UI which entries
  // come from this proxy and so they don't collide with backend names a user
  // already has configured directly in their `mcp.json`.
  const SNIPPET_PREFIX = "zelosmcp-";

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
  // builtin's tools (`zelosmcp__*`) are always available here regardless
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
    // The servers card lives in the right column now, so don't hide it
    // when there are no backends — show a placeholder instead so the
    // column doesn't collapse into an empty stub.
    serversSection.classList.remove("hidden");
    serverList.innerHTML = "";
    if (servers.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.style.color = "var(--mid)";
      empty.style.fontSize = "13px";
      empty.style.fontStyle = "italic";
      empty.textContent = "No servers running. Apply a config from the Configuration view to start backends.";
      serverList.appendChild(empty);
      return;
    }
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

      // Auth-provider indicator. Shown for any backend whose spec.auth
      // references a provider, regardless of passthrough mode. Colour
      // reflects whether the current user has connected the provider:
      //   - green  (auth-connected)    : provider exists and ready=true
      //   - yellow (auth-disconnected) : provider exists but not connected
      //   - red    (auth-missing)      : spec references an unknown provider
      const authProviderName =
        (s.spec && s.spec.auth && s.spec.auth.provider) || s.auth_provider;
      if (authProviderName) {
        const auth = document.createElement("span");
        const provider = currentAuthProviders[authProviderName];
        if (provider == null) {
          auth.className = "pill auth-missing";
          auth.textContent = `auth: ${authProviderName} \\u2718`;
          auth.title =
            `Backend references provider '${authProviderName}' but the ` +
            `provider is not configured. Check configs/auth-providers.json.`;
        } else if (provider.ready) {
          auth.className = "pill auth-connected";
          const who = provider.identity && provider.identity.username
            ? ` (${provider.identity.username})` : "";
          auth.textContent = `auth: ${authProviderName} \\u2713`;
          auth.title = `Connected to ${authProviderName}${who}. ` +
            `Open the Connections view to disconnect or refresh.`;
        } else {
          auth.className = "pill auth-disconnected";
          auth.textContent = `auth: ${authProviderName} \\u26A0`;
          auth.title = `Provider '${authProviderName}' is configured but ` +
            `not connected. Open the Connections view to authenticate.`;
        }
        // Click → jump to Connections view so the user can act on it.
        auth.style.cursor = "pointer";
        auth.onclick = (ev) => {
          ev.stopPropagation();
          setView("connections");
        };
        row.appendChild(auth);
      }

      // OAuth-passthrough indicator. Shown for any backend running in
      // passthrough mode so the operator knows requests forward to the
      // upstream issuer rather than terminating in zelosmcp. The
      // tooltip explains what the auth_state values mean.
      if (s.passthrough) {
        const pt = document.createElement("span");
        pt.className = "pill";
        pt.textContent = "passthrough";
        const authState = s.auth_state || "unknown";
        if (authState === "static_bearer") {
          pt.title = "Passthrough mode with a static fallback bearer token. " +
            "Inbound Authorization wins; the static token is injected only " +
            "when the caller has none.";
        } else if (authState === "needs_inbound_token") {
          pt.title = "Passthrough mode without a static fallback. The MCP " +
            "client must perform OAuth directly with the upstream issuer; " +
            "zelosmcp forwards the resulting Authorization header verbatim.";
        } else {
          pt.title = "Passthrough mode: inbound Authorization is forwarded " +
            "to the upstream MCP server.";
        }
        row.appendChild(pt);
      }

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

      // "Details" button — opens the same catalog payload in the center
      // pane (wider than the inline expansion below the row). Available
      // on every server, including the always-on builtin.
      const detailsBtn = document.createElement("button");
      detailsBtn.className = "btn btn-mini btn-outline";
      detailsBtn.textContent = "Details";
      detailsBtn.onclick = (ev) => {
        ev.stopPropagation();
        showServerDetails(s.name);
      };
      row.appendChild(detailsBtn);

      // "Assets" button — opens the asset editor pre-filtered to this backend.
      const assetsBtn = document.createElement("button");
      assetsBtn.className = "btn btn-mini btn-outline";
      assetsBtn.textContent = "Assets";
      assetsBtn.title = `View / edit assets for the ${s.name} backend`;
      assetsBtn.onclick = (ev) => {
        ev.stopPropagation();
        showBackendAssets(s.name);
      };
      row.appendChild(assetsBtn);

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
      // Fetch auth providers in parallel — we want the pill to colour
      // correctly on the very first render (not just after a poll cycle).
      // Skip the network call when the status reports zero auth-using
      // backends to avoid noise on simple deployments.
      const providerNames = new Set();
      for (const s of data.servers || []) {
        const p = (s.spec && s.spec.auth && s.spec.auth.provider) || s.auth_provider;
        if (p) providerNames.add(p);
      }
      if (providerNames.size > 0) {
        await refreshAuthProviders();
      } else {
        currentAuthProviders = {};
      }
      renderServers(data);
      mcpSnippet.textContent = buildSnippet(data);
      mcpSnippetAggregate.textContent = buildAggregateSnippet(data);
      refreshCursorRule(data);
      refreshCatalog(data);
      syncConfigInput(data);
      refreshPincherDashboard(false);
      updateUI();
    } catch (err) {
      addLog("ERROR: " + err.message);
    }
  }

  // Fetches /api/auth/providers and updates currentAuthProviders. Cheap to
  // call repeatedly — no work happens when the response signature is
  // unchanged so the pill doesn't flicker on every status poll.
  async function refreshAuthProviders() {
    try {
      const r = await fetch("/api/auth/providers");
      if (!r.ok) return;
      const body = await r.json();
      const list = Array.isArray(body.providers) ? body.providers : [];
      const sig = list.map((p) => `${p.name}:${p.ready ? 1 : 0}`).join(",");
      if (sig === lastAuthProvidersSig) return;
      lastAuthProvidersSig = sig;
      const map = {};
      for (const p of list) map[p.name] = p;
      currentAuthProviders = map;
    } catch (_) {
      // Silently ignore — pill falls back to disconnected on missing data.
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
  // reverseProxy and compress are included when present so a round-trip
  // through the textarea preserves infrastructure settings (e.g. the
  // pincher dashboard mount). reverseProxy.auth is intentionally omitted
  // because the status API masks bearer values as "***" and re-submitting
  // that literal string would break authentication.
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
      if (spec.reverseProxy) {
        // Strip the masked auth block so "***" is never re-submitted as a
        // bearer token. The field is omitted entirely; users who need auth
        // on the reverseProxy must supply it explicitly.
        const { auth: _auth, ...rpRest } = spec.reverseProxy;
        entry.reverseProxy = rpRest;
      }
      if (spec.compress) entry.compress = { ...spec.compress };
      out.mcpServers[s.name] = entry;
    }
    return Object.keys(out.mcpServers).length ? out : null;
  }

  // ── IDE tab state ────────────────────────────────────────────────────

  // The currently-selected IDE tab in the global Rules view.
  let globalRuleFmt = "cursor-mdc";

  // Returns the fmt query value for the currently-selected global tab.
  function globalRuleFormat() { return globalRuleFmt; }

  // Called when the user clicks a tab in the global Rules view.
  function onGlobalRuleTabClick(btn) {
    document.querySelectorAll(".rule-ide-tab[data-fmt]").forEach((b) => {
      b.classList.toggle("active", b === btn);
    });
    globalRuleFmt = btn.dataset.fmt || "cursor-mdc";
    // Toggle hint text.
    const hintCursor = document.getElementById("rule-tab-hint-cursor");
    const hintVscode = document.getElementById("rule-tab-hint-vscode");
    if (hintCursor) hintCursor.style.display = globalRuleFmt === "cursor-mdc" ? "" : "none";
    if (hintVscode) hintVscode.style.display = globalRuleFmt === "copilot-instructions" ? "" : "none";
    lastRuleSig = null;
    cursorRule.textContent = "Loading...";
    refreshCursorRule(currentStatus);
  }

  // The currently-selected IDE tab in the repo-details panel.
  let repoIdeTarget = "cursor";

  // Called when the user clicks a tab in the repo-details panel.
  function onRepoIdeTabClick(btn) {
    document.querySelectorAll(".rule-ide-tab[data-ide]").forEach((b) => {
      b.classList.toggle("active", b === btn);
    });
    repoIdeTarget = btn.dataset.ide || "cursor";
    _updateRepoIdeDependentUI();
    // Always refresh the preview and persist the choice.
    triggerPreviewRefresh();
    _persistRepoPrefs();
  }

  // Called by onchange on the tool-use and access dropdowns.
  function onRepoControlChange() {
    triggerPreviewRefresh();
    _persistRepoPrefs();
  }

  // Debounce token for globs oninput.
  let _globsDebounce = null;

  // Called by oninput on the globs text field.
  function onRepoGlobsInput() {
    onRepoStyleChange(); // keep globs enabled/disabled state consistent
    if (_globsDebounce) clearTimeout(_globsDebounce);
    _globsDebounce = setTimeout(() => {
      triggerPreviewRefresh();
      _persistRepoPrefs();
    }, 300);
  }

  // Trigger a preview refresh. No-ops when no project is open.
  function triggerPreviewRefresh() {
    if (!currentDetailsRepo) return;
    previewRepoRule();
  }

  // Persist the current dropdown values to the server via PUT /api/repos/prefs.
  // Debounced so rapid changes don't flood the server.
  let _prefsDebounce = null;
  function _persistRepoPrefs() {
    if (!currentDetailsRepo) return;
    if (_prefsDebounce) clearTimeout(_prefsDebounce);
    _prefsDebounce = setTimeout(async () => {
      try {
        const tu = document.getElementById("repo-rule-tool-use")?.value || "priority";
        const ac = document.getElementById("repo-rule-access")?.value || "read-only";
        const st = document.getElementById("repo-rule-style")?.value || "always-apply";
        const gl = document.getElementById("repo-rule-globs")?.value || "";
        const targets = repoIdeTarget === "vscode" ? ["vscode"] : ["cursor", "vscode"];
        await fetch("/api/repos/prefs", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: currentDetailsRepo.path_ro,
            tool_use: tu,
            access: ac,
            style: st,
            globs: gl,
            targets,
          }),
        });
      } catch (_) {
        // Best-effort; silently ignore errors.
      }
    }, 500);
  }

  // Copy the rule preview text to clipboard.
  function copyRepoRulePreview() {
    const pre = document.getElementById("repo-rule-preview");
    if (!pre) return;
    navigator.clipboard.writeText(pre.textContent || "").catch(() => {});
  }

  function _updateRepoIdeDependentUI() {
    // Show/hide Cursor-only controls (style + globs).
    const cursorOnlyControls = document.getElementById("repo-cursor-only-controls");
    if (cursorOnlyControls) {
      cursorOnlyControls.style.display = repoIdeTarget === "cursor" ? "contents" : "none";
    }
    // Update path hint.
    const hint = document.getElementById("repo-rule-path-hint");
    if (hint) {
      hint.textContent = repoIdeTarget === "cursor"
        ? "Writes .cursor/rules/zelosmcp.mdc"
        : "Writes .github/copilot-instructions.md";
    }
  }

  // ── Global rule preview ──────────────────────────────────────────────

  // Refetch the generated rule from /api/cursor-rule whenever the
  // running-backends set OR the access / tool-use selectors change. The
  // signature includes both control values so toggling either dropdown
  // forces a refetch.
  async function refreshCursorRule(status) {
    const access = ruleAccessValue();
    const toolUse = ruleToolUseValue();
    const fmt = globalRuleFormat();
    const sig =
      "fmt:" + fmt + "|access:" + access + "|tool_use:" + toolUse + "|" +
      (status.servers || [])
        .map((s) => s.name + ":" + (s.running ? "1" : "0"))
        .join(",");
    if (sig === lastRuleSig) return;
    lastRuleSig = sig;
    try {
      const params = new URLSearchParams({ format: fmt, access, tool_use: toolUse });
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

  function ruleToolUseValue() {
    const sel = document.getElementById("rule-tool-use");
    return sel && sel.value === "available" ? "available" : "priority";
  }

  // Triggered by the `<select id="rule-access">`. Forces a re-fetch by
  // clearing the cached signature, then runs refreshCursorRule against
  // the latest status snapshot.
  function onRuleAccessChange() {
    lastRuleSig = null;
    cursorRule.textContent = "Loading...";
    refreshCursorRule(currentStatus);
  }

  // Triggered by the `<select id="rule-tool-use">`. Same shape as
  // onRuleAccessChange — clears the cached signature and refetches.
  function onRuleToolUseChange() {
    lastRuleSig = null;
    cursorRule.textContent = "Loading...";
    refreshCursorRule(currentStatus);
  }

  // Refetch /api/catalog whenever the running-backends set or auth readiness
  // changes. OAuth-passthrough catalogs can become available after a provider
  // connects even when the backend process itself never restarted.
  // On success, re-render any
  // currently-expanded inline catalog blocks AND the center-pane
  // "Server details" view if one is currently selected.
  async function refreshCatalog(status) {
    const sig = (status.servers || [])
      .map((s) => [
        s.name,
        s.running ? "1" : "0",
        s.auth_state || "",
        s.auth_provider || "",
      ].join(":"))
      .join(",");
    if (sig === lastCatalogSig) return;
    if (catalogInFlight) return;
    lastCatalogSig = sig;
    catalogInFlight = true;
    try {
      const r = await fetch("/api/catalog");
      currentCatalog = await r.json();
      for (const name of expandedRows) renderServerCatalog(name);
      if (currentDetailsServer) renderServerDetails(currentDetailsServer);
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

  // Shared DOM builder used by BOTH the inline catalog box and the
  // center-pane "Server details" view. Empties `target` and refills it
  // with one .cat-group per non-empty kind.
  function populateCatalogInto(target, name) {
    const data = currentCatalog[name];
    target.innerHTML = "";
    if (!data) {
      target.textContent = "No catalog data available.";
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
      target.appendChild(group);
    }
    if (!any && target.childElementCount === 0) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "Backend advertised no tools, prompts, or resources.";
      target.appendChild(p);
    }
  }

  function renderServerCatalog(name) {
    const box = document.getElementById("catalog-" + cssEscape(name));
    if (!box) return;
    populateCatalogInto(box, name);
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

  // ── Server details (center pane) ────────────────────────────────────
  // Per-server "Details" button switches the center pane to this view
  // and renders the catalog payload there. Survives /api/catalog
  // refreshes via refreshCatalog -> renderServerDetails.
  function showServerDetails(name) {
    currentDetailsServer = name;
    setView("server-details");
    renderServerDetails(name);
    refreshCatalog(currentStatus);
  }

  // ── Per-backend tabbed asset manager ────────────────────────────────

  let currentBackendAssetsName = null;
  let currentBackendAssetsTab = "rule";

  async function showBackendAssets(backendName) {
    currentBackendAssetsName = backendName;
    currentBackendAssetsTab = "rule";
    const title = document.getElementById("assets-backend-title");
    const meta = document.getElementById("assets-backend-meta");
    if (title) title.textContent = `Backend: ${backendName}`;
    if (meta) meta.textContent = "";
    // Show action buttons
    for (const id of [
      "assets-yaml-edit-btn","assets-yaml-export-btn","assets-yaml-import-label",
      "assets-backend-refresh-btn","assets-add-stub-btn",
    ]) {
      const el = document.getElementById(id);
      if (el) el.style.display = "";
    }
    setView("assets-backend");
    _activateBackendTab("rule");
    await _loadBackendAssetsTab("rule", backendName);
  }

  function refreshBackendAssets() {
    if (currentBackendAssetsName)
      _loadBackendAssetsTab(currentBackendAssetsTab, currentBackendAssetsName);
  }

  function switchBackendAssetsTab(kind) {
    if (!currentBackendAssetsName) return;
    currentBackendAssetsTab = kind;
    _activateBackendTab(kind);
    _loadBackendAssetsTab(kind, currentBackendAssetsName);
    // Add button is only meaningful for specific-kind tabs, not "All"
    const addBtn = document.getElementById("assets-add-stub-btn");
    if (addBtn) {
      addBtn.disabled = (kind === "all");
      addBtn.title = kind === "all"
        ? "Switch to a specific tab to add a row"
        : `Add a new ${kind} stub for ${currentBackendAssetsName}`;
    }
  }

  // ── YAML editor ─────────────────────────────────────────────────────

  let _yamlLintDebounce = null;

  async function openYamlEditor() {
    if (!currentBackendAssetsName) return;
    const panel = document.getElementById("assets-yaml-editor-panel");
    const ta = document.getElementById("assets-yaml-textarea");
    if (!panel || !ta) return;
    ta.value = "Loading...";
    panel.style.display = "";
    try {
      const r = await fetch(`/api/assets/yaml/${encodeURIComponent(currentBackendAssetsName)}`);
      if (!r.ok) throw new Error("HTTP " + r.status);
      ta.value = await r.text();
      lintYaml(ta.value);
    } catch (err) {
      ta.value = `# Error loading YAML: ${err.message}`;
    }
  }

  function closeYamlEditor() {
    const panel = document.getElementById("assets-yaml-editor-panel");
    if (panel) panel.style.display = "none";
    const status = document.getElementById("assets-yaml-save-status");
    if (status) status.textContent = "";
  }

  function onYamlEditorInput(ta) {
    clearTimeout(_yamlLintDebounce);
    _yamlLintDebounce = setTimeout(() => lintYaml(ta.value), 400);
  }

  async function lintYaml(text) {
    if (!currentBackendAssetsName) return;
    const statusEl = document.getElementById("assets-yaml-lint-status");
    const saveBtn = document.getElementById("assets-yaml-save-btn");
    if (!statusEl || !saveBtn) return;
    statusEl.textContent = "Validating…";
    try {
      const r = await fetch(
        `/api/assets/yaml/${encodeURIComponent(currentBackendAssetsName)}/validate`,
        { method: "POST", body: text, headers: { "Content-Type": "text/yaml" } }
      );
      const data = await r.json();
      if (data.ok) {
        statusEl.innerHTML = '<span style="color:var(--ok)">✓ Valid</span>';
        saveBtn.disabled = false;
      } else {
        const errHtml = (data.errors || []).map((e) => {
          const lineLabel = e.line ? `line ${e.line} — ` : "";
          const path = e.path ? `<code>${e.path}</code>: ` : "";
          return `<li style="cursor:pointer;" onclick="jumpToYamlLine(${e.line || 1})">${lineLabel}${path}${e.message}</li>`;
        }).join("");
        statusEl.innerHTML = `<ul style="margin:0;padding:0 0 0 16px;color:var(--warn);">${errHtml}</ul>`;
        saveBtn.disabled = true;
      }
    } catch (err) {
      statusEl.textContent = "Lint error: " + err.message;
      saveBtn.disabled = true;
    }
  }

  function jumpToYamlLine(lineNo) {
    const ta = document.getElementById("assets-yaml-textarea");
    if (!ta || !lineNo) return;
    const lines = ta.value.split("\\n");
    let pos = 0;
    for (let i = 0; i < Math.min(lineNo - 1, lines.length); i++) {
      pos += lines[i].length + 1;
    }
    ta.focus();
    ta.setSelectionRange(pos, pos + (lines[lineNo - 1] || "").length);
  }

  async function saveYamlEditor() {
    if (!currentBackendAssetsName) return;
    const ta = document.getElementById("assets-yaml-textarea");
    const status = document.getElementById("assets-yaml-save-status");
    const saveBtn = document.getElementById("assets-yaml-save-btn");
    if (!ta || !status) return;
    status.textContent = "Saving…";
    saveBtn.disabled = true;
    try {
      const r = await fetch(
        `/api/assets/yaml/${encodeURIComponent(currentBackendAssetsName)}`,
        { method: "PUT", body: ta.value, headers: { "Content-Type": "text/yaml" } }
      );
      const data = await r.json();
      if (r.ok && data.ok) {
        status.textContent = `Saved (${data.rows_written} rows).`;
        closeYamlEditor();
        _loadBackendAssetsTab(currentBackendAssetsTab, currentBackendAssetsName);
      } else {
        const errs = (data.errors || []).map((e) => `${e.path}: ${e.message}`).join("; ");
        status.textContent = "Error: " + (errs || data.error || "unknown");
        saveBtn.disabled = false;
      }
    } catch (err) {
      status.textContent = "Error: " + err.message;
      saveBtn.disabled = false;
    }
  }

  async function exportYaml() {
    if (!currentBackendAssetsName) return;
    const r = await fetch(`/api/assets/yaml/${encodeURIComponent(currentBackendAssetsName)}`);
    if (!r.ok) { alert("Export failed: HTTP " + r.status); return; }
    const text = await r.text();
    const blob = new Blob([text], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${currentBackendAssetsName}.yaml`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function importYaml(input) {
    if (!input.files || !input.files[0] || !currentBackendAssetsName) return;
    const text = await input.files[0].text();
    const ta = document.getElementById("assets-yaml-textarea");
    const panel = document.getElementById("assets-yaml-editor-panel");
    if (ta && panel) {
      panel.style.display = "";
      ta.value = text;
      lintYaml(text);
    }
    input.value = "";  // reset so same file can be re-imported
  }

  async function addStubRow() {
    if (!currentBackendAssetsName || !currentBackendAssetsTab) return;
    const name = prompt(
      `New ${currentBackendAssetsTab} name for backend '${currentBackendAssetsName}':`
    );
    if (!name) return;
    const r = await fetch(
      `/api/assets/${currentBackendAssetsTab}/${encodeURIComponent(currentBackendAssetsName)}/${encodeURIComponent(name)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: "", meta: {} }),
      }
    );
    if (!r.ok) { alert("Add failed: HTTP " + r.status); return; }
    _loadBackendAssetsTab(currentBackendAssetsTab, currentBackendAssetsName);
  }

  function _activateBackendTab(kind) {
    document.querySelectorAll(".assets-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === kind);
    });
  }

  async function _fetchBackendAssets(kind, backend) {
    // Only fetch this backend's own rows.
    // Global (zelosmcp) assets are edited by clicking Assets on the zelosmcp row.
    const resp = await fetch(`/api/assets?kind=${encodeURIComponent(kind)}&backend=${encodeURIComponent(backend)}`);
    return resp.ok ? await resp.json() : [];
  }

  async function _loadBackendAssetsTab(kind, backend) {
    const container = document.getElementById("assets-backend-content");
    if (!container) return;
    container.innerHTML = '<span class="docs-empty">Loading...</span>';

    const kindLabels = { rule: "Rules", extension: "Extensions", agent: "Agents", hook: "Hooks", skill: "Skills", all: "All" };

    try {
      let rows;
      if (kind === "all") {
        // Fetch all kinds in parallel and merge.
        const kinds = ["rule", "extension", "agent", "hook", "skill"];
        const resps = await Promise.all(
          kinds.map((k) => fetch(`/api/assets?kind=${k}&backend=${encodeURIComponent(backend)}`))
        );
        const arrays = await Promise.all(resps.map((r) => r.ok ? r.json() : []));
        rows = arrays.flat();
      } else {
        rows = await _fetchBackendAssets(kind, backend);
      }

      container.innerHTML = "";

      if (rows.length === 0) {
        const msg = backend === "zelosmcp"
          ? `No global ${kindLabels[kind] || kind} assets yet.`
          : `No ${kindLabels[kind] || kind} assets for <strong>${backend}</strong>. Click <strong>+ Add</strong> to create one, or use <strong>Edit YAML</strong>.`;
        container.innerHTML = `<span class="docs-empty">${msg}</span>`;
        return;
      }

      _renderBackendAssetGroup(container, backend, rows, kind);
    } catch (err) {
      container.innerHTML = `<span class="docs-empty" style="color:var(--warn)">Error: ${err.message}</span>`;
    }
  }

  function _renderBackendAssetGroup(container, backend, rows, kind) {
    const grp = document.createElement("div");
    grp.className = "cat-group";
    grp.style.marginBottom = "8px";
    for (const row of rows) {
      grp.appendChild(_buildAssetItem(row, kind));
    }
    container.appendChild(grp);
  }

  function renderServerDetails(name) {
    const body = document.getElementById("server-details-body");
    const titleEl = document.getElementById("server-details-title");
    const meta = document.getElementById("server-details-meta");
    if (!body) return;
    if (titleEl) titleEl.textContent = `Server: ${name}`;
    const status = (currentStatus.servers || []).find((s) => s.name === name);
    if (meta) {
      const bits = [];
      if (status) {
        bits.push(status.transport || "unknown");
        if (status.error) bits.push("error");
        else if (status.running) bits.push("running");
        else bits.push("stopped");
        bits.push(makeUrl(name, false));
      } else {
        bits.push("not registered");
      }
      meta.textContent = bits.join(" • ");
    }
    if (!currentCatalog[name]) {
      body.innerHTML = "";
      const p = document.createElement("p");
      p.className = "details-empty";
      p.textContent = status && !status.running
        ? "This server isn't running. Start it from the right column to load its catalog."
        : "Loading catalog...";
      body.appendChild(p);
      return;
    }
    populateCatalogInto(body, name);
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

  // Cap DOM growth on very long sessions. Server-side history is also
  // capped (manager._log_history maxlen=2000), so the viewer can hold
  // both the replayed history and ~3000 lines of live tail before the
  // oldest entries fall off.
  const LOG_VIEWER_MAX_LINES = 5000;

  function addLog(text) {
    const line = document.createElement("div");
    let cls = "log-line" + (text.includes("ERROR") ? " error" : "");
    const visible = logLineMatchesFilter(text);
    if (!visible) cls += " hidden";
    line.className = cls;
    line.textContent = text;
    logViewer.appendChild(line);
    while (logViewer.childElementCount > LOG_VIEWER_MAX_LINES) {
      logViewer.removeChild(logViewer.firstChild);
    }
    if (visible) logViewer.scrollTop = logViewer.scrollHeight;
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

  // ── Repositories panel (right column, collapsible) ─────────────────
  // Discovers git repos under /user_data_ro and lets the user open one in
  // the middle-pane "repo-details" view. State (collapsed flag, filter
  // value) is persisted in localStorage so the panel remembers itself
  // across reloads. The list is server-cached for 30 s; clicking the
  // refresh button (?) busts that cache and re-loads.
  let currentRepos = [];
  let currentDetailsRepo = null;
  let reposLoadInflight = null;
  let reposFilterDebounce = null;
  const REPOS_LS_EXPANDED = "zelosmcp:repos:expanded";
  const REPOS_LS_FILTER = "zelosmcp:repos:filter";

  function setReposCollapsed(collapsed, persist = true) {
    const collapse = document.getElementById("repos-collapse");
    const toggle = document.getElementById("repos-toggle");
    const caret = document.getElementById("repos-caret");
    if (!collapse || !toggle) return;
    if (collapsed) {
      collapse.setAttribute("hidden", "");
      toggle.setAttribute("aria-expanded", "false");
      if (caret) caret.innerHTML = "&#9656;";
    } else {
      collapse.removeAttribute("hidden");
      toggle.setAttribute("aria-expanded", "true");
      if (caret) caret.innerHTML = "&#9662;";
    }
    if (persist) {
      try { localStorage.setItem(REPOS_LS_EXPANDED, collapsed ? "0" : "1"); } catch (_) {}
    }
  }

  function reposIsExpanded() {
    const toggle = document.getElementById("repos-toggle");
    return !!(toggle && toggle.getAttribute("aria-expanded") === "true");
  }

  function toggleReposPanel() {
    const expanding = !reposIsExpanded();
    setReposCollapsed(!expanding);
    if (expanding) loadRepos({ silent: false });
  }

  async function loadRepos({ force = false, silent = true } = {}) {
    if (reposLoadInflight) return reposLoadInflight;
    const list = document.getElementById("repos-list");
    if (!silent && list && currentRepos.length === 0) {
      list.innerHTML = '<div class="repos-empty">Loading...</div>';
    }
    const url = force ? "/api/repos?refresh=1" : "/api/repos";
    reposLoadInflight = (async () => {
      try {
        const r = await fetch(url);
        if (!r.ok) throw new Error("HTTP " + r.status);
        const data = await r.json();
        currentRepos = Array.isArray(data.repos) ? data.repos : [];
        renderReposList();
      } catch (err) {
        if (list) {
          list.innerHTML = "";
          const p = document.createElement("div");
          p.className = "repos-empty";
          p.textContent = "Failed to load repos: " + err.message;
          list.appendChild(p);
        }
      } finally {
        reposLoadInflight = null;
      }
    })();
    return reposLoadInflight;
  }

  function refreshRepos() {
    if (!reposIsExpanded()) setReposCollapsed(false);
    loadRepos({ force: true, silent: false });
  }

  function reposFilterValue() {
    const input = document.getElementById("repos-filter");
    return (input && input.value || "").trim().toLowerCase();
  }

  function onReposFilter() {
    const input = document.getElementById("repos-filter");
    if (input) {
      try { localStorage.setItem(REPOS_LS_FILTER, input.value); } catch (_) {}
    }
    if (reposFilterDebounce) clearTimeout(reposFilterDebounce);
    reposFilterDebounce = setTimeout(renderReposList, 80);
  }

  function renderReposList() {
    const list = document.getElementById("repos-list");
    const count = document.getElementById("repos-count");
    if (!list) return;
    const q = reposFilterValue();
    let filtered = q
      ? currentRepos.filter((r) => {
          const hay = (r.name + " " + r.path_ro).toLowerCase();
          return hay.includes(q);
        })
      : [...currentRepos];

    // Sort: repos with rules first, then alphabetically by name.
    filtered.sort((a, b) => {
      if (a.has_rule !== b.has_rule) return a.has_rule ? -1 : 1;
      return a.name.localeCompare(b.name);
    });

    list.innerHTML = "";
    if (count) {
      if (q) count.textContent = `${filtered.length} / ${currentRepos.length}`;
      else count.textContent = String(currentRepos.length);
    }
    if (filtered.length === 0) {
      const p = document.createElement("div");
      p.className = "repos-empty";
      p.textContent = currentRepos.length === 0
        ? "No git repos under /user_data_ro."
        : "No repos match the filter.";
      list.appendChild(p);
      return;
    }

    // Visual bucket separators between "With rules" and "Other".
    let shownRulesBucket = false;
    let shownOtherBucket = false;

    for (const repo of filtered) {
      if (repo.has_rule && !shownRulesBucket) {
        shownRulesBucket = true;
        const sep = document.createElement("div");
        sep.className = "repos-bucket-label";
        sep.textContent = "With rules";
        list.appendChild(sep);
      } else if (!repo.has_rule && !shownOtherBucket) {
        shownOtherBucket = true;
        const sep = document.createElement("div");
        sep.className = "repos-bucket-label";
        sep.textContent = shownRulesBucket ? "Other" : "No rules yet";
        list.appendChild(sep);
      }
      const row = document.createElement("button");
      row.type = "button";
      row.className = "repo-row";
      if (currentDetailsRepo && currentDetailsRepo.path_ro === repo.path_ro) {
        row.classList.add("active");
      }
      row.onclick = () => showRepoDetails(repo);

      const name = document.createElement("span");
      name.className = "repo-name";
      name.textContent = repo.name;
      row.appendChild(name);

      const path = document.createElement("span");
      path.className = "repo-path";
      path.textContent = repo.path_ro;
      path.title = repo.path_ro;
      row.appendChild(path);

      const rulePill = document.createElement("span");
      rulePill.className = "repo-pill" + (repo.has_rule ? " on" : "");
      rulePill.textContent = "rule";
      rulePill.title = repo.has_rule
        ? "zelosmcp.mdc already exists"
        : "no zelosmcp.mdc yet";
      row.appendChild(rulePill);

      // Only show pincher pill when the pincher backend is running.
      const pinchRunning = (currentStatus.servers || []).some(
        (s) => s.name === "pincher" && s.running
      );
      if (pinchRunning) {
        const piPill = document.createElement("span");
        piPill.className = "repo-pill" + (repo.pincher_indexed ? " on" : "");
        piPill.textContent = "pincher";
        piPill.title = repo.pincher_indexed
          ? "indexed in pincher"
          : "not yet indexed";
        row.appendChild(piPill);
      }

      list.appendChild(row);
    }
  }

  // ── Repo details (middle pane) ──────────────────────────────────────
  function showRepoDetails(repo) {
    currentDetailsRepo = repo;
    setView("repo-details");
    renderRepoDetails(repo);
    renderReposList(); // re-paint to mark the active row
    loadRepoAssetActions(); // populate extension run + asset push buttons
  }

  function renderRepoDetails(repo) {
    const title = document.getElementById("repo-details-title");
    const meta = document.getElementById("repo-details-meta");
    const paths = document.getElementById("repo-details-paths");
    const status = document.getElementById("repo-rule-status");
    const preview = document.getElementById("repo-rule-preview");
    if (title) title.textContent = `Repository: ${repo.name}`;
    if (meta) {
      const bits = [];
      bits.push(repo.has_rule ? "rule present" : "no rule");
      const pinchRunning = (currentStatus.servers || []).some(
        (s) => s.name === "pincher" && s.running
      );
      if (pinchRunning) {
        bits.push(repo.pincher_indexed ? "indexed" : "not indexed");
      }
      meta.textContent = bits.join(" • ");
    }
    if (paths) {
      paths.innerHTML =
        "Read-only: <code>" + escapeHtml(repo.path_ro) + "</code><br>" +
        "Read-write: <code>" + escapeHtml(repo.path_rw) + "</code>";
    }
    if (status) { status.textContent = ""; status.className = "rule-write-status"; }
    if (preview) { preview.textContent = "Loading preview…"; }
    onRepoStyleChange();
    _updateRepoIdeDependentUI();
    // Load stored prefs first, then trigger preview.
    _loadRepoPrefsAndPreview(repo);
  }

  // Load the stored prefs for a repo via GET /api/repos/prefs, populate the
  // dropdowns, then trigger the preview render.
  async function _loadRepoPrefsAndPreview(repo) {
    try {
      const r = await fetch("/api/repos/prefs?path=" + encodeURIComponent(repo.path_ro));
      if (r.ok) {
        const prefs = await r.json();
        _applyPrefsToDropdowns(prefs);
      }
    } catch (_) {
      // Silently ignore — defaults already set in HTML
    }
    previewRepoRule();
  }

  // Apply prefs from the API to the dropdown controls.
  function _applyPrefsToDropdowns(prefs) {
    const tu = document.getElementById("repo-rule-tool-use");
    const ac = document.getElementById("repo-rule-access");
    const st = document.getElementById("repo-rule-style");
    const gl = document.getElementById("repo-rule-globs");
    if (tu && prefs.tool_use) tu.value = prefs.tool_use;
    if (ac && prefs.access) ac.value = prefs.access;
    if (st && prefs.style) st.value = prefs.style;
    if (gl && prefs.globs !== undefined) gl.value = prefs.globs || "";
    onRepoStyleChange();
    _updateRepoIdeDependentUI();
    // Restore IDE target tab
    if (prefs.targets && Array.isArray(prefs.targets)) {
      const ide = prefs.targets.includes("vscode") && !prefs.targets.includes("cursor")
        ? "vscode" : "cursor";
      document.querySelectorAll(".rule-ide-tab[data-ide]").forEach((b) => {
        b.classList.toggle("active", b.dataset.ide === ide);
      });
      repoIdeTarget = ide;
      _updateRepoIdeDependentUI();
    }
  }

  function onRepoStyleChange() {
    const style = document.getElementById("repo-rule-style");
    const globs = document.getElementById("repo-rule-globs");
    if (!style || !globs) return;
    const scoped = style.value === "scoped";
    globs.disabled = !scoped;
    if (!scoped) globs.value = "";
    triggerPreviewRefresh();
    _persistRepoPrefs();
  }

  // Return the /api/cursor-rule query string for the selected IDE tab.
  function repoRuleQueryString() {
    const params = new URLSearchParams();
    const fmt = repoIdeTarget === "cursor" ? "cursor-mdc" : "copilot-instructions";
    const tu = document.getElementById("repo-rule-tool-use");
    const access = document.getElementById("repo-rule-access");
    const style = document.getElementById("repo-rule-style");
    const globs = document.getElementById("repo-rule-globs");
    params.set("format", fmt);
    if (tu) params.set("tool_use", tu.value);
    if (access) params.set("access", access.value);
    if (repoIdeTarget === "cursor") {
      if (style) params.set("style", style.value);
      if (globs && globs.value) params.set("globs", globs.value);
    }
    return params.toString();
  }

  // Return the POST body for /api/repos/write-rule.
  function repoRuleBody(targetsOverride) {
    const tu = document.getElementById("repo-rule-tool-use");
    const access = document.getElementById("repo-rule-access");
    const style = document.getElementById("repo-rule-style");
    const globs = document.getElementById("repo-rule-globs");
    const targets = targetsOverride || [repoIdeTarget];
    // For legacy compat, also send format when writing a single target.
    const fmt = targets.length === 1
      ? (targets[0] === "cursor" ? "cursor-mdc" : "copilot-instructions")
      : "cursor-mdc";
    return {
      path: currentDetailsRepo ? currentDetailsRepo.path_ro : null,
      format: fmt,
      targets,
      tool_use: tu ? tu.value : "priority",
      access: access ? access.value : "read-only",
      style: style ? style.value : "always-apply",
      globs: globs && globs.value ? globs.value : undefined,
    };
  }

  async function previewRepoRule() {
    const status = document.getElementById("repo-rule-status");
    const preview = document.getElementById("repo-rule-preview");
    if (preview) preview.textContent = "Loading...";
    if (status) { status.textContent = ""; status.className = "rule-write-status"; }
    try {
      const r = await fetch("/api/cursor-rule?" + repoRuleQueryString());
      if (!r.ok) throw new Error("HTTP " + r.status);
      const text = await r.text();
      if (preview) preview.textContent = text;
    } catch (err) {
      if (preview) preview.textContent = "";
      if (status) {
        status.className = "rule-write-status err";
        status.textContent = "Preview failed: " + err.message;
      }
    }
  }

  async function saveRepoRule() {
    if (!currentDetailsRepo) return;
    const status = document.getElementById("repo-rule-status");
    if (status) { status.className = "rule-write-status"; status.textContent = "Saving..."; }
    try {
      const r = await fetch("/api/repos/write-rule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(repoRuleBody()),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        throw new Error(data.error || ("HTTP " + r.status));
      }
      if (status) {
        status.className = "rule-write-status ok";
        status.textContent = `Saved ${data.bytes} bytes to ${data.path}`;
      }
      // Reflect "rule present" on the row without a full rescan.
      currentDetailsRepo.has_rule = true;
      const idx = currentRepos.findIndex((x) => x.path_ro === currentDetailsRepo.path_ro);
      if (idx >= 0) currentRepos[idx] = { ...currentRepos[idx], has_rule: true };
      renderReposList();
      renderRepoDetailsMetaOnly();
    } catch (err) {
      if (status) {
        status.className = "rule-write-status err";
        status.textContent = "Save failed: " + err.message;
      }
    }
  }

  // Index helper — delegates to the pincher extension invoke endpoint.
  async function indexRepo() {
    if (!currentDetailsRepo) return;
    const status = document.getElementById("repo-rule-status");
    if (status) { status.className = "rule-write-status"; status.textContent = "Indexing..."; }
    try {
      const ctx = { repo: {
        ro_path: currentDetailsRepo.path_ro,
        rw_path: currentDetailsRepo.path_rw,
        name: currentDetailsRepo.name,
      }};
      const r = await fetch("/api/assets/extension/pincher/index_project/invoke", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ctx }),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || data.message);
      if (status) {
        status.className = "rule-write-status ok";
        status.textContent = "Indexed in pincher.";
      }
      currentDetailsRepo.pincher_indexed = true;
      const idx = currentRepos.findIndex((x) => x.path_ro === currentDetailsRepo.path_ro);
      if (idx >= 0) currentRepos[idx] = { ...currentRepos[idx], pincher_indexed: true };
      renderReposList();
      renderRepoDetailsMetaOnly();
    } catch (err) {
      if (status) {
        status.className = "rule-write-status err";
        status.textContent = "Index failed: " + err.message;
      }
    }
  }

  // ── Comprehensive push ──────────────────────────────────────────────

  // Push one kind to the currently-selected IDE target (or both if
  // targetsOverride is provided).
  async function pushComprehensive(kind, targetsOverride) {
    if (!currentDetailsRepo) return;
    const status = document.getElementById("repo-rule-status");
    const targets = targetsOverride || [repoIdeTarget];
    const fmt = targets.length === 1
      ? (targets[0] === "cursor" ? "cursor-mdc" : "copilot-instructions")
      : "cursor-mdc";
    const access = document.getElementById("repo-rule-access")?.value || "read-only";
    const tool_use = document.getElementById("repo-rule-tool-use")?.value || "priority";
    const targetLabel = targets.length > 1 ? "Cursor + VS Code" : targets[0];
    if (status) { status.className = "rule-write-status"; status.textContent = `Pushing ${kind} (${targetLabel})…`; }
    try {
      const r = await fetch(`/api/assets/push/${encodeURIComponent(kind)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo: currentDetailsRepo.name, fmt, targets, access, tool_use }),
      });
      const data = await r.json();
      if (data.ok) {
        const files = (data.files || []).filter((f) => f.ok).map((f) => f.path.split("/").pop());
        const hint = data.backends_included ? `(${data.backends_included.join(", ")})` : "";
        if (status) {
          status.className = "rule-write-status ok";
          status.textContent = `Pushed ${kind} (${targetLabel}) ${hint}: ${files.join(", ") || "(no files)"}`;
        }
        if (kind === "rule") {
          currentDetailsRepo.has_rule = true;
          const idx = currentRepos.findIndex((x) => x.path_ro === currentDetailsRepo.path_ro);
          if (idx >= 0) currentRepos[idx] = { ...currentRepos[idx], has_rule: true };
          renderReposList();
          renderRepoDetailsMetaOnly();
        }
      } else {
        if (status) {
          status.className = "rule-write-status err";
          status.textContent = `Push ${kind} failed: ` + (data.error || JSON.stringify(data.files?.filter((f) => !f.ok)));
        }
      }
    } catch (err) {
      if (status) { status.className = "rule-write-status err"; status.textContent = "Push error: " + err.message; }
    }
  }

  // Push all asset kinds to the selected IDE target.
  async function pushAllAssets() {
    await pushComprehensive("rule");
    await pushComprehensive("agent");
    await pushComprehensive("hook");
    await pushComprehensive("skill");
  }

  // Push all asset kinds to BOTH Cursor and VS Code targets.
  async function pushBothTargets() {
    const both = ["cursor", "vscode"];
    await pushComprehensive("rule", both);
    await pushComprehensive("agent", both);
    await pushComprehensive("hook", both);
    await pushComprehensive("skill", both);
  }

  // Remove all zelosmcp-managed assets from the current repo.
  async function removeAllAssets() {
    if (!currentDetailsRepo) return;
    const name = currentDetailsRepo.name;
    const ok = window.confirm(
      `Remove all zelosmcp-managed files from "${name}"?\\n\\n` +
      "This will delete:\\n" +
      "  \\u2022 Rule files (zelosmcp.mdc, copilot-instructions.md)\\n" +
      "  \\u2022 Agent files\\n" +
      "  \\u2022 Skill files\\n" +
      "  \\u2022 zelosmcp.json prefs manifests\\n" +
      "  \\u2022 zelosmcp entries from hooks and mcp.json\\n\\n" +
      "Non-zelosmcp files in .cursor/, .vscode/ will be preserved."
    );
    if (!ok) return;
    const status = document.getElementById("repo-rule-status");
    if (status) { status.className = "rule-write-status"; status.textContent = "Removing…"; }
    try {
      const r = await fetch("/api/assets/remove-all", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo: name }),
      });
      const data = await r.json();
      if (data.ok) {
        const items = (data.removed || []);
        const deleted = items.filter((f) => f.action === "deleted").length;
        const cleaned = items.filter((f) => f.action === "cleaned").length;
        const parts = [];
        if (deleted) parts.push(`${deleted} file${deleted !== 1 ? "s" : ""} deleted`);
        if (cleaned) parts.push(`${cleaned} file${cleaned !== 1 ? "s" : ""} cleaned`);
        if (status) {
          status.className = "rule-write-status ok";
          status.textContent = parts.length ? `Removed: ${parts.join(", ")}` : "Nothing to remove.";
        }
        currentDetailsRepo.has_rule = false;
        const idx = currentRepos.findIndex((x) => x.path_ro === currentDetailsRepo.path_ro);
        if (idx >= 0) currentRepos[idx] = { ...currentRepos[idx], has_rule: false };
        renderReposList();
        renderRepoDetailsMetaOnly();
      } else {
        if (status) {
          status.className = "rule-write-status err";
          status.textContent = "Remove failed: " + (data.error || "unknown error");
        }
      }
    } catch (err) {
      if (status) { status.className = "rule-write-status err"; status.textContent = "Remove error: " + err.message; }
    }
  }

  // ── Bulk push to all repos with rules ────────────────────────────────

  function confirmBulkPush() {
    const reposWithRules = currentRepos.filter((r) => r.has_rule);
    const count = reposWithRules.length;
    if (count === 0) {
      alert("No repositories currently have zelosmcp rules to push to.");
      return;
    }
    const ok = window.confirm(
      `Push rules + agents + hooks + skills to ${count} repositor${count === 1 ? "y" : "ies"} with existing zelosmcp rules?\\n\\n` +
      "This will overwrite the current contents of:\\n" +
      "  .cursor/rules/zelosmcp.mdc\\n" +
      "  .github/copilot-instructions.md\\n" +
      "  .cursor/skills/*, .github/skills/*\\n" +
      "  .cursor/hooks.json, .github/hooks/hooks.json\\n" +
      "  .cursor/zelosmcp.json, .vscode/zelosmcp.json\\n\\n" +
      "Each repo's stored targets, access mode, and tool-use mode will be respected.\\n\\n" +
      "ANY MANUAL EDITS TO THESE FILES WILL BE LOST. Continue?"
    );
    if (!ok) return;
    bulkPushToAllRepos();
  }

  async function bulkPushToAllRepos() {
    const btn = document.getElementById("repos-bulk-push-btn");
    const resultsEl = document.getElementById("bulk-push-results");
    if (btn) { btn.disabled = true; btn.textContent = "Pushing…"; }
    if (resultsEl) {
      resultsEl.style.display = "";
      resultsEl.innerHTML = '<div style="font-size:12px;color:var(--mid);">Pushing to all repos with rules…</div>';
    }
    try {
      const r = await fetch("/api/repos/push-all-with-rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await r.json();
      if (resultsEl) {
        resultsEl.innerHTML = _renderBulkPushResults(data);
      }
      // Refresh the repo list so has_rule badges update.
      await loadRepos({ force: true, silent: true });
    } catch (err) {
      if (resultsEl) {
        resultsEl.innerHTML = `<div style="font-size:12px;color:red;">Bulk push failed: ${escapeHtml(err.message)}</div>`;
      }
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "⬆ All"; }
    }
  }

  function _renderBulkPushResults(data) {
    const repos = Array.isArray(data.repos) ? data.repos : [];
    if (repos.length === 0) {
      return '<div style="font-size:12px;color:var(--mid);">No repos with rules found.</div>';
    }
    const rows = repos.map((r) => {
      const kinds = Object.entries(r.kinds || {});
      const allOk = kinds.every(([, kd]) => kd.ok);
      const icon = allOk ? "✓" : "✗";
      const color = allOk ? "green" : "red";
      const details = kinds.map(([k, kd]) =>
        `<span style="color:${kd.ok ? "green" : "red"}">${k}:${kd.ok ? "ok" : (kd.error || "err")}</span>`
      ).join(" ");
      return `<div style="font-size:11px;display:flex;gap:6px;align-items:baseline;">
        <span style="color:${color};font-weight:700;">${icon}</span>
        <span style="flex:1;font-weight:600;">${escapeHtml(r.repo)}</span>
        <span>${details}</span>
      </div>`;
    }).join("");
    const okCount = repos.filter((r) => Object.values(r.kinds || {}).every((kd) => kd.ok)).length;
    const summary = `<div style="font-size:11px;font-weight:600;margin-bottom:4px;">`
      + `${okCount}/${repos.length} repos OK</div>`;
    return summary + rows;
  }

  // Update the running-backends hint in the push section.
  function updateRepoPushHint(status) {
    const hint = document.getElementById("repo-push-running-hint");
    if (!hint || !status) return;
    const running = (status.servers || []).filter((s) => s.running && !s.builtin).map((s) => s.name);
    hint.textContent = running.length
      ? `Push includes: zelosmcp + ${running.join(", ")}`
      : "Push includes: zelosmcp global (no user backends running)";
  }

  // ── Execute extensions in repo panel ────────────────────────────────

  // Called whenever a repo details pane is opened.
  async function loadRepoAssetActions() {
    const container = document.getElementById("repo-asset-actions");
    if (!container) return;
    container.innerHTML = "";
    const status = document.getElementById("repo-rule-status");

    // Update running-backends hint
    updateRepoPushHint(currentStatus);

    try {
      const extResp = await fetch("/api/assets?kind=extension");
      const extensions = extResp.ok ? await extResp.json() : [];
      const repoExts = Array.isArray(extensions) ? extensions.filter((row) => {
        const targets = (row.meta && row.meta.targets) || [];
        return targets.includes("repos_row");
      }) : [];
      if (repoExts.length === 0) return;

      const heading = document.createElement("div");
      heading.style.cssText = "font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);margin-bottom:6px;";
      heading.textContent = "Execute extensions";
      container.appendChild(heading);

      const actionsRow = document.createElement("div");
      actionsRow.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;";
      container.appendChild(actionsRow);

      for (const ext of repoExts) {
        const isRunning = (currentStatus.servers || []).some(
          (s) => s.name === ext.backend && s.running
        );
        const requiresRunning = ext.meta?.requires_running !== false;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-outline";
        btn.textContent = (ext.meta && ext.meta.label) || ext.name;
        btn.title = requiresRunning && !isRunning
          ? `${ext.meta?.description || ext.name} (${ext.backend} is not running)`
          : (ext.meta && ext.meta.description) || "";
        btn.disabled = requiresRunning && !isRunning;
        btn.onclick = async () => {
          btn.disabled = true;
          const orig = btn.textContent;
          btn.textContent = "Running…";
          if (status) { status.className = "rule-write-status"; status.textContent = btn.title || "Running..."; }
          try {
            const ctx = { repo: currentDetailsRepo ? {
              ro_path: currentDetailsRepo.path_ro,
              rw_path: currentDetailsRepo.path_rw,
              name: currentDetailsRepo.name,
            } : {} };
            const res = await fetch(
              `/api/assets/extension/${encodeURIComponent(ext.backend)}/${encodeURIComponent(ext.name)}/invoke`,
              { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ctx }) }
            );
            const data = await res.json();
            if (data.ok) {
              if (status) { status.className = "rule-write-status ok"; status.textContent = data.message; }
              if (ext.name === "index_project" && ext.backend === "pincher" && currentDetailsRepo) {
                currentDetailsRepo.pincher_indexed = true;
                const idx2 = currentRepos.findIndex((x) => x.path_ro === currentDetailsRepo.path_ro);
                if (idx2 >= 0) currentRepos[idx2] = { ...currentRepos[idx2], pincher_indexed: true };
                renderReposList();
                renderRepoDetailsMetaOnly();
              }
            } else {
              if (status) { status.className = "rule-write-status err"; status.textContent = data.error || data.message; }
            }
          } catch (err) {
            if (status) { status.className = "rule-write-status err"; status.textContent = "Error: " + err.message; }
          } finally {
            btn.disabled = requiresRunning && !isRunning;
            btn.textContent = orig;
          }
        };
        actionsRow.appendChild(btn);
      }
    } catch (_) {}
  }

  // Keep old names as aliases
  async function loadRepoExtensions() { return loadRepoAssetActions(); }
  function _makeRepoPushBtn() {}

  function renderRepoDetailsMetaOnly() {
    if (!currentDetailsRepo) return;
    const meta = document.getElementById("repo-details-meta");
    if (!meta) return;
    const bits = [];
    bits.push(currentDetailsRepo.has_rule ? "rule present" : "no rule");
    // Only show pincher status when pincher backend is running.
    const pinchRunning = (currentStatus.servers || []).some(
      (s) => s.name === "pincher" && s.running
    );
    if (pinchRunning) {
      bits.push(currentDetailsRepo.pincher_indexed ? "indexed" : "not indexed");
    }
    meta.textContent = bits.join(" • ");
  }

  // Restore persisted panel state on load. If the user had it expanded
  // last session, re-expand and load eagerly; otherwise stay collapsed
  // and defer the network call until the user opens it.
  (function initReposPanel() {
    let expanded = false;
    try {
      expanded = localStorage.getItem(REPOS_LS_EXPANDED) === "1";
      const savedFilter = localStorage.getItem(REPOS_LS_FILTER) || "";
      const filterInput = document.getElementById("repos-filter");
      if (filterInput) filterInput.value = savedFilter;
    } catch (_) {}
    setReposCollapsed(!expanded, false);
    if (expanded) loadRepos({ silent: true });
  })();

  // ── Left-nav view switcher ──────────────────────────────────────────
  // Toggle .active between .nav-item[data-view] and .view[data-view].
  // Forces a pincher-dashboard reload when the user navigates to it
  // (so a stale iframe from a previous session gets a fresh hit).
  function setView(name) {
    document.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === name));
    document.querySelectorAll(".view").forEach((v) =>
      v.classList.toggle("active", v.dataset.view === name));
    if (name === "pincher-dashboard") refreshPincherDashboard(true);
    if (name === "savings") {
      refreshSavings();
      ensureSavingsStream();
    }
    if (name === "events") {
      loadEventHistory(0);
      ensureEventsStream();
    }
    if (name === "docs") loadDocsIndex();
    if (name === "connections") loadConnections();
    if (name === "assets-rules") { currentAssetsBackendFilter = null; loadAssets("rule"); }
    if (name === "assets-extensions") { currentAssetsBackendFilter = null; loadAssets("extension"); }
    if (name === "assets-agents") { currentAssetsBackendFilter = null; loadAssets("agent"); }
    if (name === "assets-hooks") { currentAssetsBackendFilter = null; loadAssets("hook"); }
    // assets-backend is driven by showBackendAssets(); no left-nav auto-load.
  }
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.view)));

  // ── Assets pane ────────────────────────────────────────────────────

  const _ASSETS_KIND_TO_CONTAINER = {
    rule: "assets-rules-list",
    extension: "assets-extensions-list",
    agent: "assets-agents-list",
    hook: "assets-hooks-list",
  };

  let currentAssetsKind = null;
  let currentAssetsRow = null;
  let currentAssetsBackendFilter = null;  // set by showBackendAssets()

  async function loadAssets(kind, backendFilter) {
    currentAssetsKind = kind;
    if (backendFilter !== undefined) currentAssetsBackendFilter = backendFilter;
    const containerId = _ASSETS_KIND_TO_CONTAINER[kind];
    if (!containerId) return;
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '<span class="docs-empty">Loading...</span>';

    // Show / clear the backend filter banner
    const bannerId = containerId + "-filter-banner";
    let banner = document.getElementById(bannerId);
    if (currentAssetsBackendFilter) {
      if (!banner) {
        banner = document.createElement("div");
        banner.id = bannerId;
        banner.style.cssText = "display:flex;align-items:center;gap:8px;padding:6px 0 10px;font-size:12px;color:var(--muted);";
        container.parentNode.insertBefore(banner, container);
      }
      banner.innerHTML = `<span>Filtered to backend: <strong>${currentAssetsBackendFilter}</strong></span>` +
        `<button type="button" class="btn btn-mini btn-outline" onclick="clearAssetsFilter('${kind}')">Show all</button>`;
    } else if (banner) {
      banner.remove();
    }

    try {
      let url = `/api/assets?kind=${encodeURIComponent(kind)}`;
      if (currentAssetsBackendFilter) url += `&backend=${encodeURIComponent(currentAssetsBackendFilter)}`;
      const r = await fetch(url);
      if (!r.ok) throw new Error("HTTP " + r.status);
      const rows = await r.json();
      if (!Array.isArray(rows) || rows.length === 0) {
        container.innerHTML = '<span class="docs-empty">No assets found for this backend.</span>';
        return;
      }
      container.innerHTML = "";
      // Group by backend (only one group when filtered)
      const byBackend = {};
      for (const row of rows) {
        (byBackend[row.backend] = byBackend[row.backend] || []).push(row);
      }
      for (const [backend, bRows] of Object.entries(byBackend)) {
        const grp = document.createElement("div");
        grp.className = "cat-group";
        const hdr = document.createElement("div");
        hdr.className = "cat-label";
        hdr.textContent = backend;
        grp.appendChild(hdr);
        const ul = document.createElement("ul");
        for (const row of bRows) {
          ul.appendChild(_buildAssetItem(row, kind));
        }
        grp.appendChild(ul);
        container.appendChild(grp);
      }
    } catch (err) {
      container.innerHTML = `<span class="docs-empty" style="color:var(--warn)">Error loading assets: ${err.message}</span>`;
    }
  }

  function clearAssetsFilter(kind) {
    currentAssetsBackendFilter = null;
    loadAssets(kind, null);
  }

  function _buildAssetItem(row, kind) {
    // Card-style row: flex container so name+pill and action button stay aligned.
    const card = document.createElement("div");
    card.style.cssText = (
      "display:flex;align-items:center;gap:8px;" +
      "padding:7px 10px;border-radius:4px;margin-bottom:4px;" +
      "background:var(--surface);border:1px solid var(--border);"
    );

    // Left: name + source pill + optional description
    const left = document.createElement("div");
    left.style.cssText = "flex:1;min-width:0;";

    const nameRow = document.createElement("div");
    nameRow.style.cssText = "display:flex;align-items:center;gap:6px;flex-wrap:wrap;";

    const nameEl = document.createElement("code");
    nameEl.style.cssText = "font-size:12px;word-break:break-all;";
    nameEl.textContent = row.name.startsWith("tool:") ? row.name.slice(5) : row.name;
    if (row.target) nameEl.textContent += ` [${row.target}]`;
    nameRow.appendChild(nameEl);

    const sourcePill = document.createElement("span");
    sourcePill.className = "pill" + (row.source === "user" ? " on" : "");
    sourcePill.style.cssText = "font-size:10px;flex-shrink:0;";
    sourcePill.textContent = row.source;
    nameRow.appendChild(sourcePill);

    // Kind badge for "All" tab clarity
    if (kind === "all") {
      const kindBadge = document.createElement("span");
      kindBadge.className = "pill";
      kindBadge.style.cssText = "font-size:10px;flex-shrink:0;opacity:.65;";
      kindBadge.textContent = row.kind;
      nameRow.appendChild(kindBadge);
    }

    left.appendChild(nameRow);

    if (row.meta && row.meta.description) {
      const desc = document.createElement("div");
      desc.style.cssText = "font-size:11px;color:var(--muted);margin-top:2px;";
      desc.textContent = row.meta.description;
      left.appendChild(desc);
    }
    card.appendChild(left);

    // Right: action buttons (always visible, no float)
    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:4px;flex-shrink:0;";

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "btn btn-mini btn-outline";
    editBtn.textContent = "Edit";
    editBtn.onclick = () => openAssetsEdit(row, kind === "all" ? row.kind : kind);
    actions.appendChild(editBtn);

    if ((kind === "extension" || (kind === "all" && row.kind === "extension")) &&
        (row.meta?.type || "tool") === "tool") {
      const runBtn = document.createElement("button");
      runBtn.type = "button";
      runBtn.className = "btn btn-mini btn-outline";
      runBtn.textContent = row.meta?.label || "Run";
      runBtn.title = row.meta?.description || "";
      runBtn.onclick = () => runExtension(row.backend, row.name, runBtn);
      actions.appendChild(runBtn);
    }

    card.appendChild(actions);
    return card;
  }

  function openAssetsEdit(row, kind) {
    currentAssetsRow = { row, kind };
    const backdrop = document.getElementById("assets-edit-backdrop");
    const title    = document.getElementById("assets-edit-title");
    const metaEl   = document.getElementById("assets-edit-meta");
    const bodyEl   = document.getElementById("assets-edit-body");
    const status   = document.getElementById("assets-edit-status");
    if (!backdrop || !title || !bodyEl) return;
    title.textContent = row.name.startsWith("tool:") ? `Tool guidance: ${row.name.slice(5)}` : row.name;
    if (metaEl) {
      const sourceLabel = row.source === "user" ? "user-edited" : "seed";
      metaEl.textContent = `${row.kind}  ·  backend: ${row.backend}  ·  ${sourceLabel}`;
    }
    bodyEl.value = row.body || "";
    if (status) status.textContent = "";
    backdrop.classList.remove("hidden");
    bodyEl.focus();
  }

  function closeAssetsEdit() {
    const backdrop = document.getElementById("assets-edit-backdrop");
    if (backdrop) backdrop.classList.add("hidden");
    currentAssetsRow = null;
    const status = document.getElementById("assets-edit-status");
    if (status) status.textContent = "";
  }

  function _afterAssetsEdit(kind) {
    // Reload whichever view is currently showing assets.
    if (currentBackendAssetsName) {
      _loadBackendAssetsTab(kind, currentBackendAssetsName);
    } else {
      loadAssets(kind);
    }
  }

  async function saveAssetsEdit() {
    if (!currentAssetsRow) return;
    const { row, kind } = currentAssetsRow;
    const bodyEl = document.getElementById("assets-edit-body");
    const status = document.getElementById("assets-edit-status");
    if (!bodyEl || !status) return;
    status.textContent = "Saving…";
    try {
      const r = await fetch(
        `/api/assets/${row.kind}/${row.backend}/${encodeURIComponent(row.name)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ body: bodyEl.value, meta: row.meta, target: row.target }),
        }
      );
      if (!r.ok) throw new Error("HTTP " + r.status);
      status.textContent = "Saved.";
      setTimeout(() => { closeAssetsEdit(); _afterAssetsEdit(kind); }, 500);
    } catch (err) {
      status.textContent = "Error: " + err.message;
    }
  }

  async function revertAssetsEdit() {
    if (!currentAssetsRow) return;
    const { row, kind } = currentAssetsRow;
    const status = document.getElementById("assets-edit-status");
    if (!status) return;
    if (!confirm(`Revert "${row.name}" to the seed value? Your edits will be lost.`)) return;
    status.textContent = "Reverting…";
    try {
      const r = await fetch(
        `/api/assets/${row.kind}/${row.backend}/${encodeURIComponent(row.name)}`,
        { method: "DELETE" }
      );
      if (!r.ok) throw new Error("HTTP " + r.status);
      status.textContent = "Reverted.";
      setTimeout(() => { closeAssetsEdit(); _afterAssetsEdit(kind); }, 500);
    } catch (err) {
      status.textContent = "Error: " + err.message;
    }
  }

  async function pushAssetsEdit() {
    if (!currentAssetsRow) return;
    const repo = prompt("Push to which repo? (enter the repo name shown in the Repos panel)");
    if (!repo) return;
    const { row } = currentAssetsRow;
    const status = document.getElementById("assets-edit-status");
    if (status) status.textContent = "Pushing…";
    try {
      const r = await fetch(
        `/api/assets/${row.kind}/${row.backend}/${encodeURIComponent(row.name)}/push`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ repo }),
        }
      );
      const data = await r.json();
      if (data.ok) {
        if (status) status.textContent = `Pushed to ${repo}.`;
      } else {
        if (status) status.textContent = "Push failed: " + (data.error || JSON.stringify(data));
      }
    } catch (err) {
      if (status) status.textContent = "Push error: " + err.message;
    }
  }

  async function promptPushAsset(row) {
    const repo = prompt(`Push ${row.backend}/${row.name} to which repo?`);
    if (!repo) return;
    await _pushAssetToRepo(row, repo);
  }

  async function _pushAssetToRepo(row, repo) {
    try {
      const r = await fetch(`/api/assets/${row.kind}/${row.backend}/${encodeURIComponent(row.name)}/push`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo }),
      });
      const data = await r.json();
      if (data.ok) {
        const paths = (data.files || []).map((f) => f.path).join(", ");
        alert(`Pushed: ${paths}`);
      } else {
        alert("Push failed: " + (data.error || JSON.stringify(data)));
      }
    } catch (err) {
      alert("Push error: " + err.message);
    }
  }

  async function runExtension(backend, name, btn) {
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Running…";
    try {
      const ctx = {};
      const r = await fetch(`/api/assets/extension/${encodeURIComponent(backend)}/${encodeURIComponent(name)}/invoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ctx }),
      });
      const data = await r.json();
      if (data.ok) {
        btn.textContent = "Done";
        addLog(`extension ${backend}/${name}: ${data.message}`);
      } else {
        btn.textContent = "Error";
        addLog(`ERROR extension ${backend}/${name}: ${data.error || data.message}`);
      }
    } catch (err) {
      btn.textContent = "Error";
      addLog("ERROR: " + err.message);
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = origText; }, 2000);
    }
  }

  // Close assets edit on backdrop click.
  (function() {
    const backdrop = document.getElementById("assets-edit-backdrop");
    if (!backdrop) return;
    backdrop.addEventListener("click", (ev) => {
      if (ev.target === backdrop) closeAssetsEdit();
    });
  })();

  // ── Documentation view ──────────────────────────────────────────────
  // Two modes share the same view:
  //   1. Index — full-width grid of {title, slug} cards in the middle pane.
  //   2. Content — rendered HTML for one doc, with a back button.
  // setView('docs') always lands on the index unless a doc is already
  // selected (re-entry preserves your last-read doc).
  async function loadDocsIndex() {
    // Show index mode (always). Fetch the index once per session unless
    // it errored last time.
    showDocsIndex();
    if (docsLoadInflight) return;
    if (Array.isArray(docsIndex) && docsIndex.length > 0) {
      renderDocsCards();
      return;
    }
    docsLoadInflight = true;
    const meta = document.getElementById("docs-meta");
    if (meta) meta.textContent = "loading...";
    try {
      const r = await fetch("/api/docs");
      if (!r.ok) throw new Error("HTTP " + r.status);
      docsIndex = await r.json();
    } catch (err) {
      docsIndex = null;
      if (meta) meta.textContent = "error: " + err.message;
      const list = document.getElementById("docs-index");
      if (list) {
        list.innerHTML = "";
        const p = document.createElement("span");
        p.className = "docs-empty";
        p.textContent = "Failed to load docs index.";
        list.appendChild(p);
      }
      docsLoadInflight = false;
      return;
    } finally {
      docsLoadInflight = false;
    }
    if (meta) meta.textContent = `${docsIndex.length} document${docsIndex.length === 1 ? "" : "s"}`;
    renderDocsCards();
  }

  function renderDocsCards() {
    const list = document.getElementById("docs-index");
    if (!list) return;
    list.innerHTML = "";
    if (!Array.isArray(docsIndex) || docsIndex.length === 0) {
      const p = document.createElement("span");
      p.className = "docs-empty";
      p.textContent = "No documentation files found.";
      list.appendChild(p);
      return;
    }
    for (const d of docsIndex) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "docs-card";
      card.dataset.slug = d.slug;
      card.onclick = () => loadDoc(d.slug);
      const title = document.createElement("div");
      title.className = "docs-card-title";
      title.textContent = d.title;
      card.appendChild(title);
      const slug = document.createElement("div");
      slug.className = "docs-card-slug";
      slug.textContent = `docs/${d.slug}.md`;
      card.appendChild(slug);
      list.appendChild(card);
    }
  }

  function showDocsIndex() {
    currentDocSlug = null;
    const list = document.getElementById("docs-index");
    const content = document.getElementById("docs-content");
    const toolbar = document.getElementById("docs-toolbar");
    const title = document.getElementById("docs-section-title");
    if (list) list.classList.remove("hidden");
    if (content) content.classList.add("hidden");
    if (toolbar) toolbar.classList.add("hidden");
    if (title) title.textContent = "Documentation";
  }

  async function loadDoc(slug) {
    const list = document.getElementById("docs-index");
    const content = document.getElementById("docs-content");
    const toolbar = document.getElementById("docs-toolbar");
    const title = document.getElementById("docs-section-title");
    const current = document.getElementById("docs-current");
    if (!content) return;
    // Make sure we land on the docs view in case we got here via a
    // link from another tab.
    setView("docs");
    currentDocSlug = slug;
    if (list) list.classList.add("hidden");
    if (toolbar) toolbar.classList.remove("hidden");
    if (content) content.classList.remove("hidden");
    if (current) current.textContent = `docs/${slug}.md`;
    content.innerHTML = '<p class="docs-empty">Loading...</p>';
    try {
      const r = await fetch("/api/docs/" + encodeURIComponent(slug));
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      if (title) title.textContent = data.title || "Documentation";
      content.innerHTML = data.html || "";
      rewriteDocLinks(content);
      await renderMermaidIn(content);
      content.scrollTop = 0;
    } catch (err) {
      content.innerHTML = "";
      const p = document.createElement("p");
      p.className = "docs-empty";
      p.textContent = "Failed to load: " + err.message;
      content.appendChild(p);
    }
  }

  // Markdown links to other docs (e.g. `[walkthrough](docs/quickstart.md)`)
  // resolve to absolute browser paths once rendered (`/docs/quickstart.md`)
  // — that 404s against the Swagger /docs endpoint. Walk every <a> in
  // the rendered body and rewrite *.md targets to call loadDoc(slug)
  // in-place. We deliberately don't gate on `docsIndex` membership: if
  // the slug isn't shipped, loadDoc renders a clean "Failed to load:
  // HTTP 404" inside the docs view, which is a far better UX than
  // silently swallowing the click. External URLs and pure in-page
  // anchors are left alone.
  function rewriteDocLinks(container) {
    if (!container) return;
    const anchors = container.querySelectorAll("a[href]");
    for (const a of anchors) {
      const raw = a.getAttribute("href") || "";
      if (!raw) continue;
      // Pure in-page anchor — let the browser handle it.
      if (raw.startsWith("#")) continue;
      // Absolute URLs — open externally in a new tab.
      if (/^[a-z][a-z0-9+.\\-]*:\\/\\//i.test(raw) || raw.startsWith("//")) {
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener");
        continue;
      }
      // mailto:, tel:, etc. — leave alone.
      if (/^[a-z][a-z0-9+.\\-]*:/i.test(raw)) continue;
      // Strip optional ./ or ../ prefixes and leading docs/ so we land
      // at "<file>.md" and can derive the slug.
      let path = raw;
      let hash = "";
      const hashIdx = path.indexOf("#");
      if (hashIdx >= 0) {
        hash = path.slice(hashIdx);
        path = path.slice(0, hashIdx);
      }
      const cleaned = path.replace(/^\\.\\.?\\//, "").replace(/^docs\\//, "");
      const m = cleaned.match(/^([A-Za-z0-9._\\-]+)\\.md$/);
      if (m) {
        const slug = m[1].toLowerCase();
        a.setAttribute("href", "#" + slug);
        a.addEventListener("click", (ev) => {
          ev.preventDefault();
          loadDoc(slug);
          if (hash) {
            // Defer: wait for the new doc to land before scrolling.
            setTimeout(() => {
              const el = document.getElementById(hash.slice(1));
              if (el) el.scrollIntoView({ behavior: "smooth" });
            }, 250);
          }
        });
        continue;
      }
      // Non-.md repo path (e.g. ../configs/foo.json). Leave the href
      // untouched — the browser will visibly 404 against this server,
      // which is more honest than silently swallowing the click. Mark
      // it visually so the reader knows it can't be opened in-app.
      a.title = "Repo path: " + raw + " (open from the source repo, not the in-app docs)";
      a.style.textDecoration = "underline dotted";
    }
  }

  // Replace every `<pre><code class="language-mermaid">` block produced
  // by the python-markdown fenced_code extension with a `<div class=
  // "mermaid">` and ask mermaid.js to render it. No-op when mermaid
  // failed to load (e.g. offline) — the raw fenced block stays visible.
  async function renderMermaidIn(container) {
    if (!container || !window.mermaid) return;
    const blocks = container.querySelectorAll("pre > code.language-mermaid");
    if (blocks.length === 0) return;
    const created = [];
    for (const code of blocks) {
      const pre = code.parentElement;
      if (!pre || !pre.parentElement) continue;
      const div = document.createElement("div");
      div.className = "mermaid";
      // textContent decodes the &gt;/&lt; the markdown renderer emitted.
      div.textContent = code.textContent;
      pre.parentElement.replaceChild(div, pre);
      created.push(div);
    }
    if (created.length === 0) return;
    try {
      await mermaid.run({ nodes: created });
    } catch (err) {
      addLog("ERROR: mermaid render failed: " + (err && err.message ? err.message : err));
    }
  }

  // ── Pincher dashboard iframe ────────────────────────────────────────
  // The dashboard is served by pincher itself on a loopback HTTP sidecar
  // and reverse-proxied by zelosMCP under reverseProxy.mount (defaults
  // to /pincher per configs/mandatory-zelosmcp.json). Read the live
  // mount from /api/status so renaming the backend or moving the mount
  // is handled transparently.
  function pincherDashboardUrl(status) {
    for (const s of (status && status.servers) || []) {
      if (s.name !== "pincher" || !s.running || s.error) continue;
      const m = s.spec && s.spec.reverseProxy && s.spec.reverseProxy.mount;
      if (m) return m + "/v1/dashboard";
    }
    return null;
  }

  let lastDashboardUrl = null;
  function refreshPincherDashboard(force) {
    const frame = document.getElementById("pincher-dashboard-frame");
    const empty = document.getElementById("pincher-dashboard-empty");
    const meta = document.getElementById("pincher-dashboard-meta");
    if (!frame || !empty || !meta) return;
    const url = pincherDashboardUrl(currentStatus);
    if (!url) {
      frame.classList.add("hidden");
      frame.removeAttribute("src");
      empty.classList.remove("hidden");
      meta.textContent = "pincher offline";
      lastDashboardUrl = null;
      return;
    }
    empty.classList.add("hidden");
    frame.classList.remove("hidden");
    meta.textContent = url;
    if (force || url !== lastDashboardUrl) {
      frame.src = url;
      lastDashboardUrl = url;
    }
  }

  // ── Savings dashboard ───────────────────────────────────────────────
  // Pulls /api/savings every 5s while the tab is visible, and listens to
  // /api/savings/stream for instant invalidation on new call/compression/
  // pincher_stats events. The SSE connection is opened lazily the first
  // time the user navigates to the Savings view so passive page loads
  // don't keep an idle connection open.
  let savingsStream = null;
  let savingsPollTimer = null;
  let savingsFetchInflight = false;
  let eventsStream = null;
  let eventsPollTimer = null;
  let eventsFetchInflight = false;
  let eventsPageOffset = 0;
  const eventsPageLimit = 25;
  let currentEventsPage = null;
  let currentEventSelectionId = null;

  function fmtNum(n) {
    if (n === null || n === undefined) return "—";
    if (typeof n !== "number") return String(n);
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + "B";
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + "M";
    if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + "k";
    return String(n);
  }

  function fmtPct(n) {
    if (n === null || n === undefined) return "—";
    return n.toFixed(1) + "%";
  }

  function fmtUsd(n) {
    if (!n) return "$0.00";
    if (n < 0.01) return "$" + n.toFixed(4);
    return "$" + n.toFixed(2);
  }

  function isSavingsTabActive() {
    const v = document.querySelector(".view[data-view=savings]");
    return v && v.classList.contains("active");
  }

  function ensureSavingsStream() {
    if (savingsStream) return;
    try {
      savingsStream = new EventSource("/api/savings/stream");
    } catch (_) {
      savingsStream = null;
      return;
    }
    savingsStream.onmessage = (_evt) => {
      // Any server-pushed event invalidates our snapshot; refetch once
      // the tab is visible to keep idle bandwidth low.
      if (isSavingsTabActive()) refreshSavings();
    };
    savingsStream.onerror = () => {
      // EventSource auto-reconnects; nothing to do beyond keeping the
      // handle so we don't open a second one.
    };
    if (!savingsPollTimer) {
      savingsPollTimer = setInterval(() => {
        if (isSavingsTabActive()) refreshSavings();
      }, 5000);
    }
  }

  async function refreshSavings() {
    if (savingsFetchInflight) return;
    savingsFetchInflight = true;
    const meta = document.getElementById("savings-meta");
    try {
      const r = await fetch("/api/savings");
      if (r.status === 503) {
        if (meta) meta.textContent = "store not initialised";
        return;
      }
      if (!r.ok) {
        if (meta) meta.textContent = "error " + r.status;
        return;
      }
      const data = await r.json();
      renderSavings(data);
    } catch (err) {
      if (meta) meta.textContent = "error: " + err.message;
    } finally {
      savingsFetchInflight = false;
    }
  }

  function renderSavings(data) {
    const meta = document.getElementById("savings-meta");
    if (meta) {
      const enc = data.tokenizer && data.tokenizer.heuristic
        ? "heuristic" : (data.tokenizer.encoding || "tiktoken");
      const ts = new Date((data.generated_at || 0) * 1000);
      const retention = data.retention_hours ? ` • retention ${data.retention_hours}h` : "";
      meta.textContent = `${enc} • updated ${ts.toLocaleTimeString()}${retention}`;
    }

    const compressionSaved = data.compression_saved_tokens_total || 0;
    const transformSaved = data.response_transform_saved_tokens_total || 0;
    const pincherSaved = (data.pincher && data.pincher.tokens_saved_total) || 0;
    const totals = (data.calls && data.calls.totals) || {};
    const cost = (data.pincher && data.pincher.cost_avoided_usd_total) || 0;
    const transactions = totals.transactions || totals.events || totals.calls || 0;
    const upstreamOutput = totals.raw_output_tokens || data.upstream_output_tokens_total || 0;
    const returnedOutput = totals.output_tokens || data.returned_output_tokens_total || 0;

    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    };
    set("kpi-compression-saved", fmtNum(compressionSaved));
    set("kpi-transform-saved", fmtNum(transformSaved));
    set("kpi-pincher-saved", fmtNum(pincherSaved));
    set("kpi-transactions", fmtNum(transactions));
    set("kpi-upstream-output", fmtNum(upstreamOutput));
    set("kpi-returned-output", fmtNum(returnedOutput));
    set("kpi-cost", fmtUsd(cost));

    // Compression table
    const compBody = document.getElementById("savings-compression-body");
    if (compBody) {
      const rows = data.compression || [];
      if (!rows.length) {
        compBody.innerHTML = '<tr><td colspan="6" class="empty-cell">No compression snapshots yet. Run a <code>tools/list</code> against <code>/mcp</code>.</td></tr>';
      } else {
        compBody.innerHTML = rows.map((c) => `
          <tr>
            <td>${escapeHtml(c.backend)}</td>
            <td>${escapeHtml(c.level || "")}</td>
            <td class="num">${fmtNum(c.raw_tokens)}</td>
            <td class="num">${fmtNum(c.compressed_tokens)}</td>
            <td class="num">${fmtNum(c.saved_tokens)}</td>
            <td class="num">${fmtPct(c.saved_pct)}</td>
          </tr>
        `).join("");
      }
    }

    // Top tools
    const topBody = document.getElementById("savings-top-tools-body");
    if (topBody) {
      const rows = (data.calls && data.calls.top_tools) || [];
      if (!rows.length) {
        topBody.innerHTML = '<tr><td colspan="7" class="empty-cell">No proxy events recorded yet.</td></tr>';
      } else {
        topBody.innerHTML = rows.map((t) => `
          <tr>
            <td><code>${escapeHtml(t.qualified)}</code></td>
            <td class="num">${fmtNum(t.events || t.calls || 0)}</td>
            <td class="num">${fmtNum(t.input_tokens || 0)}</td>
            <td class="num">${fmtNum(t.raw_output_tokens || 0)}</td>
            <td class="num">${fmtNum(t.output_tokens || 0)}</td>
            <td class="num">${fmtNum(t.transform_saved_tokens || 0)}</td>
            <td class="num">${Number(t.avg_latency_ms || 0).toFixed(0)} ms</td>
          </tr>
        `).join("");
      }
    }

    const transformBody = document.getElementById("savings-transform-body");
    if (transformBody) {
      const rows = data.response_transforms || [];
      if (!rows.length) {
        transformBody.innerHTML = '<tr><td colspan="5" class="empty-cell">No transformed responses recorded yet.</td></tr>';
      } else {
        transformBody.innerHTML = rows.map((row) => `
          <tr>
            <td>${escapeHtml(row.transform_type || "raw")}</td>
            <td class="num">${fmtNum(row.events || 0)}</td>
            <td class="num">${fmtNum(row.raw_output_tokens || 0)}</td>
            <td class="num">${fmtNum(row.output_tokens || 0)}</td>
            <td class="num">${fmtNum(row.transform_saved_tokens || 0)}</td>
          </tr>
        `).join("");
      }
    }

    // Per-backend bars
    const barsHost = document.getElementById("savings-backend-bars");
    if (barsHost) {
      const rows = (data.calls && data.calls.per_backend) || [];
      if (!rows.length) {
        barsHost.innerHTML = '<div class="empty-cell">No proxy events yet.</div>';
      } else {
        const max = Math.max(1, ...rows.map((r) => r.events || r.calls || 0));
        barsHost.innerHTML = rows.map((b) => {
          const count = b.events || b.calls || 0;
          const pct = Math.round((count / max) * 100);
          return `
            <div class="backend-bar">
              <div class="backend-bar-name">${escapeHtml(b.backend)}</div>
              <div class="backend-bar-track"><div class="backend-bar-fill" style="width: ${pct}%"></div></div>
              <div class="backend-bar-count">${fmtNum(count)} events</div>
            </div>`;
        }).join("");
      }
    }

    // Pincher stats
    const pre = document.getElementById("savings-pincher-stats");
    if (pre) {
      const stats = data.pincher && data.pincher.latest_stats;
      if (!stats) {
        pre.textContent = "No pincher__stats snapshot yet.";
      } else {
        // Prefer the formatted text content from pincher's CallToolResult;
        // fall back to a JSON dump.
        let body = null;
        if (Array.isArray(stats.content) && stats.content.length) {
          body = stats.content.filter((c) => typeof c === "string").join("\\n").trim();
        }
        pre.textContent = body || JSON.stringify(stats, null, 2);
      }
    }
  }

  function isEventsTabActive() {
    const v = document.querySelector(".view[data-view=events]");
    return v && v.classList.contains("active");
  }

  function currentEventFilters() {
    return {
      backend: (document.getElementById("events-backend-filter")?.value || "").trim(),
      method: (document.getElementById("events-method-filter")?.value || "").trim(),
      tool: (document.getElementById("events-tool-filter")?.value || "").trim(),
      errors_only: !!document.getElementById("events-errors-only")?.checked,
    };
  }

  function ensureEventsStream() {
    if (eventsStream) return;
    try {
      eventsStream = new EventSource("/api/events/stream");
    } catch (_) {
      eventsStream = null;
      return;
    }
    eventsStream.onmessage = () => {
      if (isEventsTabActive()) loadEventHistory(eventsPageOffset);
    };
    eventsStream.onerror = () => {};
    if (!eventsPollTimer) {
      eventsPollTimer = setInterval(() => {
        if (isEventsTabActive()) loadEventHistory(eventsPageOffset);
      }, 5000);
    }
  }

  function onEventsFilterChange() {
    eventsPageOffset = 0;
    if (isEventsTabActive()) loadEventHistory(0);
  }

  function changeEventsPage(direction) {
    const nextOffset = Math.max(0, eventsPageOffset + (direction * eventsPageLimit));
    if (nextOffset === eventsPageOffset && direction < 0) return;
    loadEventHistory(nextOffset);
  }

  function renderEventDetails(event) {
    const pre = document.getElementById("events-detail");
    if (!pre) return;
    if (!event) {
      pre.textContent = "Select an event row to inspect its details.";
      return;
    }
    const sections = [];
    // Header
    const upstream = event.raw_output_tokens || 0;
    const returned = event.output_tokens || 0;
    const saved = upstream - returned;
    const savedStr = saved < 0 ? `(${Math.abs(saved)})` : String(saved);
    sections.push(`── Event: ${event.event_id} ──`);
    sections.push(`Time:      ${event.ts ? new Date(event.ts * 1000).toISOString() : '—'}`);
    sections.push(`Backend:   ${event.backend || '—'}`);
    sections.push(`Method:    ${event.method || '—'}`);
    sections.push(`Tool:      ${event.tool || '—'}`);
    sections.push(`Qualified: ${event.qualified || '—'}`);
    sections.push(`Latency:   ${event.latency_ms || 0} ms`);
    sections.push(`Input:     ${event.input_tokens || 0} tokens`);
    sections.push(`Upstream:  ${upstream} tokens`);
    sections.push(`Returned:  ${returned} tokens`);
    sections.push(`Saved:     ${savedStr} tokens`);
    if (event.transform_type) sections.push(`Transform: ${event.transform_type}`);
    if (event.error) sections.push(`Error:     ${event.error_message || 'yes'}`);
    sections.push('');

    // Input message
    sections.push('── Input Message ──');
    if (event.input_text) {
      try { sections.push(JSON.stringify(JSON.parse(event.input_text), null, 2)); }
      catch(e) { sections.push(event.input_text); }
    } else {
      sections.push('(no input)');
    }
    sections.push('');

    // Upstream message (raw from backend, before transforms)
    sections.push('── Upstream Message (raw from backend) ──');
    if (event.upstream_text) {
      try { sections.push(JSON.stringify(JSON.parse(event.upstream_text), null, 2)); }
      catch(e) { sections.push(event.upstream_text); }
    } else {
      sections.push('(not captured)');
    }
    sections.push('');

    // Returned message (after transforms, sent to IDE/LLM)
    sections.push('── Returned Message (sent to IDE) ──');
    if (event.returned_text) {
      try { sections.push(JSON.stringify(JSON.parse(event.returned_text), null, 2)); }
      catch(e) { sections.push(event.returned_text); }
    } else {
      sections.push('(not captured)');
    }
    sections.push('');

    // Meta
    if (event.meta) {
      sections.push('── Meta ──');
      sections.push(JSON.stringify(event.meta, null, 2));
    }
    pre.textContent = sections.join('\\n');
  }

  function selectEvent(eventId) {
    currentEventSelectionId = eventId;
    const page = currentEventsPage || { events: [] };
    const event = (page.events || []).find((row) => row.event_id === eventId) || null;
    renderEventDetails(event);
    document.querySelectorAll("#events-body tr[data-event-id]").forEach((row) => {
      row.classList.toggle("is-selected", row.dataset.eventId === eventId);
    });
  }

  function renderEventsPage(page) {
    currentEventsPage = page;
    const meta = document.getElementById("events-meta");
    const status = document.getElementById("events-status");
    const body = document.getElementById("events-body");
    const prev = document.getElementById("events-prev");
    const next = document.getElementById("events-next");
    if (!body) return;

    const rows = page.events || [];
    const total = page.total || 0;
    const start = total ? eventsPageOffset + 1 : 0;
    const end = Math.min(total, eventsPageOffset + rows.length);
    if (meta) {
      const retention = page.retention_hours ? `retention ${page.retention_hours}h` : "retention —";
      meta.textContent = `${retention} • ${fmtNum(total)} total`;
    }
    if (status) {
      status.textContent = total
        ? `Showing ${fmtNum(start)}–${fmtNum(end)} of ${fmtNum(total)} events`
        : "No matching events.";
    }
    if (prev) prev.disabled = eventsPageOffset <= 0;
    if (next) next.disabled = (eventsPageOffset + rows.length) >= total;

    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="9" class="empty-cell">No matching proxy events.</td></tr>';
      currentEventSelectionId = null;
      renderEventDetails(null);
      return;
    }

    body.innerHTML = rows.map((row) => {
      const upstream = row.raw_output_tokens || 0;
      const returned = row.output_tokens || 0;
      const saved = upstream - returned;
      const savedStr = saved < 0 ? `(${fmtNum(Math.abs(saved))})` : fmtNum(saved);
      const ts = row.ts ? new Date(row.ts * 1000).toLocaleTimeString() : "—";
      const qualified = row.qualified || row.tool || row.method || "—";
      const rowClass = row.error ? "is-error" : "";
      return `
        <tr class="${rowClass}" data-event-id="${escapeHtml(row.event_id)}" onclick="selectEvent('${escapeHtml(row.event_id)}')">
          <td>${escapeHtml(ts)}</td>
          <td>${escapeHtml(row.backend || "—")}</td>
          <td><code>${escapeHtml(row.method || "—")}</code></td>
          <td><code>${escapeHtml(qualified)}</code></td>
          <td class="num">${fmtNum(row.input_tokens || 0)}</td>
          <td class="num">${fmtNum(upstream)}</td>
          <td class="num">${fmtNum(returned)}</td>
          <td class="num">${savedStr}</td>
          <td class="num">${Number(row.latency_ms || 0).toFixed(0)} ms</td>
        </tr>`;
    }).join("");

    const selectionStillVisible = rows.some((row) => row.event_id === currentEventSelectionId);
    selectEvent(selectionStillVisible ? currentEventSelectionId : rows[0].event_id);
  }

  async function loadEventHistory(offset) {
    if (eventsFetchInflight) return;
    if (typeof offset === "number") eventsPageOffset = Math.max(0, offset);
    eventsFetchInflight = true;
    const status = document.getElementById("events-status");
    if (status) status.textContent = "Loading event history…";
    try {
      const filters = currentEventFilters();
      const params = new URLSearchParams({
        limit: String(eventsPageLimit),
        offset: String(eventsPageOffset),
      });
      if (filters.backend) params.set("backend", filters.backend);
      if (filters.method) params.set("method", filters.method);
      if (filters.tool) params.set("tool", filters.tool);
      if (filters.errors_only) params.set("errors_only", "1");
      const r = await fetch(`/api/events?${params.toString()}`);
      if (r.status === 503) {
        if (status) status.textContent = "event store not initialised";
        return;
      }
      if (!r.ok) {
        if (status) status.textContent = "error " + r.status;
        return;
      }
      renderEventsPage(await r.json());
    } catch (err) {
      if (status) status.textContent = "error: " + err.message;
    } finally {
      eventsFetchInflight = false;
    }
  }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  // ── Connections (auth providers) ───────────────────────────────────
  // Renders one card per provider returned by /api/auth/providers.
  // Connect button POSTs to /api/auth/<name>/start, then opens
  // verification_uri_complete in a new tab and consumes the SSE
  // stream at /api/auth/<name>/stream until terminal state.
  let connectModalSse = null;
  let connectModalProvider = null;
  let connectModalSession = null;

  async function loadConnections() {
    const meta = document.getElementById("connections-meta");
    const list = document.getElementById("connections-list");
    if (!list) return;
    if (meta) meta.textContent = "loading...";
    list.innerHTML = '<span class="docs-empty">Loading providers...</span>';
    let providers = [];
    try {
      const r = await fetch("/api/auth/providers");
      if (!r.ok) throw new Error("HTTP " + r.status);
      const body = await r.json();
      providers = Array.isArray(body.providers) ? body.providers : [];
    } catch (err) {
      if (meta) meta.textContent = "error: " + err.message;
      list.innerHTML = '<span class="docs-empty">Failed to load providers.</span>';
      return;
    }
    if (meta) {
      meta.textContent = providers.length === 0
        ? "no providers configured"
        : `${providers.length} provider${providers.length === 1 ? "" : "s"}`;
    }
    list.innerHTML = "";
    if (providers.length === 0) {
      const p = document.createElement("span");
      p.className = "docs-empty";
      p.textContent = "No auth providers configured. Edit configs/auth-providers.json.";
      list.appendChild(p);
      return;
    }
    providers.forEach((entry) => list.appendChild(renderConnectionCard(entry)));
  }

  function renderConnectionCard(entry) {
    const card = document.createElement("div");
    card.className = "connection-card";

    const body = document.createElement("div");
    body.className = "connection-card-body";

    const title = document.createElement("span");
    title.className = "connection-card-title";
    title.textContent = entry.name;
    body.appendChild(title);

    const status = document.createElement("span");
    status.className = "connection-card-status";
    const dot = document.createElement("span");
    dot.className = "connection-status-dot";
    if (entry.ready) dot.classList.add("connected");
    else if (entry.supports_device_flow || entry.supports_authorization_code) dot.classList.add("gated");
    status.appendChild(dot);
    const statusText = document.createElement("span");
    if (entry.ready && entry.identity && entry.identity.username) {
      statusText.textContent = "Connected";
    } else if (entry.ready) {
      statusText.textContent = "Ready (legacy)";
    } else if (entry.supports_device_flow || entry.supports_authorization_code) {
      statusText.textContent = "Not connected";
    } else {
      statusText.textContent = "Always available";
    }
    statusText.appendChild(document.createTextNode(" \u00b7 " + entry.type));
    status.appendChild(statusText);
    body.appendChild(status);

    if (entry.identity && entry.identity.username) {
      const ident = document.createElement("span");
      ident.className = "connection-card-identity";
      if (entry.identity.avatar_url) {
        const img = document.createElement("img");
        img.src = entry.identity.avatar_url;
        img.alt = "";
        ident.appendChild(img);
      }
      const nm = document.createElement("span");
      nm.textContent = "@" + entry.identity.username;
      ident.appendChild(nm);
      body.appendChild(ident);
    }

    if (entry.membership_hint) {
      const hint = document.createElement("span");
      hint.className = "connection-card-hint";
      hint.textContent = "Membership required: " + entry.membership_hint;
      body.appendChild(hint);
    }

    const actions = document.createElement("div");
    actions.className = "connection-card-actions";
    if (entry.supports_device_flow || entry.supports_authorization_code) {
      const connect = document.createElement("button");
      connect.type = "button";
      connect.className = "btn btn-mini " + (entry.ready ? "btn-outline" : "btn-primary");
      connect.textContent = entry.ready ? "Reconnect" : "Connect";
      connect.addEventListener("click", () => startConnect(entry));
      actions.appendChild(connect);
    }
    if (entry.ready && entry.type !== "static" && entry.type !== "passthrough") {
      const signout = document.createElement("button");
      signout.type = "button";
      signout.className = "btn btn-mini btn-outline";
      signout.textContent = "Sign out";
      signout.addEventListener("click", () => signOutProvider(entry));
      actions.appendChild(signout);
    }

    card.appendChild(body);
    card.appendChild(actions);
    return card;
  }

  async function startConnect(entry) {
    showConnectModal(entry);
    setConnectModalStatus("Requesting authorization URL...", null);
    let session;
    try {
      const r = await fetch(`/api/auth/${encodeURIComponent(entry.name)}/start`, {
        method: "POST",
      });
      if (!r.ok) {
        const body = await r.text();
        throw new Error(`HTTP ${r.status}: ${body}`);
      }
      session = await r.json();
    } catch (err) {
      setConnectModalStatus("Failed to start: " + err.message, "error");
      return;
    }
    connectModalSession = session;
    connectModalProvider = entry.name;
    const codeRow = document.getElementById("connect-modal-code-row");
    const codeEl = document.getElementById("connect-modal-code");
    if (codeRow) {
      if (session.user_code) codeRow.classList.remove("hidden");
      else codeRow.classList.add("hidden");
    }
    if (codeEl) codeEl.textContent = session.user_code || "";
    const link = document.getElementById("connect-modal-authorize-link");
    if (link) {
      link.href = session.authorization_url || session.verification_uri_complete || session.verification_uri;
      link.style.display = "inline-flex";
    }
    setConnectModalStatus("Waiting for authorization in browser...", null);
    streamConnectStatus(entry.name, session.session_id);
  }

  function streamConnectStatus(provider, sessionId) {
    if (connectModalSse) {
      try { connectModalSse.close(); } catch (e) {}
      connectModalSse = null;
    }
    const url = `/api/auth/${encodeURIComponent(provider)}/stream?session=${encodeURIComponent(sessionId)}`;
    const sse = new EventSource(url);
    connectModalSse = sse;
    sse.onmessage = (ev) => {
      let frame;
      try { frame = JSON.parse(ev.data); } catch { return; }
      if (frame.state === "complete") {
        const who = frame.identity && frame.identity.username
          ? "@" + frame.identity.username
          : "your account";
        setConnectModalStatus("Connected as " + who, "complete");
        try { sse.close(); } catch (e) {}
        connectModalSse = null;
        // Refresh the cards and server catalog state so auth-gated
        // passthrough backends populate their details pane immediately.
        setTimeout(() => {
          closeConnectModal();
          loadConnections();
          refreshStatus();
        }, 1500);
      } else if (frame.state === "expired") {
        setConnectModalStatus("Code expired. Click Connect again.", "error");
        try { sse.close(); } catch (e) {}
        connectModalSse = null;
      } else if (frame.state === "error") {
        setConnectModalStatus("Error: " + (frame.error || "unknown"), "error");
        try { sse.close(); } catch (e) {}
        connectModalSse = null;
      }
    };
    sse.onerror = () => {
      // Browser may auto-reconnect; don't tear down on transient errors.
    };
  }

  function showConnectModal(entry) {
    const backdrop = document.getElementById("connect-modal-backdrop");
    const title = document.getElementById("connect-modal-title");
    const hint = document.getElementById("connect-modal-hint");
    const codeRow = document.getElementById("connect-modal-code-row");
    const link = document.getElementById("connect-modal-authorize-link");
    if (!backdrop) return;
    backdrop.classList.remove("hidden");
    if (title) title.textContent = "Connect " + entry.name;
    if (hint) {
      if (entry.membership_hint) {
        hint.textContent = "You must be a member of " + entry.membership_hint
          + " to authorize. The browser will reject the consent otherwise.";
        hint.classList.remove("hidden");
      } else {
        hint.classList.add("hidden");
      }
    }
    if (codeRow) codeRow.classList.add("hidden");
    if (link) link.style.display = "none";
  }

  function closeConnectModal() {
    const backdrop = document.getElementById("connect-modal-backdrop");
    if (backdrop) backdrop.classList.add("hidden");
    if (connectModalSse) {
      try { connectModalSse.close(); } catch (e) {}
      connectModalSse = null;
    }
    connectModalProvider = null;
    connectModalSession = null;
  }

  function setConnectModalStatus(text, kind) {
    const el = document.getElementById("connect-modal-status");
    if (!el) return;
    el.textContent = text;
    el.classList.remove("complete", "error");
    if (kind) el.classList.add(kind);
  }

  function copyConnectCode() {
    const codeEl = document.getElementById("connect-modal-code");
    if (!codeEl || !navigator.clipboard) return;
    navigator.clipboard.writeText(codeEl.textContent || "").catch(() => {});
  }

  async function signOutProvider(entry) {
    if (!confirm(`Sign out of ${entry.name}? You'll need to re-authorize to use it again.`)) return;
    try {
      const r = await fetch(`/api/auth/${encodeURIComponent(entry.name)}/revoke`, {
        method: "POST",
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
    } catch (err) {
      alert("Sign out failed: " + err.message);
      return;
    }
    loadConnections();
    refreshStatus();
  }

  // Click outside modal to close.
  (function () {
    const backdrop = document.getElementById("connect-modal-backdrop");
    if (!backdrop) return;
    backdrop.addEventListener("click", (ev) => {
      if (ev.target === backdrop) closeConnectModal();
    });
  })();

  // Initial status
  refreshStatus();
