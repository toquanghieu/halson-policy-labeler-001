import { useCallback, useEffect, useState } from "react";
import { api, type LabelValue, type ListItem, type PolicyRow } from "../lib/api";
import { CaseFile } from "../components/CaseFile";
import { Confidence, Eyebrow, SectionChip, Stamp } from "../components/ui";
import { usePolicy } from "../lib/policyContext";
import { clickable } from "../lib/a11y";

const LABELS: (LabelValue | "")[] = ["", "VIOLATING", "NEEDS_REVIEW", "ALLOWED"];
const LABEL_NAME: Record<string, string> = { "": "All decisions", VIOLATING: "Violating", NEEDS_REVIEW: "Needs review", ALLOWED: "Allowed" };
const PAGE = 50;

interface Filters {
  label: LabelValue | "";
  section: string;
  tier: string;
  eval_only: boolean;
  disagree: boolean;
  q: string;
  order: string;
}

export function Queue({
  version,
  selected,
  onSelect,
  onVersion,
}: {
  version?: string;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onVersion: (v: string | undefined) => void;
}) {
  const { policy } = usePolicy();
  const [versions, setVersions] = useState<PolicyRow[]>([]);
  useEffect(() => {
    api.policies().then(setVersions).catch(() => setVersions([]));
  }, []);
  const published = versions.find((r) => r.status === "published")?.version;
  const [f, setF] = useState<Filters>({ label: "", section: "", tier: "", eval_only: false, disagree: false, q: "", order: "risk" });
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<ListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [qDraft, setQDraft] = useState("");

  const base = { policy_version: version, eval_only: f.eval_only, disagree: f.disagree, section: f.section, tier: f.tier, q: f.q };

  useEffect(() => {
    let live = true;
    api
      .labels({ ...base, label: f.label, order: f.order, limit: PAGE, offset })
      .then((r) => {
        if (!live) return;
        setItems(r.items);
        setTotal(r.total);
        setError(null);
        if (!selected && r.items[0]) onSelect(r.items[0].job_id);
      })
      .catch((e) => live && setError(String(e.message ?? e)));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, f, offset]);

  // Counts per label for the current non-label filters.
  useEffect(() => {
    let live = true;
    Promise.all(LABELS.map((l) => api.labels({ ...base, label: l, limit: 1 }).then((r) => [l, r.total] as const)))
      .then((pairs) => live && setCounts(Object.fromEntries(pairs)))
      .catch(() => undefined);
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, f.eval_only, f.disagree, f.section, f.tier, f.q]);

  const set = (patch: Partial<Filters>) => {
    setOffset(0);
    setF((prev) => ({ ...prev, ...patch }));
  };

  // j / k to move through the list, like a mail client.
  const move = useCallback(
    (delta: number) => {
      const i = items.findIndex((x) => x.job_id === selected);
      const next = items[Math.min(items.length - 1, Math.max(0, i + delta))];
      if (next) {
        onSelect(next.job_id);
        document.getElementById(`row-${next.job_id}`)?.scrollIntoView({ block: "nearest" });
      }
    },
    [items, selected, onSelect],
  );
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return; // Cmd+K and friends belong to the browser
      if (e.key === "j") move(1);
      if (e.key === "k") move(-1);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [move]);

  return (
    <div className="queue">
      <aside className="rail">
        <div className="facet">
          <Eyebrow>Policy version</Eyebrow>
          <select
            className="version-select"
            value={version ?? published ?? ""}
            onChange={(e) => onVersion(e.target.value === published ? undefined : e.target.value)}
            aria-label="Policy version"
          >
            {versions.map((r) => (
              <option key={r.version} value={r.version}>
                {r.version} · {r.status} · {(r.labeled ?? 0).toLocaleString()} decided
              </option>
            ))}
          </select>
          <span className="small muted">Decisions made under this version. Others: pick one here, or compare inside a case.</span>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            set({ q: qDraft.trim() });
          }}
        >
          <input type="search" aria-label="Search decisions by text or job id" placeholder="Search text or job id…" value={qDraft} onChange={(e) => setQDraft(e.target.value)} />
        </form>

        <div>
          <h4 className="eyebrow">Decision</h4>
          <div className="facet">
            {LABELS.map((l) => (
              <button key={l || "all"} className={f.label === l ? "on" : ""} onClick={() => set({ label: l })}>
                <span>{LABEL_NAME[l]}</span>
                <span className="count">{counts[l] ?? "·"}</span>
              </button>
            ))}
          </div>
        </div>

        <div>
          <h4 className="eyebrow">Policy section</h4>
          <div className="facet">
            <button className={f.section === "" ? "on" : ""} onClick={() => set({ section: "" })}>
              <span>Any section</span>
            </button>
            {policy?.sections.map((s) => (
              <button key={s.id} className={f.section === s.id ? "on" : ""} onClick={() => set({ section: s.id })} title={s.text}>
                <span style={{ display: "flex", gap: 6, alignItems: "center", minWidth: 0 }}>
                  <SectionChip id={s.id} />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.name}</span>
                </span>
              </button>
            ))}
          </div>
        </div>

        <div>
          <h4 className="eyebrow">Handled by</h4>
          <div className="facet">
            {[["", "Any"], ["tier1", "First pass only"], ["tier2", "Escalated"], ["failed", "Failed → manual"]].map(([v, n]) => (
              <button key={v} className={f.tier === v ? "on" : ""} onClick={() => set({ tier: v })}>
                <span>{n}</span>
              </button>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <label className="check">
            <input type="checkbox" checked={f.eval_only} onChange={(e) => set({ eval_only: e.target.checked })} />
            <span>Eval set only (400)</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={f.disagree} onChange={(e) => set({ disagree: e.target.checked })} />
            <span>Disagrees with dataset label</span>
          </label>
        </div>

        <div className="muted small" style={{ marginTop: "auto" }}>
          <kbd>j</kbd> / <kbd>k</kbd> to move through the list
        </div>
      </aside>

      <section className="list">
        <div className="list-head">
          <span className="small">
            <strong>{total.toLocaleString()}</strong> <span className="muted">postings · policy {version ?? "published"}</span>
          </span>
          <select value={f.order} onChange={(e) => set({ order: e.target.value })} aria-label="Sort">
            <option value="risk">Riskiest first</option>
            <option value="confidence">Least confident first</option>
            <option value="recent">Most recent</option>
            <option value="id">Job id</option>
          </select>
        </div>
        {error && <div style={{ padding: 14 }}><div className="error-box">{error}</div></div>}
        {!error && items.length === 0 && <div className="empty">Nothing matches these filters.</div>}
        {items.map((it) => {
          const miss = it.gold !== null && ((it.gold === 1 && it.label === "ALLOWED") || (it.gold === 0 && it.label === "VIOLATING"));
          return (
            <div
              key={it.job_id}
              id={`row-${it.job_id}`}
              className={`row${selected === it.job_id ? " sel" : ""}`}
              {...clickable(() => onSelect(it.job_id))}
            >
              <Stamp label={it.label} />
              <span className="title" title={it.title}>{it.title || "(untitled)"}</span>
              <Confidence value={it.confidence} />
              <div className="meta">
                <span className="id">#{it.job_id}</span>
                {it.policy_sections.map((s) => <SectionChip key={s} id={s} />)}
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 220 }}>{it.location}</span>
                {it.tier === "tier2" && <span className="gold-tag">escalated</span>}
                {it.gold !== null && (
                  <span className={`gold-tag${miss ? " miss" : ""}`} title="EMSCAD dataset label (evaluation only)">
                    gold: {it.gold ? "fraud" : "legit"}
                  </span>
                )}
              </div>
            </div>
          );
        })}
        {total > PAGE && (
          <div className="pager">
            <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Prev</button>
            <span className="muted">{offset + 1}–{Math.min(total, offset + PAGE)} of {total}</span>
            <button className="btn" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next →</button>
          </div>
        )}
      </section>

      {selected ? <CaseFile jobId={selected} version={version} /> : <div className="case"><Eyebrow>No posting selected</Eyebrow></div>}
    </div>
  );
}
