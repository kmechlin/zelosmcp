import { describe, it, expect, vi, beforeEach } from "vitest";
import { runStaticAnalysis } from "../src/static-analyzer.js";

beforeEach(() => {
  vi.restoreAllMocks();
});

const MOCK_TOOLS_NULL = [
  {
    name: "pincher__search",
    description: "Full search description with lots of text...",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string" },
        kind: { type: "string" },
        language: { type: "string" },
      },
    },
  },
  {
    name: "pincher__context",
    description: "Full context description...",
    inputSchema: {
      type: "object",
      properties: { id: { type: "string" } },
    },
  },
];

const MOCK_TOOLS_COMPRESSED = [
  {
    name: "pincher__get_tool_schema",
    description: "Wrapper: - search: Search symbols\n- context: Get context",
    inputSchema: {
      type: "object",
      properties: { tool_name: { type: "string" } },
    },
  },
  {
    name: "pincher__invoke_tool",
    description: "Invoke a tool",
    inputSchema: {
      type: "object",
      properties: { tool_name: { type: "string" }, tool_input: { type: "object" } },
    },
  },
];

const MOCK_TOOLS_MAX = [
  {
    name: "pincher__list_tools",
    description: "List available tools",
    inputSchema: { type: "object", properties: {} },
  },
];

function mockFetchForMode(url: string, init?: RequestInit): Promise<Response> {
  const body = init?.body as string | undefined;

  if (url.endsWith("/api/status")) {
    return Promise.resolve(
      new Response(
        JSON.stringify({
          servers: [
            {
              name: "pincher",
              running: true,
              builtin: false,
              spec: { name: "pincher", transport: "stdio", command: "pincher", args: ["serve"] },
            },
          ],
        }),
        { status: 200 },
      ),
    );
  }

  if (url.endsWith("/api/start")) {
    return Promise.resolve(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
  }

  if (url.endsWith("/mcp") && body) {
    const parsed = JSON.parse(body);
    if (parsed.method === "initialize") {
      return Promise.resolve(
        new Response(
          JSON.stringify({ jsonrpc: "2.0", id: 1, result: {} }),
          { status: 200 },
        ),
      );
    }
    if (parsed.method === "tools/list") {
      // Determine which tools to return based on what config was last POSTed
      const lastCall = (globalThis as Record<string, unknown>).__lastAppliedConfig as string | undefined;
      let tools = MOCK_TOOLS_NULL;
      if (lastCall?.includes('"level":"medium"')) {
        tools = MOCK_TOOLS_COMPRESSED;
      } else if (lastCall?.includes('"level":"max"')) {
        tools = MOCK_TOOLS_MAX;
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            jsonrpc: "2.0",
            id: 2,
            result: { tools },
          }),
          { status: 200 },
        ),
      );
    }
  }

  return Promise.resolve(new Response("not found", { status: 404 }));
}

describe("runStaticAnalysis", () => {
  it("returns results for all three modes", async () => {
    (globalThis as Record<string, unknown>).__lastAppliedConfig = undefined;

    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      if (typeof url === "string" && url.endsWith("/api/start") && init?.body) {
        (globalThis as Record<string, unknown>).__lastAppliedConfig = init.body as string;
      }
      return mockFetchForMode(url, init);
    }));

    const results = await runStaticAnalysis("http://localhost:8000", ["null", "medium", "max"]);

    expect(results).toHaveLength(3);
    expect(results[0].mode).toBe("null");
    expect(results[1].mode).toBe("medium");
    expect(results[2].mode).toBe("max");

    // null mode should have more tokens than compressed
    expect(results[0].toolDefsTokens).toBeGreaterThan(results[2].toolDefsTokens);

    globalThis.fetch = original;
  }, 15000);

  it("returns correct tool counts per mode", async () => {
    (globalThis as Record<string, unknown>).__lastAppliedConfig = undefined;

    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      if (typeof url === "string" && url.endsWith("/api/start") && init?.body) {
        (globalThis as Record<string, unknown>).__lastAppliedConfig = init.body as string;
      }
      return mockFetchForMode(url, init);
    }));

    const results = await runStaticAnalysis("http://localhost:8000", ["null", "medium", "max"]);

    expect(results[0].toolCount).toBe(2);
    expect(results[1].toolCount).toBe(2);
    expect(results[2].toolCount).toBe(1);
  }, 15000);
});
