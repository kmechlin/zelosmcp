import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

describe("cli", () => {
  it("cli.ts file exists and is valid TypeScript", () => {
    const content = readFileSync(
      resolve(__dirname, "../src/cli.ts"),
      "utf-8",
    );
    expect(content).toContain("new Command()");
    expect(content).toContain('.command("static")');
    expect(content).toContain('.command("run")');
    expect(content).toContain('.command("refetch")');
    expect(content).toContain('.command("report")');
  });

  it("cli has static subcommand with --url option", () => {
    const content = readFileSync(
      resolve(__dirname, "../src/cli.ts"),
      "utf-8",
    );
    expect(content).toContain("--url");
    expect(content).toContain("runStaticAnalysis");
  });

  it("cli has run subcommand with --model option", () => {
    const content = readFileSync(
      resolve(__dirname, "../src/cli.ts"),
      "utf-8",
    );
    expect(content).toContain("--model");
    expect(content).toContain("runSuite");
  });

  it("cli has refetch subcommand with --run-log option", () => {
    const content = readFileSync(
      resolve(__dirname, "../src/cli.ts"),
      "utf-8",
    );
    expect(content).toContain("--run-log");
    expect(content).toContain("refetchRunLog");
  });

  it("cli requires CURSOR_API_KEY for run command", () => {
    const content = readFileSync(
      resolve(__dirname, "../src/cli.ts"),
      "utf-8",
    );
    expect(content).toContain("CURSOR_API_KEY");
  });

  it("cli has report subcommand with --run-log option", () => {
    const content = readFileSync(
      resolve(__dirname, "../src/cli.ts"),
      "utf-8",
    );
    expect(content).toContain('.command("report")');
    expect(content).toContain("renderRunLogHtml");
  });
});
