import { describe, it, expect, vi, beforeEach } from "vitest";

const { mockWait, mockSend, mockDispose, mockAgent, mockGetSessionCookie, mockFetchTokenUsage } = vi.hoisted(() => {
  const mockWait = vi.fn().mockResolvedValue({
    status: "finished",
    durationMs: 1200,
  });
  const mockSend = vi.fn().mockResolvedValue({ wait: mockWait });
  const mockDispose = vi.fn().mockResolvedValue(undefined);
  const mockAgent = { send: mockSend, [Symbol.asyncDispose]: mockDispose };
  const mockGetSessionCookie = vi.fn().mockReturnValue("user_123%3A%3Afaketoken");
  const mockFetchTokenUsage = vi.fn().mockResolvedValue({
    inputTokens: 1000,
    outputTokens: 200,
    cacheWriteTokens: 500,
    cacheReadTokens: 3000,
    totalTokens: 4700,
  });
  return { mockWait, mockSend, mockDispose, mockAgent, mockGetSessionCookie, mockFetchTokenUsage };
});

vi.mock("@cursor/sdk", () => ({
  Agent: {
    create: vi.fn().mockResolvedValue(mockAgent),
  },
}));

vi.mock("../src/usage-api.js", () => ({
  getSessionCookie: mockGetSessionCookie,
  fetchTokenUsage: mockFetchTokenUsage,
}));

vi.mock("../src/static-analyzer.js", () => ({
  runStaticAnalysis: vi.fn().mockResolvedValue([]),
}));

vi.mock("node:fs", async () => {
  const actual = await vi.importActual<typeof import("node:fs")>("node:fs");
  return {
    ...actual,
    existsSync: vi.fn().mockReturnValue(false),
    mkdirSync: vi.fn(),
    writeFileSync: vi.fn(),
    readFileSync: vi.fn().mockReturnValue("[]"),
    rmSync: vi.fn(),
  };
});

import { cleanProjectAssets, runPrompt, runSuite } from "../src/runner.js";
import { Agent } from "@cursor/sdk";
import { existsSync, rmSync } from "node:fs";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(Agent.create).mockResolvedValue(mockAgent);
  mockSend.mockResolvedValue({ wait: mockWait });
  mockWait.mockResolvedValue({ status: "finished", durationMs: 1200 });
  mockDispose.mockResolvedValue(undefined);
  mockGetSessionCookie.mockReturnValue("user_123%3A%3Afaketoken");
  mockFetchTokenUsage.mockResolvedValue({
    inputTokens: 1000,
    outputTokens: 200,
    cacheWriteTokens: 500,
    cacheReadTokens: 3000,
    totalTokens: 4700,
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ ok: true }),
  }));
});

describe("cleanProjectAssets", () => {
  it("removes zelosMCP rules and pushed asset files", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url.includes("kind=skill")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { kind: "skill", backend: "pincher", name: "zelosmcp-pincher" },
          ]),
        });
      }
      if (url.includes("kind=agent")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { kind: "agent", backend: "pincher", name: "explore" },
          ]),
        });
      }
      if (url.includes("kind=prompt")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { kind: "prompt", backend: "pincher", name: "find-callers" },
          ]),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    }));

    await cleanProjectAssets(
      "http://localhost:8000",
      "/Users/KMECHL/workspace/zelosmcp",
    );

    expect(vi.mocked(rmSync)).toHaveBeenCalledWith(
      "/Users/KMECHL/workspace/zelosmcp/.cursor/rules/zelosmcp.mdc",
      { recursive: true, force: true },
    );
    expect(vi.mocked(rmSync)).toHaveBeenCalledWith(
      "/Users/KMECHL/workspace/zelosmcp/.cursor/skills/zelosmcp-pincher",
      { recursive: true, force: true },
    );
    expect(vi.mocked(rmSync)).toHaveBeenCalledWith(
      "/Users/KMECHL/workspace/zelosmcp/.cursor/agents/explore.md",
      { recursive: true, force: true },
    );
    expect(vi.mocked(rmSync)).toHaveBeenCalledWith(
      "/Users/KMECHL/workspace/zelosmcp/.cursor/commands/find-callers.md",
      { recursive: true, force: true },
    );
  });
});

describe("runSuite asset refresh behavior", () => {
  it("cleans assets first and pushes assets after later config reloads", async () => {
    vi.useFakeTimers();
    vi.mocked(existsSync).mockImplementation((path) =>
      String(path).endsWith("/zelosmcp/.git"),
    );
    const mockFetch = vi.fn((url: string, init?: RequestInit) => {
      if (url.includes("/api/assets?kind=")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (url.endsWith("/api/status")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            servers: [
              {
                name: "pincher",
                running: true,
                builtin: false,
                spec: {
                  name: "pincher",
                  transport: "stdio",
                  command: "pincher",
                  compress: { level: "medium", scope: "aggregator" },
                },
              },
            ],
          }),
        });
      }
      if (url.endsWith("/api/start")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
      }
      if (url.includes("/api/assets/push/")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
    });
    vi.stubGlobal("fetch", mockFetch);

    const promise = runSuite({
      zelosmcpUrl: "http://localhost:8000",
      model: "composer-2-fast",
      apiKey: "key",
      prompts: [{ id: "p", text: "prompt", category: "test" }],
      modes: ["null", "medium"],
      delayMs: 0,
      outputPath: "results/run-log.json",
      enableRules: true,
    });

    await vi.runAllTimersAsync();
    await promise;
    vi.useRealTimers();

    const calls = mockFetch.mock.calls.map(([url, init]) => ({
      url: String(url),
      body: init && "body" in init ? String(init.body) : "",
    }));
    const pushCalls = calls.filter((c) => c.url.includes("/api/assets/push/"));
    expect(pushCalls.map((c) => c.url)).toEqual([
      "http://localhost:8000/api/assets/push/rule",
      "http://localhost:8000/api/assets/push/agent",
      "http://localhost:8000/api/assets/push/skill",
      "http://localhost:8000/api/assets/push/prompt",
    ]);
    expect(pushCalls[0].body).toContain('"repo":"zelosmcp"');
    // First stage config reload is intentionally left asset-free to keep
    // the no-assets baseline; pushes happen for the next stage.
    expect(mockFetch.mock.calls.filter(([url]) => String(url).endsWith("/api/start")).length).toBeGreaterThanOrEqual(2);
  });
});

describe("runPrompt", () => {
  it("calls Agent.create with the correct options", async () => {
    await runPrompt({
      zelosmcpUrl: "http://localhost:8000",
      mode: "medium",
      prompt: { id: "test", text: "Explain the architecture", category: "exploration" },
      model: "composer-2-fast",
      apiKey: "test-key",
    });

    expect(Agent.create).toHaveBeenCalledWith(
      expect.objectContaining({
        apiKey: "test-key",
        model: { id: "composer-2-fast" },
      }),
    );
  });

  it("calls agent.send with the prompt text", async () => {
    await runPrompt({
      zelosmcpUrl: "http://localhost:8000",
      mode: "medium",
      prompt: { id: "test", text: "Explain the architecture", category: "exploration" },
      model: "composer-2-fast",
      apiKey: "test-key",
    });

    expect(mockSend).toHaveBeenCalledWith("Explain the architecture");
  });

  it("captures token usage from the usage API after run.wait()", async () => {
    mockFetchTokenUsage.mockResolvedValueOnce({
      inputTokens: 4200,
      outputTokens: 350,
      cacheWriteTokens: 1000,
      cacheReadTokens: 8000,
      totalTokens: 13550,
    });

    const entry = await runPrompt({
      zelosmcpUrl: "http://localhost:8000",
      mode: "medium",
      prompt: { id: "test", text: "test", category: "test" },
      model: "composer-2-fast",
      apiKey: "test-key",
    });

    expect(entry.inputTokens).toBe(4200);
    expect(entry.outputTokens).toBe(350);
    expect(entry.cacheWriteTokens).toBe(1000);
    expect(entry.cacheReadTokens).toBe(8000);
    expect(entry.totalTokens).toBe(13550);
    expect(entry.status).toBe("ok");
  });

  it("records ok status and no tokens when usage API returns null", async () => {
    mockFetchTokenUsage.mockResolvedValueOnce(null);

    const entry = await runPrompt({
      zelosmcpUrl: "http://localhost:8000",
      mode: "null",
      prompt: { id: "test", text: "test", category: "test" },
      model: "composer-2-fast",
      apiKey: "test-key",
    });

    expect(entry.status).toBe("ok");
    expect(entry.inputTokens).toBeUndefined();
    expect(entry.totalTokens).toBeUndefined();
  });

  it("skips usage API when no session cookie is available", async () => {
    mockGetSessionCookie.mockReturnValueOnce(null);

    const entry = await runPrompt({
      zelosmcpUrl: "http://localhost:8000",
      mode: "null",
      prompt: { id: "test", text: "test", category: "test" },
      model: "composer-2-fast",
      apiKey: "test-key",
    });

    expect(mockFetchTokenUsage).not.toHaveBeenCalled();
    expect(entry.inputTokens).toBeUndefined();
  });

  it("records start and end timestamps", async () => {
    const before = new Date().toISOString();
    const entry = await runPrompt({
      zelosmcpUrl: "http://localhost:8000",
      mode: "null",
      prompt: { id: "test", text: "test", category: "test" },
      model: "composer-2-fast",
      apiKey: "test-key",
    });
    const after = new Date().toISOString();

    expect(entry.startTime >= before).toBe(true);
    expect(entry.endTime <= after).toBe(true);
    expect(entry.startTime <= entry.endTime).toBe(true);
  });

  it("records error status and no tokens when agent.send throws", async () => {
    mockSend.mockRejectedValueOnce(new Error("network error"));

    const entry = await runPrompt({
      zelosmcpUrl: "http://localhost:8000",
      mode: "max",
      prompt: { id: "fail", text: "bad prompt", category: "test" },
      model: "composer-2-fast",
      apiKey: "test-key",
    });

    expect(entry.status).toBe("error");
    expect(entry.inputTokens).toBeUndefined();
    expect(entry.totalTokens).toBeUndefined();
  });

  it("disposes the agent even when send throws", async () => {
    mockSend.mockRejectedValueOnce(new Error("network error"));

    await runPrompt({
      zelosmcpUrl: "http://localhost:8000",
      mode: "max",
      prompt: { id: "fail", text: "bad prompt", category: "test" },
      model: "composer-2-fast",
      apiKey: "test-key",
    });

    expect(mockDispose).toHaveBeenCalledTimes(1);
  });

  it("passes mcpServers config to Agent.create", async () => {
    await runPrompt({
      zelosmcpUrl: "http://localhost:9000",
      mode: "medium",
      prompt: { id: "test", text: "test", category: "test" },
      model: "composer-2-fast",
      apiKey: "key",
    });

    expect(Agent.create).toHaveBeenCalledWith(
      expect.objectContaining({
        mcpServers: {
          zelosmcp: {
            url: "http://localhost:9000/mcp",
          },
        },
      }),
    );
  });
});
