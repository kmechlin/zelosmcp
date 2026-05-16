#!/usr/bin/env node
import { Command } from "commander";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve, dirname, basename } from "node:path";
import { runStaticAnalysis } from "./static-analyzer.js";
import { renderStaticTable, renderRunLogHtml } from "./report.js";
import { runSuite, readRunLog, refetchRunLog, staticResultsPath, findProjectRoot, findProjectRules } from "./runner.js";
import { getSessionCookie } from "./usage-api.js";
import type { PromptDef, StaticResult } from "./types.js";

const program = new Command();

program
  .name("zelosmcp-bench")
  .description("zelosMCP compression benchmark tool")
  .version("0.1.0");

program
  .command("static")
  .description(
    "Count tokens in tool definitions under each compression mode",
  )
  .requiredOption("--url <url>", "zelosMCP base URL", "http://localhost:8000")
  .option("--json", "Output raw JSON instead of a table")
  .action(async (opts: { url: string; json?: boolean }) => {
    console.log("Running static analysis...\n");
    const results = await runStaticAnalysis(opts.url);

    if (opts.json) {
      console.log(JSON.stringify(results, null, 2));
    } else {
      console.log(renderStaticTable(results));
    }
  });

program
  .command("run")
  .description("Send prompts via @cursor/sdk under each compression mode")
  .requiredOption("--url <url>", "zelosMCP base URL", "http://localhost:8000")
  .requiredOption("--model <model>", "Cursor model ID", "composer-2")
  .option("--delay <ms>", "Delay between prompts in ms", "5000")
  .option(
    "--output <path>",
    "Run log output path",
    "results/run-log.json",
  )
  .option(
    "--prompts <path>",
    "Prompt suite JSON path",
    "prompts/suite.json",
  )
  .option("--rules", "Load project Cursor rules into each agent run")
  .action(
    async (opts: {
      url: string;
      model: string;
      delay: string;
      output: string;
      prompts: string;
      rules?: boolean;
    }) => {
      const apiKey = process.env.CURSOR_API_KEY;
      if (!apiKey) {
        console.error(
          "Error: CURSOR_API_KEY environment variable is required for the run command.",
        );
        process.exit(1);
      }

      const promptsPath = resolve(opts.prompts);
      const prompts = JSON.parse(
        readFileSync(promptsPath, "utf-8"),
      ) as PromptDef[];

      const projectRoot = findProjectRoot(process.cwd());
      const ruleFiles = findProjectRules(projectRoot);

      console.log(`Running ${prompts.length} prompts × 3 modes...`);
      console.log(`Model:   ${opts.model}`);
      console.log(`Root:    ${projectRoot}`);

      if (ruleFiles.length > 0) {
        const status = opts.rules ? "enabled" : "disabled (pass --rules to include)";
        console.log(`Rules:   ${status}`);
        for (const f of ruleFiles) {
          console.log(`         ${f}`);
        }
      } else {
        console.log(`Rules:   none found in ${projectRoot}/.cursor/rules/`);
      }

      console.log(`Output:  ${opts.output}\n`);

      const runLogPath = resolve(opts.output);

      await runSuite({
        zelosmcpUrl: opts.url,
        model: opts.model,
        apiKey,
        prompts,
        delayMs: parseInt(opts.delay, 10),
        outputPath: runLogPath,
        enableRules: opts.rules,
      });

      const entries = readRunLog(runLogPath);
      const sidecarPath = staticResultsPath(runLogPath);
      const staticResults = existsSync(sidecarPath)
        ? (JSON.parse(readFileSync(sidecarPath, "utf-8")) as StaticResult[])
        : undefined;
      const reportPath = resolve(dirname(runLogPath), basename(runLogPath, ".json") + ".html");
      writeFileSync(reportPath, renderRunLogHtml(entries, staticResults));

      console.log(`\nRun log:  ${opts.output}`);
      console.log(`Report:   ${reportPath}`);
      process.exit(0);
    },
  );

program
  .command("refetch")
  .description("Re-query the Cursor usage API for every entry in a run log and update token counts")
  .requiredOption("--run-log <path>", "Path to run-log.json")
  .option("--poll-interval <ms>", "Delay between polls per entry (ms)", "2000")
  .option("--max-polls <n>", "Max polls per entry", "2")
  .action(async (opts: { runLog: string; pollInterval: string; maxPolls: string }) => {
    const cookie = getSessionCookie();
    if (!cookie) {
      console.error(
        "Error: could not read Cursor session token from local SQLite DB.\n" +
        "Set CURSOR_SESSION_TOKEN to your WorkosCursorSessionToken cookie value.",
      );
      process.exit(1);
    }

    const runLogPath = resolve(opts.runLog);
    const log = readRunLog(runLogPath);
    console.log(`Refetching token data for ${log.length} entries...`);

    const { updated, total } = await refetchRunLog(runLogPath, cookie!, {
      pollIntervalMs: parseInt(opts.pollInterval, 10),
      maxPolls: parseInt(opts.maxPolls, 10),
    });

    console.log(`Done. ${updated}/${total} entries updated with higher token counts.`);
    process.exit(0);
  });

program
  .command("report")
  .description("Generate an HTML token-usage report from a run log")
  .requiredOption("--run-log <path>", "Path to run-log.json")
  .option("--output <path>", "Path for the HTML output file")
  .action((opts: { runLog: string; output?: string }) => {
    const runLogPath = resolve(opts.runLog);
    const entries = readRunLog(runLogPath);

    const sidecarPath = staticResultsPath(runLogPath);
    const staticResults = existsSync(sidecarPath)
      ? (JSON.parse(readFileSync(sidecarPath, "utf-8")) as StaticResult[])
      : undefined;

    const outputPath = opts.output
      ? resolve(opts.output)
      : resolve(dirname(runLogPath), basename(runLogPath, ".json") + ".html");

    const html = renderRunLogHtml(entries, staticResults);
    writeFileSync(outputPath, html);
    if (staticResults) {
      console.log(`Static analysis included from ${sidecarPath}`);
    }
    console.log(`Report written to ${outputPath}`);
  });

program.parse();
