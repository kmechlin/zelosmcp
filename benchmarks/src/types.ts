export type Mode = "null" | "low" | "medium" | "high" | "max";

export const MODES: readonly Mode[] = [
  "null",
  "low",
  "medium",
  "high",
  "max",
] as const;

export type ResponseFormat = "raw" | "toon" | "compact_json";

export const RESPONSE_FORMATS: readonly ResponseFormat[] = [
  "raw",
  "toon",
  "compact_json",
] as const;

export type IdeId = "cursor" | "copilot" | "claude";

export interface StaticResult {
  mode: Mode;
  toolDefsBytes: number;
  toolDefsTokens: number;
  toolCount: number;
}

export interface RunLogEntry {
  /** IDE that produced this entry. Optional for backward compatibility with pre-v2 run logs. */
  ide?: IdeId;
  mode: Mode;
  promptId: string;
  /** Full text of the prompt sent to the agent. */
  promptText?: string;
  model: string;
  startTime: string;
  endTime: string;
  status: "ok" | "error";
  inputTokens?: number;
  outputTokens?: number;
  cacheWriteTokens?: number;
  cacheReadTokens?: number;
  totalTokens?: number;
}


export interface PromptDef {
  id: string;
  text: string;
  category: string;
}

// ── Transcript types ──────────────────────────────────────────────────────────

export type TranscriptEventType =
  | "thought"
  | "text"
  | "tool_call"
  | "tool_result"
  | "sub_agent_start"
  | "sub_agent_end"
  | "error";

export interface TranscriptEvent {
  type: TranscriptEventType;
  timestamp: string;
  /** Human-readable text or thinking content. */
  content?: string;
  /** For tool_call: the tool name. */
  toolName?: string;
  /** For tool_call: the tool arguments. */
  toolInput?: Record<string, unknown>;
  /** For tool_result: the tool output text (truncated if large). */
  toolOutput?: string;
  /** For sub_agent_start / sub_agent_end: the agent name or task. */
  agentName?: string;
  /** Nested events from a sub-agent invocation. */
  subEvents?: TranscriptEvent[];
}

export interface Transcript {
  ide: IdeId;
  mode: Mode;
  promptId: string;
  model: string;
  startTime: string;
  endTime: string;
  events: TranscriptEvent[];
  /** Raw stdout/stderr captured from the CLI (Copilot adapter). */
  rawOutput?: string;
}

export interface RulesConfig {
  /** Resolved absolute path of the rules directory (or primary rules file). */
  dir: string;
  /** Absolute paths of all discovered rule files. */
  files: string[];
}

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  cacheWriteTokens: number;
  cacheReadTokens: number;
  totalTokens: number;
  costCents: number;
  isHeadless: boolean;
  earliestEventTimestamp: string;
}

export interface CsvRow {
  date: string;
  user: string;
  cloudAgentId: string;
  automationId: string;
  kind: string;
  model: string;
  maxMode: string;
  inputWithCacheWrite: number;
  inputWithoutCacheWrite: number;
  cacheRead: number;
  outputTokens: number;
  totalTokens: number;
  cost: string;
}

export interface CorrelatedResult {
  runLogEntry: RunLogEntry;
  csvRow: CsvRow | null;
  matchDeltaMs: number | null;
  warning?: string;
}

export interface BenchmarkReport {
  staticResults: StaticResult[];
  correlatedResults: CorrelatedResult[];
}
