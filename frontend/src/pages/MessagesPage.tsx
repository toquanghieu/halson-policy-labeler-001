import { useCallback, useEffect, useRef, useState } from "react";
import { api, type InboxPage, type InboxScope, type Job, type Posting } from "../lib/api";
import { parseUpload } from "../lib/importFile";
import { Eyebrow } from "../components/ui";
import { StateChip } from "../components/StateChip";
import { useHealth, workerProblem } from "../lib/workers";
import { errorText, useLatest } from "../lib/latest";

const SCOPES: { id: InboxScope; name: string; hint: string }[] = [
  { id: "inbox", name: "Inbox", hint: "the brief's 400 eval postings + everything imported" },
  { id: "imported", name: "Imported", hint: "added through this tab or the API" },
  { id: "eval", name: "Eval set", hint: "the 400 fixed job ids" },
  { id: "all", name: "All dataset rows", hint: "includes the 17k development rows" },
];
const PAGE = 50;

/** Postings waiting for a decision under the published policy. Labeling one sends it to the
 *  live queue; once its decision is stored it leaves this list and appears under Decisions. */
export function MessagesPage({ openDecisions }: { openDecisions: () => void }) {
  const [scope, setScope] = useState<InboxScope>("inbox");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<InboxPage | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [job, setJob] = useState<Job | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [models, setModels] = useState("");
  const health = useHealth(5000);
  const anyQueued = page?.items.some((i) => i.state?.status === "queued" || i.state?.status === "labeling") ?? false;

  const latest = useLatest();
  const load = useCallback(() => {
    latest.run(
      api.inbox({ scope, q: q || undefined, limit: PAGE, offset }),
      (p) => {
        setPage(p);
        setMsg((m) => (m?.kind === "err" ? null : m)); // a successful reload clears a load error
      },
      (e) => setMsg({ kind: "err", text: errorText(e) }),
    );
  }, [scope, q, offset, latest]);
  useEffect(() => {
    const t = setTimeout(load, 200);
    return () => clearTimeout(t);
  }, [load]);
  useEffect(() => setPicked(new Set()), [scope, q]);
  useEffect(() => {
    api.health().then((h) => setModels(h.provider === "fake" ? "fake provider" : `${h.tier1} → ${h.tier2 || "no escalation"}`));
  }, []);

  useEffect(() => {
    if (!anyQueued) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [anyQueued, load]);

  // Follow the labeling job; refresh the list as postings get their decision.
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return;
    const t = setTimeout(async () => {
      const j = await api.job(job.id).catch(() => null);
      if (j) {
        setJob(j);
        load();
      }
    }, 1500);
    return () => clearTimeout(t);
  }, [job, load]);

  const startLabel = async (body: Parameters<typeof api.labelInbox>[0], n: number) => {
    if (!page) return;
    if (!confirm(`Label ${n.toLocaleString()} posting${n === 1 ? "" : "s"} under ${page.policy_version} (${models})?`)) return;
    setMsg(null);
    try {
      const r = await api.labelInbox(body);
      setJob(await api.job(r.job_id));
      setPicked(new Set());
    } catch (e) {
      setMsg({ kind: "err", text: String((e as Error).message ?? e) });
    }
  };

  const toggle = (id: string) =>
    setPicked((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const allOnPage = page?.items.length ? page.items.every((i) => picked.has(i.job_id)) : false;

  return (
    <div className="messages">
      <section className="msg-main">
        <div className="report-head">
          <div>
            <Eyebrow>Waiting for a decision under {page?.policy_version ?? "…"} · {models}</Eyebrow>
            <h2 className="h-serif">Messages</h2>
          </div>
          <div className="btn-row">
            <button className="btn" disabled={picked.size === 0} onClick={() => startLabel({ ids: [...picked] }, picked.size)}>
              Label selected{picked.size ? ` (${picked.size})` : ""}
            </button>
            <button
              className="btn primary"
              disabled={!page?.total}
              onClick={() => startLabel({ all_in_scope: true, scope, q: q || undefined }, Math.min(page!.total, 5000))}
              title="Every unlabeled posting in this view (up to 5,000)"
            >
              Label all {page ? Math.min(page.total, 5000).toLocaleString() : ""} in view
            </button>
          </div>
        </div>

        <div className="scope-row">
          {SCOPES.map((s) => (
            <button
              key={s.id}
              className={`ver-chip${scope === s.id ? " on" : ""}`}
              title={s.hint}
              onClick={() => {
                setScope(s.id);
                setOffset(0);
              }}
            >
              {s.name} <span className="muted">{page ? page.counts[s.id].toLocaleString() : "…"}</span>
            </button>
          ))}
          <input
            className="search"
            type="search"
            aria-label="Search messages by text or job id"
            placeholder="Search text or job id…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
          />
        </div>

        {job && (
          <div className="note-box job-line">
            <span>
              Job #{job.id}: {job.done + job.failed} / {job.total} labeled{job.failed ? ` (${job.failed} failed)` : ""} · {job.status}
            </span>
            <span className="progress">
              <i style={{ width: `${job.total ? ((job.done + job.failed) / job.total) * 100 : 100}%` }} />
            </span>
            {(job.status === "queued" || job.status === "running") && (
              <button
                className="btn danger"
                onClick={async () => {
                  if (!confirm(`Cancel job #${job.id}? Postings not yet labeled stay here.`)) return;
                  await api.cancelJob(job.id).catch(() => undefined);
                  setJob(await api.job(job.id));
                  load();
                }}
              >
                Cancel
              </button>
            )}
            {job.status === "done" && (
              <button className="btn" onClick={openDecisions}>
                See them in Decisions →
              </button>
            )}
          </div>
        )}
        {msg && <div className={msg.kind === "err" ? "error-box" : "note-box"}>{msg.text}</div>}
        {workerProblem(health) && (anyQueued || job) && <div className="warn-box">{workerProblem(health)}</div>}

        {page && page.items.length === 0 ? (
          <div className="empty">Nothing waiting here. Every posting in this view has a decision under {page.policy_version}.</div>
        ) : (
          <table className="flips msg-table">
            <thead>
              <tr>
                <th style={{ width: 28 }}>
                  <input
                    type="checkbox"
                    aria-label="Select page"
                    checked={allOnPage}
                    onChange={() =>
                      setPicked((s) => {
                        const n = new Set(s);
                        page?.items.forEach((i) => (allOnPage ? n.delete(i.job_id) : n.add(i.job_id)));
                        return n;
                      })
                    }
                  />
                </th>
                <th>Posting</th>
                <th>Source</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {page?.items.map((i) => (
                <tr key={i.job_id} className={picked.has(i.job_id) ? "picked" : ""} onClick={() => toggle(i.job_id)}>
                  <td>
                    <input type="checkbox" checked={picked.has(i.job_id)} onChange={() => toggle(i.job_id)} onClick={(e) => e.stopPropagation()} />
                  </td>
                  <td>
                    <div>
                      <strong>{i.title || "(untitled)"}</strong> <span className="mono small muted">#{i.job_id}</span>
                    </div>
                    <div className="small muted">
                      {i.location || "no location"}
                      {i.has_company_profile ? "" : " · no company profile"}
                    </div>
                    {i.snippet && <div className="small snippet">{i.snippet}</div>}
                  </td>
                  <td>
                    <span className="gold-tag">{i.source}</span>
                  </td>
                  <td>
                    <StateChip state={i.state} />
                    {i.state?.status === "failed" && i.state.error && <div className="small" style={{ color: "var(--violating)", maxWidth: 220 }}>{i.state.error}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {page && page.total > PAGE && (
          <div className="pager">
            <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              ← Prev
            </button>
            <span className="muted">
              {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total.toLocaleString()}
            </span>
            <button className="btn" disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>
              Next →
            </button>
          </div>
        )}
      </section>

      <ImportPanel
        onImported={(text) => {
          setMsg({ kind: "ok", text });
          setScope("inbox");
          setOffset(0);
          load();
        }}
      />
    </div>
  );
}

function ImportPanel({ onImported }: { onImported: (summary: string) => void }) {
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState("");
  const [parsed, setParsed] = useState<Partial<Posting>[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!text.trim()) {
      setParsed(null);
      setError(null);
      return;
    }
    try {
      const ps = parseUpload(text, fileName);
      setParsed(ps);
      setError(ps.length ? null : "no postings found");
    } catch (e) {
      setParsed(null);
      setError(`cannot read this: ${(e as Error).message}`);
    }
  }, [text, fileName]);

  const submit = async () => {
    if (!parsed?.length) return;
    setBusy(true);
    setError(null);
    try {
      let stored = 0;
      let fresh = 0;
      for (let i = 0; i < parsed.length; i += 5000) {
        const r = await api.importPostings(parsed.slice(i, i + 5000));
        stored += r.stored;
        fresh += r.new;
      }
      onImported(`Imported ${stored.toLocaleString()} posting${stored === 1 ? "" : "s"} (${fresh.toLocaleString()} new). They wait here until you label them.`);
      setText("");
      setFileName("");
      if (fileRef.current) fileRef.current.value = "";
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="msg-import">
      <Eyebrow>Load postings</Eyebrow>
      <p className="small muted" style={{ margin: 0 }}>
        JSON (one posting, a list, or <span className="mono">{'{"postings": [...]}'}</span>) or a CSV with the dataset's
        column names. Only <span className="mono">job_id</span> matters; without one, an id is generated. Importing stores
        them; nothing is labeled until you say so.
      </p>
      <input
        ref={fileRef}
        type="file"
        accept=".json,.csv,application/json,text/csv"
        onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          setFileName(f.name);
          setText(await f.text());
        }}
      />
      <textarea
        spellCheck={false}
        aria-label="Postings to import, as JSON"
        placeholder={'{"job_id": "new-1", "title": "Data Entry Clerk", "description": "…"}'}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setFileName("");
        }}
      />
      {error && <div className="error-box">{error}</div>}
      {parsed && parsed.length > 0 && (
        <div className="small">
          {parsed.length.toLocaleString()} posting{parsed.length === 1 ? "" : "s"} ready
          {parsed.length <= 3 && `: ${parsed.map((p) => p.job_id).join(", ")}`}
        </div>
      )}
      <button className="btn primary" disabled={!parsed?.length || busy} onClick={submit}>
        {busy ? "Importing…" : `Import${parsed?.length ? ` ${parsed.length.toLocaleString()}` : ""}`}
      </button>
    </aside>
  );
}
