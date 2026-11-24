import type { LabelState } from "../lib/api";

const TEXT: Record<LabelState["status"], string> = {
  queued: "queued",
  labeling: "labeling…",
  done: "labeled",
  failed: "failed",
};

/** queued → labeling → labeled | failed, for one posting under one version. */
export function StateChip({ state }: { state: LabelState | null | undefined }) {
  if (!state) return <span className="state none">not requested</span>;
  return (
    <span className={`state ${state.status}`} title={state.error || `job #${state.run_id ?? "?"} · ${state.updated_at}`}>
      {TEXT[state.status]}
      {state.run_id && state.status !== "done" ? ` · job #${state.run_id}` : ""}
    </span>
  );
}
