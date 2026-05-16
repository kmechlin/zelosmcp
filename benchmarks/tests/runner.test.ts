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

vi.mock("node:fs", async () => {
  const actual = await vi.importActual<typeof import("node:fs")>("node:fs");
  return {
    ...actual,
    existsSync: vi.fn().mockReturnValue(false),
    mkdirSync: vi.fn(),
    writeFileSync: vi.fn(),
    readFileSync: vi.fn().mockReturnValue("[]"),
  };
});

import { runPrompt } from "../src/runner.js";
import { Agent } from "@cursor/sdk";

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
