# RESULTS

What was measured, on what, and how far each number can be trusted. Every table, case
and experiment in full: [docs/results-details.md](docs/results-details.md).

## Summary

The fixed eval set: 400 postings (100 fraud, 300 legitimate), policy v1. There are **two
runs** on it, and the names are used the same way everywhere, including the UI:

- **Run 1: first prompt (the headline).** The prompt as first written, before anything was
  learned from the eval set. In the UI: `v1`.
- **Run 2: the code as submitted.** It runs after a prompt fix that eval errors prompted
  (§3), so it is shown **next to** Run 1, never instead of it. In the UI: `v1@current`.

NEEDS_REVIEW is scored both ways. **Strict** counts only VIOLATING as a fraud call (what the
system blocks by itself). **Flagged** counts VIOLATING or NEEDS_REVIEW (blocked, or seen by
an analyst).

| | Run 1: first prompt | Run 2: submitted code |
|---|---|---|
| strict P / R / F1 | 0.95 / 0.19 / 0.32 | 0.96 / 0.22 / 0.36 |
| flagged P / R / F1 | 0.68 / 0.44 / 0.53 | 0.45 / 0.73 / 0.56 |
| review rate · escalated to tier 2 | 11% · 38% | 35% · 14% |
| cost per 1,000 postings, API prices (§6) | ≈ $6.2 | ≈ $2.55 (≈ $1.53 with Sonnet as tier 2) |
| latency p50 / p95 | 5.7 s / 17.4 s | 5.9 s / 14.3 s |

- **Gold labels are not the policy.** 29 of the 56 frauds Run 1 allowed break no written
  rule (§2). Recall against EMSCAD is capped by what the policy defines.
- **Verification found no invented posting text.** Of 381 Haiku quotes, 8 were rejected,
  and none was a fabricated quote of the posting (§4). Only verified quotes can make a label
  VIOLATING.
- **Escalation is the cost lever.** Tier 2 is ≈30× tier 1 per call. Escalation fell from 38%
  to 14% between the runs, and cost per posting fell ≈60% (§6).

## How far to trust these numbers

- **No API key was used for the runs.** All model calls went through the local `claude`
  CLI: same models (Haiku 5.5 → Opus 5.5), prompt, schema and verification as the API path.
  **Quality numbers are representative; cost and latency are not.** CLI token counts
  overstate API cost: the Evaluation page shows them as *CLI token counts* ($8.55 and $3.36
  per 1,000), and §6 converts them to API prices.
- **The API path was exercised once**: one Haiku call from the UI. Structured output parsed,
  label VIOLATING, 3.7 s, and the 1,980-token system prompt was written to cache. Cache
  reads, Opus as tier 2 and effort levels on the API are not measured.
- **Run 1 is reproducible from the repo.** The first prompt is the current one minus the
  change shown in §3. Its decisions with traces, and Run 2's, are in
  `data/seed/seed.json.gz`, which a fresh `docker compose up` loads. The raw outputs are in
  `data/eval/`. The repo was squashed to one commit before submission, so no hash is cited.

## 1. The two runs

| predicted ↓ / gold → | Run 1: fraud | Run 1: legit | Run 2: fraud | Run 2: legit |
|---|---|---|---|---|
| **VIOLATING** | 19 | 1 | 22 | 1 |
| **NEEDS_REVIEW** | 25 | 20 | 51 | 89 |
| **ALLOWED** | 56 | 279 | 27 | 210 |

- **Automatic enforcement is precise in both runs:** 1 false VIOLATING in 300 legitimate
  postings, and that one is arguably policy-correct (§2).
- **Run 1 misses frauds without a company profile.** The model read "missing fields are
  weak signals" as "§5 does not apply". §3 fixes that reading.
- **Run 2 halves the missed frauds** (56 → 27; flagged recall 0.44 → 0.73), and it costs
  review work. §5 as written sends 89 of 300 legitimate postings to an analyst, because
  many legitimate employers also leave the profile empty. That is the policy's trade-off,
  not the model's. `policies/v3.yaml` narrows §5 (§5.1).

### 1.1 Run 2 in detail

Run 2 uses the fixed prompt, the 1,000-row router and the policy's combination rule
rendered for the model (`data/eval/current-v1_current.json`). Escalation fell from 38% to
14%, so cost per 1,000 fell by 60%, and p95 latency improved.

**Against a plain classifier.** A TF-IDF router alone scores 0.92 / 0.89 / 0.90 against gold
at a 0.5 threshold. It learns the *style* of EMSCAD frauds; it cannot name the rule broken
or quote proof. Part of its score is leakage: 43 of the 100 eval frauds have a near-identical
description in its training data. That is why it routes and never labels.

## 2. Error analysis (Run 1)

The 56 frauds labeled ALLOWED are two different problems:

| | count | what it is |
|---|---|---|
| Fraud with a company profile and no policy-defined behaviour | 29 | **Policy coverage limit.** E.g. **1205**: named employer, $60–90k, "apply on the platform". ALLOWED is policy-correct. |
| Fraud with **no company profile** where the model declined §5 | 27 | **Prompt problem**, fixed in §3. E.g. **2617**, **1663**, **7481**. |

A second model does not fix either one. The router flagged 50 of the 56, and Opus agreed
that no section applied.

- **17761** impersonates a real employer with a real-looking profile and plausible pay;
  v1 has no section for that.
- **3612** is commission-only sales: the brief's own edge case makes it ALLOWED, and gold
  says fraud.
- **3174**, the only false VIOLATING, asks for "capital investment" to start: arguably §1.

**Where misses cluster (development data).** Of 149 frauds the teacher allowed on 1,000 dev
postings, 63 are cloned listings, for example "Aker Solutions" postings copied under other
titles and cities. Duplication alone is not a signal: 157 of 500 legitimate postings are
also cloned. The fix is a policy section plus a tool that finds the original listing.
[All nine eval cases and two illustrative ones](docs/results-details.md#2-error-analysis).

## 3. The prompt fix

The policy was **not** changed. One instruction was added to step 4 of the prompt, and
`ESCALATE_BELOW_CONFIDENCE` went from 0.7 to 0.6, because Haiku reports 0.5–0.6 even on
clear ALLOWED cases:

```diff
 4. Missing information is not a quote. If a section rests on something being
    absent (for example an empty company profile), list it under `facts` using
    only the names given in the facts block. Facts alone are weak evidence and can
    never be the sole basis for a section whose effect is VIOLATING.
+   They are exactly what review-only sections are for: when a review-only
+   section names a condition (for example "no company profile") and the facts
+   block shows it is true, report that section with the fact, unless the
+   posting text itself clearly satisfies what the section asks for (for
+   example, it names and describes the employer). Policy guidance that missing
+   fields are "weak signals" limits what can be VIOLATING; it does not stop a
+   review-only section from matching.
```

Later the two quoted examples were made generic ("when a review-only section's text names a
condition that the facts block shows is true"), because nothing section-specific may live in
the prompt. Run 2 uses that wording. [Both diffs, and the two later code changes to step 4](docs/results-details.md#31-change).

### 3.2 Checked on dev postings only

The fix was developed on development postings, where the same failure appears. On the same
16 dev postings before → after: frauds flagged 6/8 → 7/8, legitimate VIOLATING 0 → 0,
legitimate in review 0 → 1, escalation 31% → 6%. On 10 dev postings with no profile, §5 was
applied on 9; the tenth names its employer, so ALLOWED is right.
[Tables](docs/results-details.md#32-checked-on-dev-postings-only).

## 4. Evidence: does Haiku fabricate quotes?

Every quote in every stored call (463 decisions, 633 calls):

| | Haiku 5.5 | Opus 5.5 |
|---|---|---|
| quotes returned | 381 | 81 |
| found verbatim, or after normalising case / whitespace / quote marks | 373 | 81 |
| **rejected** | **8** | 0 |

The 8 rejections: 7 quoted the prompt's own scaffolding (metadata lines, empty-field
markers), and 1 was a false rejection on an HTML entity, since fixed. **No invented posting
content in 462 quotes.** The errors that did occur were omissions and harsh readings, which
verification is not designed to catch (DESIGN.md §4).

v1 is kept verbatim from the brief, so it does not declare `accepts_facts`. That is safe:
VIOLATING always needs a verified quote, and in Run 2, 162 of the 163 §5 decisions have a §5
quote. [Per-quote table](docs/results-details.md#4-evidence-does-haiku-fabricate-quotes-and-does-verification-work).

## 5. Policy v2 demo

`policies/v2.yaml` adds §6 *Vague home-based clerical work* (review only) and one edge case,
with no code change: the section reaches the prompt, the schema enum, the label rule and the
UI from the document. On 12 dev postings picked to exercise it (`make demo-v2-dev`):
- 2 home-based clerical frauds move from NEEDS_REVIEW to VIOLATING (§5 + §6);
- 2 more add §6 to an existing VIOLATING;
- none of the 6 office clerical jobs at named employers is touched.

On a 20-posting eval shadow-run (`make demo-v2`), §6 matched nothing. Four labels still
changed between runs, all on §5. That is model inconsistency, which is why a shadow-run diff
must be read against a same-policy rerun (DESIGN.md §5). v2's eval numbers are not held out:
phrase counts were printed on the eval set once while choosing it.
[Full tables](docs/results-details.md#5-policy-v2-demo).

### 5.1 v3: a policy fix found on eval, checked on dev

`policies/v3.yaml` narrows §5 to postings that never say who is hiring. Its text is a
general rule, not fitted to eval cases. It is demonstrated on six **dev** postings (README),
and a v3 run on the eval set would not be held out. The dev measurement is not done yet.

## 6. Cost and latency

Measured via the CLI (Run 1: 400 Haiku calls, 151 Opus calls):

| | tier 1 (Haiku) | tier 2 (Opus) |
|---|---|---|
| output tokens per call (incl. thinking) | 824 | 583 |
| latency per call (mean) | 5.7 s | 7.5 s |

- **Latency per posting:** Run 1 p50 5.7 s, p95 17.4 s, max 24.7 s. Everything is under
  the 60 s target, including ≈1 s of CLI start-up per call.
- **Cost.** CLI token counts give $8.55 (Run 1) and $3.36 (Run 2) per 1,000 postings. That
  overstates the API, because the CLI writes its own ≈1.8k-token wrapper to cache on every
  call. **At API prices**, from the measured output tokens and a cached system prompt:
  tier 1 ≈ $0.50 and tier 2 ≈ $15 per 1,000 calls, which gives **≈ $6.2 (Run 1, 38%
  escalated)** and **≈ $2.55 (Run 2, 14%)** per 1,000 postings, or ≈ $1.53 with Sonnet as
  tier 2.

## 7–9. Other experiments

| | result | verdict |
|---|---|---|
| **7. Router on 1,000 balanced rows** instead of 17,480 | AP 0.976 → 0.950; replayed escalation 37.8% → 39.2% (no model calls) | default now: trains in seconds, closer to a realistic golden-set budget |
| **8. Few-shot examples** (`policies/v1.1.yaml`), 60 held-out dev postings | flagged recall 0.63 → 0.57, escalation 18% → 27% | not adopted |
| **9. Distilled 1.5B student** (Qwen2.5 + LoRA, trained on Claude's verified dev labels) | flagged F1 0.57 vs 0.56 for the cascade; strict recall 0.12 vs 0.22; 1.6 s, $0 per call; quotes rejected 4% (60% before distillation) | kept as an option for tier 1; needs retraining per policy version |

Details: [§7](docs/results-details.md#7-router-1000-balanced-training-rows-instead-of-17480),
[§8](docs/results-details.md#8-few-shot-examples-tried-on-dev-not-adopted),
[§9](docs/results-details.md#9-distilled-small-model-nice-to-have-measured-kept-as-an-option).

## 10. Reproduce

```bash
make eval                                   # all 400, published policy, resumable
LLM_PROVIDER=claude_cli make eval           # same, without an API key
uv run python -m scripts.run_eval --version v1 --tag current --provider claude_cli   # Run 2
make demo-v2                                # draft v2 → shadow-run on eval → compare → publish
make demo-v2-dev                            # v1 vs v2 on 12 dev postings where §6 applies
uv run python -m scripts.router_compare     # §7, no model calls
```

§8 and §9 commands, including the Apple-silicon model server: [docs/results-details.md §10](docs/results-details.md#10-reproduce).
