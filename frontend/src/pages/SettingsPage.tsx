import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api, type ProviderInfo, type ProviderName, type ProviderTest, type RuntimeSettings, type SettingsDoc } from "../lib/api";
import { Eyebrow } from "../components/ui";

type Key = keyof RuntimeSettings;
type NumKey = "escalate_below_confidence" | "router_disagree_threshold" | "backfill_days";

/** Run-time settings: who serves each tier (provider + model), when to escalate, how far a
 *  backfill reaches, and the providers' keys and URLs. Saved server-side; the API applies them
 *  at once, workers within a few seconds. Keys are write-only: only their last 4 characters show. */
export function SettingsPage({ onSaved }: { onSaved: () => void }) {
  const [doc, setDoc] = useState<SettingsDoc | null>(null);
  const [draft, setDraft] = useState<RuntimeSettings | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [secretsBackend, setSecretsBackend] = useState("");
  const [liveModels, setLiveModels] = useState<Partial<Record<ProviderName, string[]>>>({});

  const load = (d: SettingsDoc) => {
    setDoc(d);
    setDraft(d.values);
    setTyped({});
    setBad({});
  };
  const loadProviders = useCallback(() => {
    api
      .providers()
      .then((r) => {
        setProviders(r.providers);
        setSecretsBackend(r.secrets_backend);
      })
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    api.settings().then(load).catch((e) => setMsg({ kind: "err", text: String(e.message ?? e) }));
    loadProviders();
  }, [loadProviders]);

  const [typed, setTyped] = useState<Partial<Record<NumKey, string>>>({});
  const [bad, setBad] = useState<Partial<Record<NumKey, string>>>({});
  if (!doc || !draft) return <div className="settings">{msg ? <div className="error-box">{msg.text}</div> : <div className="empty">Loading…</div>}</div>;

  const set = <K extends Key>(k: K, v: RuntimeSettings[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d));
  const changed = (Object.keys(draft) as Key[]).filter((k) => draft[k] !== doc.values[k]);

  const save = async () => {
    setMsg(null);
    try {
      const patch = Object.fromEntries(changed.map((k) => [k, draft[k]])) as Partial<RuntimeSettings>;
      load(await api.updateSettings(patch));
      setMsg({ kind: "ok", text: "Saved. The API uses it now; workers pick it up within a few seconds." });
      loadProviders();
      onSaved();
    } catch (e) {
      setMsg({ kind: "err", text: String((e as Error).message ?? e) });
    }
  };
  const reset = async () => {
    if (!confirm("Drop every override and go back to the environment defaults?")) return;
    setMsg(null);
    try {
      load(await api.resetSettings());
      setMsg({ kind: "ok", text: "Back to the environment defaults." });
      loadProviders();
      onSaved();
    } catch (e) {
      setMsg({ kind: "err", text: String((e as Error).message ?? e) });
    }
  };

  const info = (name: ProviderName) => providers.find((p) => p.name === name);
  const modelsFor = (name: ProviderName) => [...new Set([...(info(name)?.models ?? []), ...(liveModels[name] ?? [])])];
  const price = (provider: ProviderName, m: string) => {
    const p = doc.prices_per_mtok[m];
    if (p) return `$${p.input} in / $${p.output} out per 1M tokens`;
    return provider === "slm" || provider === "claude_cli" ? "no per-token bill" : "price not in the table: cost shows as $0";
  };

  const tier = (t: 1 | 2) => {
    const pk = `tier${t}_provider` as const;
    const mk = `tier${t}_model` as const;
    const ek = `tier${t}_effort` as const;
    const listId = `models-${t}`;
    return (
      <>
        <Row doc={doc} k={pk} label={`Tier ${t} provider`}>
          <select value={draft[pk]} onChange={(e) => set(pk, e.target.value as ProviderName)}>
            {doc.choices.providers.map((p) => (
              <option key={p} value={p}>
                {info(p)?.label ?? p}
              </option>
            ))}
          </select>
        </Row>
        <Row doc={doc} k={mk} label={t === 1 ? "Tier 1 model" : "Tier 2 model (escalations)"} hint={draft[mk] ? price(draft[pk], draft[mk]) : ""}>
          <input
            className="text"
            list={listId}
            value={draft[mk]}
            placeholder={t === 2 ? "empty = no escalation" : "model id"}
            onChange={(e) => set(mk, e.target.value.trim())}
          />
          <datalist id={listId}>
            {modelsFor(draft[pk]).map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </Row>
        <Row doc={doc} k={ek} label={`Tier ${t} effort`}>
          <select value={draft[ek]} onChange={(e) => set(ek, e.target.value)}>
            {doc.choices.efforts.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </Row>
      </>
    );
  };
  // Number fields keep what is typed; an empty or out-of-range value is flagged and blocks Save,
  // instead of silently becoming 0 (0 here would turn escalation off).
  const number = (k: NumKey, step: number, min: number, max: number) => (
    <>
      <input
        type="number"
        step={step}
        min={min}
        max={max}
        value={typed[k] ?? draft[k]}
        aria-invalid={k in bad}
        onChange={(e) => {
          const raw = e.target.value;
          setTyped((t) => ({ ...t, [k]: raw }));
          const v = Number(raw);
          if (raw.trim() === "" || !Number.isFinite(v) || v < min || v > max) {
            setBad((b) => ({ ...b, [k]: `enter a number from ${min} to ${max}` }));
          } else {
            setBad((b) => {
              const next = { ...b };
              delete next[k];
              return next;
            });
            set(k, v);
          }
        }}
      />
      {bad[k] && <span className="field-error"> {bad[k]}</span>}
    </>
  );

  return (
    <div className="settings">
      <div className="report-head">
        <div>
          <Eyebrow>Run-time settings · keys kept in: {secretsBackend || "…"}</Eyebrow>
          <h2 className="h-serif">Settings</h2>
        </div>
        <div className="btn-row">
          <button className="btn" onClick={reset} disabled={doc.overridden.length === 0}>
            Reset to defaults
          </button>
          <button
          className="btn primary"
          onClick={save}
          disabled={changed.length === 0 || Object.keys(bad).length > 0}
          title={Object.keys(bad).length ? "fix the highlighted fields first" : undefined}
        >
            Save{changed.length ? ` (${changed.length})` : ""}
          </button>
        </div>
      </div>
      {msg && <div className={msg.kind === "err" ? "error-box" : "note-box"}>{msg.text}</div>}

      <section>
        <Eyebrow>Models</Eyebrow>
        {tier(1)}
        {tier(2)}
      </section>

      <section>
        <Eyebrow>Model providers · keys, endpoints, connection test</Eyebrow>
        <div className="providers">
          {providers
            .filter((p) => p.name !== "fake")
            .map((p) => (
              <ProviderCard
                key={p.name}
                p={p}
                baseUrl={p.base_url_setting && p.base_url_setting !== "slm_base_url" ? draft[p.base_url_setting] : p.base_url}
                onBaseUrl={p.base_url_setting && p.base_url_setting !== "slm_base_url" ? (v) => set(p.base_url_setting as "openai_base_url" | "compat_base_url", v) : undefined}
                defaultModel={draft.tier1_provider === p.name ? draft.tier1_model : draft.tier2_provider === p.name ? draft.tier2_model : p.models[0] ?? ""}
                unsavedUrl={!!p.base_url_setting && p.base_url_setting !== "slm_base_url" && draft[p.base_url_setting] !== doc.values[p.base_url_setting]}
                onModels={(ms) => setLiveModels((x) => ({ ...x, [p.name]: ms }))}
                onChanged={loadProviders}
              />
            ))}
        </div>
      </section>

      <section>
        <Eyebrow>Escalation</Eyebrow>
        <Row doc={doc} k="escalate_below_confidence" label="Escalate below confidence">
          {number("escalate_below_confidence", 0.05, 0, 1)}
        </Row>
        <Row doc={doc} k="router_disagree_threshold" label="Router disagreement threshold">
          {number("router_disagree_threshold", 0.05, 0, 1)}
        </Row>
      </section>

      <section>
        <Eyebrow>Backfill after a policy change</Eyebrow>
        <Row doc={doc} k="backfill_days" label="Backfill window (days)">
          {number("backfill_days", 1, 1, 365)}
        </Row>
        <Row doc={doc} k="backfill_on_publish" label="Backfill on publish">
          <label className="check">
            <input type="checkbox" checked={draft.backfill_on_publish} onChange={(e) => set("backfill_on_publish", e.target.checked)} />
            <span>Tick "re-label" by default in the publish dialog</span>
          </label>
        </Row>
      </section>

      <div className="note-box">
        Keys typed here go to the secret store ({secretsBackend || "…"}): encrypted in the database locally, AWS Secrets
        Manager in production, never returned by the API. A key in <span className="mono">.env</span> works too and is
        used when nothing is stored here. Database, queues and buckets are environment settings and need a restart.
        Decisions record the provider and model that made them, so a change never rewrites past labels.
      </div>
    </div>
  );
}

function ProviderCard({
  p,
  baseUrl,
  onBaseUrl,
  defaultModel,
  unsavedUrl,
  onModels,
  onChanged,
}: {
  p: ProviderInfo;
  baseUrl: string | null;
  onBaseUrl?: (v: string) => void;
  defaultModel: string;
  unsavedUrl: boolean;
  onModels: (ms: string[]) => void;
  onChanged: () => void;
}) {
  const [key, setKey] = useState("");
  const [model, setModel] = useState(defaultModel);
  const [test, setTest] = useState<ProviderTest | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setModel(defaultModel), [defaultModel]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`card provider${p.used_by.length ? " in-use" : ""}`}>
      <div className="provider-head">
        <strong>{p.label}</strong>
        {p.used_by.length > 0 && <span className="state done">tier {p.used_by.join(" + ")}</span>}
      </div>
      {p.free && <div className="small muted">{p.free}</div>}

      {p.secret && (
        <div className="provider-row">
          <span className="small mono">{p.secret.name}</span>
          <span className="small">{p.secret.set ? `set ${p.secret.hint} · ${p.secret.source}` : "not set"}</span>
          <input className="text" type="password" autoComplete="off" placeholder="paste a key" value={key} onChange={(e) => setKey(e.target.value)} />
          <button
            className="btn"
            disabled={!key || busy}
            onClick={() =>
              run(async () => {
                await api.putSecret(p.secret!.name, key);
                setKey("");
                onChanged();
              })
            }
          >
            Save key
          </button>
          {p.secret.source?.startsWith("settings") || p.secret.source === "AWS Secrets Manager" ? (
            <button className="btn danger" disabled={busy} onClick={() => run(async () => (await api.deleteSecret(p.secret!.name), onChanged()))}>
              Remove
            </button>
          ) : null}
        </div>
      )}

      {baseUrl !== null && (
        <div className="provider-row">
          <span className="small mono">base URL</span>
          {onBaseUrl ? (
            <>
              <input className="text wide" value={baseUrl} onChange={(e) => onBaseUrl(e.target.value.trim())} />
              {p.presets && (
                <select value="" onChange={(e) => e.target.value && onBaseUrl(e.target.value)}>
                  <option value="">presets…</option>
                  {Object.entries(p.presets).map(([n, u]) => (
                    <option key={n} value={u}>
                      {n}
                    </option>
                  ))}
                </select>
              )}
              {unsavedUrl && <span className="small" style={{ color: "var(--review)" }}>unsaved: Save above, then test</span>}
            </>
          ) : (
            <span className="small mono">{baseUrl}</span>
          )}
        </div>
      )}

      <div className="provider-row">
        <span className="small mono">test</span>
        <input className="text" placeholder="model (optional)" value={model} onChange={(e) => setModel(e.target.value.trim())} />
        <button
          className="btn"
          disabled={busy || unsavedUrl}
          onClick={() =>
            run(async () => {
              const r = await api.testProvider(p.name, model || undefined);
              setTest(r);
              if (r.models) onModels(r.models);
            })
          }
        >
          {busy ? "Testing…" : "Test connection"}
        </button>
      </div>
      {test && (
        <div className={test.ok ? "note-box" : "error-box"}>
          {test.ok ? "Connected" : "Failed"}
          {test.latency_ms !== undefined ? ` · ${test.latency_ms} ms` : ""}
          {test.models ? ` · ${test.models.length} models listed (now offered in the model fields)` : ""}
          {test.note ? ` · ${test.note}` : ""}
          {test.error ? `: ${test.error}` : ""}
        </div>
      )}
      {err && <div className="error-box">{err}</div>}
    </div>
  );
}

function Row({ doc, k, label, children, hint }: { doc: SettingsDoc; k: Key; label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="setting">
      <div>
        <strong>{label}</strong>
        {doc.overridden.includes(k) && <span className="overridden">changed</span>}
        <div className="small muted">default: {String(doc.defaults[k]) || "off"}</div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div>{children}</div>
        <div className="small muted">
          {doc.descriptions[k]}
          {hint ? ` ${hint}` : ""}
        </div>
      </div>
    </div>
  );
}
