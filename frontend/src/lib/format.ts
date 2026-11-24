import type { CSSProperties } from "react";
import type { LabelValue } from "./api";

// Each section gets a hue by its position in the policy, so a new section added
// in a policy version is colored automatically, with no UI change.
const PALETTE: [string, string][] = [
  ["#9c2f55", "#f6dde6"],
  ["#5b3d99", "#e7e0f4"],
  ["#11706b", "#d7ecea"],
  ["#b2551a", "#f7e3d3"],
  ["#2f5b8f", "#dbe6f3"],
  ["#5f6b14", "#e7ebcf"],
  ["#7a4a2a", "#efe2d6"],
  ["#2f6f3a", "#dcebd9"],
];

export function sectionStyle(index: number): CSSProperties {
  const [fg, bg] = PALETTE[((index % PALETTE.length) + PALETTE.length) % PALETTE.length];
  return { ["--sec-fg" as string]: fg, ["--sec-bg" as string]: bg } as CSSProperties;
}

export const LABEL_TEXT: Record<LabelValue, string> = {
  VIOLATING: "Violating",
  NEEDS_REVIEW: "Needs review",
  ALLOWED: "Allowed",
};

export const FACT_TEXT: Record<string, string> = {
  no_company_profile: "no company profile",
  no_company_logo: "no logo",
  no_salary: "no salary",
  no_screening_questions: "no screening questions",
};

/** Matches ESCALATE_BELOW_CONFIDENCE's default: below this the agent escalates. */
export const LOW_CONFIDENCE = 0.6;

export const fmtUsd = (v: number) => (v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);
export const fmtMs = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);

/** "24 Nov 2026, 11:20" in the viewer's time zone; the full ISO timestamp goes in a tooltip. */
export const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
