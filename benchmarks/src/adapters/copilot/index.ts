/**
 * CopilotAdapter — uses the official GitHub Copilot CLI (`copilot`) as the
 * execution engine for benchmark prompts.
 *
 * The CLI handles authentication (device-flow login via `copilot /login`),
 * model routing, and MCP server connectivity natively. This adapter spawns
 * `copilot -p "<prompt>" --allow-tool='zelosmcp' --model <model> -s` for
 * each prompt and captures stdout as the response.
 *
 * MCP wiring: before each run the adapter ensures a `zelosmcp` entry exists
 * in the Copilot CLI config (`~/.copilot/mcp-config.json` or
 * `$COPILOT_HOME/mcp-config.json`).
 *
 * Auth fallback: the CLI checks `GITHUB_TOKEN` automatically when a
 * device-flow session isn't present.
 */
import { execFile as execFileCb, execFileSync } from "node:child_process";
import { promisify } from "node:util";
import type {
  IdeAdapter,
  AdapterRunOpts,
  AdapterRunResult,
  AdapterPushOpts,
} from "../../core/adapter.js";
import type { IdeId, RulesConfig, Transcript, TranscriptEvent } from "../../core/types.js";
import { pushAssets as corePushAssets } from "../../configs.js";
import { findCopilotRules, cleanCopilotAssets } from "./rules.js";
import { ensureMcpConfig } from "./mcp-config.js";

const execFile = promisify(execFileCb);

/** Default timeout per CLI invocation (2 minutes). */
const RUN_TIMEOUT_MS = 120_000;

// ── CopilotAdapter ────────────────────────────────────────────────────────────

export class CopilotAdapter implements IdeAdapter {
  readonly id: IdeId = "copilot";
  readonly label = "GitHub Copilot (CLI)";
  readonly defaultModel = "claude-sonnet-4.5";

  async run(opts: AdapterRunOpts): Promise<AdapterRunResult> {
    const startTime = new Date().toISOString();
    let status: "ok" | "error" = "ok";
    let transcript: Transcript | undefined;

    try {
      // Make sure zelosmcp is registered in the CLI's MCP config
      await ensureMcpConfig(opts.mcpServerUrl);

      const args = buildCopilotArgs(opts);

      const { stdout, stderr } = await execFile("copilot", args, {
        cwd: opts.projectRoot,
        timeout: RUN_TIMEOUT_MS,
        maxBuffer: 10 * 1024 * 1024, // 10 MB
        env: { ...process.env },
      });

      if (stderr) {
        console.error(`    [copilot stderr] ${stderr.trim()}`);
        if (stderr.includes("No such agent")) {
          throw new Error(`Agent not found: ${stderr.trim()}`);
        }
      }
      if (!stdout.trim()) {
        console.warn("    [copilot] empty response");
      }

      if (opts.logTranscripts) {
        const endTime = new Date().toISOString();
        transcript = parseCopilotJsonl(stdout, {
          ide: "copilot",
          mode: "null", // filled in by caller
          promptId: opts.prompt.id,
          model: opts.model,
          startTime,
          endTime,
        });
      }
    } catch (err) {
      status = "error";
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`    Error: ${msg}`);
    }

    const endTime = new Date().toISOString();
    if (transcript) transcript.endTime = endTime;

    // The Copilot CLI does not expose per-token usage in -s mode,
    // so tokenUsage is omitted (timing-only metrics).
    return { status, startTime, endTime, transcript };
  }

  findRules(projectRoot: string, rulesDir?: string): RulesConfig {
    return findCopilotRules(projectRoot, rulesDir);
  }

  async cleanAssets(zelosmcpUrl: string, projectRoot: string): Promise<void> {
    return cleanCopilotAssets(zelosmcpUrl, projectRoot);
  }

  async pushAssets(zelosmcpUrl: string, opts: AdapterPushOpts): Promise<void> {
    await corePushAssets(zelosmcpUrl, {
      repo: opts.repo,
      kinds: opts.kinds,
      targets: ["vscode"],
      access: opts.access,
      toolUse: opts.toolUse,
    });
  }

  validateEnv(): { ok: boolean; missing: string[] } {
    const missing: string[] = [];
    try {
      execFileSync("copilot", ["--version"], { stdio: "pipe" });
    } catch {
      missing.push("copilot CLI (install: npm i -g @github/copilot or brew install copilot-cli, then run: copilot /login)");
    }
    return { ok: missing.length === 0, missing };
  }

  // No refetchTokenUsage — the CLI doesn't expose per-token metrics.
}

export function buildCopilotArgs(
  opts: Pick<AdapterRunOpts, "prompt" | "model" | "agent" | "logTranscripts">,
): string[] {
  const args = [
    "-p", opts.prompt.text,
    // Keep the benchmark focused on the zelosmcp MCP surface instead of
    // falling back to generic CLI tools such as bash/edit/create.
    "--allow-tool=zelosmcp",
    "--allow-tool=skill",
    "--deny-tool=bash",
    "--deny-tool=create",
    "--deny-tool=edit",
    "--deny-tool=glob",
    "--model", opts.model,
    "-s",
  ];

  if (opts.agent) {
    args.push("--agent", opts.agent);
  }

  if (opts.logTranscripts) {
    args.push("--output-format", "json");
  }

  return args;
}

// ── JSONL transcript parser ───────────────────────────────────────────────────

interface JsonlSeed {
  ide: IdeId;
  mode: string;
  promptId: string;
  model: string;
  startTime: string;
  endTime: string;
}

/**
 * Parse Copilot CLI `--output-format json` JSONL output into a Transcript.
 *
 * Each line is a JSON object with `{ type, data, id, timestamp, ... }`.
 * The `type` field uses a dotted namespace:
 *
 *   - `assistant.message`         — complete assistant text (after deltas)
 *   - `assistant.message_delta`   — streaming text chunk
 *   - `assistant.reasoning`       — complete reasoning block
 *   - `assistant.reasoning_delta` — streaming thinking chunk
 *   - `tool.execution_start`      — tool call with name + arguments
 *   - `tool.execution_complete`   — tool result
 *   - `user.message`              — the original user prompt
 *   - `result`                    — session summary (usage, exit code)
 *   - `session.*`                 — MCP server status, tools loaded (skipped)
 *   - `assistant.turn_start/end`  — turn boundaries (skipped)
 *   - `assistant.message_start`   — message boundary (skipped)
 *
 * Deltas are accumulated per-ID; the final `assistant.message` /
 * `assistant.reasoning` events supersede them and are preferred.
 */
export function parseCopilotJsonl(
  raw: string,
  seed: JsonlSeed,
): Transcript {
  const events: TranscriptEvent[] = [];
  const now = () => new Date().toISOString();

  // Track IDs of reasoning/message blocks that got a final event,
  // so we can de-duplicate deltas when the final is present.
  const finalReasoningIds = new Set<string>();
  const finalMessageIds = new Set<string>();
  // Map toolCallId → toolName so we can label tool.execution_complete events
  // (which only carry the callId, not the name).
  const toolCallNames = new Map<string, string>();

  // First pass: collect all parsed objects
  const parsed: Array<{ obj: Record<string, unknown>; ts: string }> = [];
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const obj = JSON.parse(trimmed) as Record<string, unknown>;
      const ts = typeof obj.timestamp === "string" ? obj.timestamp : now();
      parsed.push({ obj, ts });

      const data = obj.data as Record<string, unknown> | undefined;
      // Track finals
      if (obj.type === "assistant.reasoning" && data?.reasoningId) {
        finalReasoningIds.add(String(data.reasoningId));
      }
      if (obj.type === "assistant.message" && data?.messageId) {
        finalMessageIds.add(String(data.messageId));
      }
      // Track tool call names for later correlation
      if (obj.type === "tool.execution_start" && data?.toolCallId && data?.toolName) {
        toolCallNames.set(String(data.toolCallId), String(data.toolName));
      }
      // Also extract from assistant.message toolRequests
      if (obj.type === "assistant.message" && data?.toolRequests) {
        const reqs = data.toolRequests as Array<{ toolCallId?: string; name?: string }>;
        if (Array.isArray(reqs)) {
          for (const req of reqs) {
            if (req.toolCallId && req.name) {
              toolCallNames.set(req.toolCallId, req.name);
            }
          }
        }
      }
    } catch {
      events.push({ type: "text", timestamp: now(), content: trimmed });
    }
  }

  // Second pass: emit structured events
  for (const { obj, ts } of parsed) {
    const type = String(obj.type ?? "unknown");
    const data = (obj.data ?? {}) as Record<string, unknown>;

    switch (type) {
      // ── User prompt ─────────────────────────────────────────────────
      case "user.message":
        events.push({
          type: "text",
          timestamp: ts,
          content: `[user] ${String(data.content ?? "")}`,
        });
        break;

      // ── Assistant text (final, complete) ────────────────────────────
      case "assistant.message": {
        const content = String(data.content ?? "");
        if (content) {
          events.push({ type: "text", timestamp: ts, content });
        }

        // Also extract tool requests embedded in the message
        const toolRequests = data.toolRequests as Array<{
          toolCallId?: string;
          name?: string;
          arguments?: Record<string, unknown>;
        }> | undefined;
        if (Array.isArray(toolRequests)) {
          for (const req of toolRequests) {
            events.push({
              type: "tool_call",
              timestamp: ts,
              toolName: req.name ?? "",
              toolInput: req.arguments,
            });
          }
        }
        break;
      }

      // ── Assistant text (streaming delta) — only if no final ────────
      case "assistant.message_delta": {
        const msgId = String(data.messageId ?? "");
        if (msgId && finalMessageIds.has(msgId)) break; // final supersedes
        const delta = String(data.deltaContent ?? "");
        if (delta) {
          events.push({ type: "text", timestamp: ts, content: delta });
        }
        break;
      }

      // ── Reasoning (final, complete) ─────────────────────────────────
      case "assistant.reasoning": {
        const content = String(data.content ?? "");
        if (content) {
          events.push({ type: "thought", timestamp: ts, content });
        }
        break;
      }

      // ── Reasoning (streaming delta) — only if no final ─────────────
      case "assistant.reasoning_delta": {
        const rId = String(data.reasoningId ?? "");
        if (rId && finalReasoningIds.has(rId)) break; // final supersedes
        const delta = String(data.deltaContent ?? "");
        if (delta) {
          events.push({ type: "thought", timestamp: ts, content: delta });
        }
        break;
      }

      // ── Tool call start ─────────────────────────────────────────────
      case "tool.execution_start":
        events.push({
          type: "tool_call",
          timestamp: ts,
          toolName: String(data.toolName ?? ""),
          toolInput: data.arguments as Record<string, unknown> | undefined,
        });
        break;

      // ── Tool call result ────────────────────────────────────────────
      case "tool.execution_complete": {
        const callId = String(data.toolCallId ?? "");
        const toolName = String(data.toolName ?? "") || toolCallNames.get(callId) || "";
        const result = data.result as Record<string, unknown> | undefined;
        const output = result
          ? String(result.detailedContent ?? result.content ?? JSON.stringify(result))
          : "";
        events.push({
          type: "tool_result",
          timestamp: ts,
          toolName,
          toolOutput: truncate(output, 32_768),
        });
        break;
      }

      // ── Session summary ─────────────────────────────────────────────
      case "result":
        events.push({
          type: "text",
          timestamp: ts,
          content: `[session] exit=${data.exitCode ?? "?"}, usage=${JSON.stringify(data.usage ?? {})}`,
        });
        break;

      // ── Skip noise: session setup, turn boundaries, message starts ──
      case "session.mcp_server_status_changed":
      case "session.mcp_servers_loaded":
      case "session.skills_loaded":
      case "session.tools_updated":
      case "assistant.turn_start":
      case "assistant.turn_end":
      case "assistant.message_start":
        break;

      default:
        // Unknown event types — keep raw for debugging
        events.push({
          type: "text",
          timestamp: ts,
          content: `[${type}] ${JSON.stringify(data)}`,
        });
        break;
    }
  }

  return {
    ide: seed.ide,
    mode: seed.mode as Transcript["mode"],
    promptId: seed.promptId,
    model: seed.model,
    startTime: seed.startTime,
    endTime: seed.endTime,
    events,
    rawOutput: raw,
  };
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + `… [truncated, ${s.length} chars total]` : s;
}
