import { existsSync, readdirSync, rmSync, statSync } from "node:fs";
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
      } else if (/\.md$/i.test(entry.name)) {
        results.push(full);
      }
    }
  }

  walk(dir);
  return results.sort();
}

/**
 * Return rule/instruction files for the VS Code + GitHub Copilot IDE.
 *
 * Auto-discovery:
 *   1. `.github/copilot-instructions.md`
 *
 * Override: when `rulesDir` is provided (relative to projectRoot or absolute),
 * that directory is walked for `*.md` files instead.
 */
export function findCopilotRules(projectRoot: string, rulesDir?: string): RulesConfig {
  if (rulesDir) {
    const dir = isAbsolute(rulesDir) ? rulesDir : join(projectRoot, rulesDir);
    if (!existsSync(dir)) return { dir, files: [] };

    // If the path points directly to a file, wrap it
    try {
      const stat = statSync(dir);
      if (stat.isFile()) return { dir: join(dir, ".."), files: [dir] };
    } catch {
      return { dir, files: [] };
    }
    return { dir, files: walkMarkdownFiles(dir) };
  }

  const githubDir = join(projectRoot, ".github");

  const candidates: string[] = [
    join(githubDir, "copilot-instructions.md"),
  ];

  const files = candidates.filter((f) => existsSync(f));

  return {
    dir: githubDir,
    files: [...new Set(files)].sort(),
  };
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
 * Remove pushed Copilot-side zelosMCP instruction files and asset directories
 * from the project's `.github/` directory.
 */
export async function cleanCopilotAssets(
  zelosmcpUrl: string,
  projectRoot: string,
): Promise<void> {
  const githubDir = join(projectRoot, ".github");

  const removals = new Set<string>([
    join(githubDir, "copilot-instructions.md"),
    join(projectRoot, ".vscode", "zelosmcp.json"),
  ]);

  try {
    for (const row of await fetchAssets(zelosmcpUrl, "skill")) {
      removals.add(join(githubDir, "skills", assetSlug(row.name)));
    }
    for (const row of await fetchAssets(zelosmcpUrl, "agent")) {
      removals.add(join(githubDir, "agents", `${assetSlug(row.name)}.agent.md`));
    }
    for (const row of await fetchAssets(zelosmcpUrl, "prompt")) {
      removals.add(join(githubDir, "prompts", `${assetSlug(row.name)}.md`));
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
