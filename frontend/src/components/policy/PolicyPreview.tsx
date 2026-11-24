import type { PolicyDoc } from "../../lib/api";
import { sectionStyle } from "../../lib/format";
import { Eyebrow, SectionChip } from "../ui";

/** The parsed policy as the agent reads it, with sections added or changed vs the published one. */
export function PolicyPreview({
  parsed,
  diff,
  removed,
}: {
  parsed: PolicyDoc;
  diff: Record<string, "added" | "changed" | undefined>;
  removed: PolicyDoc["sections"];
}) {
  return (
    <>
      <div>
        <Eyebrow>What the agent reads · {parsed.version}</Eyebrow>
        <p className="small" style={{ color: "var(--ink-2)", margin: "4px 0 0" }}>
          {parsed.summary}
        </p>
      </div>
      {parsed.sections.map((s, i) => (
        <div
          key={s.id}
          className={`sec-card${diff[s.id] === "added" ? " diff-added" : diff[s.id] === "changed" ? " diff-changed" : ""}`}
          style={sectionStyle(i)}
        >
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
            <span className="name">
              <SectionChip id={s.id} /> {s.name}
            </span>
            <span className="effect" style={{ color: s.effect === "violating" ? "var(--violating)" : "var(--review)" }}>
              {s.effect === "violating" ? "→ violating" : "→ review only"}
              {diff[s.id] ? ` · ${diff[s.id]}` : ""}
            </span>
          </div>
          <p>{s.text}</p>
        </div>
      ))}
      {removed.map((s) => (
        <div key={s.id} className="note-box">
          Removed vs published: {s.id} {s.name}
        </div>
      ))}
      {parsed.edge_cases.length > 0 && (
        <div className="card">
          <h4 className="eyebrow">Edge cases</h4>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {parsed.edge_cases.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
