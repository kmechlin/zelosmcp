import { existsSync, readdirSync, rmSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import type { RulesConfig } from "../../core/types.js";
import { fetchAssets } from "../../configs.js";

function walkMarkdownFiles(dir: string): string[] {
  const results: string[] = [];

  function walk(d: string): void {
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      const full = join(d, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (/\.(md|mdc)$/i.test(entry.name)) {
        results.push(full);
      }
    }
  }

  walk(dir);
  return results.sort();
}

/**
 * Return rule files for the Cursor IDE.
 *
 * Default: all `.md` / `.mdc` files under `{projectRoot}/.cursor/rules/`.
 * Override: when `rulesDir` is provided (relative to projectRoot or absolute),
 * that directory is walked instead.
 */
export function findCursorRules(projectRoot: string, rulesDir?: string): RulesConfig {
  const dir = rulesDir
    ? isAbsolute(rulesDir)
      ? rulesDir
      : join(projectRoot, rulesDir)
    : join(projectRoot, ".cursor", "rules");

  if (!existsSync(dir)) return { dir, files: [] };
  return { dir, files: walkMarkdownFiles(dir) };
}

function assetSlug(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 64) || "asset"
  );
}

/**
 * Remove pushed Cursor-side zelosMCP rules/skills/agents/prompts from
 * the project's `.cursor/` directory.
 */
export async function cleanCursorAssets(
  zelosmcpUrl: string,
  projectRoot: string,
): Promise<void> {
  const cursorRoot = join(projectRoot, ".cursor");
  const removals = new Set<string>([
    join(cursorRoot, "rules", "zelosmcp.mdc"),
    join(cursorRoot, "zelosmcp.json"),
  ]);

  try {
    for (const row of await fetchAssets(zelosmcpUrl, "skill")) {
      removals.add(join(cursorRoot, "skills", assetSlug(row.name)));
    }
    for (const row of await fetchAssets(zelosmcpUrl, "agent")) {
      removals.add(join(cursorRoot, "agents", `${assetSlug(row.name)}.md`));
    }
    for (const row of await fetchAssets(zelosmcpUrl, "prompt")) {
      removals.add(join(cursorRoot, "commands", `${assetSlug(row.name)}.md`));
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(
      `Asset cleanup metadata fetch failed (continuing with static paths): ${msg}`,
    );
  }

  for (const p of removals) {
    rmSync(p, { recursive: true, force: true });
  }
}
