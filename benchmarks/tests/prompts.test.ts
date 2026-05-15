import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { PromptDef } from "../src/types.js";

const suiteJson = JSON.parse(
  readFileSync(resolve(__dirname, "../prompts/suite.json"), "utf-8"),
) as PromptDef[];

describe("prompts/suite.json", () => {
  it("contains at least 5 prompts", () => {
    expect(suiteJson.length).toBeGreaterThanOrEqual(5);
  });

  it("every prompt has id, text, and category", () => {
    for (const p of suiteJson) {
      expect(p.id).toBeTruthy();
      expect(p.text).toBeTruthy();
      expect(p.category).toBeTruthy();
    }
  });

  it("all prompt ids are unique", () => {
    const ids = suiteJson.map((p) => p.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("includes code-exploration category", () => {
    expect(suiteJson.some((p) => p.category === "code-exploration")).toBe(true);
  });

  it("includes symbol-lookup category", () => {
    expect(suiteJson.some((p) => p.category === "symbol-lookup")).toBe(true);
  });

  it("includes search category", () => {
    expect(suiteJson.some((p) => p.category === "search")).toBe(true);
  });
});
