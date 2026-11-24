import type { Comparison } from "../../lib/api";
import { SectionChip, Stamp } from "../ui";

/** How decisions moved between the published version and this one, on postings both labeled. */
export function ImpactCard({ cmp, openCase }: { cmp: Comparison; openCase: (id: string, version: string) => void }) {
  return (
    <div className="card">
      <h4 className="eyebrow">
        Impact vs {cmp.from} on {cmp.compared} postings labeled by both
      </h4>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "6px 0 10px" }}>
        {Object.entries(cmp.transitions)
          .filter(([k]) => k.split("->")[0] !== k.split("->")[1])
          .map(([k, n]) => (
            <span key={k} className="fact on">
              {k.replace("->", " → ")}: {n}
            </span>
          ))}
        {cmp.changed.length === 0 && <span className="muted small">No decision changed.</span>}
      </div>
      <table className="flips">
        <tbody>
          {cmp.changed.slice(0, 12).map((c) => (
            <tr key={c.job_id} style={{ cursor: "pointer" }} onClick={() => openCase(c.job_id, cmp.to)}>
              <td className="mono small">
                <button className="linklike" onClick={(e) => (e.stopPropagation(), openCase(c.job_id, cmp.to))}>
                  #{c.job_id}
                </button>
              </td>
              <td>
                <Stamp label={c.from.label} /> → <Stamp label={c.to.label} />
              </td>
              <td>
                {c.to.sections.map((s) => (
                  <SectionChip key={s} id={s} />
                ))}
              </td>
              <td className="small muted">{c.gold === null ? "" : c.gold ? "gold: fraud" : "gold: legit"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {cmp.changed.length > 12 && <div className="muted small">…and {cmp.changed.length - 12} more</div>}
    </div>
  );
}
