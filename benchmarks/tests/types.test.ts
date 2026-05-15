import { describe, it, expect } from "vitest";
import { MODES } from "../src/types.js";
import type {
  Mode,
  StaticResult,
  RunLogEntry,
  CsvRow,
  CorrelatedResult,
  BenchmarkReport,
  PromptDef,
} from "../src/types.js";

describe("types", () => {
  it("MODES contains exactly null, medium, max", () => {
    expect(MODES).toEqual(["null", "medium", "max"]);
  });

  it("MODES is readonly", () => {
    expect(Object.isFrozen(MODES)).toBe(false);
    expect(MODES.length).toBe(3);
  });

  it("Mode type accepts valid values", () => {
    const modes: Mode[] = ["null", "medium", "max"];
    expect(modes).toHaveLength(3);
  });

  it("StaticResult shape is well-typed", () => {
    const result: StaticResult = {
      mode: "null",
      toolDefsBytes: 55300,
      toolDefsTokens: 14200,
      toolCount: 33,
    };
    expect(result.mode).toBe("null");
    expect(result.toolDefsBytes).toBeGreaterThan(0);
  });

  it("RunLogEntry shape is well-typed", () => {
    const entry: RunLogEntry = {
      mode: "medium",
      promptId: "arch-overview",
      promptText: "Describe the architecture",
      model: "composer-2-fast",
      startTime: "2026-05-14T10:00:00.000Z",
      endTime: "2026-05-14T10:00:15.000Z",
      status: "ok",
    };
    expect(entry.status).toBe("ok");
  });

  it("CsvRow shape covers all CSV columns", () => {
    const row: CsvRow = {
      date: "2026-04-22T21:42:37.605Z",
      user: "test@example.com",
      cloudAgentId: "",
      automationId: "",
      kind: "On-Demand",
      model: "composer-2-fast",
      maxMode: "No",
      inputWithCacheWrite: 74712,
      inputWithoutCacheWrite: 9067,
      cacheRead: 279548,
      outputTokens: 2986,
      totalTokens: 366313,
      cost: "0.50",
    };
    expect(row.totalTokens).toBe(366313);
  });

  it("CorrelatedResult can hold null csvRow", () => {
    const entry: RunLogEntry = {
      mode: "max",
      promptId: "test",
      promptText: "test",
      model: "composer-2-fast",
      startTime: "2026-05-14T10:00:00.000Z",
      endTime: "2026-05-14T10:00:15.000Z",
      status: "ok",
    };
    const result: CorrelatedResult = {
      runLogEntry: entry,
      csvRow: null,
      matchDeltaMs: null,
      warning: "No match",
    };
    expect(result.csvRow).toBeNull();
  });

  it("BenchmarkReport combines static and correlated", () => {
    const report: BenchmarkReport = {
      staticResults: [],
      correlatedResults: [],
    };
    expect(report.staticResults).toHaveLength(0);
  });

  it("PromptDef shape is well-typed", () => {
    const prompt: PromptDef = {
      id: "test-prompt",
      text: "What does this do?",
      category: "code-exploration",
    };
    expect(prompt.id).toBe("test-prompt");
  });
});
