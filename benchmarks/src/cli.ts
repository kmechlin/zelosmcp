#!/usr/bin/env node
import { Command } from "commander";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve, dirname, basename } from "node:path";
import { runStaticAnalysis } from "./static-analyzer.js";
import { renderStaticTable, renderRunLogHtml } from "./report.js";
import { readRunLog, refetchRunLog, staticResultsPath, findProjectRoot, findProjectRules, isRetryableError } from "./runner.js";
import { getSessionCookie } from "./usage-api.js";
import { coreRunSuite, type AdapterConfig } from "./core/runner.js";
import { loadAdapter, listAdapterIds } from "./adapters/registry.js";
import {
  applySecretsFile,
  findDefaultSecretsFile,
  resolveSecretsPath,
} from "./secrets.js";
import {
  MODES,
  RESPONSE_FORMATS,
  type IdeId,
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
  .description("Send prompts via IDE adapter(s) under each compression mode")
  .requiredOption("--url <url>", "zelosMCP base URL", "http://localhost:8000")
  .option(
    "--ide <ide>",
    `IDE adapter(s) to benchmark. Valid: cursor, copilot, zelos, all (default: cursor)`,
    "cursor",
  )
  .option("--model <model>", "Model ID applied to all adapters; overridden by --cursor-model / --copilot-model / --zelos-model")
  .option("--cursor-model <model>", "Model ID for Cursor (default: composer-2)")
  .option("--copilot-model <model>", "Model ID for Copilot (default: claude-sonnet-4.5)")
  .option("--zelos-model <model>", "Model ID for Zelos (default: claude-sonnet-4.5)")
  .option("--rules-dir <path>", "Custom rules directory (relative to project root or absolute)")
  .option(
    "--secrets-file <path>",
    `Dotenv-style file with API keys (default: auto-detect ${"`.bench.env`"} in CWD / benchmarks dir). ` +
      "Existing env vars take precedence.",
  )
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
    "Do not remove pushed IDE rules/assets before the benchmark starts",
  )
  .option(
    "--refresh-assets",
    "Push fresh assets after each server config reload (default: enabled with --rules)",
  )
  .option(
    "--no-refresh-assets",
    "Do not push assets after server config reloads",
  )
  .option("--rules", "Load project IDE rules into each agent run")
  .option(
    "--agent <name>",
    "Agent name to activate (e.g. zelos-agent). Passed as --agent to the Copilot CLI.",
  )
  .option(
    "--log-transcripts",
    "Save per-prompt transcript JSON files (tool calls, thoughts, dialog) to results/transcripts/",
  )
  .action(
    async (opts: {
      url: string;
      ide: string;
      model?: string;
      cursorModel?: string;
      copilotModel?: string;
      zelosModel?: string;
      rulesDir?: string;
      secretsFile?: string;
      delay: string;
      output: string;
      prompts: string;
      mode?: string;
      pinResponseFormat?: string;
      pinStripMeta?: string;
      cleanAssets?: boolean;
      refreshAssets?: boolean;
      rules?: boolean;
      agent?: string;
      logTranscripts?: boolean;
    }) => {
      // ── Load secrets file (before adapter env validation) ────────────────
      if (opts.secretsFile) {
        const secretsPath = resolveSecretsPath(opts.secretsFile);
        if (!existsSync(secretsPath)) {
          console.error(`Error: secrets file not found: ${secretsPath}`);
          process.exit(1);
        }
        const applied = applySecretsFile(secretsPath);
        if (applied.size > 0) {
          console.log(`Loaded ${applied.size} secret(s) from ${secretsPath}: ${[...applied].join(", ")}`);
        }
      } else {
        const auto = findDefaultSecretsFile();
        if (auto) {
          const applied = applySecretsFile(auto);
          if (applied.size > 0) {
            console.log(`Auto-loaded ${applied.size} secret(s) from ${auto}: ${[...applied].join(", ")}`);
          }
        }
      }

      // ── Parse IDE selection ───────────────────────────────────────────────
      const ideArg = (opts.ide ?? "cursor").toLowerCase().trim();
      let ideIds: IdeId[];
      if (ideArg === "all") {
        ideIds = listAdapterIds();
      } else if (ideArg === "cursor" || ideArg === "copilot" || ideArg === "zelos") {
        ideIds = [ideArg];
      } else {
        console.error(
          `Error: invalid --ide value: ${ideArg}. Valid: cursor, copilot, zelos, all`,
        );
        process.exit(1);
      }

      // ── Load adapters and validate environment ────────────────────────────
      const adapterConfigs: AdapterConfig[] = [];
      for (const id of ideIds) {
        const adapter = await loadAdapter(id);
        const { ok, missing } = adapter.validateEnv();
        if (!ok) {
          console.error(
            `Error: missing env var(s) for ${adapter.label}: ${missing.join(", ")}`,
          );
          process.exit(1);
        }
        const modelForIde =
          id === "cursor"
            ? (opts.cursorModel ?? opts.model ?? adapter.defaultModel)
            : id === "copilot"
              ? (opts.copilotModel ?? opts.model ?? adapter.defaultModel)
              : id === "zelos"
                ? (opts.zelosModel ?? opts.model ?? adapter.defaultModel)
                : (opts.model ?? adapter.defaultModel);
        adapterConfigs.push({ adapter, model: modelForIde });
      }

      // ── Validate other options ────────────────────────────────────────────
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
      const prompts = JSON.parse(readFileSync(promptsPath, "utf-8")) as PromptDef[];

      const projectRoot = findProjectRoot(process.cwd());

      const modeCount = (modes ?? MODES).length;
      const adapterLabels = adapterConfigs
        .map((c) => `${c.adapter.label}/${c.model}`)
        .join(", ");
      console.log(
        `Running ${prompts.length} prompts × ${modeCount} mode${modeCount === 1 ? "" : "s"} × ` +
          `${adapterConfigs.length} adapter${adapterConfigs.length === 1 ? "" : "s"}...`,
      );
      console.log(`Adapters: ${adapterLabels}`);
      console.log(`Modes:    ${(modes ?? MODES).join(", ")}`);
      if (pinResponseFormat !== undefined) console.log(`Pin:      response_format=${pinResponseFormat}`);
      if (pinStripMeta !== undefined) console.log(`Pin:      strip_meta=${pinStripMeta}`);
      console.log(`Assets:   clean=${opts.cleanAssets !== false}, refresh=${opts.refreshAssets ?? Boolean(opts.rules)}`);
      if (opts.logTranscripts) console.log(`Transcripts: enabled (results/transcripts/)`);
      if (opts.agent) console.log(`Agent:    ${opts.agent}`);
      if (opts.rulesDir) console.log(`Rules dir: ${opts.rulesDir}`);
      console.log(`Root:     ${projectRoot}`);
      console.log(`Output:   ${opts.output}\n`);

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
        await coreRunSuite({
          zelosmcpUrl: opts.url,
          adapterConfigs,
          prompts,
          modes,
          delayMs: parseInt(opts.delay, 10),
          outputPath: runLogPath,
          enableRules: opts.rules,
          rulesDir: opts.rulesDir,
          agent: opts.agent,
          pinResponseFormat,
          pinStripMeta,
          cleanAssets: opts.cleanAssets,
          refreshAssets: opts.refreshAssets,
          logTranscripts: opts.logTranscripts,
        });
      } catch (err) {
        exitCode = 1;
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`\ncoreRunSuite failed: ${msg}`);
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
