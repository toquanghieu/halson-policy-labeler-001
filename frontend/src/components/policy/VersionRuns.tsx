import type { Job } from "../../lib/api";

/** Recent runs (shadow-runs, re-labels, backfills) of one version, with progress and Cancel. */
export function VersionRuns({ title, jobs, onCancel }: { title: string; jobs: Job[]; onCancel: (j: Job) => Promise<unknown> }) {
  return (
    <div className="card">
      <h4 className="eyebrow">{title}</h4>
      {jobs.map((j) => (
        <div key={j.id} className="job-line" style={{ marginTop: 6 }}>
          <span className="mono small" style={{ width: 120 }}>
            #{j.id} {j.detail.replace("scope=", "")}
          </span>
          <span className="progress">
            <i style={{ width: `${((j.done + j.failed) / Math.max(1, j.total)) * 100}%` }} />
          </span>
          <span className="mono small">
            {j.done + j.failed}/{j.total}
          </span>
          <span className="small muted">{j.status}</span>
          {(j.status === "queued" || j.status === "running") && (
            <button
              className="btn danger small-btn"
              onClick={async () => {
                const left = j.total - j.done - j.failed;
                if (
                  !confirm(
                    `Cancel job #${j.id}? ${left.toLocaleString()} posting(s) not yet labeled will be skipped; labels already made stay.`,
                  )
                )
                  return;
                await onCancel(j);
              }}
            >
              Cancel
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
