/**
 * Dotenv-style secrets file loader.
 *
 * Supports the standard format:
 *   # comment
 *   KEY=value
 *   KEY="quoted value"
 *   KEY='single quoted'
 *   export KEY=value   (the `export` prefix is stripped)
 *
 * Values loaded from the file are merged into process.env.
 * Existing env vars are NOT overwritten, so a real env var always wins.
 *
 * Default filename: `.bench.env`  (checked in CWD and in the directory
 * that contains the currently-running script).
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_FILENAME = ".bench.env";

/** Parse a dotenv-style string into a key→value map. */
export function parseSecretsFile(content: string): Record<string, string> {
  const result: Record<string, string> = {};

  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();

    // Skip blanks and comments
    if (!line || line.startsWith("#")) continue;

    // Strip optional leading `export `
    const withoutExport = line.replace(/^export\s+/, "");

    const eqIdx = withoutExport.indexOf("=");
    if (eqIdx === -1) continue; // not a KEY=VALUE line

    const key = withoutExport.slice(0, eqIdx).trim();
    if (!key || !/^[A-Z_][A-Z0-9_]*$/i.test(key)) continue; // invalid key

    let value = withoutExport.slice(eqIdx + 1).trim();

    // Strip matching surrounding quotes (double or single)
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    // Inline comment: strip ` # ...` only outside quoted values
    // (already unquoted at this point — safe to strip trailing comment)
    const commentIdx = value.indexOf(" #");
    if (commentIdx !== -1) value = value.slice(0, commentIdx).trim();

    result[key] = value;
  }

  return result;
}

/**
 * Load a secrets file and merge its values into `process.env`.
 * Existing env vars take precedence (no overwrite).
 *
 * Returns the set of keys that were actually written to process.env.
 */
export function applySecretsFile(filePath: string): Set<string> {
  const content = readFileSync(filePath, "utf-8");
  const parsed = parseSecretsFile(content);
  const applied = new Set<string>();

  for (const [key, value] of Object.entries(parsed)) {
    if (process.env[key] === undefined) {
      process.env[key] = value;
      applied.add(key);
    }
  }

  return applied;
}

/**
 * Look for a `.bench.env` file in the following order:
 *   1. `cwd`
 *   2. Directory of the running script (src/)
 *   3. Parent of the running script (benchmarks/)
 *
 * Returns the first existing path, or `null` if none found.
 */
export function findDefaultSecretsFile(): string | null {
  const scriptDir = (() => {
    try {
      return dirname(fileURLToPath(import.meta.url));
    } catch {
      return process.cwd();
    }
  })();

  const candidates = [
    join(process.cwd(), DEFAULT_FILENAME),
    join(scriptDir, DEFAULT_FILENAME),
    join(scriptDir, "..", DEFAULT_FILENAME),
  ];

  for (const p of candidates) {
    if (existsSync(p)) return resolve(p);
  }
  return null;
}

/**
 * Resolve a secrets file path from a CLI flag value.
 * Absolute paths are used as-is; relative paths are resolved from CWD.
 */
export function resolveSecretsPath(flag: string): string {
  return isAbsolute(flag) ? flag : resolve(process.cwd(), flag);
}
