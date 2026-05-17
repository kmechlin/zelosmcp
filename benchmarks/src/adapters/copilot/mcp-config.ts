/**
 * Ensure the zelosMCP server entry exists in the Copilot CLI's MCP config.
 *
 * Config location: `$COPILOT_HOME/mcp-config.json` (defaults to `~/.copilot/mcp-config.json`).
 *
 * This merges gracefully: existing user-configured servers are preserved;
 * only the `zelosmcp` entry is added or updated to match the given URL.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

interface McpServerEntry {
  type: string;
  url: string;
  tools: string[];
}

interface McpConfig {
  mcpServers?: Record<string, McpServerEntry>;
}

function copilotHome(): string {
  return process.env.COPILOT_HOME ?? join(homedir(), ".copilot");
}

function configPath(): string {
  return join(copilotHome(), "mcp-config.json");
}

/**
 * Read, merge, and write the Copilot CLI MCP config so that `zelosmcp`
 * points at the given URL. Existing servers are left untouched.
 */
export async function ensureMcpConfig(zelosmcpUrl: string): Promise<void> {
  const cfgPath = configPath();
  let config: McpConfig = {};

  if (existsSync(cfgPath)) {
    try {
      config = JSON.parse(readFileSync(cfgPath, "utf-8")) as McpConfig;
    } catch {
      // Malformed JSON — start fresh but warn
      console.warn(`[copilot] could not parse ${cfgPath}; overwriting mcpServers block`);
    }
  }

  const servers = config.mcpServers ?? {};
  const existing = servers.zelosmcp;

  if (existing?.url === zelosmcpUrl && existing?.type === "http" && existing?.tools?.[0] === "*") {
    return; // Already up to date
  }

  servers.zelosmcp = { type: "http", url: zelosmcpUrl, tools: ["*"] };
  config.mcpServers = servers;

  const dir = copilotHome();
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }
  writeFileSync(cfgPath, JSON.stringify(config, null, 2) + "\n");
}
