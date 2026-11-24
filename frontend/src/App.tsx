import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type PolicyDoc } from "./lib/api";
import { PolicyContext } from "./lib/policyContext";
import { useHealth } from "./lib/workers";
import { useLatest } from "./lib/latest";
import { EvalPage } from "./pages/EvalPage";
import { MessagesPage } from "./pages/MessagesPage";
import { PolicyPage } from "./pages/PolicyPage";
import { Queue } from "./pages/QueuePage";
import { SettingsPage } from "./pages/SettingsPage";
import { WorkPage } from "./pages/WorkPage";

type Route = { page: "messages" | "work" | "queue" | "policy" | "eval" | "settings"; version?: string; id?: string };

function parseHash(): Route {
  const [path, query = ""] = window.location.hash.replace(/^#\/?/, "").split("?");
  const q = new URLSearchParams(query);
  const page = path === "messages" || path === "work" || path === "policy" || path === "eval" || path === "settings" ? path : "queue";
  return { page, version: q.get("v") ?? undefined, id: q.get("id") ?? undefined };
}

/** toHash, keeping the versions being compared (cmp=...) when staying on the Decisions page, so
 *  the analyst walks through cases with the same side-by-side view. */
function keepCmp(r: Route): string {
  const hash = toHash(r);
  if (r.page !== "queue") return hash;
  const cmp = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("cmp");
  return cmp ? `${hash}${hash.includes("?") ? "&" : "?"}cmp=${encodeURIComponent(cmp)}` : hash;
}

/** "claude-haiku-5-5" -> "Haiku 5.5" for the header; the full ids are in the tooltip. */
function shortModel(id: string): string {
  const m = /^claude-([a-z]+)-(\d+)(?:-(\d+))?/.exec(id);
  if (!m) return id.length > 24 ? `${id.slice(0, 22)}…` : id;
  return `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}${m[3] ? `.${m[3]}` : ""}`;
}

function toHash(r: Route): string {
  const q = new URLSearchParams();
  if (r.version) q.set("v", r.version);
  if (r.id) q.set("id", r.id);
  const s = q.toString();
  return `#/${r.page}${s ? `?${s}` : ""}`;
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  const [published, setPublished] = useState<string | null>(null);
  const [policy, setPolicy] = useState<PolicyDoc | null>(null);
  const [models, setModels] = useState("");
  const [modelIds, setModelIds] = useState("");
  const health = useHealth(10000);
  const workers = health?.workers ?? null;

  useEffect(() => {
    const h = () => setRoute(parseHash());
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);

  const go = useCallback((r: Route) => {
    window.location.hash = keepCmp(r);
  }, []);

  const loadHealth = useCallback(() => {
    api
      .health()
      .then((h) => {
        setPublished(h.published_policy);
        setModels(h.provider === "fake" ? "fake provider" : `${shortModel(h.tier1)} → ${h.tier2 ? shortModel(h.tier2) : "no escalation"}`);
        setModelIds(`tier 1: ${h.tier1} · tier 2: ${h.tier2 || "off"}`);
      })
      .catch(() => setModels("API unreachable")); // the header says so instead of an unhandled rejection
  }, []);
  useEffect(loadHealth, [loadHealth]);

  // Section names and colors follow the version being viewed.
  const viewing = route.version ?? published ?? undefined;
  const latestPolicy = useLatest();
  useEffect(() => {
    if (!viewing) return;
    // Ablation runs (e.g. v1@opus-only) have no policy row; fall back to the base version.
    // Only the newest version's answer is applied, so switching fast never shows the previous
    // version's names and colours.
    const base = viewing.split("@")[0];
    latestPolicy.run(
      api.policy(viewing).catch(() => api.policy(base)),
      (p) => setPolicy(p.parsed),
      () => setPolicy(null),
    );
  }, [viewing, latestPolicy]);

  const ctx = useMemo(
    () => ({
      policy,
      sectionIndex: (id: string) => Math.max(0, policy?.sections.findIndex((s) => s.id === id) ?? 0),
      section: (id: string) => policy?.sections.find((s) => s.id === id),
    }),
    [policy],
  );

  return (
    <PolicyContext.Provider value={ctx}>
      <div className="shell">
        <header className="topbar">
          <div className="brand">
            Casebook<small>RECRUITMENT FRAUD DESK</small>
          </div>
          <nav className="nav">
            <a href={toHash({ page: "messages" })} className={route.page === "messages" ? "active" : ""}>Messages</a>
            <a href={toHash({ page: "work" })} className={route.page === "work" ? "active" : ""}>Work</a>
            <a href={toHash({ page: "queue" })} className={route.page === "queue" ? "active" : ""}>Decisions</a>
            <a href={toHash({ page: "policy" })} className={route.page === "policy" ? "active" : ""}>Policy</a>
            <a href={toHash({ page: "eval" })} className={route.page === "eval" ? "active" : ""}>Evaluation</a>
            <a href={toHash({ page: "settings" })} className={route.page === "settings" ? "active" : ""}>Settings</a>
          </nav>
          <div className="spacer" />
          <div className="policy-pill">
            {route.version && route.version !== published && (
              <a href={toHash({ page: route.page })} style={{ color: "var(--review)" }}>viewing {route.version} · back to published</a>
            )}
            <span><span className="dot" /> {published ?? "…"} published</span>
            <span className="muted models" title={modelIds}>
              {models}
            </span>
            {workers && (
              <a
                href={toHash({ page: "work" })}
                className={workers.length && workers.some((w) => w.status !== "waiting") ? "" : "no-worker"}
                title={
                  workers
                    .map((w) => `${w.worker_id} · ${w.role} · ${w.status === "waiting" ? `waiting: ${w.detail}` : `${w.processed} done · ${w.inflight} in flight`}`)
                    .join("\n") || "no heartbeat in the last 30 s"
                }
              >
                {!workers.length
                  ? "no worker running"
                  : workers.every((w) => w.status === "waiting")
                    ? `${workers.length} waiting`
                    : `${workers.length} worker${workers.length === 1 ? "" : "s"}`}
              </a>
            )}
          </div>
        </header>
        <main className="page">
          {route.page === "queue" && (
            <Queue
              key={viewing}
              version={route.version}
              selected={route.id ?? null}
              onVersion={(v) => go({ page: "queue", version: v })}
              onSelect={(id) => {
                const r = { ...route, id: id ?? undefined };
                window.history.replaceState(null, "", keepCmp(r));
                setRoute(r);
              }}
            />
          )}
          {route.page === "policy" && (
            <PolicyPage onPublished={loadHealth} openCase={(id, version) => go({ page: "queue", id, version })} />
          )}
          {route.page === "messages" && <MessagesPage openDecisions={() => go({ page: "queue" })} />}
          {route.page === "settings" && <SettingsPage onSaved={loadHealth} />}
          {route.page === "work" && (
            <WorkPage
              openCase={(id, version) => {
                const pub = published ?? "";
                window.location.hash = version === pub ? `#/queue?id=${encodeURIComponent(id)}` : `#/queue?id=${encodeURIComponent(id)}&cmp=${encodeURIComponent(version)}`;
              }}
            />
          )}
          {route.page === "eval" && <EvalPage initial={route.version} openQueue={(version) => go({ page: "queue", version })} />}
        </main>
      </div>
    </PolicyContext.Provider>
  );
}
