import { useCallback, useEffect, useState } from "react";
import { api, type WorkState, type WorkView } from "../lib/api";
import { workerProblem } from "../lib/workers";
import { Eyebrow } from "../components/ui";
import { StateChip } from "../components/StateChip";
import { errorText, useLatest } from "../lib/latest";

const PAGE = 50;
const STATES: { key: WorkState | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "labeling", label: "Labeling now" },
  { key: "queued", label: "Queued" },
  { key: "failed", label: "Failed" },
];

function ago(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** What the system is doing now: postings queued, being labeled, or failed, per policy version,
 *  next to the broker's queue depth, the workers and the open jobs. Refreshes every 3 s. */
export function WorkPage({ openCase }: { openCase: (id: string, version: string) => void }) {
  const [state, setState] = useState<WorkState | "all">("all");
  const [version, setVersion] = useState("");
  const [offset, setOffset] = useState(0);
  const [view, setView] = useState<WorkView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const latest = useLatest();
  const load = useCallback(() => {
    latest.run(
      api.work({ status: state === "all" ? undefined : state, version: version || undefined, limit: PAGE, offset }),
      (v) => {
        setView(v);
        setErr(null);
      },
      (e) => setErr(errorText(e)),
    );
  }, [state, version, offset, latest]);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  const totals = { queued: 0, labeling: 0, failed: 0 };
  for (const c of Object.values(view?.counts ?? {})) {
    totals.queued += c.queued ?? 0;
    totals.labeling += c.labeling ?? 0;
    totals.failed += c.failed ?? 0;
  }
  const versions = Object.keys(view?.counts ?? {}).sort();
  // Every queue the broker reports (sync, live, backfill).
  const queues = Object.values(view?.queues ?? {});
  const brokerWaiting = queues.reduce((n, q) => n + (q?.visible ?? 0), 0);
  const brokerInFlight = queues.reduce((n, q) => n + (q?.in_flight ?? 0), 0);
  const problem = workerProblem(view);
  const running = view?.workers.filter((w) => w.status !== "waiting").length ?? 0;

  const act = async (fn: () => Promise<string>) => {
    try {
      setNote(await fn());
      load();
    } catch (e) {
      setErr(String((e as Error).message ?? e));
    }
  };

  return (
    <div className="settings work">
      <div className="report-head">
        <div>
          <Eyebrow>Work in progress</Eyebrow>
          <h2 className="h-serif">Work</h2>
          <div className="small muted">Queued, being labeled, or failed, per policy version. Labeled postings move to Decisions. Refreshes every 3 s.</div>
        </div>
      </div>

      {err && <div className="error-box">{err}</div>}
      {note && <div className="note-box">{note}</div>}
      {problem && (totals.queued > 0 || totals.labeling > 0) && <div className="warn-box">{problem}</div>}

      <div className="work-tiles">
        <Tile label="Labeling now" value={totals.labeling} sub="a worker has taken it" />
        <Tile label="Queued" value={totals.queued} sub="waiting for a worker" />
        <Tile label="Failed" value={totals.failed} sub="labeled NEEDS_REVIEW, or dead-lettered" />
        <Tile label="Broker" value={brokerWaiting} sub={`messages waiting · ${brokerInFlight} in flight`} />
        <Tile
          label="Workers"
          value={view?.workers.length ?? 0}
          sub={view && view.workers.length ? `${running} taking messages · ${view.workers.length - running} waiting` : "none running"}
        />
      </div>
      {view && totals.queued > 0 && brokerWaiting + brokerInFlight === 0 && (
        <div className="warn-box">
          The database says {totals.queued} queued, but the broker holds no messages (a local ElasticMQ loses them on restart). Use
          <em> Re-send</em> on the job below to put them back.
        </div>
      )}

      {view && view.jobs.length > 0 && (
        <section className="card">
          <Eyebrow>Open jobs</Eyebrow>
          <table className="prf work-jobs">
            <thead>
              <tr>
                <th>Job</th>
                <th>Kind</th>
                <th>Version</th>
                <th>Progress</th>
                <th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {view.jobs.map((j) => (
                <tr key={j.id}>
                  <td className="mono">#{j.id}</td>
                  <td>
                    {j.kind}
                    {j.detail ? <span className="small muted"> · {j.detail}</span> : null}
                  </td>
                  <td className="mono">{j.policy_version}</td>
                  <td className="mono">
                    {j.done + j.failed}/{j.total}
                    {j.failed ? <span style={{ color: "var(--violating)" }}> · {j.failed} failed</span> : null}
                  </td>
                  <td className="small muted">{ago(j.created_at)}</td>
                  <td>
                    <div className="btn-row" style={{ justifyContent: "flex-end" }}>
                      <button className="btn" title="Send the messages of its pending postings again" onClick={() => act(async () => `Job #${j.id}: ${(await api.requeueJob(j.id)).resent} message(s) re-sent.`)}>
                        Re-send
                      </button>
                      <button
                        className="btn"
                        onClick={() => {
                          const left = j.total - j.done - j.failed;
                          if (!confirm(`Cancel job #${j.id}? ${left.toLocaleString()} posting(s) not yet labeled will be skipped; labels already made stay.`)) return;
                          act(async () => `Job #${j.id} cancelled: ${(await api.cancelJob(j.id)).cancelled_items} posting(s) taken off the queue.`);
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <div className="btn-row work-filters">
        {STATES.map((s) => (
          <button
            key={s.key}
            className={`btn${state === s.key ? " primary" : ""}`}
            onClick={() => {
              setState(s.key);
              setOffset(0);
            }}
          >
            {s.label}
            {s.key !== "all" ? ` (${totals[s.key]})` : ""}
          </button>
        ))}
        <select
          className="text"
          value={version}
          onChange={(e) => {
            setVersion(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">all versions</option>
          {versions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </div>

      {view && view.items.length === 0 ? (
        <div className="empty">Nothing {state === "all" ? "queued, labeling or failed" : state} {version ? `under ${version}` : ""}.</div>
      ) : (
        <table className="flips msg-table">
          <thead>
            <tr>
              <th>Posting</th>
              <th>Version</th>
              <th>Status</th>
              <th>Since</th>
            </tr>
          </thead>
          <tbody>
            {view?.items.map((i) => (
              <tr key={`${i.posting_id}|${i.policy_version}`} onClick={() => openCase(i.posting_id, i.policy_version)} title="Open the posting">
                <td>
                  <div>
                    <button className="linklike" onClick={(e) => (e.stopPropagation(), openCase(i.posting_id, i.policy_version))}>
                      <strong>{i.title || "(untitled)"}</strong>
                    </button>{" "}
                    <span className="mono small muted">#{i.posting_id}</span>
                  </div>
                  <div className="small muted">{i.location || "no location"}</div>
                </td>
                <td className="mono">{i.policy_version}</td>
                <td>
                  <StateChip state={i} />
                  {i.status === "failed" && i.error && <div className="small" style={{ color: "var(--violating)", maxWidth: 320 }}>{i.error}</div>}
                </td>
                <td className="small muted" title={i.updated_at}>
                  {ago(i.updated_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {view && view.total > PAGE && (
        <div className="pager">
          <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            ← Prev
          </button>
          <span className="muted">
            {offset + 1}–{Math.min(offset + PAGE, view.total)} of {view.total.toLocaleString()}
          </span>
          <button className="btn" disabled={offset + PAGE >= view.total} onClick={() => setOffset(offset + PAGE)}>
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: number; sub: string }) {
  return (
    <div className="tile">
      <Eyebrow>{label}</Eyebrow>
      <div className="work-num">{value.toLocaleString()}</div>
      <div className="sub">{sub}</div>
    </div>
  );
}
