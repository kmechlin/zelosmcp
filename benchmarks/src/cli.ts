#!/usr/bin/env node
import { Command } from "commander";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve, dirname, basename } from "node:path";
import { runStaticAnalysis } from "./static-analyzer.js";
import { renderStaticTable, renderRunLogHtml } from "./report.js";
import { runSuite, readRunLog, refetchRunLog, staticResultsPath, findProjectRoot, findProjectRules, isRetryableError } from "./runner.js";
import { getSessionCookie } from "./usage-api.js";
import {
  MODES,
  RESPONSE_FORMATS,
  type Mode,
  type PromptDef,
  type ResponseFormat,
  type StaticResult,
} from "./types.js";

// The @cursor/sdk spawns background tasks during Agent.create (e.g.
// LocalIgnoreService.init → getTeamReposOrEmptyIfNotInTeam) whose
// rejections are never awaited. When Cursor's API key exchange
// endpoint hiccups (corporate-proxy / Zscaler / transient DNS), the
// rejection escapes and Node 26 crashes the whole process by default
// — taking the benchmark with it. Install permissive handlers so the
// suite keeps running. Retryable network errors are logged quietly;
// everything else is logged loudly but still non-fatal.
process.on("unhandledRejection", (reason: unknown) => {
  const msg = reason instanceof Error ? reason.message : String(reason);
  if (isRetryableError(reason)) {
    console.error(`\n[bench] suppressed transient SDK rejection: ${msg}`);
    return;
  }
  console.error(`\n[bench] unhandled rejection (non-fatal): ${msg}`);
  if (reason instanceof Error && reason.stack) console.error(reason.stack);
});

process.on("uncaughtException", (err: unknown) => {
  const msg = err instanceof Error ? err.message : String(err);
  if (isRetryableError(err)) {
    console.error(`\n[bench] suppressed transient SDK exception: ${msg}`);
    return;
  }
  console.error(`\n[bench] uncaught exception (non-fatal): ${msg}`);
  if (err instanceof Error && err.stack) console.error(err.stack);
});

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
  .option(
    "--mode <modes>",
    `Comma-separated subset of modes to run (default: all). Valid: ${MODES.join(", ")}`,
  )
  .option(
    "--pin-response-format <fmt>",
    `Pin every server's response_format across all modes to isolate the ` +
      `compression-only signal. Valid: ${RESPONSE_FORMATS.join(", ")}`,
  )
  .option(
    "--pin-strip-meta <true|false>",
    "Pin every server's strip_meta across all modes (true|false)",
  )
  .option(
    "--no-clean-assets",
    "Do not remove pushed .cursor rules/assets before the benchmark starts",
  )
  .option(
    "--refresh-assets",
    "Push fresh .cursor assets after each server config reload (default: enabled with --rules)",
  )
  .option(
    "--no-refresh-assets",
    "Do not push .cursor assets after server config reloads",
  )
  .option("--rules", "Load project Cursor rules into each agent run")
  .action(
    async (opts: {
      url: string;
      model: string;
      delay: string;
      output: string;
      prompts: string;
      mode?: string;
      pinResponseFormat?: string;
      pinStripMeta?: string;
      cleanAssets?: boolean;
      refreshAssets?: boolean;
      rules?: boolean;
    }) => {
      const apiKey = process.env.CURSOR_API_KEY;
      if (!apiKey) {
        console.error(
          "Error: CURSOR_API_KEY environment variable is required for the run command.",
        );
        process.exit(1);
      }

      let modes: readonly Mode[] | undefined;
      if (opts.mode) {
        const requested = opts.mode.split(",").map((m) => m.trim()).filter(Boolean);
        const invalid = requested.filter((m): m is string => !MODES.includes(m as Mode));
        if (invalid.length > 0) {
          console.error(
            `Error: invalid --mode value(s): ${invalid.join(", ")}. ` +
              `Valid: ${MODES.join(", ")}`,
          );
          process.exit(1);
        }
        modes = requested as Mode[];
      }

      let pinResponseFormat: ResponseFormat | undefined;
      if (opts.pinResponseFormat !== undefined) {
        const fmt = opts.pinResponseFormat.trim();
        if (!RESPONSE_FORMATS.includes(fmt as ResponseFormat)) {
          console.error(
            `Error: invalid --pin-response-format value: ${fmt}. ` +
              `Valid: ${RESPONSE_FORMATS.join(", ")}`,
          );
          process.exit(1);
        }
        pinResponseFormat = fmt as ResponseFormat;
      }

      let pinStripMeta: boolean | undefined;
      if (opts.pinStripMeta !== undefined) {
        const v = opts.pinStripMeta.trim().toLowerCase();
        if (v === "true") pinStripMeta = true;
        else if (v === "false") pinStripMeta = false;
        else {
          console.error(
            `Error: invalid --pin-strip-meta value: ${opts.pinStripMeta}. ` +
              `Valid: true, false`,
          );
          process.exit(1);
        }
      }

      const promptsPath = resolve(opts.prompts);
      const prompts = JSON.parse(
        readFileSync(promptsPath, "utf-8"),
      ) as PromptDef[];

      const projectRoot = findProjectRoot(process.cwd());
      const ruleFiles = findProjectRules(projectRoot);

      const modeCount = (modes ?? MODES).length;
      console.log(`Running ${prompts.length} prompts × ${modeCount} mode${modeCount === 1 ? "" : "s"}...`);
      console.log(`Model:   ${opts.model}`);
      console.log(`Modes:   ${(modes ?? MODES).join(", ")}`);
      if (pinResponseFormat !== undefined) {
        console.log(`Pin:     response_format=${pinResponseFormat}`);
      }
      if (pinStripMeta !== undefined) {
        console.log(`Pin:     strip_meta=${pinStripMeta}`);
      }
      console.log(`Assets:  clean=${opts.cleanAssets !== false}, refresh=${opts.refreshAssets ?? Boolean(opts.rules)}`);
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

      const writeReport = (): string | undefined => {
        if (!existsSync(runLogPath)) return undefined;
        const entries = readRunLog(runLogPath);
        const sidecarPath = staticResultsPath(runLogPath);
        const staticResults = existsSync(sidecarPath)
          ? (JSON.parse(readFileSync(sidecarPath, "utf-8")) as StaticResult[])
          : undefined;
        const reportPath = resolve(dirname(runLogPath), basename(runLogPath, ".json") + ".html");
        writeFileSync(reportPath, renderRunLogHtml(entries, staticResults));
        return reportPath;
      };

      let exitCode = 0;
      try {
        await runSuite({
          zelosmcpUrl: opts.url,
          model: opts.model,
          apiKey,
          prompts,
          modes,
          delayMs: parseInt(opts.delay, 10),
          outputPath: runLogPath,
          enableRules: opts.rules,
          pinResponseFormat,
          pinStripMeta,
          cleanAssets: opts.cleanAssets,
          refreshAssets: opts.refreshAssets,
        });
      } catch (err) {
        exitCode = 1;
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`\nrunSuite failed: ${msg}`);
      } finally {
        const reportPath = writeReport();
        console.log(`\nRun log:  ${opts.output}`);
        if (reportPath) console.log(`Report:   ${reportPath}`);
      }
      process.exit(exitCode);
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
