import { useEffect, useState } from "react";
import { api, type PolicyRow, type RuntimeSettings } from "../../lib/api";
import { useLatest } from "../../lib/latest";

/** Confirmation before publishing (or rolling back to) a version, with the backfill choice and its size. */
export function PublishPanel({
  row,
  defaults,
  onCancel,
  onConfirm,
}: {
  row: PolicyRow;
  defaults: RuntimeSettings | null;
  onCancel: () => void;
  onConfirm: (backfill: boolean, days: number) => Promise<void> | void;
}) {
  const [backfill, setBackfill] = useState(defaults?.backfill_on_publish ?? false);
  const [days, setDays] = useState(defaults?.backfill_days ?? 30);
  const [count, setCount] = useState<number | null>(null);
  const rollback = row.status === "retired";
  const latestPreview = useLatest();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setCount(null);
    const t = setTimeout(() => {
      latestPreview.run(
        api.relabelPreview(row.version, days),
        (r) => setCount(r.count),
        () => setCount(null),
      );
    }, 200);
    return () => clearTimeout(t);
  }, [row.version, days, latestPreview]);

  return (
    <div className="publish-panel">
      <div>
        <strong>{rollback ? `Roll back to ${row.version}?` : `Publish ${row.version}?`}</strong>
        <div className="small muted">
          New postings are labeled with it immediately.{" "}
          {rollback ? "The current version is retired; its labels stay stored." : "The current version is retired, not deleted."}
        </div>
      </div>
      <label className="check">
        <input type="checkbox" checked={backfill} onChange={(e) => setBackfill(e.target.checked)} />
        <span>
          Also re-label postings from the last{" "}
          <input
            className="num"
            type="number"
            min={1}
            max={365}
            value={days}
            onChange={(e) => setDays(Math.max(1, Math.min(365, Number(e.target.value) || 1)))}
          />{" "}
          days on the backfill queue
          <span className="muted">
            {" "}
            ·{" "}
            {count === null
              ? "counting…"
              : `${count.toLocaleString()} posting${count === 1 ? "" : "s"} decided under other versions, not yet under ${row.version}`}
          </span>
        </span>
      </label>
      <div className="btn-row">
        <button
          className="btn primary"
          disabled={busy}
          onClick={async () => {
            if (busy) return; // one publish per click, however fast the double-click
            setBusy(true);
            try {
              await onConfirm(backfill, days);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Publishing…" : rollback ? "Roll back" : "Publish"}
          {backfill && count ? ` + re-label ${count.toLocaleString()}` : ""}
        </button>
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
