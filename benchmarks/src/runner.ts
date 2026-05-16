import { Agent } from "@cursor/sdk";
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import {
  MODES,
  type Mode,
  type ResponseFormat,
  type RunLogEntry,
  type PromptDef,
  type StaticResult,
} from "./types.js";
import {
  buildConfig,
  applyConfig,
  fetchCurrentConfig,
  detectMode,
  fetchAssets,
  pushAssets,
  type BuildConfigOverrides,
} from "./configs.js";
import { getSessionCookie, fetchTokenUsage } from "./usage-api.js";
import { runStaticAnalysis } from "./static-analyzer.js";

/**
 * Inspect an error for the connect-rpc / @cursor/sdk "transient network" markers.
 * Connect surfaces `code: 'unavailable'` and `cause.isRetryable: true` on
 * fetch-failed / DNS-flake / corporate-proxy interruptions.
 */
export function isRetryableError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const e = err as Record<string, unknown>;
  if (e.code === "unavailable" || e.code === 2) return true;
  const cause = e.cause as Record<string, unknown> | undefined;
  if (cause && (cause.isRetryable === true || cause.code === "unavailable")) {
    return true;
  }
  return false;
}

/**
 * Run `fn` with exponential backoff on retryable errors.
 * Total wait across attempts (3): ~0 + 1s + 2s = 3s.
 */
async function withRetry<T>(
  fn: () => Promise<T>,
  opts: { attempts?: number; baseMs?: number; label?: string } = {},
): Promise<T> {
  const attempts = opts.attempts ?? 3;
  const baseMs = opts.baseMs ?? 1000;
  let lastErr: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      if (i === attempts - 1 || !isRetryableError(err)) throw err;
      const wait = baseMs * Math.pow(2, i);
      const msg = err instanceof Error ? err.message : String(err);
      console.error(
        `    Retryable error${opts.label ? ` (${opts.label})` : ""}: ${msg}. ` +
          `Retrying in ${wait}ms (attempt ${i + 2}/${attempts})...`,
      );
      await new Promise((r) => setTimeout(r, wait));
    }
  }
  throw lastErr;
}

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

  let agent: Agent | undefined;
  try {
    agent = await withRetry(
      () =>
        Agent.create({
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
        }),
      { label: "Agent.create" },
    );

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
    if (agent) {
      try {
        await agent[Symbol.asyncDispose]();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`    Dispose error (non-fatal): ${msg}`);
      }
    }
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
  /**
   * Optional pinning to isolate the compression-only signal from the
   * other shrinking layers (TOON transform, _meta stripping). When set,
   * the value is injected into every server's spec before /api/start
   * so all modes share the same orthogonal config.
   */
  pinResponseFormat?: ResponseFormat;
  pinStripMeta?: boolean;
  /**
   * Remove pushed Cursor-side zelosMCP rules/skills/agents/prompts before
   * the first prompt. This gives the first stage a no-local-assets baseline.
   */
  cleanAssets?: boolean;
  /**
   * Push fresh assets after every server config reload before running the
   * next stage's prompts. Defaults to `enableRules`.
   */
  refreshAssets?: boolean;
}

function assetSlug(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64) || "asset";
}

function removePath(path: string): void {
  rmSync(path, { recursive: true, force: true });
}

export async function cleanProjectAssets(
  zelosmcpUrl: string,
  projectRoot: string,
): Promise<void> {
  const cursorRoot = join(projectRoot, ".cursor");
  const removals = new Set<string>([
    join(cursorRoot, "rules", "zelosmcp.mdc"),
    join(cursorRoot, "zelosmcp.json"),
  ]);

  try {
    for (const row of await fetchAssets(zelosmcpUrl, "skill")) {
      removals.add(join(cursorRoot, "skills", assetSlug(row.name)));
    }
    for (const row of await fetchAssets(zelosmcpUrl, "agent")) {
      removals.add(join(cursorRoot, "agents", `${assetSlug(row.name)}.md`));
    }
    for (const row of await fetchAssets(zelosmcpUrl, "prompt")) {
      removals.add(join(cursorRoot, "commands", `${assetSlug(row.name)}.md`));
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`Asset cleanup metadata fetch failed (continuing with static paths): ${msg}`);
  }

  for (const path of removals) {
    removePath(path);
  }
}

export async function runSuite(opts: RunSuiteOpts): Promise<RunLogEntry[]> {
  const modes = opts.modes ?? MODES;
  const delayMs = opts.delayMs ?? 5000;
  const entries: RunLogEntry[] = [];
  const projectRoot = findProjectRoot(process.cwd());
  const repoName = basename(projectRoot);
  const cleanAssets = opts.cleanAssets ?? true;
  const refreshAssets = opts.refreshAssets ?? Boolean(opts.enableRules);

  if (cleanAssets) {
    console.log("Removing pushed Cursor rules/assets before benchmark...");
    await cleanProjectAssets(opts.zelosmcpUrl, projectRoot);
    console.log("Asset cleanup complete.\n");
  }

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

  // Seed with whatever mode the proxy is already configured for so we
  // can skip the first applyConfig when it matches. This eliminates one
  // /api/start (and the associated 503 reload window for Cursor's MCP
  // connection) on common single-mode runs.
  let currentMode: Mode | null = detectMode(baseConfig);
  if (currentMode !== null) {
    console.log(`Detected current proxy mode: ${currentMode}`);
  }

  const overrides: BuildConfigOverrides = {};
  if (opts.pinResponseFormat !== undefined) {
    overrides.pinResponseFormat = opts.pinResponseFormat;
  }
  if (opts.pinStripMeta !== undefined) {
    overrides.pinStripMeta = opts.pinStripMeta;
  }
  const hasPins = Object.keys(overrides).length > 0;

  // When pin overrides are in play we cannot reuse the proxy's current
  // mode, even if the compression level matches — the pinned fields
  // may not match what's currently applied. Force re-apply for the
  // first mode in that case.
  let forceFirstApply = hasPins;
  let firstStage = true;

  for (const mode of modes) {
    let configChanged = false;
    if (mode === currentMode && !forceFirstApply) {
      console.log(`\n--- Mode: ${mode} (already applied, skipping reload) ---`);
    } else {
      const config = buildConfig(baseConfig, mode, overrides);
      await applyConfig(opts.zelosmcpUrl, config);
      currentMode = mode;
      forceFirstApply = false;
      configChanged = true;
      await new Promise((r) => setTimeout(r, 2000));
      console.log(`\n--- Mode: ${mode} ---`);
    }

    if (configChanged && refreshAssets && !firstStage) {
      console.log(`  Refreshing pushed assets for ${repoName} after config reload...`);
      await pushAssets(opts.zelosmcpUrl, {
        repo: repoName,
        targets: ["cursor"],
        access: "read-write",
        toolUse: "priority",
      });
      console.log("  Asset refresh complete.");
    } else if (configChanged && refreshAssets && firstStage && cleanAssets) {
      console.log("  Skipping asset refresh for first stage to preserve no-assets baseline.");
    }

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
    firstStage = false;
  }

  // Restore default — but only if we left the proxy in a non-default
  // mode OR pin overrides were applied (in which case the server has
  // pinned response_format/strip_meta values it wouldn't normally
  // have, and we should clear them by reverting to baseConfig). Saves
  // one /api/start (and its 503 reload window) on medium-only or
  // already-medium runs with no pins.
  if (currentMode !== "medium" || hasPins) {
    const restoreConfig = buildConfig(baseConfig, "medium");
    await applyConfig(opts.zelosmcpUrl, restoreConfig);
    if (refreshAssets) {
      console.log("\nRefreshing pushed assets after restoring default mode...");
      await pushAssets(opts.zelosmcpUrl, {
        repo: repoName,
        targets: ["cursor"],
        access: "read-write",
        toolUse: "priority",
      });
    }
  }

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
