import { encodingForModel } from "js-tiktoken";

let _enc: ReturnType<typeof encodingForModel> | null = null;

function getEncoder() {
  if (!_enc) {
    _enc = encodingForModel("gpt-4o");
  }
  return _enc;
}

export function countTokens(text: string): number {
  return getEncoder().encode(text).length;
}

export function countToolDefTokens(toolDefs: unknown[]): number {
  const serialized = JSON.stringify(toolDefs);
  return countTokens(serialized);
}
