export type Mode = "null" | "medium" | "max";

export const MODES: readonly Mode[] = ["null", "medium", "max"] as const;

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
