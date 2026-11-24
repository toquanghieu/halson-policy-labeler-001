import { useEffect, useState } from "react";
import { api, type LabelValue, type Metrics, type PolicyRow } from "../lib/api";
import { Eyebrow, SectionChip, Stamp } from "../components/ui";
import { fmtMs, fmtUsd } from "../lib/format";
import { errorText, useLatest } from "../lib/latest";

// Validated with the dataviz palette checker (light surface): both pass CVD and contrast.
const FRAUD = "#b0365f";
const LEGIT = "#2563b8";
const ROWS: LabelValue[] = ["VIOLATING", "NEEDS_REVIEW", "ALLOWED"];

export function EvalPage({ initial, openQueue }: { initial?: string; openQueue: (version: string) => void }) {
  const [versions, setVersions] = useState<PolicyRow[]>([]);
  // Named eval runs without a policy row of their own (v1@current: v1 under the submitted code).
  const [runs, setRuns] = useState<{ version: string; eval_decisions: number }[]>([]);
  const [version, setVersion] = useState<string | undefined>();
  const [m, setM] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.policies(), api.metricRuns().catch(() => [])]).then(([rows, rs]) => {
      setVersions(rows);
      const extra = rs.filter((r) => !rows.some((p) => p.version === r.version));
      setRuns(extra);
      const known = (v: string) => rows.some((r) => r.version === v) || extra.some((r) => r.version === v);
      const wanted = initial && known(initial) ? initial : undefined;
      setVersion(wanted ?? (rows.find((r) => r.status === "published") ?? rows[0])?.version);
    });
  }, [initial]);

  const latest = useLatest();
  useEffect(() => {
    if (!version) return;
    setM(null);
    setError(null);
    latest.run(api.metrics(version), setM, (e) => setError(errorText(e)));
  }, [version, latest]);

  const maxCell = m ? Math.max(1, ...ROWS.flatMap((r) => [m.confusion[r].fraud, m.confusion[r].legit])) : 1;

  return (
    <div className="report">
      <div className="report-head">
        <div>
          <Eyebrow>Fixed evaluation set · 400 postings · 100 fraudulent in EMSCAD</Eyebrow>
          <h2 className="h-serif" style={{ fontSize: 28 }}>
            How the labeler does against the dataset
          </h2>
        </div>
        <div className="btn-row">
          <label className="small muted" htmlFor="ver">
            Policy / run
          </label>
          <select id="ver" value={version} onChange={(e) => setVersion(e.target.value)} className="btn">
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                {v.version} · {v.status}
              </option>
            ))}
            {runs.map((r) => (
              <option key={r.version} value={r.version}>
                {r.version} · eval run ({r.eval_decisions})
              </option>
            ))}
          </select>
          {version && (
            <button className="btn" onClick={() => openQueue(version)}>
              Browse these decisions →
            </button>
          )}
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}
      {m && m.n === 0 && (
        <div className="note-box">
          No eval-set labels under {version} yet. Run <code>make eval</code>, or shadow-run this version from the Policy page.
        </div>
      )}

      {m && m.n > 0 && (
        <>
          <div className="note-box">
            The dataset label is binary, the policy is not. <strong>NEEDS_REVIEW</strong> is counted explicitly and scored two ways:{" "}
            <em>strict</em> treats only VIOLATING as a fraud call (what the system blocks by itself); <em>flagged</em> counts VIOLATING or
            NEEDS_REVIEW (what is blocked or reaches an analyst). Accuracy is not shown: at 4.8% prevalence it says nothing.
          </div>

          <div className="tiles">
            <Tile
              label="Flagged recall"
              value={m.flagged.recall.toFixed(2)}
              sub={`${m.flagged.tp} of ${m.flagged.tp + m.flagged.fn} fraud postings blocked or sent to review`}
            />
            <Tile
              label="Strict precision"
              value={m.strict.precision.toFixed(2)}
              sub={`${m.strict.tp} of ${m.strict.tp + m.strict.fp} VIOLATING calls are fraud in gold`}
            />
            <Tile label="Review rate" value={`${Math.round(m.review_rate * 100)}%`} sub="share sent to an analyst" />
            <Tile
              label="Cost / 1,000"
              value={fmtUsd(m.cost_per_1k_usd)}
              sub={
                // cost_unpriced: decisions by a model with no list price (OpenAI, a compatible endpoint), counted as $0
                m.cost_unpriced
                  ? `at least: ${m.cost_unpriced} postings used an unpriced model ($0)`
                  : m.cost_basis?.includes("claude_cli")
                    ? "Claude CLI token counts × list price: overstates API cost (RESULTS.md §6 has the API estimate)"
                    : `${fmtUsd(m.total_cost_usd)} for ${m.n} postings`
              }
            />
            <Tile
              label="Latency p50 / p95"
              value={`${fmtMs(m.latency_ms.p50)}`}
              sub={`p95 ${fmtMs(m.latency_ms.p95)} · max ${fmtMs(m.latency_ms.max)}`}
            />
            <Tile
              label="Escalated"
              value={`${Math.round(m.escalation_rate * 100)}%`}
              sub={Object.entries(m.tiers)
                .map(([k, v]) => `${k} ${v}`)
                .join(" · ")}
            />
          </div>

          <div className="two">
            <div className="card">
              <h4 className="eyebrow">Confusion matrix · predicted label × dataset label</h4>
              <table className="cm">
                <thead>
                  <tr>
                    <th className="rowh">Predicted</th>
                    <th>Gold: fraud</th>
                    <th>Gold: legit</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {ROWS.map((r) => (
                    <tr key={r}>
                      <th className="rowh">
                        <Stamp label={r} />
                      </th>
                      {(["fraud", "legit"] as const).map((g) => (
                        <td key={g} className="cell" title={`${m.confusion[r][g]} postings predicted ${r}, gold ${g}`}>
                          <i style={{ background: g === "fraud" ? FRAUD : LEGIT, opacity: 0.05 + 0.35 * (m.confusion[r][g] / maxCell) }} />
                          <span>{m.confusion[r][g]}</span>
                        </td>
                      ))}
                      <td className="cell">
                        <span>{m.confusion[r].fraud + m.confusion[r].legit}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="card">
              <h4 className="eyebrow">Fraudulent class · precision, recall, F1</h4>
              <table className="prf">
                <thead>
                  <tr>
                    <th>Scoring</th>
                    <th>Precision</th>
                    <th>Recall</th>
                    <th>F1</th>
                    <th>TP</th>
                    <th>FP</th>
                    <th>FN</th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    [
                      ["Strict (VIOLATING only)", m.strict],
                      ["Flagged (VIOLATING + REVIEW)", m.flagged],
                    ] as const
                  ).map(([n, s]) => (
                    <tr key={n}>
                      <td>{n}</td>
                      <td>{s.precision.toFixed(3)}</td>
                      <td>{s.recall.toFixed(3)}</td>
                      <td>
                        <strong>{s.f1.toFixed(3)}</strong>
                      </td>
                      <td className="mono">{s.tp}</td>
                      <td className="mono">{s.fp}</td>
                      <td className="mono">{s.fn}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
              <h4 className="eyebrow">Which sections fire, and on what</h4>
              <div className="legend">
                <span style={{ ["--c" as string]: FRAUD }}>gold fraud</span>
                <span style={{ ["--c" as string]: LEGIT }}>gold legit</span>
              </div>
            </div>
            <div className="bars" style={{ marginTop: 10 }}>
              {Object.keys(m.sections).length === 0 && <span className="muted small">No section matched in this run.</span>}
              {Object.entries(m.sections)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([sid, c]) => {
                  const maxN = Math.max(...Object.values(m.sections).map((x) => x.fraud + x.legit));
                  return (
                    <div className="bar-row" key={sid}>
                      <SectionChip id={sid} withName={false} />
                      <div className="bar-track" style={{ width: `${((c.fraud + c.legit) / maxN) * 100}%`, gap: 2 }}>
                        <i
                          style={{
                            width: `${(c.fraud / Math.max(1, c.fraud + c.legit)) * 100}%`,
                            background: FRAUD,
                            borderRadius: "2px 0 0 2px",
                          }}
                          title={`${c.fraud} gold fraud`}
                        />
                        <i style={{ flex: 1, background: LEGIT, borderRadius: "0 2px 2px 0" }} title={`${c.legit} gold legit`} />
                      </div>
                      <span className="mono small">
                        {c.fraud} / {c.legit}
                      </span>
                    </div>
                  );
                })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="tile">
      <Eyebrow>{label}</Eyebrow>
      <div className="num">{value}</div>
      <div className="sub">{sub}</div>
    </div>
  );
}
