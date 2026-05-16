import { Agent } from "@cursor/sdk";
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import {
  MODES,
  type Mode,
  type RunLogEntry,
  type PromptDef,
  type StaticResult,
} from "./types.js";
import { buildConfig, applyConfig, fetchCurrentConfig } from "./configs.js";
import { getSessionCookie, fetchTokenUsage } from "./usage-api.js";
import { runStaticAnalysis } from "./static-analyzer.js";

export interface RunPromptOpts {
  zelosmcpUrl: string;
  mode: Mode;
  prompt: PromptDef;
  model: string;
  apiKey: string;
  enableRules?: boolean;
}

export async function runPrompt(opts: RunPromptOpts): Promise<RunLogEntry> {
  const startTime = new Date().toISOString();
  let status: "ok" | "error" = "ok";
  let inputTokens: number | undefined;
  let outputTokens: number | undefined;
  let cacheWriteTokens: number | undefined;
  let cacheReadTokens: number | undefined;
  let totalTokens: number | undefined;

  const projectRoot = findProjectRoot(process.cwd());

  const agent = await Agent.create({
    apiKey: opts.apiKey,
    model: { id: opts.model },
    local: {
      cwd: projectRoot,
      ...(opts.enableRules ? { settingSources: ["project"] } : {}),
    },
    mcpServers: {
      zelosmcp: {
        url: `${opts.zelosmcpUrl}/mcp`,
      },
    },
  });

  try {
    const run = await agent.send(opts.prompt.text);

    await run.wait();

    const cookie = getSessionCookie();
    if (cookie) {
      const startMs = new Date(startTime).getTime();
      const endMs = Date.now();
      const usage = await fetchTokenUsage(startMs, endMs, cookie);
      if (usage) {
        inputTokens = usage.inputTokens;
        outputTokens = usage.outputTokens;
        cacheWriteTokens = usage.cacheWriteTokens;
        cacheReadTokens = usage.cacheReadTokens;
        totalTokens = usage.totalTokens;
      }
    }
  } catch (err) {
    status = "error";
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`    Error: ${msg}`);
  } finally {
    await agent[Symbol.asyncDispose]();
  }

  const endTime = new Date().toISOString();

  return {
    mode: opts.mode,
    promptId: opts.prompt.id,
    model: opts.model,
    startTime,
    endTime,
    status,
    inputTokens,
    outputTokens,
    cacheWriteTokens,
    cacheReadTokens,
    totalTokens,
  };
}

export function staticResultsPath(runLogPath: string): string {
  return join(dirname(runLogPath), "static-results.json");
}

/** Walk up from startDir until a directory containing `.git` is found. */
export function findProjectRoot(startDir: string): string {
  let dir = startDir;
  while (true) {
    if (existsSync(join(dir, ".git"))) return dir;
    const parent = resolve(dir, "..");
    if (parent === dir) return startDir; // filesystem root, give up
    dir = parent;
  }
}

/**
 * Return absolute paths of all Cursor rule files found under
 * `{projectRoot}/.cursor/rules/`. Recurses into subdirectories.
 * Returns an empty array if the rules directory does not exist.
 */
export function findProjectRules(projectRoot: string): string[] {
  const rulesDir = join(projectRoot, ".cursor", "rules");
  if (!existsSync(rulesDir)) return [];

  const results: string[] = [];

  function walk(dir: string): void {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (/\.(md|mdc)$/i.test(entry.name)) {
        results.push(full);
      }
    }
  }

  walk(rulesDir);
  return results.sort();
}

export interface RunSuiteOpts {
  zelosmcpUrl: string;
  model: string;
  apiKey: string;
  prompts: PromptDef[];
  modes?: readonly Mode[];
  delayMs?: number;
  outputPath: string;
  enableRules?: boolean;
}

export async function runSuite(opts: RunSuiteOpts): Promise<RunLogEntry[]> {
  const modes = opts.modes ?? MODES;
  const delayMs = opts.delayMs ?? 5000;
  const entries: RunLogEntry[] = [];

  console.log("Running static analysis...");
  const staticResults: StaticResult[] = await runStaticAnalysis(opts.zelosmcpUrl);
  const sidecarPath = staticResultsPath(opts.outputPath);
  const sidecarDir = dirname(sidecarPath);
  if (!existsSync(sidecarDir)) {
    mkdirSync(sidecarDir, { recursive: true });
  }
  writeFileSync(sidecarPath, JSON.stringify(staticResults, null, 2));
  console.log(`Static results written to ${sidecarPath}\n`);

  const baseConfig = await fetchCurrentConfig(opts.zelosmcpUrl);

  for (const mode of modes) {
    const config = buildConfig(baseConfig, mode);
    await applyConfig(opts.zelosmcpUrl, config);
    await new Promise((r) => setTimeout(r, 2000));

    console.log(`\n--- Mode: ${mode} ---`);

    for (const prompt of opts.prompts) {
      console.log(`  Prompt: ${prompt.id} ...`);

      const entry = await runPrompt({
        zelosmcpUrl: opts.zelosmcpUrl,
        mode,
        prompt,
        model: opts.model,
        apiKey: opts.apiKey,
        enableRules: opts.enableRules,
      });

      entries.push(entry);
      appendToRunLog(opts.outputPath, entry);

      console.log(`    Status: ${entry.status}  (${entry.startTime} → ${entry.endTime})`);

      if (delayMs > 0) {
        await new Promise((r) => setTimeout(r, delayMs));
      }
    }
  }

  // Restore default
  const restoreConfig = buildConfig(baseConfig, "medium");
  await applyConfig(opts.zelosmcpUrl, restoreConfig);

  // Final pass: re-query every entry in case late-arriving events pushed counts higher
  const cookie = getSessionCookie();
  if (cookie) {
    console.log("\nRefetching token data for all entries...");
    const { updated } = await refetchRunLog(opts.outputPath, cookie);
    console.log(`  ${updated}/${entries.length} entries updated with higher token counts`);
  }

  return readRunLog(opts.outputPath);
}

function appendToRunLog(path: string, entry: RunLogEntry): void {
  const dir = dirname(path);
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }

  let log: RunLogEntry[] = [];
  if (existsSync(path)) {
    log = JSON.parse(readFileSync(path, "utf-8")) as RunLogEntry[];
  }

  log.push(entry);
  writeFileSync(path, JSON.stringify(log, null, 2));
}

export function readRunLog(path: string): RunLogEntry[] {
  return JSON.parse(readFileSync(path, "utf-8")) as RunLogEntry[];
}

export async function refetchRunLog(
  path: string,
  cookie: string,
  pollOpts: { pollIntervalMs?: number; maxPolls?: number } = {},
): Promise<{ updated: number; total: number }> {
  const log = readRunLog(path);
  let updated = 0;

  for (let i = 0; i < log.length; i++) {
    const entry = log[i];
    const startMs = new Date(entry.startTime).getTime();
    const endMs = new Date(entry.endTime).getTime();

    const usage = await fetchTokenUsage(startMs, endMs, cookie, pollOpts);
    if (usage && (usage.totalTokens ?? 0) > (entry.totalTokens ?? 0)) {
      log[i] = {
        ...entry,
        inputTokens: usage.inputTokens,
        outputTokens: usage.outputTokens,
        cacheWriteTokens: usage.cacheWriteTokens,
        cacheReadTokens: usage.cacheReadTokens,
        totalTokens: usage.totalTokens,
      };
      updated++;
    }
  }

  writeFileSync(path, JSON.stringify(log, null, 2));
  return { updated, total: log.length };
}
