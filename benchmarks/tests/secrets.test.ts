import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { parseSecretsFile, applySecretsFile } from "../src/secrets.js";
import { writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("parseSecretsFile", () => {
  it("parses simple KEY=VALUE pairs", () => {
    const result = parseSecretsFile("FOO=bar\nBAZ=qux");
    expect(result).toEqual({ FOO: "bar", BAZ: "qux" });
  });

  it("strips leading # comments and blank lines", () => {
    const result = parseSecretsFile("# comment\n\nKEY=value\n# another");
    expect(result).toEqual({ KEY: "value" });
  });

  it("strips double-quoted values", () => {
    expect(parseSecretsFile('KEY="hello world"')).toEqual({ KEY: "hello world" });
  });

  it("strips single-quoted values", () => {
    expect(parseSecretsFile("KEY='hello world'")).toEqual({ KEY: "hello world" });
  });

  it("strips export prefix", () => {
    expect(parseSecretsFile("export CURSOR_API_KEY=abc123")).toEqual({
      CURSOR_API_KEY: "abc123",
    });
  });

  it("strips trailing inline comments", () => {
    expect(parseSecretsFile("KEY=value # this is a comment")).toEqual({ KEY: "value" });
  });

  it("ignores lines without =", () => {
    expect(parseSecretsFile("NOTAKEY\nFOO=bar")).toEqual({ FOO: "bar" });
  });

  it("handles CRLF line endings", () => {
    expect(parseSecretsFile("A=1\r\nB=2\r\n")).toEqual({ A: "1", B: "2" });
  });

  it("returns empty object for empty file", () => {
    expect(parseSecretsFile("")).toEqual({});
  });
});

describe("applySecretsFile", () => {
  let tmpFile: string;
  const testKey = "__BENCH_TEST_SECRET__";

  beforeEach(() => {
    tmpFile = join(tmpdir(), `bench-secrets-${Date.now()}.env`);
    delete process.env[testKey];
  });

  afterEach(() => {
    rmSync(tmpFile, { force: true });
    delete process.env[testKey];
  });

  it("writes parsed values into process.env", () => {
    writeFileSync(tmpFile, `${testKey}=secretvalue`);
    const applied = applySecretsFile(tmpFile);
    expect(process.env[testKey]).toBe("secretvalue");
    expect(applied.has(testKey)).toBe(true);
  });

  it("does not overwrite an existing env var", () => {
    process.env[testKey] = "original";
    writeFileSync(tmpFile, `${testKey}=override`);
    const applied = applySecretsFile(tmpFile);
    expect(process.env[testKey]).toBe("original");
    expect(applied.has(testKey)).toBe(false);
  });
});
