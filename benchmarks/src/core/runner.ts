import { existsSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import {
  MODES,
  type Mode,
  type RunLogEntry,
  type PromptDef,
  type StaticResult,
  type ResponseFormat,
  type Transcript,
} from "../types.js";
import {
  buildConfig,
  applyConfig,
  fetchCurrentConfig,
  detectMode,
  type BuildConfigOverrides,
} from "../configs.js";
import { runStaticAnalysis } from "../static-analyzer.js";
import type { IdeAdapter } from "./adapter.js";

// ── Shared path / log utilities ───────────────────────────────────────────────

/** Walk up from startDir until a directory containing `.git` is found. */
export function findProjectRoot(startDir: string): string {
  let dir = startDir;
  while (true) {
    if (existsSync(join(dir, ".git"))) return dir;
    const parent = resolve(dir, "..");
    if (parent === dir) return startDir; // filesystem root
    dir = parent;
  }
}

export function staticResultsPath(runLogPath: string): string {
  return join(dirname(runLogPath), "static-results.json");
}

export function appendToRunLog(path: string, entry: RunLogEntry): void {
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

/** Write a transcript JSON file to results/transcripts/<ide>-<mode>-<promptId>.json. */
function saveTranscript(runLogPath: string, transcript: Transcript): void {
  const transcriptsDir = join(dirname(runLogPath), "transcripts");
  if (!existsSync(transcriptsDir)) {
    mkdirSync(transcriptsDir, { recursive: true });
  }
  const filename = `${transcript.ide}-${transcript.mode}-${transcript.promptId}.json`;
  const filePath = join(transcriptsDir, filename);
  writeFileSync(filePath, JSON.stringify(transcript, null, 2));
  console.log(`    Transcript saved: ${filePath}`);
}

// ── Core run primitives ───────────────────────────────────────────────────────

export interface CoreRunPromptOpts {
  zelosmcpUrl: string;
  mode: Mode;
  prompt: PromptDef;
  model: string;
  enableRules?: boolean;
  rulesDir?: string;
  agent?: string;
  projectRoot: string;
  logTranscripts?: boolean;
}

export interface CoreRunPromptResult {
  entry: RunLogEntry;
  transcript?: Transcript;
}

export async function coreRunPrompt(
  adapter: IdeAdapter,
  opts: CoreRunPromptOpts,
): Promise<CoreRunPromptResult> {
  const result = await adapter.run({
    prompt: opts.prompt,
    model: opts.model,
    enableRules: opts.enableRules ?? false,
    rulesDir: opts.rulesDir,
    agent: opts.agent,
    projectRoot: opts.projectRoot,
    mcpServerUrl: `${opts.zelosmcpUrl}/mcp`,
    logTranscripts: opts.logTranscripts,
  });

  const entry: RunLogEntry = {
    ide: adapter.id,
    mode: opts.mode,
    promptId: opts.prompt.id,
    promptText: opts.prompt.text,
    model: opts.model,
    startTime: result.startTime,
    endTime: result.endTime,
    status: result.status,
    inputTokens: result.tokenUsage?.inputTokens,
    outputTokens: result.tokenUsage?.outputTokens,
    cacheWriteTokens: result.tokenUsage?.cacheWriteTokens,
    cacheReadTokens: result.tokenUsage?.cacheReadTokens,
    totalTokens: result.tokenUsage?.totalTokens,
  };

  // Patch the mode into the transcript (adapters don't know the mode)
  let transcript = result.transcript;
  if (transcript) {
    transcript = { ...transcript, mode: opts.mode };
  }

  return { entry, transcript };
}

// ── Core suite orchestrator ───────────────────────────────────────────────────

export interface AdapterConfig {
  adapter: IdeAdapter;
  model: string;
}

export interface CoreRunSuiteOpts {
  zelosmcpUrl: string;
  adapterConfigs: AdapterConfig[];
  prompts: PromptDef[];
  modes?: readonly Mode[];
  delayMs?: number;
  outputPath: string;
  enableRules?: boolean;
  rulesDir?: string;
  /** Agent name to activate for adapters that support it (e.g. "zelos-agent"). */
  agent?: string;
  pinResponseFormat?: ResponseFormat;
  pinStripMeta?: boolean;
  cleanAssets?: boolean;
  refreshAssets?: boolean;
  /** When true, save per-prompt transcript JSON files to results/transcripts/. */
  logTranscripts?: boolean;
}

export async function coreRunSuite(opts: CoreRunSuiteOpts): Promise<RunLogEntry[]> {
  const modes = opts.modes ?? MODES;
  const delayMs = opts.delayMs ?? 5000;
  const projectRoot = findProjectRoot(process.cwd());
  const repoName = basename(projectRoot);
  const shouldCleanAssets = opts.cleanAssets ?? true;
  const shouldRefreshAssets = opts.refreshAssets ?? Boolean(opts.enableRules);

  // ── Step 1: Clean IDE assets ─────────────────────────────────────────────
  if (shouldCleanAssets) {
    for (const { adapter } of opts.adapterConfigs) {
      console.log(`Removing pushed ${adapter.label} rules/assets before benchmark...`);
      await adapter.cleanAssets(opts.zelosmcpUrl, projectRoot);
    }
    console.log("Asset cleanup complete.\n");
  }

  // ── Step 2: Static analysis (IDE-agnostic) ───────────────────────────────
  console.log("Running static analysis...");
  const staticResults: StaticResult[] = await runStaticAnalysis(opts.zelosmcpUrl);
  const sidecarPath = staticResultsPath(opts.outputPath);
  const sidecarDir = dirname(sidecarPath);
  if (!existsSync(sidecarDir)) {
    mkdirSync(sidecarDir, { recursive: true });
  }
  writeFileSync(sidecarPath, JSON.stringify(staticResults, null, 2));
  console.log(`Static results written to ${sidecarPath}\n`);

  // ── Step 3: Compression-mode loop ────────────────────────────────────────
  const baseConfig = await fetchCurrentConfig(opts.zelosmcpUrl);
  let currentMode: Mode | null = detectMode(baseConfig);
  if (currentMode !== null) {
    console.log(`Detected current proxy mode: ${currentMode}`);
  }

  const overrides: BuildConfigOverrides = {};
  if (opts.pinResponseFormat !== undefined) overrides.pinResponseFormat = opts.pinResponseFormat;
  if (opts.pinStripMeta !== undefined) overrides.pinStripMeta = opts.pinStripMeta;
  const hasPins = Object.keys(overrides).length > 0;
  let forceFirstApply = hasPins;
  let firstStage = true;

  const runLogPath = resolve(opts.outputPath);

  try {
    for (const mode of modes) {
      let configChanged = false;

      if (mode === currentMode && !forceFirstApply) {
        const labels = opts.adapterConfigs.map((c) => c.adapter.label).join(", ");
        console.log(`\n--- Mode: ${mode} (already applied, skipping reload) [${labels}] ---`);
      } else {
        const config = buildConfig(baseConfig, mode, overrides);
        await applyConfig(opts.zelosmcpUrl, config);
        currentMode = mode;
        forceFirstApply = false;
        configChanged = true;
        await new Promise((r) => setTimeout(r, 2000));
        const labels = opts.adapterConfigs.map((c) => c.adapter.label).join(", ");
        console.log(`\n--- Mode: ${mode} [${labels}] ---`);
      }

      if (configChanged && shouldRefreshAssets && !firstStage) {
        for (const { adapter } of opts.adapterConfigs) {
          console.log(
            `  Refreshing pushed assets for ${repoName} (${adapter.label}) after config reload...`,
          );
          await adapter.pushAssets(opts.zelosmcpUrl, {
            repo: repoName,
            access: "read-write",
            toolUse: "priority",
          });
        }
        console.log("  Asset refresh complete.");
      } else if (shouldRefreshAssets && firstStage && shouldCleanAssets && !opts.agent) {
        console.log(
          "  Skipping asset refresh for first stage to preserve no-assets baseline.",
        );
      } else if (shouldRefreshAssets && firstStage && opts.agent) {
        // When --agent is specified, assets must be pushed even on first stage
        // (regardless of config change) so the agent file exists on disk.
        for (const { adapter } of opts.adapterConfigs) {
          console.log(
            `  Pushing assets for ${repoName} (${adapter.label}) — required for --agent ${opts.agent}...`,
          );
          await adapter.pushAssets(opts.zelosmcpUrl, {
            repo: repoName,
            access: "read-write",
            toolUse: "priority",
          });
        }
        console.log("  Asset push complete.");
      }

      for (const prompt of opts.prompts) {
        for (const { adapter, model } of opts.adapterConfigs) {
          console.log(`  Prompt: ${prompt.id} [${adapter.label}] ...`);

          const { entry, transcript } = await coreRunPrompt(adapter, {
            zelosmcpUrl: opts.zelosmcpUrl,
            mode,
            prompt,
            model,
            enableRules: opts.enableRules,
            rulesDir: opts.rulesDir,
            agent: opts.agent,
            projectRoot,
            logTranscripts: opts.logTranscripts,
          });

          appendToRunLog(runLogPath, entry);
          console.log(`    Status: ${entry.status}  (${entry.startTime} → ${entry.endTime})`);

          if (transcript && opts.logTranscripts) {
            saveTranscript(runLogPath, transcript);
          }
        }

        if (delayMs > 0) {
          await new Promise((r) => setTimeout(r, delayMs));
        }
      }
      firstStage = false;
    }

    // ── Step 5: Final refetch for adapters that support it ─────────────────
    for (const { adapter } of opts.adapterConfigs) {
      if (!adapter.refetchTokenUsage) continue;
      const log = readRunLog(runLogPath);
      const adapterEntries = log.filter((e) => e.ide === adapter.id);
      if (adapterEntries.length === 0) continue;

      console.log(`\nRefetching token data for ${adapter.label} entries...`);
      let updated = 0;

      for (let i = 0; i < log.length; i++) {
        if (log[i].ide !== adapter.id) continue;
        const usage = await adapter.refetchTokenUsage!(log[i]);
        if (usage && (usage.totalTokens ?? 0) > (log[i].totalTokens ?? 0)) {
          log[i] = {
            ...log[i],
            inputTokens: usage.inputTokens,
            outputTokens: usage.outputTokens,
            cacheWriteTokens: usage.cacheWriteTokens,
            cacheReadTokens: usage.cacheReadTokens,
            totalTokens: usage.totalTokens,
          };
          updated++;
        }
      }

      writeFileSync(runLogPath, JSON.stringify(log, null, 2));
      console.log(`  ${updated}/${adapterEntries.length} entries updated with higher token counts`);
    }
  } finally {
    // ── Step 4: Restore initial config (runs even on error/interrupt) ───────
    // Always re-apply the config captured before the benchmark started so the
    // server returns to its pre-benchmark state regardless of which modes ran
    // or whether the run completed successfully.
    try {
      await applyConfig(opts.zelosmcpUrl, baseConfig);
      if (shouldRefreshAssets) {
        for (const { adapter } of opts.adapterConfigs) {
          console.log(`\nRefreshing pushed assets (${adapter.label}) after restoring config...`);
          await adapter.pushAssets(opts.zelosmcpUrl, {
            repo: repoName,
            access: "read-write",
            toolUse: "priority",
          });
        }
      }
      console.log("\nServer config restored to pre-benchmark state.");
    } catch (restoreErr) {
      const msg = restoreErr instanceof Error ? restoreErr.message : String(restoreErr);
      console.error(`\nWARNING: Failed to restore server config: ${msg}`);
      console.error(
        "The server may be left in a benchmark test mode. " +
        "Manually restore via POST /api/start with your original config, " +
        "or restart zelosMCP.",
      );
    }
  }

  return readRunLog(runLogPath);
}
