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

export interface StaticResult {
  mode: Mode;
  toolDefsBytes: number;
  toolDefsTokens: number;
  toolCount: number;
}

export interface RunLogEntry {
  mode: Mode;
  promptId: string;
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
