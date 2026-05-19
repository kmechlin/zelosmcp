import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

// ── Tests for mcp-config.ts ─────────────────────────────────────────────────

describe("ensureMcpConfig", () => {
  let tmpHome: string;
  const origEnv = { ...process.env };

  beforeEach(() => {
    tmpHome = join(tmpdir(), `copilot-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmpHome, { recursive: true });
    process.env.COPILOT_HOME = tmpHome;
  });

  afterEach(() => {
    process.env = { ...origEnv };
    rmSync(tmpHome, { recursive: true, force: true });
  });

  it("creates mcp-config.json when it does not exist", async () => {
    const { ensureMcpConfig } = await import("../src/adapters/copilot/mcp-config.js");
    await ensureMcpConfig("http://localhost:8000/mcp");

    const cfgPath = join(tmpHome, "mcp-config.json");
    expect(existsSync(cfgPath)).toBe(true);

    const config = JSON.parse(readFileSync(cfgPath, "utf-8"));
    expect(config.mcpServers.zelosmcp).toEqual({
      type: "http",
      url: "http://localhost:8000/mcp",
      tools: ["*"],
    });
  });

  it("preserves existing servers when adding zelosmcp", async () => {
    const cfgPath = join(tmpHome, "mcp-config.json");
    writeFileSync(cfgPath, JSON.stringify({
      mcpServers: {
        "my-other-server": { type: "sse", url: "http://example.com/sse" },
      },
    }));

    const { ensureMcpConfig } = await import("../src/adapters/copilot/mcp-config.js");
    await ensureMcpConfig("http://localhost:8000/mcp");

    const config = JSON.parse(readFileSync(cfgPath, "utf-8"));
    expect(config.mcpServers["my-other-server"]).toEqual({
      type: "sse",
      url: "http://example.com/sse",
    });
    expect(config.mcpServers.zelosmcp).toEqual({
      type: "http",
      url: "http://localhost:8000/mcp",
      tools: ["*"],
    });
  });

  it("updates zelosmcp URL when it differs", async () => {
    const cfgPath = join(tmpHome, "mcp-config.json");
    writeFileSync(cfgPath, JSON.stringify({
      mcpServers: {
        zelosmcp: { type: "http", url: "http://old-host:8000/mcp", tools: ["*"] },
      },
    }));

    const { ensureMcpConfig } = await import("../src/adapters/copilot/mcp-config.js");
    await ensureMcpConfig("http://localhost:9000/mcp");

    const config = JSON.parse(readFileSync(cfgPath, "utf-8"));
    expect(config.mcpServers.zelosmcp.url).toBe("http://localhost:9000/mcp");
  });

  it("no-ops when zelosmcp is already correct", async () => {
    const cfgPath = join(tmpHome, "mcp-config.json");
    const original = JSON.stringify({
      mcpServers: {
        zelosmcp: { type: "http", url: "http://localhost:8000/mcp", tools: ["*"] },
      },
    }, null, 2) + "\n";
    writeFileSync(cfgPath, original);

    const { ensureMcpConfig } = await import("../src/adapters/copilot/mcp-config.js");
    await ensureMcpConfig("http://localhost:8000/mcp");

    // File should not be rewritten
    expect(readFileSync(cfgPath, "utf-8")).toBe(original);
  });

  it("creates COPILOT_HOME directory if it does not exist", async () => {
    const nested = join(tmpHome, "deep", "nested");
    process.env.COPILOT_HOME = nested;

    const { ensureMcpConfig } = await import("../src/adapters/copilot/mcp-config.js");
    await ensureMcpConfig("http://localhost:8000/mcp");

    expect(existsSync(join(nested, "mcp-config.json"))).toBe(true);
  });
});

// ── Tests for CopilotAdapter ────────────────────────────────────────────────

describe("CopilotAdapter", () => {
  it("has correct id, label, and defaultModel", async () => {
    const { CopilotAdapter } = await import("../src/adapters/copilot/index.js");
    const adapter = new CopilotAdapter();
    expect(adapter.id).toBe("copilot");
    expect(adapter.label).toBe("GitHub Copilot (CLI)");
    expect(adapter.defaultModel).toBe("claude-sonnet-4.5");
  });

  it("validateEnv returns correct shape", async () => {
    const { CopilotAdapter } = await import("../src/adapters/copilot/index.js");
    const adapter = new CopilotAdapter();
    const result = adapter.validateEnv();

    expect(result).toHaveProperty("ok");
    expect(result).toHaveProperty("missing");
    expect(Array.isArray(result.missing)).toBe(true);

    if (result.ok) {
      expect(result.missing).toHaveLength(0);
    } else {
      expect(result.missing.length).toBeGreaterThan(0);
      expect(result.missing[0]).toContain("copilot CLI");
    }
  });

  it("findRules delegates to findCopilotRules", async () => {
    const { CopilotAdapter } = await import("../src/adapters/copilot/index.js");
    const adapter = new CopilotAdapter();

    const tmpDir = join(tmpdir(), `copilot-rules-test-${Date.now()}`);
    mkdirSync(tmpDir, { recursive: true });

    try {
      const result = adapter.findRules(tmpDir);
      expect(result).toHaveProperty("dir");
      expect(result).toHaveProperty("files");
      expect(Array.isArray(result.files)).toBe(true);
    } finally {
      rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  it("buildCopilotArgs scopes tool permissions to zelosmcp and skill", async () => {
    const { buildCopilotArgs } = await import("../src/adapters/copilot/index.js");
    const args = buildCopilotArgs({
      prompt: { id: "p1", text: "Explain startup", category: "test" },
      model: "claude-sonnet-4.5",
      agent: "zelos-agent",
      logTranscripts: true,
    });

    expect(args).toContain("--allow-tool=zelosmcp");
    expect(args).toContain("--allow-tool=skill");
    expect(args).not.toContain("--allow-all-tools");
    expect(args).toContain("--deny-tool=bash");
    expect(args).toContain("--deny-tool=create");
    expect(args).toContain("--deny-tool=edit");
    expect(args).toContain("--deny-tool=glob");
    expect(args).toContain("--agent");
    expect(args).toContain("zelos-agent");
    expect(args).toContain("--output-format");
    expect(args).toContain("json");
  });
});

// ── Tests for adapter registry ──────────────────────────────────────────────

describe("adapter registry", () => {
  it("loads the copilot adapter via registry", async () => {
    const { loadAdapter } = await import("../src/adapters/registry.js");
    const adapter = await loadAdapter("copilot");
    expect(adapter.id).toBe("copilot");
    expect(adapter.label).toBe("GitHub Copilot (CLI)");
  });

  it("lists copilot in adapter IDs", async () => {
    const { listAdapterIds } = await import("../src/adapters/registry.js");
    const ids = listAdapterIds();
    expect(ids).toContain("copilot");
  });
});

// ── Tests for parseCopilotJsonl ─────────────────────────────────────────────

describe("parseCopilotJsonl", () => {
  const seed = {
    ide: "copilot" as const,
    mode: "medium",
    promptId: "test-prompt",
    model: "claude-sonnet-4.5",
    startTime: "2026-01-01T00:00:00Z",
    endTime: "2026-01-01T00:01:00Z",
  };

  it("parses assistant.message events", async () => {
    const { parseCopilotJsonl } = await import("../src/adapters/copilot/index.js");
    const input = '{"type":"assistant.message","data":{"messageId":"m1","content":"Hello world"},"timestamp":"2026-01-01T00:00:01Z"}\n';
    const t = parseCopilotJsonl(input, seed);

    const textEvents = t.events.filter((e) => e.type === "text");
    expect(textEvents).toHaveLength(1);
    expect(textEvents[0].content).toBe("Hello world");
    expect(t.rawOutput).toBe(input);
  });

  it("parses assistant.reasoning events", async () => {
    const { parseCopilotJsonl } = await import("../src/adapters/copilot/index.js");
    const input = '{"type":"assistant.reasoning","data":{"reasoningId":"r1","content":"Let me think..."},"timestamp":"2026-01-01T00:00:01Z"}\n';
    const t = parseCopilotJsonl(input, seed);

    const thoughts = t.events.filter((e) => e.type === "thought");
    expect(thoughts).toHaveLength(1);
    expect(thoughts[0].content).toBe("Let me think...");
  });

  it("parses tool.execution_start and tool.execution_complete events", async () => {
    const { parseCopilotJsonl } = await import("../src/adapters/copilot/index.js");
    const input = [
      '{"type":"tool.execution_start","data":{"toolCallId":"tc1","toolName":"pincher__search","arguments":{"query":"foo"}},"timestamp":"2026-01-01T00:00:01Z"}',
      '{"type":"tool.execution_complete","data":{"toolCallId":"tc1","toolName":"pincher__search","success":true,"result":{"content":"found 3 results","detailedContent":"found 3 results"}},"timestamp":"2026-01-01T00:00:02Z"}',
    ].join("\n") + "\n";
    const t = parseCopilotJsonl(input, seed);

    const calls = t.events.filter((e) => e.type === "tool_call");
    const results = t.events.filter((e) => e.type === "tool_result");
    expect(calls).toHaveLength(1);
    expect(calls[0].toolName).toBe("pincher__search");
    expect(calls[0].toolInput).toEqual({ query: "foo" });
    expect(results).toHaveLength(1);
    expect(results[0].toolOutput).toBe("found 3 results");
  });

  it("extracts tool requests from assistant.message", async () => {
    const { parseCopilotJsonl } = await import("../src/adapters/copilot/index.js");
    const input = JSON.stringify({
      type: "assistant.message",
      data: {
        messageId: "m1",
        content: "I'll search for that.",
        toolRequests: [{ toolCallId: "tc1", name: "search", arguments: { q: "test" } }],
      },
      timestamp: "2026-01-01T00:00:01Z",
    }) + "\n";
    const t = parseCopilotJsonl(input, seed);

    expect(t.events.filter((e) => e.type === "text")).toHaveLength(1);
    expect(t.events.filter((e) => e.type === "tool_call")).toHaveLength(1);
    expect(t.events.filter((e) => e.type === "tool_call")[0].toolName).toBe("search");
  });

  it("deduplicates deltas when final event is present", async () => {
    const { parseCopilotJsonl } = await import("../src/adapters/copilot/index.js");
    const input = [
      '{"type":"assistant.reasoning_delta","data":{"reasoningId":"r1","deltaContent":"chunk1"},"timestamp":"2026-01-01T00:00:01Z"}',
      '{"type":"assistant.reasoning_delta","data":{"reasoningId":"r1","deltaContent":"chunk2"},"timestamp":"2026-01-01T00:00:02Z"}',
      '{"type":"assistant.reasoning","data":{"reasoningId":"r1","content":"chunk1chunk2"},"timestamp":"2026-01-01T00:00:03Z"}',
    ].join("\n") + "\n";
    const t = parseCopilotJsonl(input, seed);

    const thoughts = t.events.filter((e) => e.type === "thought");
    // Only the final reasoning event, deltas should be skipped
    expect(thoughts).toHaveLength(1);
    expect(thoughts[0].content).toBe("chunk1chunk2");
  });

  it("keeps deltas when no final event exists", async () => {
    const { parseCopilotJsonl } = await import("../src/adapters/copilot/index.js");
    const input = [
      '{"type":"assistant.message_delta","data":{"messageId":"m1","deltaContent":"Hello "},"timestamp":"2026-01-01T00:00:01Z"}',
      '{"type":"assistant.message_delta","data":{"messageId":"m1","deltaContent":"world"},"timestamp":"2026-01-01T00:00:02Z"}',
    ].join("\n") + "\n";
    const t = parseCopilotJsonl(input, seed);

    const texts = t.events.filter((e) => e.type === "text");
    expect(texts).toHaveLength(2);
    expect(texts[0].content).toBe("Hello ");
    expect(texts[1].content).toBe("world");
  });

  it("handles non-JSON lines gracefully", async () => {
    const { parseCopilotJsonl } = await import("../src/adapters/copilot/index.js");
    const input = "Welcome to Copilot!\n" + '{"type":"assistant.message","data":{"messageId":"m1","content":"hi"},"timestamp":"2026-01-01T00:00:01Z"}\n';
    const t = parseCopilotJsonl(input, seed);

    const texts = t.events.filter((e) => e.type === "text");
    expect(texts).toHaveLength(2);
    expect(texts[0].content).toBe("Welcome to Copilot!");
    expect(texts[1].content).toBe("hi");
  });

  it("skips session noise events", async () => {
    const { parseCopilotJsonl } = await import("../src/adapters/copilot/index.js");
    const input = [
      '{"type":"session.mcp_servers_loaded","data":{"servers":[]},"timestamp":"2026-01-01T00:00:01Z"}',
      '{"type":"session.tools_updated","data":{"model":"claude-sonnet-4.5"},"timestamp":"2026-01-01T00:00:01Z"}',
      '{"type":"assistant.turn_start","data":{"turnId":"0"},"timestamp":"2026-01-01T00:00:01Z"}',
    ].join("\n") + "\n";
    const t = parseCopilotJsonl(input, seed);

    expect(t.events).toHaveLength(0);
  });

  it("preserves seed metadata in transcript", async () => {
    const { parseCopilotJsonl } = await import("../src/adapters/copilot/index.js");
    const t = parseCopilotJsonl("", seed);

    expect(t.ide).toBe("copilot");
    expect(t.mode).toBe("medium");
    expect(t.promptId).toBe("test-prompt");
    expect(t.model).toBe("claude-sonnet-4.5");
  });
});
