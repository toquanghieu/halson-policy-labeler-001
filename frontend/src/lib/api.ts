export type LabelValue = "VIOLATING" | "ALLOWED" | "NEEDS_REVIEW";

export interface EvidenceSpan {
  section: string;
  quote: string;
  field: string;
  start: number;
  end: number;
}

export interface Step {
  tier: string;
  model?: string;
  label?: LabelValue;
  sections?: string[];
  confidence?: number;
  rejected_quotes?: { section: string; quote: string }[];
  dropped_sections?: { section: string; reason: string }[];
  raw?: { findings: { section: string; quotes: string[]; facts: string[]; explanation: string }[]; rationale: string };
  usage?: Record<string, number>;
  cost_usd?: number;
  latency_ms?: number;
  error?: string;
}

export interface LabelResult {
  job_id: string;
  label: LabelValue;
  policy_sections: string[];
  evidence: string[];
  rationale: string;
  confidence: number;
  policy_version: string;
  evidence_spans: EvidenceSpan[];
  trace: {
    facts: Record<string, boolean>;
    hints: { kind: string; field: string; text: string }[];
    router_score: number | null;
    steps: Step[];
    escalation_reasons?: string[];
    tier: string;
    cost_usd: number;
    latency_ms: number;
    section_facts?: Record<string, string[]>;
  };
}

export interface Posting {
  job_id: string;
  title: string;
  company_profile: string;
  description: string;
  requirements: string;
  benefits: string;
  location: string;
  department: string;
  salary_range: string;
  employment_type: string;
  required_experience: string;
  required_education: string;
  industry: string;
  function: string;
  telecommuting: boolean;
  has_company_logo: boolean;
  has_questions: boolean;
}

export interface ListItem {
  job_id: string;
  title: string;
  location: string;
  label: LabelValue;
  policy_sections: string[];
  confidence: number;
  rationale: string;
  tier: string;
  gold: number | null;
  is_eval: boolean;
}

export interface PostingDetail {
  posting: Posting;
  gold: number | null;
  is_eval: boolean;
  policy_version: string;
  result: LabelResult | null;
  history: { policy_version: string; label: LabelValue; sections: string[]; confidence: number; created_at: string }[];
}

export interface Section {
  id: string;
  name: string;
  effect: "violating" | "review_only";
  text: string;
}

export interface PolicyDoc {
  version: string;
  title: string;
  summary: string;
  sections: Section[];
  edge_cases: string[];
}

export interface PolicyRow {
  version: string;
  source: string;
  status: "draft" | "published" | "retired";
  note: string;
  created_at: string;
  published_at: string | null;
  labeled?: number;
}

export interface Job {
  id: number;
  kind: string;
  policy_version: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  total: number;
  done: number;
  failed: number;
  detail: string;
  created_at: string;
  finished_at: string | null;
}

export interface PRF {
  precision: number;
  recall: number;
  f1: number;
  tp: number;
  fp: number;
  fn: number;
}

export interface Metrics {
  policy_version: string;
  n: number;
  n_scored: number;
  confusion: Record<LabelValue, { fraud: number; legit: number }>;
  strict: PRF;
  flagged: PRF;
  review_rate: number;
  sections: Record<string, { fraud: number; legit: number }>;
  tiers: Record<string, number>;
  escalation_rate: number;
  cost_per_1k_usd: number;
  total_cost_usd: number;
  /** Decisions made by a model with no list price (cost counted as $0). */
  cost_unpriced?: number;
  /** How the token counts were measured: "api", or "claude_cli" (overstates API cost). */
  cost_basis?: string[];
  latency_ms: { p50: number; p95: number; max: number };
}

export interface Comparison {
  from: string;
  to: string;
  compared: number;
  transitions: Record<string, number>;
  changed: { job_id: string; from: { label: LabelValue; sections: string[] }; to: { label: LabelValue; sections: string[] }; gold: number | null }[];
}

export type ProviderName = "anthropic" | "claude_cli" | "openai" | "openai_compat" | "slm" | "fake";

export interface RuntimeSettings {
  tier1_provider: ProviderName;
  tier2_provider: ProviderName;
  openai_base_url: string;
  compat_base_url: string;
  tier1_model: string;
  tier1_effort: string;
  tier2_model: string;
  tier2_effort: string;
  escalate_below_confidence: number;
  router_disagree_threshold: number;
  backfill_days: number;
  backfill_on_publish: boolean;
}

export interface SettingsDoc {
  values: RuntimeSettings;
  defaults: RuntimeSettings;
  overridden: (keyof RuntimeSettings)[];
  descriptions: Record<keyof RuntimeSettings, string>;
  choices: { models: string[]; efforts: string[]; providers: ProviderName[] };
  prices_per_mtok: Record<string, { input: number; output: number }>;
  provider: string;
}

/** Where one posting stands under one version. */
export interface LabelState {
  status: "queued" | "labeling" | "done" | "failed";
  run_id: number | null;
  error: string;
  updated_at: string;
}

export type WorkState = "queued" | "labeling" | "failed";

export interface WorkItem {
  posting_id: string;
  policy_version: string;
  status: WorkState;
  run_id: number | null;
  error: string;
  updated_at: string;
  title: string | null;
  location: string | null;
}

export interface WorkView {
  counts: Record<string, Partial<Record<WorkState, number>>>;
  total: number;
  items: WorkItem[];
  queues: Health["queues"];
  workers: WorkerInfo[];
  jobs: Job[];
}

export interface SecretInfo {
  name: string;
  set: boolean;
  source: string | null;
  hint: string | null;
}

export interface ProviderInfo {
  name: ProviderName;
  label: string;
  free: string | null;
  models: string[];
  presets: Record<string, string> | null;
  base_url_setting: "openai_base_url" | "compat_base_url" | "slm_base_url" | null;
  base_url: string | null;
  secret: SecretInfo | null;
  used_by: number[];
}

export interface ProviderTest {
  provider: string;
  ok: boolean;
  models: string[] | null;
  model: string | null;
  error?: string;
  note?: string;
  latency_ms?: number;
  reply?: unknown;
}

export interface VersionView {
  policy_version: string;
  status: PolicyRow["status"];
  result: LabelResult | null;
  /** When this version's decision was made (UTC ISO), or null if never labeled under it. */
  labeled_at: string | null;
  state: LabelState | null;
}

export interface WorkerInfo {
  worker_id: string;
  role: "standalone" | "embedded";
  concurrency: number;
  inflight: number;
  processed: number;
  failed: number;
  started_at: string;
  last_seen: string;
  /** "waiting": alive but taking no messages (e.g. no API key yet); `detail` says why. */
  status: "running" | "waiting";
  detail: string;
}

export interface Health {
  published_policy: string;
  tier1: string;
  tier2: string;
  provider: string;
  router: boolean;
  embedded_worker: boolean;
  workers: WorkerInfo[];
  queues: Record<string, { visible?: number; in_flight?: number; error?: string }>;
}

export interface InboxItem {
  job_id: string;
  title: string;
  location: string;
  snippet: string;
  has_company_profile: boolean;
  gold: number | null;
  is_eval: boolean;
  source: "eval set" | "imported" | "dataset";
  created_at: string;
  state: LabelState | null;
}

export type InboxScope = "inbox" | "eval" | "imported" | "all";

export interface InboxPage {
  policy_version: string;
  scope: InboxScope;
  total: number;
  counts: Record<InboxScope, number>;
  items: InboxItem[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  health: () => req<Health>("/api/health"),
  labels: (params: Record<string, string | number | boolean | undefined>) =>
    req<{ policy_version: string; total: number; items: ListItem[] }>(`/api/labels${qs(params)}`),
  posting: (id: string, version?: string) => req<PostingDetail>(`/api/postings/${encodeURIComponent(id)}${qs({ policy_version: version })}`),
  policies: () => req<PolicyRow[]>("/api/policies"),
  policy: (v: string) => req<PolicyRow & { parsed: PolicyDoc }>(`/api/policies/${encodeURIComponent(v)}`),
  validate: (source: string) =>
    req<{ ok: boolean; error?: string; parsed?: PolicyDoc; rendered?: string }>("/api/policies/validate", {
      method: "POST",
      body: JSON.stringify({ source }),
    }),
  createPolicy: (source: string, note: string) =>
    req<{ version: string }>("/api/policies", { method: "POST", body: JSON.stringify({ source, note }) }),
  updatePolicy: (v: string, source: string, note: string) =>
    req<{ version: string }>(`/api/policies/${encodeURIComponent(v)}`, { method: "PUT", body: JSON.stringify({ source, note }) }),
  deletePolicy: (v: string) => req<{ deleted: string }>(`/api/policies/${encodeURIComponent(v)}`, { method: "DELETE" }),
  publish: (v: string, opts: { backfill?: boolean; days?: number } = {}) =>
    req<{ version: string; backfill_job_id: number | null; backfill_error?: string }>(`/api/policies/${encodeURIComponent(v)}/publish`, {
      method: "POST",
      body: JSON.stringify(opts),
    }),
  relabel: (v: string, scope: string, opts: { limit?: number; days?: number; ids?: string[]; skip_labeled?: boolean } = {}) =>
    req<{ job_id: number }>(`/api/policies/${encodeURIComponent(v)}/relabel`, {
      method: "POST",
      body: JSON.stringify({ scope, ...opts }),
    }),
  relabelPreview: (v: string, days: number, skipLabeled = true) =>
    req<{ count: number; already_under_version: number; only_under_other_versions: number }>(
      `/api/policies/${encodeURIComponent(v)}/relabel/preview${qs({ scope: "recent", days, skip_labeled: skipLabeled ? "true" : "false" })}`,
    ),
  postingVersions: (id: string, versions: string[]) =>
    req<{ job_id: string; versions: VersionView[] }>(
      `/api/postings/${encodeURIComponent(id)}/versions?${versions.map((v) => `v=${encodeURIComponent(v)}`).join("&")}`,
    ),
  job: (id: number) => req<Job>(`/api/jobs/${id}`),
  cancelJob: (id: number) => req<{ cancelled_items: number }>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  requeueJob: (id: number) => req<{ job_id: number; resent: number }>(`/api/jobs/${id}/requeue`, { method: "POST" }),
  work: (p: { status?: WorkState; version?: string; limit?: number; offset?: number }) =>
    req<WorkView>(`/api/work${qs({ status: p.status, version: p.version, limit: p.limit, offset: p.offset })}`),
  inbox: (params: { scope: InboxScope; q?: string; limit?: number; offset?: number }) =>
    req<InboxPage>(`/api/inbox${qs(params)}`),
  importPostings: (postings: Partial<Posting>[]) =>
    req<{ stored: number; new: number; updated: number }>("/api/inbox/import", {
      method: "POST",
      body: JSON.stringify({ postings }),
    }),
  labelInbox: (body: { ids?: string[]; all_in_scope?: boolean; scope?: InboxScope; q?: string }) =>
    req<{ job_id: number; count: number; policy_version: string }>("/api/inbox/label", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  settings: () => req<SettingsDoc>("/api/settings"),
  updateSettings: (values: Partial<RuntimeSettings>) =>
    req<SettingsDoc>("/api/settings", { method: "PUT", body: JSON.stringify({ values }) }),
  resetSettings: () => req<SettingsDoc>("/api/settings", { method: "DELETE" }),
  providers: () => req<{ secrets_backend: string; providers: ProviderInfo[] }>("/api/settings/providers"),
  putSecret: (name: string, value: string) =>
    req<SecretInfo>(`/api/settings/secrets/${name}`, { method: "PUT", body: JSON.stringify({ value }) }),
  deleteSecret: (name: string) => req<SecretInfo>(`/api/settings/secrets/${name}`, { method: "DELETE" }),
  testProvider: (name: string, model?: string) =>
    req<ProviderTest>(`/api/settings/providers/${name}/test`, { method: "POST", body: JSON.stringify({ model: model || null }) }),
  jobs: () => req<Job[]>("/api/jobs"),
  metrics: (v?: string) => req<Metrics>(`/api/metrics${qs({ policy_version: v })}`),
  metricRuns: () => req<{ version: string; eval_decisions: number }[]>("/api/metrics/runs"),
  compare: (a: string, b: string) => req<Comparison>(`/api/compare${qs({ a, b })}`),
};
