import { Agent } from "@cursor/sdk";
import type {
  IdeAdapter,
  AdapterRunOpts,
  AdapterRunResult,
  AdapterPushOpts,
} from "../../core/adapter.js";
import type { IdeId, RulesConfig, RunLogEntry, TokenUsage, Transcript, TranscriptEvent } from "../../core/types.js";
import { pushAssets as corePushAssets } from "../../configs.js";
import { getSessionCookie, fetchTokenUsage } from "../../usage-api.js";
import { findCursorRules, cleanCursorAssets } from "./rules.js";

// ── Retry helpers (re-exported for CLI suppression handlers) ──────────────────

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

// ── CursorAdapter ─────────────────────────────────────────────────────────────

export class CursorAdapter implements IdeAdapter {
  readonly id: IdeId = "cursor";
  readonly label = "Cursor";
  readonly defaultModel = "composer-2";

  constructor(private readonly _apiKey?: string) {}

  private get apiKey(): string {
    return this._apiKey ?? process.env.CURSOR_API_KEY ?? "";
  }

  async run(opts: AdapterRunOpts): Promise<AdapterRunResult> {
    const startTime = new Date().toISOString();
    let status: "ok" | "error" = "ok";
    let tokenUsage: TokenUsage | undefined;
    let transcript: Transcript | undefined;

    let agent: InstanceType<typeof Agent> | undefined;
    try {
      agent = await withRetry(
        () =>
          Agent.create({
            apiKey: this.apiKey,
            model: { id: opts.model },
            local: {
              cwd: opts.projectRoot,
              ...(opts.enableRules ? { settingSources: ["project"] } : {}),
            },
            mcpServers: {
              zelosmcp: { url: opts.mcpServerUrl },
            },
          }),
        { label: "Agent.create" },
      );

      const run = await agent.send(opts.prompt.text);
      await run.wait();

      // Capture conversation transcript if requested
      if (opts.logTranscripts) {
        try {
          const turns = await run.conversation();
          transcript = parseCursorTurns(turns, {
            ide: "cursor",
            mode: "null", // filled in by caller
            promptId: opts.prompt.id,
            model: opts.model,
            startTime,
          });
        } catch (err) {
          console.error(
            `    Transcript capture error (non-fatal): ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }

      const cookie = getSessionCookie();
      if (cookie) {
        const startMs = new Date(startTime).getTime();
        const endMs = Date.now();
        const usage = await fetchTokenUsage(startMs, endMs, cookie);
        if (usage) tokenUsage = usage as unknown as TokenUsage;
      }
    } catch (err) {
      status = "error";
      console.error(`    Error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      if (agent) {
        try {
          await agent[Symbol.asyncDispose]();
        } catch (err) {
          console.error(
            `    Dispose error (non-fatal): ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
    }

    const endTime = new Date().toISOString();
    if (transcript) transcript.endTime = endTime;

    return { status, tokenUsage, startTime, endTime, transcript };
  }

  findRules(projectRoot: string, rulesDir?: string): RulesConfig {
    return findCursorRules(projectRoot, rulesDir);
  }

  async cleanAssets(zelosmcpUrl: string, projectRoot: string): Promise<void> {
    return cleanCursorAssets(zelosmcpUrl, projectRoot);
  }

  async pushAssets(zelosmcpUrl: string, opts: AdapterPushOpts): Promise<void> {
    await corePushAssets(zelosmcpUrl, {
      repo: opts.repo,
      kinds: opts.kinds,
      targets: ["cursor"],
      access: opts.access,
      toolUse: opts.toolUse,
    });
  }

  validateEnv(): { ok: boolean; missing: string[] } {
    const missing: string[] = [];
    if (!this._apiKey && !process.env.CURSOR_API_KEY) missing.push("CURSOR_API_KEY");
    return { ok: missing.length === 0, missing };
  }

  async refetchTokenUsage(entry: RunLogEntry): Promise<TokenUsage | null> {
    const cookie = getSessionCookie();
    if (!cookie) return null;
    const usage = await fetchTokenUsage(
      new Date(entry.startTime).getTime(),
      new Date(entry.endTime).getTime(),
      cookie,
    );
    return usage as unknown as TokenUsage | null;
  }
}

// ── Cursor conversation → Transcript parser ───────────────────────────────────

import type { ConversationTurn } from "@cursor/sdk";

interface CursorSeed {
  ide: "cursor";
  mode: string;
  promptId: string;
  model: string;
  startTime: string;
}

/**
 * Convert the Cursor SDK's `ConversationTurn[]` into our `Transcript` format.
 *
 * Each turn contains `steps` which are discriminated unions with types like
 * `assistantMessage`, `toolCall` (with sub-types: `mcp`, `shell`, `read`, etc.),
 * `thinkingMessage`, and `agentConversationTurn` (sub-agents).
 */
function parseCursorTurns(
  turns: ConversationTurn[],
  seed: CursorSeed,
): Transcript {
  const events: TranscriptEvent[] = [];
  const now = () => new Date().toISOString();

  for (const turn of turns) {
    const t = turn as Record<string, unknown>;
    const turnType = String(t.type ?? "unknown");

    if (turnType === "agentConversationTurn") {
      const inner = t.turn as Record<string, unknown> | undefined;
      if (!inner) continue;

      // User message
      const userMsg = inner.userMessage as { text?: string } | undefined;
      if (userMsg?.text) {
        events.push({ type: "text", timestamp: now(), content: `[user] ${userMsg.text}` });
      }

      // Steps
      const steps = (inner.steps ?? []) as Record<string, unknown>[];
      parseSteps(steps, events, now);
    } else if (turnType === "shellConversationTurn") {
      const inner = t.turn as Record<string, unknown> | undefined;
      if (!inner) continue;
      events.push({
        type: "tool_call",
        timestamp: now(),
        toolName: "shell",
        toolInput: { command: String((inner as Record<string, unknown>).command ?? "") },
      });
    }
  }

  return {
    ide: seed.ide,
    mode: seed.mode as Transcript["mode"],
    promptId: seed.promptId,
    model: seed.model,
    startTime: seed.startTime,
    endTime: "", // filled by caller
    events,
  };
}

function parseSteps(
  steps: Record<string, unknown>[],
  events: TranscriptEvent[],
  now: () => string,
): void {
  for (const step of steps) {
    const stepType = String(step.type ?? "unknown");

    switch (stepType) {
      case "assistantMessage": {
        const msg = step.message as { text?: string } | undefined;
        events.push({
          type: "text",
          timestamp: now(),
          content: msg?.text ?? "",
        });
        break;
      }

      case "thinkingMessage": {
        const msg = step.message as { text?: string; thinkingDurationMs?: number } | undefined;
        events.push({
          type: "thought",
          timestamp: now(),
          content: msg?.text ?? "",
        });
        break;
      }

      case "toolCall": {
        const msg = step.message as Record<string, unknown> | undefined;
        if (!msg) break;
        const toolType = String(msg.type ?? "unknown");
        const args = msg.args as Record<string, unknown> | undefined;
        const result = msg.result as Record<string, unknown> | undefined;

        if (toolType === "mcp") {
          const mcpArgs = args as {
            toolName?: string;
            providerIdentifier?: string;
            args?: Record<string, unknown>;
          } | undefined;

          events.push({
            type: "tool_call",
            timestamp: now(),
            toolName: mcpArgs?.toolName ?? "mcp",
            toolInput: mcpArgs?.args,
            content: mcpArgs?.providerIdentifier
              ? `provider: ${mcpArgs.providerIdentifier}`
              : undefined,
          });

          if (result) {
            const output = extractResultText(result);
            events.push({
              type: "tool_result",
              timestamp: now(),
              toolName: mcpArgs?.toolName ?? "mcp",
              toolOutput: truncateCursor(output, 4096),
            });
          }
        } else {
          // Built-in tools: shell, read, write, edit, grep, glob, ls, etc.
          events.push({
            type: "tool_call",
            timestamp: now(),
            toolName: toolType,
            toolInput: args as Record<string, unknown> | undefined,
          });

          if (result) {
            const output = extractResultText(result);
            events.push({
              type: "tool_result",
              timestamp: now(),
              toolName: toolType,
              toolOutput: truncateCursor(output, 4096),
            });
          }
        }
        break;
      }

      default:
        // Unknown step types — record raw for debugging
        events.push({
          type: "text",
          timestamp: now(),
          content: `[${stepType}] ${JSON.stringify(step)}`,
        });
        break;
    }
  }
}

function extractResultText(result: Record<string, unknown>): string {
  // Cursor results have nested shapes; try common paths
  if (typeof result.value === "string") return result.value;
  const value = result.value as Record<string, unknown> | undefined;
  if (value?.content) {
    const content = value.content as Array<{ text?: { text?: string } }>;
    if (Array.isArray(content)) {
      return content.map((c) => c.text?.text ?? "").filter(Boolean).join("\n");
    }
  }
  return JSON.stringify(result);
}

function truncateCursor(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + `… [truncated, ${s.length} chars total]` : s;
}
