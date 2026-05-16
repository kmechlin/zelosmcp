import type { Mode, ResponseFormat } from "./types.js";

export interface ZelosMcpConfig {
  mcpServers: Record<string, Record<string, unknown>>;
  /**
   * Top-level builtin config (parsed by `parse_config` into BuiltinConfig).
   * Preserved through the bench's fetch → buildConfig → applyConfig
   * round-trip so the bench doesn't silently reset user customizations
   * (response_format / strip_meta / compress on the builtin backend).
   */
  builtin?: Record<string, unknown>;
  [key: string]: unknown;
}

/**
 * Compression-mode overrides applied to every server in `buildConfig`.
 * `pinResponseFormat` / `pinStripMeta` let the bench isolate the
 * compression-only token-delta signal by holding the orthogonal
 * shrinking layers (TOON transform, _meta stripping) constant across
 * all modes. Both default to "leave the server's existing value alone".
 */
export interface BuildConfigOverrides {
  pinResponseFormat?: ResponseFormat;
  pinStripMeta?: boolean;
}

function compressValue(mode: Mode): null | { level: string } {
  switch (mode) {
    case "null":
      return null;
    case "low":
      return { level: "low" };
    case "medium":
      return { level: "medium" };
    case "high":
      return { level: "high" };
    case "max":
      return { level: "max" };
  }
}

export function buildConfig(
  baseConfig: ZelosMcpConfig,
  mode: Mode,
  overrides: BuildConfigOverrides = {},
): ZelosMcpConfig {
  const result: ZelosMcpConfig = {
    ...baseConfig,
    mcpServers: {},
  };

  for (const [name, serverDef] of Object.entries(baseConfig.mcpServers)) {
    const next: Record<string, unknown> = {
      ...serverDef,
      compress: compressValue(mode),
    };
    if (overrides.pinResponseFormat !== undefined) {
      next.response_format = overrides.pinResponseFormat;
    }
    if (overrides.pinStripMeta !== undefined) {
      next.strip_meta = overrides.pinStripMeta;
    }
    result.mcpServers[name] = next;
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

export interface PushAssetsOpts {
  repo: string;
  kinds?: readonly string[];
  targets?: readonly string[];
  access?: "read-only" | "read-write";
  toolUse?: "available" | "priority";
}

export async function pushAssets(
  url: string,
  opts: PushAssetsOpts,
): Promise<void> {
  const kinds = opts.kinds ?? ["rule", "agent", "skill", "prompt"];
  for (const kind of kinds) {
    const resp = await fetch(`${url}/api/assets/push/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repo: opts.repo,
        targets: opts.targets ?? ["cursor"],
        access: opts.access ?? "read-write",
        tool_use: opts.toolUse ?? "priority",
      }),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`POST /api/assets/push/${kind} failed (${resp.status}): ${text}`);
    }
    const body = (await resp.json()) as { ok?: boolean; error?: string };
    if (body.ok === false) {
      throw new Error(`/api/assets/push/${kind} returned ok=false: ${body.error ?? "unknown"}`);
    }
  }
}

export interface AssetSummaryRow {
  kind: string;
  backend: string;
  name: string;
  meta?: Record<string, unknown>;
}

export async function fetchAssets(
  url: string,
  kind: string,
): Promise<AssetSummaryRow[]> {
  const resp = await fetch(`${url}/api/assets?kind=${encodeURIComponent(kind)}`);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`GET /api/assets?kind=${kind} failed (${resp.status}): ${text}`);
  }
  const body = await resp.json();
  return Array.isArray(body) ? (body as AssetSummaryRow[]) : [];
}

const SPEC_INTERNAL_KEYS = new Set(["name", "transport", "reverseProxy"]);

/**
 * Detect the compression mode currently applied to `config`.
 * Returns null if servers disagree or no server carries a `compress` field
 * (mixed/unknown state — safest to re-apply explicitly).
 */
export function detectMode(config: ZelosMcpConfig): Mode | null {
  const seen = new Set<Mode>();
  for (const serverDef of Object.values(config.mcpServers)) {
    const compress = (serverDef as { compress?: unknown }).compress;
    if (compress === null) {
      seen.add("null");
    } else if (compress && typeof compress === "object" && "level" in compress) {
      const level = (compress as { level?: unknown }).level;
      if (level === "low" || level === "medium" || level === "high" || level === "max") {
        seen.add(level);
      } else {
        return null;
      }
    } else if (compress === undefined) {
      // Server has no compress field — treat as unknown, force re-apply.
      return null;
    } else {
      return null;
    }
  }
  if (seen.size !== 1) return null;
  return seen.values().next().value!;
}

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
    builtin?: Record<string, unknown>;
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

  const result: ZelosMcpConfig = { mcpServers };
  // Preserve active BuiltinConfig so applyConfig doesn't silently reset
  // it on every round-trip. Only round-trip when non-empty — sending
  // `{builtin: {}}` would set the builtin config to all-defaults, which
  // is semantically the same as omitting the key.
  if (status.builtin && Object.keys(status.builtin).length > 0) {
    result.builtin = status.builtin;
  }
  return result;
}
