HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZELOSMCP</title>
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

  /* ── App shell (three-column landscape grid) ──
     Designed for wide displays. Uses the full viewport up to 2000px so
     the activity log and pincher dashboard get the room they need. The
     right column is sized to fit a fully-formed MCP URL on one line
     (`http://localhost:8000/<name>/mcp`) plus pills + a STOP button. */
  .app-topbar {
    display: flex;
    align-items: center;
    gap: 12px;
    max-width: 2000px;
    margin: 0 auto;
    padding: 24px 32px 0;
  }

  .app-shell {
    display: grid;
    grid-template-columns: 220px minmax(0, 1fr) 480px;
    gap: 32px;
    max-width: 2000px;
    margin: 0 auto;
    padding: 24px 32px 32px;
    align-items: start;
  }

  .left-col, .mid-col, .right-col { min-width: 0; }

  .right-col {
    position: sticky;
    top: 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  /* ── Left nav ── */
  .nav-group { margin-bottom: 24px; }

  .nav-group-label {
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--mid);
    margin-bottom: 8px;
  }

  .nav-item {
    display: block;
    width: 100%;
    text-align: left;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 8px 12px;
    font-family: var(--font);
    font-size: 14px;
    font-weight: 500;
    color: var(--black);
    cursor: pointer;
    margin-bottom: 4px;
    transition: background 0.1s ease, color 0.1s ease, border-color 0.1s ease;
  }
  .nav-item:hover { background: var(--surface); }
  .nav-item.active {
    background: var(--black);
    color: var(--white);
    border-color: var(--black);
  }

  /* ── Views (only one visible at a time) ── */
  .view { display: none; }
  .view.active { display: block; }

  /* ── Pincher dashboard iframe ── */
  .dashboard-frame {
    display: block;
    width: 100%;
    height: calc(100vh - 200px);
    min-height: 480px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--white);
  }
  .dashboard-empty {
    color: var(--mid);
    font-size: 14px;
    line-height: 1.6;
  }
  .dashboard-empty code {
    font-family: var(--mono);
    font-size: 13px;
    background: var(--white);
    padding: 2px 6px;
    border-radius: 4px;
  }
  .dashboard-meta {
    margin-left: auto;
    font-family: var(--mono);
    font-size: 12px;
    font-weight: 500;
    color: var(--mid);
    text-transform: none;
    letter-spacing: 0;
  }

  /* ── Savings dashboard ── */
  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
  }
  .kpi {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    background: var(--white);
  }
  .kpi-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--mid);
    margin-bottom: 6px;
  }
  .kpi-value {
    font-size: 22px;
    font-weight: 600;
    font-family: var(--mono);
  }
  .savings-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .savings-table th,
  .savings-table td {
    padding: 8px 10px;
    border-bottom: 1px solid var(--border);
    text-align: left;
    vertical-align: middle;
  }
  .savings-table th {
    font-weight: 600;
    color: var(--mid);
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.06em;
  }
  .savings-table td.num,
  .savings-table th.num { text-align: right; font-family: var(--mono); }
  .savings-table .empty-cell {
    color: var(--mid);
    font-style: italic;
    text-align: center;
    padding: 20px;
  }
  .backend-bars {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .backend-bar {
    display: grid;
    grid-template-columns: 140px 1fr 90px;
    gap: 12px;
    align-items: center;
    font-size: 13px;
  }
  .backend-bar-name { font-family: var(--mono); }
  .backend-bar-track {
    height: 10px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 5px;
    overflow: hidden;
  }
  .backend-bar-fill {
    height: 100%;
    background: var(--accent);
  }
  .backend-bar-count {
    text-align: right;
    font-family: var(--mono);
    color: var(--mid);
  }
  .savings-pre {
    font-family: var(--mono);
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 360px;
    overflow: auto;
    color: var(--black);
  }

  /* Below ~1240px the 220 + 480 columns leave the middle column too
     narrow (the activity log starts wrapping awkwardly). Collapse to a
     single column with the system actions floated up below the nav. */
  @media (max-width: 1240px) {
    .app-shell { grid-template-columns: 1fr; gap: 24px; padding: 24px; }
    .app-topbar { padding: 24px 24px 0; }
    .left-col { order: 1; }
    .right-col { order: 2; position: static; top: auto; }
    .mid-col { order: 3; }
  }

  /* ── Top bar ── */
  .header {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 1;
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
    flex-shrink: 0;
  }

  /* ── Connections (auth providers) ── */
  .connections-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .connection-card {
    background: var(--white);
    border-radius: 12px;
    padding: 16px 20px;
    display: flex;
    align-items: center;
    gap: 16px;
    border: 1px solid var(--border);
  }
  .connection-card-body {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  .connection-card-title {
    font-size: 14px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--black);
  }
  .connection-card-status {
    font-size: 12px;
    color: var(--mid);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .connection-status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--mid);
    flex-shrink: 0;
  }
  .connection-status-dot.connected {
    background: #15803d;
  }
  .connection-status-dot.gated {
    background: #c2410c;
  }
  .connection-card-hint {
    font-size: 11px;
    color: var(--mid);
    font-style: italic;
  }
  .connection-card-identity {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--black);
  }
  .connection-card-identity img {
    width: 22px;
    height: 22px;
    border-radius: 50%;
  }
  .connection-card-actions {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }

  /* Device-flow modal */
  .connect-modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .connect-modal-backdrop.hidden {
    display: none;
  }
  .connect-modal {
    background: var(--white);
    border-radius: 16px;
    padding: 28px;
    min-width: 360px;
    max-width: 480px;
    position: relative;
    box-shadow: 0 12px 48px rgba(0, 0, 0, 0.25);
  }
  .connect-modal h3 {
    margin: 0 0 12px 0;
    font-size: 18px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .connect-modal-hint {
    background: #fef3c7;
    border-left: 3px solid #d97706;
    padding: 8px 12px;
    margin: 0 0 12px 0;
    font-size: 12px;
    color: #92400e;
  }
  .connect-modal-status {
    margin-top: 16px;
    font-size: 12px;
    color: var(--mid);
  }
  .connect-modal-status.complete {
    color: #15803d;
    font-weight: 700;
  }
  .connect-modal-status.error {
    color: #b91c1c;
    font-weight: 700;
  }
  .connect-modal-code-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 12px 0;
    background: #f3f4f6;
    padding: 10px 14px;
    border-radius: 8px;
    font-family: var(--mono);
  }
  .connect-modal-code-row code {
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--black);
  }
  .connect-modal-code-label {
    font-size: 11px;
    text-transform: uppercase;
    color: var(--mid);
    letter-spacing: 0.05em;
  }
  .connect-modal-close {
    position: absolute;
    top: 12px;
    right: 16px;
    background: transparent;
    border: none;
    font-size: 24px;
    cursor: pointer;
    color: var(--mid);
    line-height: 1;
  }
  .connect-modal-close:hover {
    color: var(--black);
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
    flex-wrap: wrap;
    gap: 8px 12px;
    background: var(--white);
    border-radius: 12px;
    padding: 12px 16px;
    border: 1px solid var(--border);
    cursor: pointer;
    transition: background 0.1s ease;
    user-select: none;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .server-row > * { min-width: 0; }
  .server-row:hover {
    background: var(--surface);
  }

  .server-name {
    font-family: var(--mono);
    font-size: 14px;
    font-weight: 700;
    color: var(--black);
  }

  .server-row .grow { flex: 1; min-width: 0; }

  /* Specificity override: the meta also carries the `.grow` class
     (legacy from the old single-row layout), and `.server-row .grow`
     would otherwise collapse it to `flex: 1 1 0%` and force a
     character-per-line break-all when it lands on its own wrap line.
     We pin it to a full-width line of its own with a real basis. */
  .server-row .server-meta {
    flex: 0 0 100%;
    min-width: 0;
    width: 100%;
    margin-left: 24px;
    font-family: var(--mono);
    font-size: 12px;
    color: var(--mid);
    word-break: break-word;
    overflow-wrap: anywhere;
  }

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

  /* ── Repositories panel (right column, collapsible) ── */
  .repos-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
  }
  .section-label-toggle {
    background: none;
    border: 0;
    padding: 0;
    cursor: pointer;
    font: inherit;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--black);
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1;
    text-align: left;
  }
  .section-label-toggle .caret {
    font-size: 12px;
    color: var(--mid);
    width: 10px;
    display: inline-block;
    text-align: center;
  }
  .repos-count {
    color: var(--mid);
    font-weight: 400;
    text-transform: none;
    letter-spacing: 0;
    font-size: 12px;
    margin-left: 4px;
  }
  .repos-refresh-btn {
    padding: 4px 10px;
    font-size: 12px;
    line-height: 1;
    background: var(--white);
    border: 1px solid var(--border);
    border-radius: 999px;
    cursor: pointer;
  }
  .repos-refresh-btn:hover { background: var(--surface); }
  .repos-filter {
    width: 100%;
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-family: var(--mono);
    font-size: 12px;
    margin-bottom: 8px;
    background: var(--white);
  }
  .repos-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 360px;
    overflow-y: auto;
  }
  .repo-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border-radius: 6px;
    cursor: pointer;
    background: none;
    border: 1px solid transparent;
    text-align: left;
    width: 100%;
    font: inherit;
    color: var(--black);
    min-width: 0;
  }
  .repo-row:hover {
    background: var(--surface);
    border-color: var(--border);
  }
  .repo-row.active {
    background: var(--surface);
    border-color: var(--black);
  }
  .repo-row .repo-name {
    font-weight: 700;
    font-size: 13px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 160px;
  }
  .repo-row .repo-path {
    color: var(--mid);
    font-family: var(--mono);
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }
  .repo-pill {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--mid);
    line-height: 1;
    flex-shrink: 0;
  }
  .repo-pill.on {
    color: var(--success);
    border-color: var(--success);
  }
  .repos-empty {
    color: var(--mid);
    font-size: 12px;
    font-style: italic;
    padding: 8px 4px;
  }

  /* ── Repo details (middle pane) ── */
  .rule-write-form {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 8px 16px;
    align-items: center;
    margin: 12px 0;
  }
  .rule-write-form label {
    display: contents;
    font-size: 13px;
    color: var(--black);
  }
  .rule-write-form select,
  .rule-write-form input {
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-family: var(--font);
    font-size: 13px;
    background: var(--white);
  }
  .rule-write-form input[disabled] {
    background: var(--surface);
    color: var(--mid);
  }
  .rule-write-actions {
    display: flex;
    gap: 8px;
    margin: 12px 0;
    flex-wrap: wrap;
  }
  .rule-write-status {
    font-size: 13px;
    color: var(--mid);
    min-height: 1.2em;
    margin-top: 8px;
  }
  .rule-write-status.ok { color: var(--success); }
  .rule-write-status.err { color: var(--error); }
  .repo-paths {
    font-size: 12px;
    color: var(--mid);
    font-family: var(--mono);
    margin: 0 0 12px 0;
  }
  .repo-paths code {
    color: var(--black);
  }

  .pill {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    padding: 3px 8px;
    border-radius: 999px;
    line-height: 1;
    white-space: nowrap;
    flex-shrink: 0;
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
    height: calc(100vh - 240px);
    min-height: 480px;
    overflow-y: auto;
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.6;
    color: var(--black);
  }

  .log-viewer:empty::before {
    content: "Waiting for events...";
    color: var(--mid);
  }

  .log-filter {
    width: 100%;
    box-sizing: border-box;
    padding: 8px 12px;
    margin-bottom: 8px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--white);
    color: var(--black);
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.4;
    outline: none;
  }
  .log-filter:focus {
    border-color: var(--accent);
  }

  .log-line {
    white-space: pre-wrap;
    word-break: break-all;
  }

  .log-line.error {
    color: var(--error);
  }

  .log-line.hidden {
    display: none;
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

  /* ── Documentation view ── */
  /* Two modes share the same view: index (list of cards) and content
     (rendered HTML). Switched by toggling .hidden on each container. */
  .docs-toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
  }
  .docs-toolbar .btn-back {
    width: auto;
    padding: 8px 18px;
    font-size: 12px;
    border-radius: 999px;
  }
  .docs-toolbar .docs-current {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--mid);
  }
  .docs-index {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
  }
  .docs-card {
    display: block;
    width: 100%;
    text-align: left;
    background: var(--white);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 18px;
    cursor: pointer;
    font-family: var(--font);
    color: var(--black);
    transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.05s ease;
  }
  .docs-card:hover {
    border-color: var(--black);
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.06);
  }
  .docs-card:active { transform: translateY(1px); }
  .docs-card-title {
    font-size: 16px;
    font-weight: 700;
    margin-bottom: 4px;
    color: var(--black);
  }
  .docs-card-slug {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--mid);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .docs-content {
    background: var(--white);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px 28px;
    font-size: 15px;
    line-height: 1.65;
    color: var(--black);
    overflow-x: auto;
    min-height: 240px;
  }
  .docs-content h1, .docs-content h2, .docs-content h3,
  .docs-content h4, .docs-content h5, .docs-content h6 {
    font-weight: 700;
    line-height: 1.25;
    margin: 1.4em 0 0.5em;
  }
  .docs-content h1 { font-size: 26px; margin-top: 0; }
  .docs-content h2 { font-size: 20px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
  .docs-content h3 { font-size: 17px; }
  .docs-content h4 { font-size: 15px; color: var(--mid); text-transform: uppercase; letter-spacing: 0.05em; }
  .docs-content p { margin: 0 0 1em; }
  .docs-content a { color: var(--accent); text-decoration: none; }
  .docs-content a:hover { text-decoration: underline; }
  .docs-content ul, .docs-content ol { margin: 0 0 1em 1.4em; }
  .docs-content li { margin-bottom: 0.25em; }
  .docs-content code {
    font-family: var(--mono);
    font-size: 13px;
    background: var(--surface);
    padding: 2px 6px;
    border-radius: 4px;
  }
  .docs-content pre {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    overflow-x: auto;
    margin: 0 0 1em;
  }
  .docs-content pre code {
    background: transparent;
    padding: 0;
    font-size: 13px;
    line-height: 1.55;
  }
  .docs-content blockquote {
    border-left: 3px solid var(--border);
    color: var(--mid);
    padding-left: 14px;
    margin: 0 0 1em;
  }
  .docs-content table {
    border-collapse: collapse;
    width: 100%;
    margin: 0 0 1em;
    font-size: 14px;
  }
  .docs-content th, .docs-content td {
    border: 1px solid var(--border);
    padding: 8px 10px;
    text-align: left;
    vertical-align: top;
  }
  .docs-content th {
    background: var(--surface);
    font-weight: 700;
  }
  .docs-content hr {
    border: none;
    border-top: 1px solid var(--border);
    margin: 1.5em 0;
  }
  .docs-content img { max-width: 100%; height: auto; }
  /* Mermaid blocks: rendered SVGs from mermaid.js. The library injects
     SVGs directly into the .mermaid container; we just give them a
     light frame consistent with the other code blocks. */
  .docs-content .mermaid {
    background: var(--white);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px;
    margin: 0 0 1em;
    overflow-x: auto;
    text-align: center;
  }
  .docs-content .mermaid svg { max-width: 100%; height: auto; }
  .docs-empty {
    color: var(--mid);
    font-style: italic;
    font-size: 14px;
  }

  /* Stack the docs index on narrow viewports — single column, full
     width in the middle pane. */
  @media (max-width: 1240px) {
    .docs-index { grid-template-columns: 1fr; }
  }

  /* ── Server details view (catalog rendered in the center pane) ── */
  .details-empty {
    color: var(--mid);
    font-style: italic;
    font-size: 14px;
  }
  .details-body .cat-group { margin-bottom: 20px; }
  .details-body .cat-group:last-child { margin-bottom: 0; }
  .details-body .cat-label {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--mid);
    margin-bottom: 10px;
  }
  .details-body ul { list-style: none; }
  .details-body li {
    padding: 6px 0;
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.55;
    border-left: 2px solid var(--border);
    padding-left: 12px;
    margin-bottom: 8px;
  }
  .details-body li code { font-weight: 700; }
  .details-body li .desc {
    font-family: var(--font);
    color: var(--mid);
    font-size: 13px;
    margin-top: 3px;
    white-space: pre-wrap;
  }
  .details-body li details { margin-top: 6px; }
  .details-body li details summary {
    cursor: pointer;
    color: var(--mid);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .details-body li pre {
    background: var(--surface);
    border-radius: 6px;
    padding: 10px 12px;
    font-size: 12px;
    line-height: 1.5;
    margin-top: 6px;
    overflow-x: auto;
  }
  .details-body .empty {
    color: var(--mid);
    font-style: italic;
    font-size: 13px;
  }

  /* ── Utilities ── */
  .hidden { display: none !important; }
</style>
</head>
<body>

<!-- Top bar -->
<div class="app-topbar">
  <div class="header">
    <span class="wordmark">ZELOSMCP</span>
    <span class="header-spacer"></span>
    <a class="docs-link" href="/docs" target="_blank" rel="noopener">API Docs</a>
  </div>
</div>

<div class="app-shell">

  <!-- Left nav -->
  <aside class="left-col">
    <div class="nav-group">
      <div class="nav-group-label">Configuration</div>
      <button type="button" class="nav-item active" data-view="configuration">Servers config</button>
    </div>
    <div class="nav-group">
      <div class="nav-group-label">Rules generation</div>
      <button type="button" class="nav-item" data-view="rules">Cursor rule (.mdc)</button>
    </div>
    <div class="nav-group">
      <div class="nav-group-label">Dashboards</div>
      <button type="button" class="nav-item" data-view="savings">Savings</button>
      <button type="button" class="nav-item" data-view="pincher-dashboard">Pincher</button>
    </div>
    <div class="nav-group">
      <div class="nav-group-label">Authentication</div>
      <button type="button" class="nav-item" data-view="connections">Connections</button>
    </div>
    <div class="nav-group">
      <div class="nav-group-label">Event logging</div>
      <button type="button" class="nav-item" data-view="logs">Activity</button>
    </div>
    <div class="nav-group">
      <div class="nav-group-label">Help</div>
      <button type="button" class="nav-item" data-view="docs">Documentation</button>
    </div>
  </aside>

  <!-- Middle content (only one .view is .active at a time) -->
  <main class="mid-col">

    <!-- Configuration view -->
    <section class="view active" data-view="configuration">
      <p class="intro">Paste a Cursor <code>mcp.json</code>-style config below. Each server is mounted at <code>localhost:8000/&lt;name&gt;/mcp</code> (raw passthrough), and the aggregate endpoint at <code>localhost:8000/mcp</code> exposes every running server's tools, prompts, and resources under the <code>&lt;server&gt;__&lt;name&gt;</code> namespace (resource URIs keep their original form). The aggregate is the recommended way to wire Cursor &mdash; one entry, every backend.</p>

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
        </div>
      </div>

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
    "zelosmcp-aggregate": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}</pre>
          </div>
        </div>
      </div>

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
    "zelosmcp-aggregate": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}</pre>
          </div>
        </div>
      </div>
    </section>

    <!-- Rules generation view -->
    <section class="view" data-view="rules">
      <div class="section">
        <div class="section-label">
          <span>Cursor rule (.mdc)</span>
          <span class="rule-access-control">
            <label for="rule-tool-use">Tool use:</label>
            <select id="rule-tool-use" onchange="onRuleToolUseChange()">
              <option value="priority" selected>Priority (encourage MCP tools)</option>
              <option value="available">Available (neutral catalog)</option>
            </select>
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
            <strong>Tool use</strong> &mdash; <em>Priority</em> (default) tells the agent to prefer MCP tools over shell commands and
            includes a curated playbook for the mandatory backends; <em>Available</em> emits a neutral catalog with no prioritization.
            <strong>Read-only</strong> mode forbids the agent from calling mutating tools &mdash; safe default for
            inspection-style projects (code review, demos). Switch to <strong>Read-write</strong> when the agent needs
            to make changes through the MCPs. Save the body below as <code>.cursor/rules/zelosmcp.mdc</code> in any
            workspace (or <code>~/.cursor/rules/</code> for global).
          </p>
          <div class="snippet">
            <button class="snippet-copy" id="copy-rule-btn" onclick="copyRule()">Copy</button>
            <pre id="cursor-rule">Loading...</pre>
          </div>
        </div>
      </div>
    </section>

    <!-- Savings dashboard view -->
    <section class="view" data-view="savings">
      <div class="section">
        <div class="section-label">
          <span>Token savings</span>
          <span class="dashboard-meta" id="savings-meta">&mdash;</span>
        </div>
        <div class="card">
          <p class="intro" style="margin: 0 0 12px 0;">
            Aggregated savings across three sources: <strong>tool-list compression</strong>
            (raw vs. wrapper-pair tokens served on every <code>tools/list</code>),
            <strong>per-call accounting</strong> (input + output tokens of every
            <code>tools/call</code>), and <strong>pincher self-reported BPE savings</strong>
            (parsed from each pincher response's <code>_meta</code> envelope plus
            periodic <code>pincher__stats</code> snapshots). Token counts use
            <code>tiktoken</code>'s <code>cl100k_base</code> encoding when
            available; otherwise a <code>len/4</code> heuristic.
          </p>

          <div class="kpi-grid" id="savings-kpis">
            <div class="kpi"><div class="kpi-label">Tokens saved (compression)</div><div class="kpi-value" id="kpi-compression-saved">&mdash;</div></div>
            <div class="kpi"><div class="kpi-label">Tokens saved (pincher)</div><div class="kpi-value" id="kpi-pincher-saved">&mdash;</div></div>
            <div class="kpi"><div class="kpi-label">Calls recorded</div><div class="kpi-value" id="kpi-calls">&mdash;</div></div>
            <div class="kpi"><div class="kpi-label">Cost avoided (pincher)</div><div class="kpi-value" id="kpi-cost">&mdash;</div></div>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-label">Compression by backend</div>
        <div class="card">
          <table class="savings-table" id="savings-compression-table">
            <thead>
              <tr>
                <th>Backend</th>
                <th>Level</th>
                <th class="num">Raw tokens</th>
                <th class="num">Compressed</th>
                <th class="num">Saved</th>
                <th class="num">% saved</th>
              </tr>
            </thead>
            <tbody id="savings-compression-body">
              <tr><td colspan="6" class="empty-cell">No compression snapshots yet. Run a <code>tools/list</code> against <code>/mcp</code>.</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="section">
        <div class="section-label">Top tools by token volume</div>
        <div class="card">
          <table class="savings-table" id="savings-top-tools">
            <thead>
              <tr>
                <th>Tool</th>
                <th class="num">Calls</th>
                <th class="num">Tokens</th>
                <th class="num">Avg latency</th>
              </tr>
            </thead>
            <tbody id="savings-top-tools-body">
              <tr><td colspan="4" class="empty-cell">No calls recorded yet.</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="section">
        <div class="section-label">Per-backend activity</div>
        <div class="card">
          <div id="savings-backend-bars" class="backend-bars">
            <div class="empty-cell">No call events yet.</div>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-label">Pincher session stats</div>
        <div class="card">
          <pre id="savings-pincher-stats" class="savings-pre">No pincher__stats snapshot yet. Pincher must be running and the poller (<code>ZELOSMCP_PINCHER_POLL_SECS</code>, default 60s) must have run at least once.</pre>
        </div>
      </div>
    </section>

    <!-- Pincher dashboard view -->
    <section class="view" data-view="pincher-dashboard">
      <div class="section">
        <div class="section-label">
          <span>Pincher dashboard</span>
          <span class="dashboard-meta" id="pincher-dashboard-meta">&mdash;</span>
        </div>
        <div class="card dashboard-empty hidden" id="pincher-dashboard-empty">
          Pincher isn't running. Start the <code>pincher</code> backend from the
          Servers panel on the right to load its dashboard. The dashboard URL is
          read from the live <code>reverseProxy.mount</code> (defaults to
          <code>/pincher/v1/dashboard</code>).
        </div>
        <iframe
          id="pincher-dashboard-frame"
          class="dashboard-frame hidden"
          title="Pincher dashboard"
          referrerpolicy="no-referrer"
        ></iframe>
      </div>
    </section>

    <!-- Event logging view -->
    <section class="view" data-view="logs">
      <div class="section">
        <div class="section-label">Activity</div>
        <div class="card">
          <input
            type="text"
            id="log-filter"
            class="log-filter"
            placeholder="Filter (substring, e.g. pincher_)"
            autocomplete="off"
            spellcheck="false"
          />
          <div class="log-viewer" id="log-viewer"></div>
        </div>
      </div>
    </section>

    <!-- Connections view: per-provider auth cards + device-flow modal -->
    <section class="view" data-view="connections">
      <p class="intro">Per-user authentication for OAuth-protected MCP backends. Connecting a provider here unlocks the corresponding wrappers in <code>tools/list</code> at <code>localhost:8000/mcp</code> &mdash; until a backend's provider is connected, its wrappers are hidden from Cursor entirely.</p>

      <div class="section">
        <div class="section-label">
          <span>Connections</span>
          <span class="dashboard-meta" id="connections-meta">&mdash;</span>
        </div>
        <div id="connections-list" class="connections-list">
          <span class="docs-empty">Loading providers...</span>
        </div>
      </div>

      <!-- Device-flow modal. Hidden by default; shown via showConnectModal() -->
      <div class="connect-modal-backdrop hidden" id="connect-modal-backdrop">
        <div class="connect-modal" role="dialog" aria-modal="true" aria-labelledby="connect-modal-title">
          <button type="button" class="connect-modal-close" onclick="closeConnectModal()" aria-label="Close">&times;</button>
          <h3 id="connect-modal-title">Connect</h3>
          <p class="connect-modal-hint hidden" id="connect-modal-hint"></p>
          <p id="connect-modal-instructions">One-time browser authorization. We will open the upstream provider's authorize page in a new tab.</p>
          <div class="connect-modal-code-row hidden" id="connect-modal-code-row">
            <span class="connect-modal-code-label">Code:</span>
            <code id="connect-modal-code">&mdash;</code>
            <button type="button" class="btn-mini btn-outline" onclick="copyConnectCode()">Copy</button>
          </div>
          <a id="connect-modal-authorize-link" class="btn btn-primary" target="_blank" rel="noopener" style="display:none; text-decoration:none; margin-top: 16px;">Authorize on Upstream</a>
          <p class="connect-modal-status" id="connect-modal-status">Starting...</p>
        </div>
      </div>
    </section>

    <!-- Documentation view -->
    <section class="view" data-view="docs">
      <div class="section">
        <div class="section-label">
          <span id="docs-section-title">Documentation</span>
          <span class="dashboard-meta" id="docs-meta">&mdash;</span>
        </div>
        <div class="docs-toolbar hidden" id="docs-toolbar">
          <button type="button" class="btn btn-outline btn-back" onclick="showDocsIndex()">&larr; All docs</button>
          <span class="docs-current" id="docs-current">&mdash;</span>
        </div>
        <div id="docs-index" class="docs-index">
          <span class="docs-empty">Loading...</span>
        </div>
        <article class="docs-content hidden" id="docs-content"></article>
      </div>
    </section>

    <!-- Server details view (catalog rendered in the center pane) -->
    <section class="view" data-view="server-details">
      <div class="section">
        <div class="section-label">
          <span id="server-details-title">Server details</span>
          <span class="dashboard-meta" id="server-details-meta">&mdash;</span>
        </div>
        <div class="card">
          <div class="details-body" id="server-details-body">
            <p class="details-empty">Click <strong>Details</strong> on any server in the right column to inspect its tools, prompts, and resources here.</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Repo details view: rule editor + pincher index button for one repo -->
    <section class="view" data-view="repo-details">
      <div class="section">
        <div class="section-label">
          <span id="repo-details-title">Repository</span>
          <span class="dashboard-meta" id="repo-details-meta">&mdash;</span>
        </div>
        <div class="card">
          <p class="repo-paths" id="repo-details-paths">&mdash;</p>
          <p class="intro" style="margin: 0 0 12px 0;">
            Generate a Cursor rule and write it to <code>.cursor/rules/zelosmcp.mdc</code> in the selected repo, or register the repo with pincher so its tools are queryable. The rule body is identical to the one produced by <strong>Cursor rule (.mdc)</strong> in the left nav &mdash; same generator, same knobs.
          </p>

          <div class="rule-write-form">
            <label for="repo-rule-format">Format</label>
            <select id="repo-rule-format">
              <option value="cursor-mdc" selected>cursor-mdc (.cursor/rules/zelosmcp.mdc)</option>
              <option value="copilot-instructions">copilot-instructions (.github/copilot-instructions.md)</option>
            </select>
            <label for="repo-rule-tool-use">Tool use</label>
            <select id="repo-rule-tool-use">
              <option value="priority" selected>Priority (encourage MCP tools)</option>
              <option value="available">Available (neutral catalog)</option>
            </select>
            <label for="repo-rule-access">Access</label>
            <select id="repo-rule-access">
              <option value="read-only" selected>Read-only (safe)</option>
              <option value="read-write">Read-write (allows mutation)</option>
            </select>
            <label for="repo-rule-style">Style</label>
            <select id="repo-rule-style" onchange="onRepoStyleChange()">
              <option value="always-apply" selected>Always apply</option>
              <option value="scoped">Scoped (uses globs)</option>
            </select>
            <label for="repo-rule-globs">Globs</label>
            <input type="text" id="repo-rule-globs" placeholder="**/*.py" disabled>
          </div>

          <div class="rule-write-actions">
            <button type="button" class="btn btn-outline" onclick="previewRepoRule()">Preview</button>
            <button type="button" class="btn btn-primary" onclick="saveRepoRule()">Save rule to repo</button>
            <button type="button" class="btn btn-outline" onclick="indexRepo()">Index in pincher</button>
          </div>

          <div class="rule-write-status" id="repo-rule-status"></div>

          <div class="snippet" style="margin-top: 12px;">
            <pre id="repo-rule-preview">Click <strong>Preview</strong> to render the rule body that will be saved.</pre>
          </div>
        </div>
      </div>
    </section>

  </main>

  <!-- Right column: status badge, global action, servers list -->
  <aside class="right-col">
    <div class="card" style="display: flex; align-items: center; gap: 12px; padding: 16px;">
      <span class="badge stopped" id="badge">STOPPED</span>
      <span style="flex: 1;"></span>
    </div>
    <button class="btn btn-primary" id="action-btn" onclick="handleAction()">
      START
    </button>

    <div class="section" id="servers-section" style="margin: 0;">
      <div class="servers-header">
        <div class="section-label" style="margin: 0;">Servers</div>
        <a class="snippet-link" href="/catalog" target="_blank" rel="noopener">Full catalog</a>
      </div>
      <div class="card">
        <p class="intro" style="margin: 0 0 12px 0; font-size: 13px;">
          Click any row to inspect that backend's tools, prompts, and resources inline.
        </p>
        <div class="server-list" id="server-list"></div>
      </div>
    </div>

    <div class="section" id="repos-section" style="margin: 0;">
      <div class="repos-header">
        <button type="button"
                class="section-label-toggle"
                id="repos-toggle"
                aria-expanded="false"
                aria-controls="repos-collapse"
                onclick="toggleReposPanel()">
          <span class="caret" id="repos-caret">&#9656;</span>
          <span>Repositories</span>
          <span class="repos-count" id="repos-count">&mdash;</span>
        </button>
        <button type="button" class="repos-refresh-btn"
                id="repos-refresh-btn"
                title="Rescan /user_data_ro"
                onclick="refreshRepos()">&#x21bb;</button>
      </div>
      <div class="card" id="repos-collapse" hidden>
        <p class="intro" style="margin: 0 0 8px 0; font-size: 12px;">
          Git repos under <code>/user_data_ro</code>. Click a row to open the rule editor for that repo.
        </p>
        <input type="text"
               id="repos-filter"
               class="repos-filter"
               placeholder="Filter (substring of name or path)"
               autocomplete="off"
               spellcheck="false"
               oninput="onReposFilter()">
        <div class="repos-list" id="repos-list">
          <div class="repos-empty">Loading...</div>
        </div>
      </div>
    </div>
  </aside>

</div>

<!-- Mermaid: rendered for fenced ```mermaid blocks inside loaded docs.
     Loaded from a CDN so the page stays a single self-contained file
     (no static asset pipeline). securityLevel:'loose' lets links inside
     diagrams render normally; we never feed agent-supplied markdown
     through this path, only repo-controlled docs/*.md. -->
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>
  if (window.mermaid && typeof mermaid.initialize === "function") {
    mermaid.initialize({ startOnLoad: false, securityLevel: "loose", theme: "default" });
  }
</script>

<script>
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
      refreshPincherDashboard(false);
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
  // running-backends set OR the access / tool-use selectors change. The
  // signature includes both control values so toggling either dropdown
  // forces a refetch.
  async function refreshCursorRule(status) {
    const access = ruleAccessValue();
    const toolUse = ruleToolUseValue();
    const sig =
      "access:" + access + "|tool_use:" + toolUse + "|" +
      (status.servers || [])
        .map((s) => s.name + ":" + (s.running ? "1" : "0"))
        .join(",");
    if (sig === lastRuleSig) return;
    lastRuleSig = sig;
    try {
      const params = new URLSearchParams({ access, tool_use: toolUse });
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

  // Refetch /api/catalog whenever the running-backends set changes (same
  // signature pattern as refreshCursorRule). On success, re-render any
  // currently-expanded inline catalog blocks AND the center-pane
  // "Server details" view if one is currently selected.
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
    const filtered = q
      ? currentRepos.filter((r) => {
          const hay = (r.name + " " + r.path_ro).toLowerCase();
          return hay.includes(q);
        })
      : currentRepos;
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
    for (const repo of filtered) {
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

      const piPill = document.createElement("span");
      piPill.className = "repo-pill" + (repo.pincher_indexed ? " on" : "");
      piPill.textContent = "pincher";
      piPill.title = repo.pincher_indexed
        ? "indexed in pincher"
        : "not yet indexed";
      row.appendChild(piPill);

      list.appendChild(row);
    }
  }

  // ── Repo details (middle pane) ──────────────────────────────────────
  function showRepoDetails(repo) {
    currentDetailsRepo = repo;
    setView("repo-details");
    renderRepoDetails(repo);
    renderReposList(); // re-paint to mark the active row
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
      bits.push(repo.pincher_indexed ? "indexed" : "not indexed");
      meta.textContent = bits.join(" • ");
    }
    if (paths) {
      paths.innerHTML =
        "Read-only: <code>" + escapeHtml(repo.path_ro) + "</code><br>" +
        "Read-write: <code>" + escapeHtml(repo.path_rw) + "</code>";
    }
    if (status) { status.textContent = ""; status.className = "rule-write-status"; }
    if (preview) {
      preview.textContent = "Click Preview to render the rule body that will be saved.";
    }
    onRepoStyleChange();
  }

  function onRepoStyleChange() {
    const style = document.getElementById("repo-rule-style");
    const globs = document.getElementById("repo-rule-globs");
    if (!style || !globs) return;
    const scoped = style.value === "scoped";
    globs.disabled = !scoped;
    if (!scoped) globs.value = "";
  }

  function repoRuleQueryString() {
    const params = new URLSearchParams();
    const fmt = document.getElementById("repo-rule-format");
    const tu = document.getElementById("repo-rule-tool-use");
    const access = document.getElementById("repo-rule-access");
    const style = document.getElementById("repo-rule-style");
    const globs = document.getElementById("repo-rule-globs");
    if (fmt) params.set("format", fmt.value);
    if (tu) params.set("tool_use", tu.value);
    if (access) params.set("access", access.value);
    if (style) params.set("style", style.value);
    if (globs && globs.value) params.set("globs", globs.value);
    return params.toString();
  }

  function repoRuleBody() {
    const fmt = document.getElementById("repo-rule-format");
    const tu = document.getElementById("repo-rule-tool-use");
    const access = document.getElementById("repo-rule-access");
    const style = document.getElementById("repo-rule-style");
    const globs = document.getElementById("repo-rule-globs");
    return {
      path: currentDetailsRepo ? currentDetailsRepo.path_ro : null,
      format: fmt ? fmt.value : "cursor-mdc",
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

  async function indexRepo() {
    if (!currentDetailsRepo) return;
    const status = document.getElementById("repo-rule-status");
    if (status) { status.className = "rule-write-status"; status.textContent = "Indexing..."; }
    try {
      const r = await fetch("/api/repos/index", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: currentDetailsRepo.path_ro }),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        throw new Error(data.error || ("HTTP " + r.status));
      }
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

  function renderRepoDetailsMetaOnly() {
    if (!currentDetailsRepo) return;
    const meta = document.getElementById("repo-details-meta");
    if (!meta) return;
    const bits = [];
    bits.push(currentDetailsRepo.has_rule ? "rule present" : "no rule");
    bits.push(currentDetailsRepo.pincher_indexed ? "indexed" : "not indexed");
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
    if (name === "docs") loadDocsIndex();
    if (name === "connections") loadConnections();
  }
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.view)));

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
      meta.textContent = `${enc} • updated ${ts.toLocaleTimeString()}`;
    }

    const compressionSaved = data.compression_saved_tokens_total || 0;
    const pincherSaved = (data.pincher && data.pincher.tokens_saved_total) || 0;
    const totals = (data.calls && data.calls.totals) || {};
    const cost = (data.pincher && data.pincher.cost_avoided_usd_total) || 0;

    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    };
    set("kpi-compression-saved", fmtNum(compressionSaved));
    set("kpi-pincher-saved", fmtNum(pincherSaved));
    set("kpi-calls", fmtNum(totals.calls || 0));
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
        topBody.innerHTML = '<tr><td colspan="4" class="empty-cell">No calls recorded yet.</td></tr>';
      } else {
        topBody.innerHTML = rows.map((t) => `
          <tr>
            <td><code>${escapeHtml(t.qualified)}</code></td>
            <td class="num">${fmtNum(t.calls)}</td>
            <td class="num">${fmtNum(t.tokens)}</td>
            <td class="num">${t.avg_latency_ms.toFixed(0)} ms</td>
          </tr>
        `).join("");
      }
    }

    // Per-backend bars
    const barsHost = document.getElementById("savings-backend-bars");
    if (barsHost) {
      const rows = (data.calls && data.calls.per_backend) || [];
      if (!rows.length) {
        barsHost.innerHTML = '<div class="empty-cell">No call events yet.</div>';
      } else {
        const max = Math.max(1, ...rows.map((r) => r.calls || 0));
        barsHost.innerHTML = rows.map((b) => {
          const pct = Math.round(((b.calls || 0) / max) * 100);
          return `
            <div class="backend-bar">
              <div class="backend-bar-name">${escapeHtml(b.backend)}</div>
              <div class="backend-bar-track"><div class="backend-bar-fill" style="width: ${pct}%"></div></div>
              <div class="backend-bar-count">${fmtNum(b.calls)} calls</div>
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
        // Refresh the cards so the connected state shows up.
        setTimeout(() => { closeConnectModal(); loadConnections(); }, 1500);
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
<title>ZELOSMCP — Tool catalog</title>
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
    <span class="wordmark">ZELOSMCP</span>
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
