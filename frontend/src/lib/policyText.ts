import type { PolicyRow } from "./api";

/** The next free vN after the existing versions. */
export function nextVersion(rows: PolicyRow[]): string {
  const nums = rows.map((r) => Number(/^v(\d+)$/.exec(r.version)?.[1] ?? 0));
  return `v${Math.max(0, ...nums) + 1}`;
}

/** The document with its `version:` line set to `v`. */
export function withVersion(source: string, v: string): string {
  return /^version:.*$/m.test(source) ? source.replace(/^version:.*$/m, `version: ${v}`) : `version: ${v}\n${source}`;
}
