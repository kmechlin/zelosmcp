import { MODES, type Mode, type StaticResult } from "./types.js";
import { buildConfig, applyConfig, fetchCurrentConfig } from "./configs.js";
import { countToolDefTokens } from "./tokens.js";

interface ToolsListResult {
  result: {
    tools: unknown[];
  };
}

async function fetchToolsList(url: string): Promise<unknown[]> {
  const resp = await fetch(`${url}/mcp`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2025-11-25",
        capabilities: {},
        clientInfo: { name: "benchmark", version: "1" },
      },
    }),
  });
  if (!resp.ok) {
    throw new Error(`MCP initialize failed (${resp.status})`);
  }

  const listResp = await fetch(`${url}/mcp`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 2,
      method: "tools/list",
      params: {},
    }),
  });
  if (!listResp.ok) {
    throw new Error(`MCP tools/list failed (${listResp.status})`);
  }

  const body = (await listResp.json()) as ToolsListResult;
  return body.result.tools;
}

export async function runStaticAnalysis(
  url: string,
  modes: readonly Mode[] = MODES,
): Promise<StaticResult[]> {
  const baseConfig = await fetchCurrentConfig(url);
  const results: StaticResult[] = [];

  for (const mode of modes) {
    const config = buildConfig(baseConfig, mode);
    await applyConfig(url, config);

    // Small delay to let servers restart
    await new Promise((r) => setTimeout(r, 2000));

    const tools = await fetchToolsList(url);
    const serialized = JSON.stringify(tools);

    results.push({
      mode,
      toolDefsBytes: new TextEncoder().encode(serialized).byteLength,
      toolDefsTokens: countToolDefTokens(tools),
      toolCount: tools.length,
    });
  }

  // Restore medium (default) config at the end
  const restoreConfig = buildConfig(baseConfig, "medium");
  await applyConfig(url, restoreConfig);

  return results;
}
