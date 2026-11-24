import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Comparison, type Job, type PolicyDoc, type PolicyRow, type RuntimeSettings } from "../lib/api";
import { Eyebrow } from "../components/ui";
import { errorText, useLatest } from "../lib/latest";
import { nextVersion, withVersion } from "../lib/policyText";
import { ImpactCard } from "../components/policy/ImpactCard";
import { PolicyPreview } from "../components/policy/PolicyPreview";
import { PublishPanel } from "../components/policy/PublishPanel";
import { VersionRuns } from "../components/policy/VersionRuns";

export function PolicyPage({ onPublished, openCase }: { onPublished: () => void; openCase: (id: string, version: string) => void }) {
  const [rows, setRows] = useState<PolicyRow[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [note, setNote] = useState("");
  const [dirty, setDirty] = useState(false);
  const [parsed, setParsed] = useState<PolicyDoc | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [published, setPublished] = useState<PolicyDoc | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [cmp, setCmp] = useState<Comparison | null>(null);
  const [publishing, setPublishing] = useState<string | null>(null);
  const [rt, setRt] = useState<RuntimeSettings | null>(null);

  useEffect(() => {
    api.settings().then((s) => setRt(s.values)).catch(() => setRt(null));
  }, []);

  const refresh = useCallback(async () => {
    const list = await api.policies();
    setRows(list);
    const pub = list.find((r) => r.status === "published");
    if (pub) setPublished((await api.policy(pub.version)).parsed);
    return list;
  }, []);

  useEffect(() => {
    refresh().then((list) => {
      const pub = list.find((r) => r.status === "published") ?? list[0];
      if (pub) open(pub);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll jobs while any is active.
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      const js = await api.jobs().catch(() => []);
      if (stop) return;
      setJobs(js);
      const active = js.some((j) => j.status === "queued" || j.status === "running");
      setTimeout(tick, active ? 1000 : 5000);
    };
    tick();
    return () => {
      stop = true;
    };
  }, []);

  // Live validation of the editor content (only the newest request's answer counts).
  const latestValidate = useLatest();
  const latestCompare = useLatest();
  useEffect(() => {
    const t = setTimeout(() => {
      latestValidate.run(
        api.validate(source),
        (r) => {
          setParsed(r.ok ? r.parsed! : null);
          setParseError(r.ok ? null : r.error ?? "invalid");
        },
        (e) => {
          setParsed(null);
          setParseError(`could not validate: ${errorText(e)}`);
        },
      );
    }, 250);
    return () => clearTimeout(t);
  }, [source, latestValidate]);

  const row = rows.find((r) => r.version === sel) ?? null;
  const isNew = sel === null;
  const editable = isNew || row?.status === "draft";

  const open = (r: PolicyRow) => {
    setSel(r.version);
    setSource(r.source);
    setNote(r.note);
    setDirty(false);
    setMsg(null);
    setPublishing(null);
  };

  // Recompute the impact when the selection changes or a run finishes, not on every jobs poll.
  const finishedRuns = jobs
    .filter((j) => j.status === "done")
    .map((j) => j.id)
    .join(",");
  const publishedVersion = rows.find((r) => r.status === "published")?.version;
  useEffect(() => {
    if (!sel || !publishedVersion || sel === publishedVersion) {
      latestCompare.cancel(); // a compare still in flight must not bring the old card back
      setCmp(null);
      return;
    }
    latestCompare.run(api.compare(publishedVersion, sel), setCmp, () => setCmp(null));
  }, [sel, publishedVersion, finishedRuns, latestCompare]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setMsg(null);
    try {
      await fn();
      setMsg({ kind: "ok", text: ok });
      return true;
    } catch (e) {
      setMsg({ kind: "err", text: String((e as Error).message ?? e) });
      return false;
    }
  };

  const saveDraft = async () => {
    if (!parsed) return;
    const done = await act(
      () => (isNew ? api.createPolicy(source, note) : api.updatePolicy(sel!, source, note)),
      `Draft ${parsed.version} saved.`,
    );
    if (done) {
      const list = await refresh();
      const r = list.find((x) => x.version === parsed.version);
      if (r) open(r);
    }
  };

  const newFrom = () => {
    setSel(null);
    setSource(withVersion(source, nextVersion(rows)));
    setNote("");
    setDirty(true);
    setMsg(null);
  };

  const diff = useMemo(() => {
    const map: Record<string, "added" | "changed" | undefined> = {};
    if (!parsed || !published || parsed.version === published.version) return map;
    for (const s of parsed.sections) {
      const old = published.sections.find((x) => x.id === s.id);
      map[s.id] = !old ? "added" : old.text !== s.text || old.effect !== s.effect || old.name !== s.name ? "changed" : undefined;
    }
    return map;
  }, [parsed, published]);
  const removed = published && parsed ? published.sections.filter((s) => !parsed.sections.some((x) => x.id === s.id)) : [];

  const versionJobs = jobs.filter((j) => j.policy_version === (sel ?? parsed?.version)).slice(0, 4);

  return (
    <div className="workbench">
      <aside className="versions">
        <Eyebrow>Policy versions</Eyebrow>
        {rows.map((r) => (
          <button
            key={r.version}
            className={`ver${sel === r.version ? " sel" : ""}`}
            onClick={() => {
              if (r.version === sel) return;
              if (dirty && !confirm(`Discard your unsaved changes to ${sel ?? "the new version"}?`)) return;
              open(r);
            }}
          >
            <span className="v">
              {r.version} <span className={`status ${r.status}`}>{r.status}</span>
            </span>
            <span className="muted small">
              {(r.labeled ?? 0).toLocaleString()} labeled · {r.created_at.slice(0, 10)}
            </span>
            {r.note && <span className="small" style={{ color: "var(--ink-2)" }}>{r.note}</span>}
          </button>
        ))}
        {isNew && (
          <div className="ver sel">
            <span className="v">
              {parsed?.version ?? "new"} <span className="status draft">unsaved</span>
            </span>
          </div>
        )}
        <button className="btn" style={{ marginTop: 8 }} onClick={newFrom}>
          + New version from this
        </button>
      </aside>

      <section className="editor">
        <div className="editor-bar">
          <div>
            <Eyebrow>{editable ? "Editing draft" : `${row?.status} · read-only`}</Eyebrow>
            <strong className="h-serif" style={{ fontSize: 18 }}>{parsed?.title ?? "Policy"}</strong>
          </div>
          <div className="btn-row">
            {editable && (
              <button className="btn" disabled={!parsed || (!dirty && !isNew)} onClick={saveDraft}>
                Save draft
              </button>
            )}
            {row?.status === "draft" && !dirty && (
              <>
                <button
                  className="btn"
                  onClick={() => act(() => api.relabel(row.version, "eval"), `Shadow run of ${row.version} on the eval set started. The published policy keeps serving.`)}
                >
                  Shadow-run on eval set
                </button>
                <button className="btn primary" onClick={() => setPublishing(row.version)}>
                  Publish…
                </button>
                <button
                  className="btn danger"
                  onClick={async () => {
                    if (!confirm(`Delete draft ${row.version}?`)) return;
                    if (await act(() => api.deletePolicy(row.version), "Draft deleted.")) {
                      const list = await refresh();
                      const pub = list.find((r) => r.status === "published");
                      if (pub) open(pub);
                    }
                  }}
                >
                  Delete
                </button>
              </>
            )}
            {row?.status === "retired" && (
              <button
                className="btn"
                onClick={() => setPublishing(row.version)}
                title="Make this version the published one again. Its stored labels are reused."
              >
                Re-publish (roll back)…
              </button>
            )}
            {row?.status === "published" && (
              <button
                className="btn"
                onClick={async () => {
                  const days = rt?.backfill_days ?? 30;
                  let pv;
                  try {
                    pv = await api.relabelPreview(row.version, days, false);
                  } catch (e) {
                    setMsg({ kind: "err", text: String((e as Error).message ?? e) });
                    return;
                  }
                  const count = pv.count;
                  if (
                    !confirm(
                      `Re-label ${count.toLocaleString()} postings decided in the last ${days} days under ${row.version}?\n\n` +
                        `${pv.already_under_version.toLocaleString()} already have a ${row.version} decision (the Decisions tab) and are re-run; ` +
                        `${pv.only_under_other_versions.toLocaleString()} were decided only under other versions and move to ${row.version}.\n\nEach one is a model call.`,
                    )
                  )
                    return;
                  act(() => api.relabel(row.version, "recent", { days }), `Re-labeling ${count.toLocaleString()} postings under ${row.version}.`);
                }}
                title="Re-label every posting received in the backfill window (Settings) under this version"
              >
                Re-label last {rt?.backfill_days ?? 30} days
              </button>
            )}
          </div>
        </div>
        {publishing && row && publishing === row.version && (
          <PublishPanel
            row={row}
            defaults={rt}
            onCancel={() => setPublishing(null)}
            onConfirm={async (backfill, days) => {
              const rollback = row.status === "retired";
              let backfillError: string | undefined;
              const ok = await act(
                async () => {
                  backfillError = (await api.publish(row.version, { backfill, days })).backfill_error;
                },
                `${row.version} is ${rollback ? "published again (rolled back)" : "now published"}.` +
                  (backfill ? ` Re-labeling the last ${days} days on the backfill queue; progress below.` : ""),
              );
              // Published, but the re-label could not be queued: say so, and keep the version live.
              if (ok && backfillError) setMsg({ kind: "err", text: backfillError });
              setPublishing(null);
              if (ok) {
                await refresh();
                onPublished();
              }
            }}
          />
        )}
        <textarea
          spellCheck={false}
          readOnly={!editable}
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            setDirty(true);
          }}
          aria-label="Policy YAML"
        />
        {editable && (
          <input
            className="note"
            placeholder="Change note (why this version exists)…"
            value={note}
            onChange={(e) => {
              setNote(e.target.value);
              setDirty(true);
            }}
          />
        )}
      </section>

      <section className="preview">
        {msg && <div className={msg.kind === "err" ? "error-box" : "note-box"}>{msg.text}</div>}
        {parseError && (
          <div className="error-box">
            <strong>This document will be rejected:</strong>
            {"\n"}
            {parseError}
          </div>
        )}

        {versionJobs.length > 0 && (
          <VersionRuns
            title={`Runs for ${sel ?? parsed?.version}`}
            jobs={versionJobs}
            onCancel={(j) => act(() => api.cancelJob(j.id), `Job #${j.id} cancelled.`)}
          />
        )}

        {cmp && cmp.compared > 0 && <ImpactCard cmp={cmp} openCase={openCase} />}

        {parsed && <PolicyPreview parsed={parsed} diff={diff} removed={removed} />}
      </section>
    </div>
  );
}

