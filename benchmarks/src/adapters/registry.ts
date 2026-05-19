import type { IdeAdapter } from "../core/adapter.js";
import type { IdeId } from "../core/types.js";

/**
 * Dynamically load and instantiate an IDE adapter by its stable ID.
 *
 * Dynamic imports keep the cursor and copilot SDKs lazily loaded so that
 * the CLI doesn't need both SDKs installed when only one adapter is in use.
 */
export async function loadAdapter(id: IdeId): Promise<IdeAdapter> {
  switch (id) {
    case "cursor": {
      const { CursorAdapter } = await import("./cursor/index.js");
      return new CursorAdapter();
    }
    case "copilot": {
      const { CopilotAdapter } = await import("./copilot/index.js");
      return new CopilotAdapter();
    }
    case "zelos": {
      const { ZelosAdapter } = await import("./zelos/index.js");
      return new ZelosAdapter();
    }
    case "claude":
      throw new Error(`IDE adapter "${id}" is not yet implemented`);
    default: {
      const _exhaustive: never = id;
      throw new Error(`Unknown IDE adapter: ${_exhaustive}`);
    }
  }
}

/** Return all supported adapter IDs in the order they run in "all" mode. */
export function listAdapterIds(): IdeId[] {
  return ["cursor", "copilot", "zelos"];
}
