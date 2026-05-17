import type { IdeId, RunLogEntry, RulesConfig, TokenUsage, PromptDef, Transcript } from "./types.js";

// ── Per-run options passed to adapter.run() ───────────────────────────────────

export interface AdapterRunOpts {
  prompt: PromptDef;
  model: string;
  enableRules: boolean;
  /** Override the IDE's default rules directory. Relative to projectRoot or absolute. */
  rulesDir?: string;
  projectRoot: string;
  /** Full URL of the zelosMCP /mcp endpoint, e.g. "http://localhost:8000/mcp". */
  mcpServerUrl: string;
  /** When true, adapters should capture full conversation transcripts. */
  logTranscripts?: boolean;
}

export interface AdapterRunResult {
  status: "ok" | "error";
  tokenUsage?: TokenUsage;
  startTime: string;
  endTime: string;
  /** Full conversation transcript (only populated when logTranscripts is set). */
  transcript?: Transcript;
}

// ── Options for asset push ────────────────────────────────────────────────────

export interface AdapterPushOpts {
  repo: string;
  kinds?: readonly string[];
  access?: "read-only" | "read-write";
  toolUse?: "available" | "priority";
}

// ── The adapter contract ──────────────────────────────────────────────────────

/**
 * Common interface that every IDE adapter must implement.
 * Each adapter encapsulates the IDE-specific agent SDK, token-tracking
 * mechanism, and file-system paths for rules / assets.
 *
 * To add a new IDE:
 *   1. Create `src/adapters/<ide>/index.ts` implementing this interface.
 *   2. Register it in `src/adapters/registry.ts`.
 */
export interface IdeAdapter {
  /** Stable identifier for this IDE (matches the IdeId union in types.ts). */
  readonly id: IdeId;
  /** Human-readable label, e.g. "Cursor" or "GitHub Copilot". */
  readonly label: string;
  /** Default model ID to use when --model is not explicitly set. */
  readonly defaultModel: string;

  /**
   * Send a single prompt to the IDE's agent and return the result.
   * The adapter is responsible for creating and disposing the agent session.
   */
  run(opts: AdapterRunOpts): Promise<AdapterRunResult>;

  /**
   * Return rule files for this IDE. When rulesDir is provided (from --rules-dir)
   * it overrides the IDE's default discovery logic.
   */
  findRules(projectRoot: string, rulesDir?: string): RulesConfig;

  /** Remove pushed zelosMCP assets from the project's IDE-specific directory. */
  cleanAssets(zelosmcpUrl: string, projectRoot: string): Promise<void>;

  /** Push fresh zelosMCP assets to the project's IDE-specific directory. */
  pushAssets(zelosmcpUrl: string, opts: AdapterPushOpts): Promise<void>;

  /** Verify that required environment variables are present. */
  validateEnv(): { ok: boolean; missing: string[] };

  /**
   * (Optional) Re-fetch token usage for an existing log entry using the
   * IDE's analytics API. Adapters without a usable post-hoc API return null.
   */
  refetchTokenUsage?(entry: RunLogEntry): Promise<TokenUsage | null>;
}
