import { execSync } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  cacheWriteTokens: number;
  cacheReadTokens: number;
  totalTokens: number;
  costCents: number;
  isHeadless: boolean;
  earliestEventTimestamp: string;
}

function getDbPath(): string {
  const home = homedir();
  if (process.platform === "win32") {
    return join(home, "AppData", "Roaming", "Cursor", "User", "globalStorage", "state.vscdb");
  } else if (process.platform === "darwin") {
    return join(home, "Library", "Application Support", "Cursor", "User", "globalStorage", "state.vscdb");
  } else {
    return join(home, ".config", "Cursor", "User", "globalStorage", "state.vscdb");
  }
}

function readTokenFromSqlite(): string | null {
  try {
    const dbPath = getDbPath();
    const result = execSync(
      `sqlite3 "${dbPath}" "SELECT value FROM ItemTable WHERE key = 'cursorAuth/accessToken';"`,
      { encoding: "utf-8", timeout: 5000, stdio: ["pipe", "pipe", "pipe"] },
    ).trim();
    return result || null;
  } catch {
    return null;
  }
}

function extractUserId(token: string): string | null {
  try {
    if (token.includes("::")) {
      return token.split("::")[0];
    }
    const parts = token.split(".");
    if (parts.length === 3) {
      let base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      while (base64.length % 4) base64 += "=";
      const payload = JSON.parse(Buffer.from(base64, "base64").toString()) as { sub?: string };
      if (payload.sub) {
        const match = payload.sub.match(/user_[A-Za-z0-9]+/);
        if (match) return match[0];
      }
    }
    return null;
  } catch {
    return null;
  }
}

function buildCookie(token: string, userId: string): string {
  if (token.includes("::")) {
    return token.replace("::", "%3A%3A");
  }
  return `${userId}%3A%3A${token}`;
}

export function getSessionCookie(): string | null {
  const jwt = process.env.CURSOR_SESSION_TOKEN ?? readTokenFromSqlite();
  if (!jwt) return null;

  const userId = extractUserId(jwt);
  if (!userId) return null;

  return buildCookie(jwt, userId);
}

interface UsageEvent {
  timestamp?: string;
  isHeadless?: boolean;
  tokenUsage?: {
    inputTokens?: number;
    outputTokens?: number;
    cacheWriteTokens?: number;
    cacheReadTokens?: number;
    totalCents?: number;
  };
}

interface UsageEventsResponse {
  usageEventsDisplay?: UsageEvent[];
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function queryUsageEvents(
  startMs: number,
  endMs: number,
  cookie: string,
): Promise<UsageEvent[]> {
  const response = await fetch(
    "https://cursor.com/api/dashboard/get-filtered-usage-events",
    {
      method: "POST",
      headers: {
        Cookie: `WorkosCursorSessionToken=${cookie}`,
        "Content-Type": "application/json",
        Accept: "application/json",
        Origin: "https://cursor.com",
        Referer: "https://cursor.com/dashboard",
      },
      body: JSON.stringify({
        teamId: 0,
        startDate: startMs.toString(),
        endDate: endMs.toString(),
        page: 1,
        pageSize: 50,
      }),
      signal: AbortSignal.timeout(10000),
    },
  );

  if (!response.ok) return [];
  const data = (await response.json()) as UsageEventsResponse;
  return data.usageEventsDisplay ?? [];
}

function sumEvents(events: UsageEvent[]): TokenUsage | null {
  if (events.length === 0) return null;

  let inputTokens = 0;
  let outputTokens = 0;
  let cacheWriteTokens = 0;
  let cacheReadTokens = 0;
  let costCents = 0;
  let allHeadless = true;
  let earliestTs = Infinity;

  for (const event of events) {
    inputTokens += event.tokenUsage?.inputTokens ?? 0;
    outputTokens += event.tokenUsage?.outputTokens ?? 0;
    cacheWriteTokens += event.tokenUsage?.cacheWriteTokens ?? 0;
    cacheReadTokens += event.tokenUsage?.cacheReadTokens ?? 0;
    costCents += event.tokenUsage?.totalCents ?? 0;
    if (!event.isHeadless) allHeadless = false;
    const ts = parseInt(event.timestamp ?? "0", 10);
    if (ts > 0 && ts < earliestTs) earliestTs = ts;
  }

  return {
    inputTokens,
    outputTokens,
    cacheWriteTokens,
    cacheReadTokens,
    totalTokens: inputTokens + outputTokens + cacheWriteTokens + cacheReadTokens,
    costCents,
    isHeadless: allHeadless,
    earliestEventTimestamp: earliestTs === Infinity ? "" : new Date(earliestTs).toISOString(),
  };
}

/**
 * Fetches token usage for a run, polling until the event count stabilizes.
 * Events propagate to the Cursor usage API with a delay after the run completes,
 * so we poll up to maxPolls times with pollIntervalMs between each attempt.
 */
export async function fetchTokenUsage(
  startMs: number,
  endMs: number,
  cookie: string,
  { pollIntervalMs = 3000, maxPolls = 3 }: { pollIntervalMs?: number; maxPolls?: number } = {},
): Promise<TokenUsage | null> {
  try {
    let lastCount = -1;
    let lastEvents: UsageEvent[] = [];

    for (let poll = 0; poll < maxPolls; poll++) {
      await delay(pollIntervalMs);
      const events = await queryUsageEvents(startMs, endMs, cookie);
      if (events.length > lastCount) {
        lastCount = events.length;
        lastEvents = events;
        // More events may still be arriving — keep polling
      } else {
        // Count stabilized
        break;
      }
    }

    return sumEvents(lastEvents);
  } catch {
    return null;
  }
}
