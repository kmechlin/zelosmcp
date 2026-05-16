import type { Mode } from "./types.js";

export interface ZelosMcpConfig {
  mcpServers: Record<string, Record<string, unknown>>;
  [key: string]: unknown;
}

function compressValue(mode: Mode): null | { level: string } {
  switch (mode) {
    case "null":
      return null;
    case "medium":
      return { level: "medium" };
    case "max":
      return { level: "max" };
  }
}

export function buildConfig(
  baseConfig: ZelosMcpConfig,
  mode: Mode,
): ZelosMcpConfig {
  const result: ZelosMcpConfig = {
    ...baseConfig,
    mcpServers: {},
  };

  for (const [name, serverDef] of Object.entries(baseConfig.mcpServers)) {
    result.mcpServers[name] = {
      ...serverDef,
      compress: compressValue(mode),
    };
  }

  return result;
}

export async function applyConfig(
  url: string,
  config: ZelosMcpConfig,
): Promise<void> {
  const resp = await fetch(`${url}/api/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`POST /api/start failed (${resp.status}): ${text}`);
  }

  const body = (await resp.json()) as {
    ok: boolean;
    error?: string;
    servers?: Record<string, { ok: boolean; error?: string }>;
  };
  if (!body.ok) {
    const serverErrors = body.servers
      ? Object.entries(body.servers)
          .filter(([, v]) => !v.ok)
          .map(([k, v]) => `${k}: ${v.error ?? "unknown"}`)
          .join("; ")
      : "";
    throw new Error(
      `/api/start returned ok=false: ${body.error ?? (serverErrors || "unknown")}`,
    );
  }
}

const SPEC_INTERNAL_KEYS = new Set(["name", "transport", "reverseProxy"]);

export async function fetchCurrentConfig(
  url: string,
): Promise<ZelosMcpConfig> {
  const resp = await fetch(`${url}/api/status`);
  if (!resp.ok) {
    throw new Error(
      `GET /api/status failed (${resp.status}): ${await resp.text()}`,
    );
  }

  const status = (await resp.json()) as {
    servers: Array<{
      name: string;
      running: boolean;
      builtin: boolean;
      spec: Record<string, unknown>;
    }>;
  };

  const mcpServers: Record<string, Record<string, unknown>> = {};
  for (const srv of status.servers) {
    if (srv.builtin || !srv.running || !srv.spec) continue;

    const cleaned: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(srv.spec)) {
      if (!SPEC_INTERNAL_KEYS.has(k)) {
        cleaned[k] = v;
      }
    }
    mcpServers[srv.name] = cleaned;
  }

  return { mcpServers };
}
