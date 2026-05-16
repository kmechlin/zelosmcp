import { describe, it, expect } from "vitest";
import { renderStaticTable, renderRunLogHtml } from "../src/report.js";
import type { RunLogEntry, StaticResult } from "../src/types.js";

const staticResults: StaticResult[] = [
  { mode: "null", toolDefsBytes: 55300, toolDefsTokens: 14200, toolCount: 33 },
  { mode: "low", toolDefsBytes: 24000, toolDefsTokens: 6100, toolCount: 30 },
  { mode: "medium", toolDefsBytes: 2400, toolDefsTokens: 620, toolCount: 6 },
  { mode: "high", toolDefsBytes: 1600, toolDefsTokens: 410, toolCount: 6 },
  { mode: "max", toolDefsBytes: 800, toolDefsTokens: 210, toolCount: 2 },
];

describe("renderStaticTable", () => {
  it("includes all five modes", () => {
    const output = renderStaticTable(staticResults);
    expect(output).toContain("no compression");
    expect(output).toContain("low");
    expect(output).toContain("medium");
    expect(output).toContain("high");
    expect(output).toContain("max");
  });

  it("includes token counts", () => {
    const output = renderStaticTable(staticResults);
    expect(output).toContain("14,200");
    expect(output).toContain("6,100");
    expect(output).toContain("620");
    expect(output).toContain("410");
    expect(output).toContain("210");
  });

  it("shows savings vs no compression", () => {
    const output = renderStaticTable(staticResults);
    expect(output).toContain("Savings vs no compression");
    expect(output).toContain("tokens saved");
  });

  it("calculates correct percentage savings", () => {
    const output = renderStaticTable(staticResults);
    expect(output).toContain("95.6%");
    expect(output).toContain("98.5%");
  });
});

const runLogEntries: RunLogEntry[] = [
  {
    mode: "null",
    promptId: "arch-overview",
    model: "composer-2-fast",
    startTime: "2026-05-14T10:00:00.000Z",
    endTime: "2026-05-14T10:00:15.000Z",
    status: "ok",
    inputTokens: 20000,
    outputTokens: 2000,
    cacheWriteTokens: 0,
    cacheReadTokens: 50000,
    totalTokens: 72000,
  },
  {
    mode: "low",
    promptId: "arch-overview",
    model: "composer-2-fast",
    startTime: "2026-05-14T10:00:30.000Z",
    endTime: "2026-05-14T10:00:45.000Z",
    status: "ok",
    inputTokens: 15000,
    outputTokens: 2000,
    cacheWriteTokens: 0,
    cacheReadTokens: 40000,
    totalTokens: 57000,
  },
  {
    mode: "medium",
    promptId: "arch-overview",
    model: "composer-2-fast",
    startTime: "2026-05-14T10:01:00.000Z",
    endTime: "2026-05-14T10:01:15.000Z",
    status: "ok",
    inputTokens: 10000,
    outputTokens: 2000,
    cacheWriteTokens: 0,
    cacheReadTokens: 30000,
    totalTokens: 42000,
  },
  {
    mode: "high",
    promptId: "arch-overview",
    model: "composer-2-fast",
    startTime: "2026-05-14T10:01:30.000Z",
    endTime: "2026-05-14T10:01:45.000Z",
    status: "ok",
    inputTokens: 9000,
    outputTokens: 2000,
    cacheWriteTokens: 0,
    cacheReadTokens: 25000,
    totalTokens: 36000,
  },
  {
    mode: "max",
    promptId: "arch-overview",
    model: "composer-2-fast",
    startTime: "2026-05-14T10:02:00.000Z",
    endTime: "2026-05-14T10:02:15.000Z",
    status: "ok",
    inputTokens: 8000,
    outputTokens: 2000,
    cacheWriteTokens: 0,
    cacheReadTokens: 20000,
    totalTokens: 30000,
  },
];

describe("renderRunLogHtml", () => {
  it("produces a valid HTML document", () => {
    const html = renderRunLogHtml(runLogEntries);
    expect(html).toContain("<!DOCTYPE html>");
    expect(html).toContain("</html>");
  });

  it("includes Chart.js script tag", () => {
    const html = renderRunLogHtml(runLogEntries);
    expect(html).toContain("chart.js");
  });

  it("includes mode labels", () => {
    const html = renderRunLogHtml(runLogEntries);
    expect(html).toContain("no compression");
    expect(html).toContain("low");
    expect(html).toContain("medium");
    expect(html).toContain("high");
    expect(html).toContain("max");
  });

  it("uses every mode from run log and static sidecar in charts", () => {
    const html = renderRunLogHtml(runLogEntries, staticResults);
    expect(html).toContain("Grouped bars — total token cost per prompt across 5 compression modes");
    expect(html).toContain('"no compression","low","medium","high","max"');
  });

  it("includes token values in the table", () => {
    const html = renderRunLogHtml(runLogEntries);
    expect(html).toContain("72,000");
    expect(html).toContain("42,000");
  });

  it("includes promptId in the table", () => {
    const html = renderRunLogHtml(runLogEntries);
    expect(html).toContain("arch-overview");
  });

  it("returns empty-safe HTML for an empty entry list", () => {
    const html = renderRunLogHtml([]);
    expect(html).toContain("<!DOCTYPE html>");
  });
});
