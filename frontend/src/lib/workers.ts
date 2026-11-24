import { useEffect, useState } from "react";
import { api, type Health, type WorkerInfo } from "./api";

/** Live view of /api/health, refreshed every `everyMs`. Workers come from their heartbeats. */
export function useHealth(everyMs = 10000): Health | null {
  const [h, setH] = useState<Health | null>(null);
  useEffect(() => {
    let stop = false;
    const tick = () =>
      api
        .health()
        .then((x) => !stop && setH(x))
        .catch(() => undefined)
        .finally(() => !stop && setTimeout(tick, everyMs));
    tick();
    return () => {
      stop = true;
    };
  }, [everyMs]);
  return h;
}

export const NO_WORKER_HINT =
  "No worker is running, so queued postings wait. `docker compose up` starts two; on the host, `make worker` uses your Claude login.";

/** Why queued postings are not moving, or null when at least one worker is taking messages. */
export function workerProblem(h: { workers: WorkerInfo[] } | null): string | null {
  if (!h) return null;
  if (h.workers.length === 0) return NO_WORKER_HINT;
  if (h.workers.every((w) => w.status === "waiting")) {
    const why = h.workers[0].detail || "a provider is not ready";
    return `Workers are running but waiting: ${why}. They start by themselves within seconds once it is fixed on the Settings page, with no restart; queued postings are kept.`;
  }
  return null;
}
