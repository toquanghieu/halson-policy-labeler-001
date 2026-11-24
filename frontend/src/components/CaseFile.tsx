import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, type EvidenceSpan, type PolicyDoc, type PostingDetail, type Step } from "../lib/api";
import { FACT_TEXT, fmtDateTime, fmtMs, fmtUsd } from "../lib/format";
import { PolicyContext, usePolicy, type PolicyCtx } from "../lib/policyContext";
import { Confidence, Eyebrow, SectionChip, Stamp } from "./ui";
import { VersionCompare } from "./VersionCompare";
import { highlight, spanId } from "../lib/highlight";

const FIELDS: [keyof PostingDetail["posting"], string][] = [
  ["company_profile", "Company profile"],
  ["description", "Description"],
  ["requirements", "Requirements"],
  ["benefits", "Benefits"],
];

export function CaseFile({ jobId, version }: { jobId: string; version?: string }) {
  const [data, setData] = useState<PostingDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The compared version whose decision fills the Why card, the trail and the highlights
  // (null = the version this case was opened under).
  const [focus, setFocus] = useState<string | null>(null);
  const [focusData, setFocusData] = useState<PostingDetail | null>(null);
  const [reload, setReload] = useState(0);
  const reloadCase = useCallback(() => setReload((n) => n + 1), []);
  const viewing = usePolicy();
  const [focusPolicy, setFocusPolicy] = useState<PolicyDoc | null>(null);
  const [focusError, setFocusError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  // A new case: start clean and scroll to the top.
  useEffect(() => {
    setData(null);
    setError(null);
    setFocus(null);
    scroller.current?.scrollTo({ top: 0 });
  }, [jobId, version]);
  // Load it, and reload in place (no flicker, focus kept) when a new decision arrives.
  useEffect(() => {
    let live = true;
    api
      .posting(jobId, version)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, [jobId, version, reload]);

  useEffect(() => {
    let live = true;
    setFocusData(null);
    setFocusError(null);
    if (focus)
      // A failure here concerns the compared version only: say so beside it, keep the case on screen.
      api
        .posting(jobId, focus)
        .then((d) => live && setFocusData(d))
        .catch((e) => live && setFocusError(`could not load ${focus}: ${String(e.message ?? e)}`));
    return () => {
      live = false;
    };
  }, [jobId, focus]);

  // Section names and colours follow the version whose decision is shown, not only the one viewed.
  useEffect(() => {
    let live = true;
    setFocusPolicy(null);
    if (focus) {
      const base = focus.split("@")[0]; // eval tags (v1@current) have no policy row of their own
      api
        .policy(focus)
        .catch(() => api.policy(base))
        .then((p) => live && setFocusPolicy(p.parsed))
        .catch(() => undefined);
    }
    return () => {
      live = false;
    };
  }, [focus]);
  const sectionsOf = focus && focusPolicy ? focusPolicy : viewing.policy;
  // Everything below that shows the focused decision (chips, highlights, trail) reads sections from
  // the focused version, so a chip and its highlighted quote always share a colour and a name.
  const shownCtx = useMemo<PolicyCtx>(
    () => ({
      policy: sectionsOf,
      sectionIndex: (id: string) => Math.max(0, sectionsOf?.sections.findIndex((x) => x.id === id) ?? 0),
      section: (id: string) => sectionsOf?.sections.find((x) => x.id === id),
    }),
    [sectionsOf],
  );
  const { sectionIndex, section } = shownCtx;

  if (error)
    return (
      <div className="case">
        <div className="error-box">{error}</div>
      </div>
    );
  if (!data)
    return (
      <div className="case">
        <div className="empty">Loading case…</div>
      </div>
    );

  const { posting } = data;
  // What the panels below show: the focused compared version once loaded, else the opened one.
  const shown = focus && focusData?.policy_version === focus ? focusData : data;
  const result = shown.result;
  const labeledAt = shown.history.find((h) => h.policy_version === shown.policy_version)?.created_at;
  const spans = result?.evidence_spans ?? [];
  const jump = (s: EvidenceSpan) => {
    const el = document.getElementById(spanId(s)) ?? document.querySelector<HTMLElement>(`[data-also~="${CSS.escape(spanId(s))}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
  };

  return (
    <div className="case" ref={scroller}>
      <div className="case-head">
        <div>
          <Eyebrow>
            Posting #{posting.job_id} · {posting.location || "no location"}
            {data.is_eval ? " · eval set" : ""}
          </Eyebrow>
          <h2 className="h-serif">
            {posting.title
              ? highlight(
                  posting.title,
                  spans.filter((s) => s.field === "title"),
                  sectionIndex,
                )
              : "(untitled)"}
          </h2>
        </div>
        {data.result ? (
          <Stamp label={data.result.label} big />
        ) : (
          <span className="muted small">not labeled under {data.policy_version}</span>
        )}
      </div>

      <VersionCompare
        jobId={posting.job_id}
        current={data.policy_version}
        focused={shown.policy_version}
        onFocus={(v) => setFocus(v === data.policy_version ? null : v)}
        onCurrentLabeled={reloadCase}
      />
      {focusError && <div className="error-box">{focusError}</div>}

      <PolicyContext.Provider value={shownCtx}>
        <div className="case-grid">
          <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
            {result && (
              <div className="card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <Eyebrow>
                    Why · policy {result.policy_version}
                    {labeledAt && <span title={`${labeledAt} (UTC)`}> · labeled {fmtDateTime(labeledAt)}</span>}
                  </Eyebrow>
                  <Confidence value={result.confidence} />
                </div>
                <p className="rationale">{result.rationale}</p>
                {result.policy_sections.length > 0 && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 12 }}>
                    {result.policy_sections.map((id) => (
                      <div key={id} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                        <SectionChip id={id} />
                        <span style={{ fontWeight: 500 }}>{section(id)?.name ?? "(section not in this policy)"}</span>
                        {section(id)?.effect === "review_only" && <span className="effect muted">review only</span>}
                      </div>
                    ))}
                  </div>
                )}
                {spans.length > 0 && (
                  <>
                    <div style={{ height: 12 }} />
                    <Eyebrow>Evidence — click to find it in the posting</Eyebrow>
                    <ul className="evlist" style={{ marginTop: 6 }}>
                      {spans.map((s, i) => (
                        <li key={i}>
                          <SectionChip id={s.section} />
                          <span>
                            <button className="q" onClick={() => jump(s)}>
                              “{s.quote}”
                            </button>
                            <span className="muted small"> — {s.field.replace("_", " ")}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {result.trace.section_facts && Object.keys(result.trace.section_facts).length > 0 && (
                  <div style={{ marginTop: 12 }}>
                    <Eyebrow>Absence-based facts cited</Eyebrow>
                    <div className="facts" style={{ marginTop: 4 }}>
                      {Object.entries(result.trace.section_facts).map(([sid, fs]) =>
                        fs.map((f) => (
                          <span key={sid + f} className="fact on">
                            {sid}: {FACT_TEXT[f] ?? f}
                          </span>
                        )),
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

            <div className="doc">
              <div className="doc-meta">
                <Meta
                  k="Salary"
                  v={
                    posting.salary_range
                      ? highlight(
                          posting.salary_range,
                          spans.filter((s) => s.field === "salary_range"),
                          sectionIndex,
                        )
                      : ""
                  }
                />
                <Meta k="Employment" v={posting.employment_type} />
                <Meta k="Industry" v={posting.industry} />
                <Meta k="Function" v={posting.function} />
                <Meta k="Experience" v={posting.required_experience} />
                <Meta k="Remote" v={posting.telecommuting ? "yes" : "no"} />
                <Meta k="Logo" v={posting.has_company_logo ? "yes" : "no"} />
                <Meta k="Screening questions" v={posting.has_questions ? "yes" : "no"} />
              </div>
              {FIELDS.map(([key, name]) => {
                const text = String(posting[key] ?? "");
                const fieldSpans = spans.filter((s) => s.field === key);
                return (
                  <div className="doc-field" key={key}>
                    <Eyebrow>{name}</Eyebrow>
                    <div className={`body${text.trim() ? "" : " empty-field"}`}>
                      {text.trim() ? highlight(text, fieldSpans, sectionIndex) : "Empty"}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {result && <Trail data={shown} labeledAt={labeledAt} />}
        </div>
      </PolicyContext.Provider>
    </div>
  );
}

function Meta({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div>
      <span className="muted small">{k}</span>
      <span>{v || "—"}</span>
    </div>
  );
}

function Trail({ data, labeledAt }: { data: PostingDetail; labeledAt?: string }) {
  const r = data.result!;
  const t = r.trace;
  const steps = t.steps;
  return (
    <aside className="decision">
      <div className="card">
        <h4 className="eyebrow">How this decision was made · policy {r.policy_version}</h4>
        {labeledAt && (
          <div className="muted small mono" title={`${labeledAt} (UTC)`} style={{ marginBottom: 8 }}>
            labeled {fmtDateTime(labeledAt)}
          </div>
        )}
        <ol className="trail">
          <li>
            <strong>Pre-checks</strong>
            <div className="muted small">
              router risk {t.router_score == null ? "n/a" : t.router_score.toFixed(2)} · {t.hints.length} pattern hint
              {t.hints.length === 1 ? "" : "s"}
            </div>
            <div className="facts" style={{ marginTop: 4 }}>
              {Object.entries(t.facts).map(([k, v]) => (
                <span key={k} className={`fact${v ? " on" : ""}`}>
                  {v ? "" : "has "}
                  {(FACT_TEXT[k] ?? k).replace(/^no /, v ? "no " : "")}
                </span>
              ))}
            </div>
          </li>
          {steps.map((s, i) => (
            <StepItem key={i} step={s} final={i === steps.length - 1} reasons={i === 0 ? t.escalation_reasons : undefined} />
          ))}
        </ol>
        <dl className="kv">
          <dt>cost</dt>
          <dd>{fmtUsd(t.cost_usd)}</dd>
          <dt>latency</dt>
          <dd>{fmtMs(t.latency_ms)}</dd>
          <dt>final tier</dt>
          <dd>{t.tier}</dd>
        </dl>
      </div>

      {data.gold !== null && (
        <div className="note-box">
          <Eyebrow>Dataset label</Eyebrow>
          EMSCAD marks this posting <strong>{data.gold ? "fraudulent" : "legitimate"}</strong>. Shown for evaluation only; the agent never
          sees it.
        </div>
      )}
    </aside>
  );
}

function StepItem({ step, final, reasons }: { step: Step; final: boolean; reasons?: string[] }) {
  if (step.error) {
    return (
      <li className={final ? "final" : ""}>
        <strong>{step.tier}</strong> <span style={{ color: "var(--violating)" }}>failed: {step.error}</span>
      </li>
    );
  }
  return (
    <li className={final ? "final" : ""}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <strong>{step.tier === "tier1" ? "First pass" : "Escalated review"}</strong>
        <span className="mono small muted">{step.model}</span>
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 4, flexWrap: "wrap" }}>
        {step.label && <Stamp label={step.label} />}
        {step.sections?.map((s) => (
          <SectionChip key={s} id={s} />
        ))}
        {step.confidence !== undefined && <Confidence value={step.confidence} />}
      </div>
      {step.rejected_quotes && step.rejected_quotes.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div className="muted small">Quotes not found in the posting (discarded):</div>
          {step.rejected_quotes.map((q, i) => (
            <div key={i} className="small">
              <SectionChip id={q.section} /> <span className="struck">“{q.quote}”</span>
            </div>
          ))}
        </div>
      )}
      {step.dropped_sections && step.dropped_sections.length > 0 && (
        <div className="small" style={{ marginTop: 4 }}>
          Dropped: {step.dropped_sections.map((d) => `${d.section} (${d.reason})`).join(", ")}
        </div>
      )}
      {reasons && reasons.length > 0 && (
        <div className="small" style={{ marginTop: 6, color: "var(--review)" }}>
          Escalated because: {reasons.join("; ")}
        </div>
      )}
      <details className="raw" style={{ marginTop: 4 }}>
        <summary>
          model output · {fmtUsd(step.cost_usd ?? 0)} · {fmtMs(step.latency_ms ?? 0)}
        </summary>
        <pre>{JSON.stringify(step.raw, null, 2)}</pre>
      </details>
    </li>
  );
}
