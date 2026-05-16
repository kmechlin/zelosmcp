import { describe, it, expect } from "vitest";
import { countTokens, countToolDefTokens } from "../src/tokens.js";

describe("tokens", () => {
  it("countTokens returns a positive integer for non-empty text", () => {
    const count = countTokens("Hello, world!");
    expect(count).toBeGreaterThan(0);
    expect(Number.isInteger(count)).toBe(true);
  });

  it("countTokens returns 0 for empty string", () => {
    expect(countTokens("")).toBe(0);
  });

  it("longer text produces more tokens", () => {
    const short = countTokens("Hello");
    const long = countTokens("Hello, this is a much longer sentence with many more words in it.");
    expect(long).toBeGreaterThan(short);
  });

  it("countToolDefTokens serializes and counts", () => {
    const tools = [
      { name: "foo", description: "A foo tool", inputSchema: { type: "object" } },
      { name: "bar", description: "A bar tool", inputSchema: { type: "object" } },
    ];
    const count = countToolDefTokens(tools);
    expect(count).toBeGreaterThan(0);
  });

  it("countToolDefTokens returns 0-ish for empty array", () => {
    const count = countToolDefTokens([]);
    expect(count).toBeGreaterThanOrEqual(1); // "[]" serializes to at least 1 token
  });

  it("more tools produce more tokens", () => {
    const small = countToolDefTokens([{ name: "a" }]);
    const large = countToolDefTokens(
      Array.from({ length: 20 }, (_, i) => ({
        name: `tool_${i}`,
        description: `This is tool number ${i} with a long description`,
        inputSchema: {
          type: "object",
          properties: { arg: { type: "string", description: "An argument" } },
        },
      })),
    );
    expect(large).toBeGreaterThan(small);
  });
});
