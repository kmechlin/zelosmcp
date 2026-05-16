import type { RunLogEntry, StaticResult } from "./types.js";

const MODE_LABELS: Record<string, string> = {
  null: "no compression",
  medium: "medium",
  max: "max",
};

function pad(s: string, len: number): string {
  return s.padEnd(len);
}

function padNum(n: number, len: number): string {
  return n.toLocaleString().padStart(len);
}

function modeLabel(mode: string): string {
  return MODE_LABELS[mode] ?? mode;
}

export function renderStaticTable(results: StaticResult[]): string {
  const lines: string[] = [];
  const header = `${pad("Mode", 18)} ${"Tools".padStart(8)} ${"Tokens".padStart(12)}`;
  lines.push(header);
  lines.push("-".repeat(header.length));

  for (const r of results) {
    lines.push(
      `${pad(modeLabel(r.mode), 18)} ${padNum(r.toolCount, 8)} ${padNum(r.toolDefsTokens, 12)}`,
    );
  }

  const nullResult = results.find((r) => r.mode === "null");
  if (nullResult) {
    lines.push("");
    lines.push("Savings vs no compression:");
    for (const r of results) {
      if (r.mode === "null") continue;
      const tokenSavings = nullResult.toolDefsTokens - r.toolDefsTokens;
      const pct = ((tokenSavings / nullResult.toolDefsTokens) * 100).toFixed(1);
      lines.push(
        `  ${modeLabel(r.mode)}: ${tokenSavings.toLocaleString()} tokens saved (${pct}%)`,
      );
    }
  }

  return lines.join("\n");
}

export function renderRunLogHtml(entries: RunLogEntry[], staticResults?: StaticResult[]): string {
  const modes = ["null", "medium", "max"];

  // Deduplicate prompts in insertion order
  const promptIds = [...new Set(entries.map((e) => e.promptId))];

  // Per-prompt, per-mode total: categories = prompts, series = modes
  const modeColors: Record<string, string> = {
    null: "#4e79a7",
    medium: "#59a14f",
    max: "#f28e2b",
  };

  function promptModeTotal(promptId: string, mode: string, key: keyof RunLogEntry): number {
    return entries
      .filter((e) => e.promptId === promptId && e.mode === mode)
      .reduce((s, e) => s + ((e[key] as number | undefined) ?? 0), 0);
  }

  const datasets = modes
    .map((mode) => {
      return JSON.stringify({
        label: MODE_LABELS[mode] ?? mode,
        data: promptIds.map((p) => promptModeTotal(p, mode, "totalTokens")),
        backgroundColor: modeColors[mode] ?? "#aaa",
      });
    })
    .join(",\n        ");

  const categoryLabels = JSON.stringify(promptIds);

  const tableRows = entries
    .map((e) => {
      const label = MODE_LABELS[e.mode] ?? e.mode;
      return `
      <tr>
        <td>${label}</td>
        <td>${e.promptId}</td>
        <td>${(e.inputTokens ?? 0).toLocaleString()}</td>
        <td>${(e.outputTokens ?? 0).toLocaleString()}</td>
        <td>${(e.cacheWriteTokens ?? 0).toLocaleString()}</td>
        <td>${(e.cacheReadTokens ?? 0).toLocaleString()}</td>
        <td><strong>${(e.totalTokens ?? 0).toLocaleString()}</strong></td>
      </tr>`;
    })
    .join("\n");

  const runDate = entries.length > 0 ? entries[0].startTime.slice(0, 10) : "";
  const model = entries.length > 0 ? entries[0].model : "";
  const promptCount = promptIds.length;

  // Static analysis section (only rendered when sidecar data is present)
  const nullStatic = staticResults?.find((r) => r.mode === "null");
  const staticLabels = JSON.stringify(modes.map((m) => MODE_LABELS[m] ?? m));
  const staticTokenData = JSON.stringify(
    modes.map((m) => staticResults?.find((r) => r.mode === m)?.toolDefsTokens ?? 0),
  );
  const staticToolData = JSON.stringify(
    modes.map((m) => staticResults?.find((r) => r.mode === m)?.toolCount ?? 0),
  );

  const staticTableRows = (staticResults ?? [])
    .map((r) => {
      const savings = nullStatic && r.mode !== "null"
        ? `${(((nullStatic.toolDefsTokens - r.toolDefsTokens) / nullStatic.toolDefsTokens) * 100).toFixed(1)}%`
        : "—";
      return `
      <tr>
        <td>${modeLabel(r.mode)}</td>
        <td>${r.toolCount}</td>
        <td>${r.toolDefsTokens.toLocaleString()}</td>
        <td>${savings}</td>
      </tr>`;
    })
    .join("\n");

  const staticSection = staticResults
    ? `
  <div class="section-header">
    <h2>Tool Definition Tokens</h2>
    <p class="caption">Token cost of injecting MCP tool schemas per compression mode. Source: static-results.json</p>
  </div>
  <div class="charts-row">
    <div class="chart-col">
      <canvas id="staticTokenChart" height="220"></canvas>
    </div>
    <div class="chart-col">
      <canvas id="staticToolChart" height="220"></canvas>
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Mode</th><th>Tools exposed</th><th>Tool def tokens</th><th>Token savings vs no compression</th>
      </tr>
    </thead>
    <tbody>
      ${staticTableRows}
    </tbody>
  </table>`
    : "";

  const staticScript = staticResults
    ? `
    new Chart(document.getElementById('staticTokenChart'), {
      type: 'bar',
      data: {
        labels: ${staticLabels},
        datasets: [{ label: 'Tool def tokens', data: ${staticTokenData}, backgroundColor: '#4e79a7' }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false },
          title: { display: true, text: 'Tool definition tokens by mode' },
          tooltip: { callbacks: { label: (ctx) => \` \${ctx.parsed.y.toLocaleString()} tokens\` } },
        },
        scales: {
          x: { title: { display: true, text: 'Compression mode' } },
          y: { title: { display: true, text: 'Tokens' }, ticks: { callback: (v) => Number(v).toLocaleString() } },
        },
      },
    });
    new Chart(document.getElementById('staticToolChart'), {
      type: 'bar',
      data: {
        labels: ${staticLabels},
        datasets: [{ label: 'Tools exposed', data: ${staticToolData}, backgroundColor: '#76b7b2' }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false },
          title: { display: true, text: 'Tools exposed per mode' },
          tooltip: { callbacks: { label: (ctx) => \` \${ctx.parsed.y} tools\` } },
        },
        scales: {
          x: { title: { display: true, text: 'Compression mode' } },
          y: { title: { display: true, text: 'Tool count' }, ticks: { stepSize: 1 } },
        },
      },
    });`
    : "";

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>zelosMCP Compression Benchmark</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body {
      font-family: system-ui, -apple-system, sans-serif;
      max-width: 1100px;
      margin: 2rem auto;
      padding: 0 1.5rem;
      color: #1a1a1a;
      background: #fff;
    }
    h1 { font-size: 1.5rem; margin: 0 0 0.25rem; }
    h2 { font-size: 1.1rem; margin: 2rem 0 0.5rem; color: #333; }
    .meta { font-size: 0.8rem; color: #666; margin-bottom: 2rem; }
    .charts-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 2rem;
      margin-bottom: 1rem;
    }
    .chart-col { min-width: 0; }
    .chart-wrap { margin-bottom: 1rem; }
    hr { border: none; border-top: 1px solid #e8e8e8; margin: 2.5rem 0; }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.875rem;
      margin-top: 0.5rem;
    }
    th {
      text-align: left;
      padding: 6px 10px;
      background: #f5f5f5;
      border-bottom: 2px solid #e0e0e0;
      font-weight: 600;
    }
    td {
      padding: 6px 10px;
      border-bottom: 1px solid #eee;
    }
    td:nth-child(n+3) { text-align: right; font-variant-numeric: tabular-nums; }
    tr:last-child td { border-bottom: none; }
    .caption { font-size: 0.75rem; color: #888; margin-top: 0.25rem; margin-bottom: 1rem; }
  </style>
</head>
<body>
  <h1>zelosMCP Compression Benchmark</h1>
  <p class="meta">Model: ${model} &nbsp;·&nbsp; ${promptCount} prompt${promptCount !== 1 ? "s" : ""} &nbsp;·&nbsp; Run date: ${runDate}</p>

  ${staticSection}

  ${staticResults ? "<hr>" : ""}

  <h2>Total Tokens per Prompt by Compression Mode</h2>
  <p class="caption">Grouped bars — total token cost per prompt across the three compression modes. Source: run-log.json</p>
  <div class="chart-wrap">
    <canvas id="tokenChart" height="160"></canvas>
  </div>

  <h2>Raw Data</h2>
  <table>
    <thead>
      <tr>
        <th>Mode</th><th>Prompt</th><th>Input</th><th>Output</th>
        <th>Cache Write</th><th>Cache Read</th><th>Total</th>
      </tr>
    </thead>
    <tbody>
      ${tableRows}
    </tbody>
  </table>

  <script>
    new Chart(document.getElementById('tokenChart'), {
      type: 'bar',
      data: {
        labels: ${categoryLabels},
        datasets: [
        ${datasets}
        ],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: 'bottom' },
          tooltip: {
            callbacks: {
              label: (ctx) => \` \${ctx.dataset.label}: \${ctx.parsed.y.toLocaleString()} tokens\`,
            },
          },
        },
        scales: {
          x: { title: { display: true, text: 'Prompt' } },
          y: { title: { display: true, text: 'Total tokens' },
               ticks: { callback: (v) => Number(v).toLocaleString() } },
        },
      },
    });
    ${staticScript}
  </script>
</body>
</html>`;
}
