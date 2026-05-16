import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  buildConfig,
  applyConfig,
  fetchAssets,
  fetchCurrentConfig,
  pushAssets,
} from "../src/configs.js";
import type { ZelosMcpConfig } from "../src/configs.js";

const baseConfig: ZelosMcpConfig = {
  mcpServers: {
    pincher: {
      command: "pincher",
      args: ["serve"],
    },
    filesystem: {
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-filesystem"],
    },
  },
};

describe("buildConfig", () => {
  it("sets compress=null for null mode", () => {
    const config = buildConfig(baseConfig, "null");
    expect(config.mcpServers.pincher.compress).toBeNull();
    expect(config.mcpServers.filesystem.compress).toBeNull();
  });

  it('sets compress.level="medium" for medium mode', () => {
    const config = buildConfig(baseConfig, "medium");
    expect(config.mcpServers.pincher.compress).toEqual({ level: "medium" });
  });

  it('sets compress.level="max" for max mode', () => {
    const config = buildConfig(baseConfig, "max");
    expect(config.mcpServers.filesystem.compress).toEqual({ level: "max" });
  });

  it("preserves other server config fields", () => {
    const config = buildConfig(baseConfig, "medium");
    expect(config.mcpServers.pincher.command).toBe("pincher");
    expect(config.mcpServers.pincher.args).toEqual(["serve"]);
  });

  it("does not mutate the original config", () => {
    const original = JSON.parse(JSON.stringify(baseConfig));
    buildConfig(baseConfig, "max");
    expect(baseConfig).toEqual(original);
  });
});

describe("applyConfig", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("POSTs config to /api/start", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const config = buildConfig(baseConfig, "medium");
    await applyConfig("http://localhost:8000", config);

    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/start",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(config),
      }),
    );
  });

  it("throws on HTTP error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        text: () => Promise.resolve("Internal Server Error"),
      }),
    );

    await expect(
      applyConfig("http://localhost:8000", baseConfig),
    ).rejects.toThrow("POST /api/start failed (500)");
  });

  it("throws when ok=false in response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({ ok: false, error: "bad config" }),
      }),
    );

    await expect(
      applyConfig("http://localhost:8000", baseConfig),
    ).rejects.toThrow("ok=false");
  });
});

describe("fetchCurrentConfig", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("builds config from /api/status servers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            servers: [
              {
                name: "zelosmcp",
                running: true,
                builtin: true,
                spec: { transport: "builtin" },
              },
              {
                name: "pincher",
                running: true,
                builtin: false,
                spec: { name: "pincher", transport: "stdio", command: "pincher", args: ["serve"] },
              },
              {
                name: "docker",
                running: false,
                builtin: false,
                spec: { name: "docker", transport: "stdio", command: "uvx", args: ["mcp-server-docker"] },
              },
            ],
          }),
      }),
    );

    const config = await fetchCurrentConfig("http://localhost:8000");
    expect(config.mcpServers).toHaveProperty("pincher");
    expect(config.mcpServers).not.toHaveProperty("zelosmcp");
    expect(config.mcpServers).not.toHaveProperty("docker");
    expect(config.mcpServers.pincher).not.toHaveProperty("name");
    expect(config.mcpServers.pincher).not.toHaveProperty("transport");
    expect(config.mcpServers.pincher.command).toBe("pincher");
  });
});

describe("pushAssets", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("POSTs each requested asset kind to the push endpoint", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await pushAssets("http://localhost:8000", {
      repo: "zelosmcp",
      kinds: ["rule", "prompt"],
      targets: ["cursor"],
      access: "read-write",
      toolUse: "priority",
    });

    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/assets/push/rule",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          repo: "zelosmcp",
          targets: ["cursor"],
          access: "read-write",
          tool_use: "priority",
        }),
      }),
    );
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/assets/push/prompt",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("throws when a push response is not ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        text: () => Promise.resolve("boom"),
      }),
    );

    await expect(
      pushAssets("http://localhost:8000", { repo: "zelosmcp", kinds: ["rule"] }),
    ).rejects.toThrow("POST /api/assets/push/rule failed (500)");
  });
});

describe("fetchAssets", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("GETs assets by kind", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve([
          { kind: "prompt", backend: "pincher", name: "find-callers" },
        ]),
      }),
    );

    const rows = await fetchAssets("http://localhost:8000", "prompt");
    expect(rows).toEqual([
      { kind: "prompt", backend: "pincher", name: "find-callers" },
    ]);
  });
});
