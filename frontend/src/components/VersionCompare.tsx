import { useCallback, useEffect, useRef, useState } from "react";
import { api, type PolicyRow, type VersionView } from "../lib/api";
import { useHealth, workerProblem } from "../lib/workers";
import { StateChip } from "./StateChip";
import { errorText, useLatest } from "../lib/latest";
import { fmtDateTime } from "../lib/format";
import { Confidence, Eyebrow, SectionChip, Stamp } from "./ui";

/** The compared versions live in the URL (`cmp=v1,v3`), so a refresh or a shared link keeps them. */
function readCmp(): string[] {
  const q = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
  return (q.get("cmp") ?? "").split(",").filter(Boolean);
}
function writeCmp(versions: string[]) {
  const [path, query = ""] = window.location.hash.split("?");
  const q = new URLSearchParams(query);
  if (versions.length) q.set("cmp", versions.join(","));
  else q.delete("cmp");
  const s = q.toString();
  window.history.replaceState(null, "", `${path}${s ? `?${s}` : ""}`);
}

/** One posting under several policy versions, side by side. The version being viewed is always
 *  in; the analyst ticks others. Each column shows where that version stands for this posting
 *  (queued / labeling / labeled / failed, stored server-side), so a refresh never loses a request. */
export function VersionCompare({
  jobId,
  current,
  focused,
  onFocus,
  onCurrentLabeled,
}: {
  jobId: string;
  current: string;
  /** The version whose decision the case's Why card and trail show. */
  focused: string;
  onFocus: (version: string) => void;
  /** The opened version got a (new) decision here, e.g. after "Label under" it: reload the case. */
  onCurrentLabeled?: () => void;
}) {
  const [rows, setRows] = useState<PolicyRow[]>([]);
  const [picked, setPicked] = useState<string[]>(readCmp);
  const [views, setViews] = useState<VersionView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const health = useHealth(5000);
  const stuck = workerProblem(health);

  useEffect(() => {
    api.policies().then(setRows).catch(() => setRows([]));
  }, []);
  useEffect(() => writeCmp(picked), [picked, jobId]); // re-add after the case list rewrites the hash

  const latest = useLatest();
  const versions = [current, ...picked.filter((v) => v !== current)];
  const key = versions.join(",");
  const load = useCallback(() => {
    if (versions.length < 2) {
      latest.cancel(); // the last compared version was unticked: a response still in flight is stale
      setViews([]);
      setError(null);
      return;
    }
    latest.run(
      api.postingVersions(jobId, versions),
      (r) => {
        setViews(r.versions);
        setError(null);
      },
      (e) => setError(errorText(e)),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, key, latest]);
  useEffect(load, [load]);

  // Tell the case when the opened version's decision appears or changes.
  const seen = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    const mine = views.find((v) => v.policy_version === current);
    if (!mine) return;
    const stamp = mine.result ? (mine.labeled_at ?? "labeled") : null;
    if (seen.current !== undefined && stamp && stamp !== seen.current) onCurrentLabeled?.();
    seen.current = stamp;
  }, [views, current, onCurrentLabeled]);
  useEffect(() => {
    seen.current = undefined;
  }, [jobId, current]);

  // While anything is queued or being labeled, poll until it settles.
  const inFlight = views.some((v) => !v.result && (v.state?.status === "queued" || v.state?.status === "labeling"));
  useEffect(() => {
    if (!inFlight) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [inFlight, load]);

  const toggle = (v: string) => {
    if (picked.includes(v) && v === focused) onFocus(current); // its column goes away: show the opened version again
    setPicked((p) => (p.includes(v) ? p.filter((x) => x !== v) : [...p, v]));
  };

  const labelHere = async (v: string) => {
    setError(null);
    try {
      await api.relabel(v, "ids", { ids: [jobId] });
      load();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  const others = rows.filter((r) => r.version !== current);
  if (others.length === 0) return null;

  return (
    <div className="card compare">
      <div className="compare-bar">
        <Eyebrow>Compare {current} with</Eyebrow>
        {others.map((r) => (
          <button
            key={r.version}
            className={`ver-chip${picked.includes(r.version) ? " on" : ""}`}
            onClick={() => toggle(r.version)}
            aria-pressed={picked.includes(r.version)}
          >
            {r.version} <span className={`status ${r.status}`}>{r.status}</span>
          </button>
        ))}
      </div>
      {error && <div className="error-box" style={{ marginTop: 8 }}>{error}</div>}
      {views.length > 1 && (
        <div className="compare-grid" style={{ gridTemplateColumns: `repeat(${views.length}, minmax(220px, 1fr))` }}>
          {views.map((v) => {
            const r = v.result;
            const differs = r && views[0].result && (r.label !== views[0].result.label || r.policy_sections.join() !== views[0].result.policy_sections.join());
            const waiting = !r && (v.state?.status === "queued" || v.state?.status === "labeling");
            return (
              <div
                key={v.policy_version}
                className={`compare-col${differs ? " differs" : ""}${r && v.policy_version === focused ? " focused" : ""}`}
              >
                <div className="compare-head">
                  <span className="mono">{v.policy_version}</span> <span className={`status ${v.status}`}>{v.status}</span>
                  <StateChip state={v.state} />
                  {differs && <span className="small" style={{ color: "var(--review)" }}>differs</span>}
                  {r &&
                    (v.policy_version === focused ? (
                      <span className="focus-note">details below</span>
                    ) : (
                      <button className="btn small-btn" onClick={() => onFocus(v.policy_version)}>
                        show details
                      </button>
                    ))}
                </div>
                {r ? (
                  <>
                    <div className="btn-row">
                      <Stamp label={r.label} />
                      <Confidence value={r.confidence} />
                    </div>
                    <div className="btn-row" style={{ minHeight: 20 }}>
                      {r.policy_sections.length ? r.policy_sections.map((s) => <SectionChip key={s} id={s} />) : <span className="muted small">no section</span>}
                    </div>
                    <p className="small" style={{ margin: 0 }}>{r.rationale}</p>
                    {r.evidence.length > 0 && (
                      <ul className="compare-quotes">
                        {r.evidence.map((q, i) => (
                          <li key={i}>“{q}”</li>
                        ))}
                      </ul>
                    )}
                    <div className="muted small mono">
                      {r.trace.tier} · {r.trace.steps.map((s) => s.model).filter(Boolean).join(" → ")}
                    </div>
                    {v.labeled_at && (
                      <div className="muted small mono" title={`labeled ${v.labeled_at} (UTC)`}>
                        labeled {fmtDateTime(v.labeled_at)}
                      </div>
                    )}
                    {v.state?.status === "failed" && v.state.error && <div className="small" style={{ color: "var(--violating)" }}>{v.state.error}</div>}
                  </>
                ) : waiting ? (
                  <div className="small" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    <span>
                      {v.state?.status === "labeling" ? "A worker is labeling it now." : `Queued in job #${v.state?.run_id}. It will appear here when labeled.`}
                    </span>
                    {stuck && <span className="warn-box">{stuck}</span>}
                  </div>
                ) : (
                  <div className="small muted" style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-start" }}>
                    {v.state?.status === "failed" ? (
                      <span style={{ color: "var(--violating)" }}>Last attempt failed: {v.state.error || "unknown error"}</span>
                    ) : (
                      <span>Not labeled under {v.policy_version}.</span>
                    )}
                    <button className="btn" onClick={() => labelHere(v.policy_version)}>
                      {v.state?.status === "failed" ? "Retry" : `Label under ${v.policy_version}`}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
