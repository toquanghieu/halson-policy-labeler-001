# RESULTS: the long form

Everything behind [RESULTS.md](../RESULTS.md), section for section: every confusion matrix,
the full error analysis, the per-quote verification table, the v2 demo, the router,
few-shot and distillation experiments. The two eval runs are named as in RESULTS.md:
**Run 1** = first prompt, the held-out headline (`v1` in the UI); **Run 2** = the code as
submitted (`v1@current`). Links to `DESIGN.md` and `RESULTS.md` below are relative to the repository root.

## Status of these numbers (read first)

- **No API key was used.** All model calls went through the local `claude` CLI
  (`LLM_PROVIDER=claude_cli`): same models (Haiku 5.5 tier 1, Opus 5.5 tier 2),
  same prompt, same schema, same verification as the API path. **Quality
  numbers are representative. Cost and latency are not** (see §6).
- **The API path, as far as it was exercised:** one real Anthropic API call (Haiku 5.5,
  posting `q-1`, made from the UI after a key was added). Structured output parsed, 0 quotes
  rejected, label VIOLATING, 3.7 s. Usage: 322 input, 336 output, **1,980 tokens written to
  cache**, so the system prompt is above the caching minimum and does cache. Cost: $0.00045.
  The same call reading the cache would be ≈ $0.00022. Not exercised: cache *reads*
  (they need a second call within 5 minutes), Opus as tier 2, the fallbacks beta, and
  effort levels on the API. No further API runs were made, on purpose.
- **Headline = all 400 eval postings, policy v1, first prompt** (escalation below
  0.7). The first prompt is the current one minus the change shown in full in §3.1,
  so it can be rebuilt from this repo. The repository was squashed to a single
  commit before submission, so no commit hash is cited. The run was made in two
  parts (94, then the remaining 306) with the same code. 0 failures. Its raw
  results are in `data/eval/main-v1.json`, and its decisions with traces in `data/seed/seed.json.gz`,
  which a fresh `docker compose up` loads.
- Inspecting the first 94 exposed a prompt problem (§3). The assignment says
  not to tune prompts on the eval set, and this fix was *prompted* by eval
  errors, so it is disclosed as such. Its wording is generic (no eval text, no
  section ids), and it was developed and checked on **dev-set** postings only
  (§3.2, where the same failure also appears).
- **Post-hoc: the code as submitted, on the same 400** (§1.1). This run uses the fixed
  prompt, the 1,000-row router, and the policy's combination rule rendered for the
  model. It answers "what does the code you are running score?". Because the prompt
  fix was prompted by eval errors, this number is shown beside the headline and never
  instead of it.

## 1. Eval set: 400 postings, policy v1, first prompt

Confusion matrix, NEEDS_REVIEW counted explicitly:

| predicted ↓ / gold → | fraud (100) | legit (300) |
|---|---|---|
| **VIOLATING** | 19 | 1 |
| **NEEDS_REVIEW** | 25 | 20 |
| **ALLOWED** | 56 | 279 |

**How NEEDS_REVIEW is treated.** Scored both ways, because the right answer
depends on what the number is for:

| scoring | precision | recall | F1 |
|---|---|---|---|
| **strict**: only VIOLATING counts as positive (what is enforced automatically) | **0.95** | 0.19 | 0.32 |
| **flagged**: VIOLATING + NEEDS_REVIEW positive (what a human or the system acts on) | 0.68 | **0.44** | 0.53 |
| *router alone, threshold 0.5 (no policy, no evidence, routing only)* | 0.92 | 0.89 | 0.90 |

- Review rate 11.3% (45 of 400 go to an analyst).
- Escalation: 151 of 400 (37.8%) reached tier 2.
- Sections cited: §5 on 65 postings (44 fraud / 21 legit), §4 on 14 (13 / 1),
  §2 on 8 (8 / 0), §1 on 2, §3 on 1.
- **Automatic enforcement is precise: 1 false VIOLATING in 300 legit postings**,
  and that one is arguably policy-correct (case 6 below).

### 1.1 Post-hoc: the code as submitted (not the headline)

Same 400 postings, same models (Haiku 5.5 → Opus 5.5 via the CLI). The run is stored
under `v1@current`, beside the headline run (`data/eval/current-v1_current.json`).

| predicted ↓ / gold → | fraud (100) | legit (300) |
|---|---|---|
| **VIOLATING** | 22 | 1 |
| **NEEDS_REVIEW** | 51 | 89 |
| **ALLOWED** | 27 | 210 |

| | headline (first prompt) | **post-hoc (current code)** |
|---|---|---|
| strict P / R / F1 | 0.95 / 0.19 / 0.32 | **0.96 / 0.22 / 0.36** |
| flagged P / R / F1 | 0.68 / 0.44 / 0.53 | **0.45 / 0.73 / 0.56** |
| frauds labeled ALLOWED | 56 | **27** |
| legit sent to review | 20 | **89** |
| review rate | 11% | **35%** |
| escalated to tier 2 | 38% | **14%** |
| cost / 1k (CLI token counts) | $8.55 | **$3.36** |
| latency p50 / p95 | 5.7 s / 17.4 s | **5.9 s / 14.3 s** |

What changed and what it costs:
- **The §5 fix worked as intended.** Missed frauds halved (56 → 27), and flagged recall
  rose from 0.44 to 0.73. The postings it rescued are the "no company profile" frauds of
  error analysis case 1, which now go to review.
- **§5 applied as written is expensive in review work.** 89 of the 300 legitimate
  postings now go to an analyst, against 20 before, because many legitimate postings also
  lack a company profile. Flagged precision fell from 0.68 to 0.45. This is the policy's
  trade-off, not the model's. Narrowing §5 (for example, "no profile *and* the employer
  is not named anywhere") would be a v-next policy change, shadow-run before publishing.
- **Automatic enforcement is unchanged.** Still 1 false VIOLATING out of 300.
- **Cheaper and faster.** Escalation fell from 38% to 14%, so cost per 1,000 fell by 60%.
  p95 latency improved.

**How to read this.** EMSCAD gold means "this posting was a fraud", not "this
posting breaks the written policy". The policy only defines specific
behaviours (fees, data harvesting, money handling, implausible pay,
unverifiable employer). The router beats the agent against gold because it
learns the *style* of EMSCAD frauds; it cannot say which rule was broken or
quote proof, and part of its score is leakage (43 of the 100 eval frauds have a
near-identical description in the dev set it was trained on). That is why it
routes and never labels. Where it disagrees with an ALLOWED it already forces
a second opinion; in production its disagreement is also the cue for an
analyst sample (DESIGN.md §5).

## 2. Error analysis

The 56 frauds labeled ALLOWED split into two different problems:

| | count | what it is |
|---|---|---|
| Fraud with a company profile and no policy-defined behaviour | 29 | Policy coverage limit. Example **1205** "Health + Environmental Professional": named employer, $60–90k, "apply on the platform". Nothing in §1–§5 applies; ALLOWED is the policy-correct answer. |
| Fraud with **no company profile** where the model declined §5 | 27 | **Prompt problem**, fixed in §3. Examples **1663**, **2617**, **2740**, **3056**. |

The router scored 50 of the 56 at ≥ 0.5, so they did get a tier-2 second
opinion; Opus agreed that no section applied. A second model does not fix a
policy gap, and it repeated the same §5 misreading.

**Misclassified on the eval set** (headline run; "wrong" means a fraud not labeled
VIOLATING, or a legitimate posting that was flagged):

1. **2617** (fraud → ALLOWED). "Developer and Database Administrator". The company profile
   is empty, there is no logo, and the employer is never named. The model said "the only weak
   sign is the empty company profile, so no section applies". That is wrong under §5 ("the
   employer cannot be identified: no company profile…"). The model read the edge case
   "missing fields are weak signals and must not be the sole basis for VIOLATING" as "not
   enough for §5", but that edge case only limits VIOLATING. This is the prompt problem
   fixed in §3; 27 of the 56 misses are this kind.
2. **7481** (fraud → ALLOWED). "Operating Room RN". Empty profile, and the posting says
   *"Apply using below link"* to an off-site URL. Same §5 misreading as 2617. A reading of
   §2 ("directs applicants to off-platform channels") was also available. 7483 and 8574
   are near-identical nurse postings with the same outcome.
3. **1205** (fraud → ALLOWED). "Health + Environmental Professional": named employer,
   $60–90k, "apply on the platform". Nothing in §1–§5 applies, so ALLOWED is the
   policy-correct answer. Fixing this needs a new policy section, not a better model.
   29 of the 56 misses are this kind.
4. **17761** (fraud → ALLOWED). "Payroll Clerk" at "DRS Technologies", with a detailed,
   real-looking company profile and a plausible $53–67k. The posting impersonates a real
   employer. v1 has no section for impersonation or cloned listings, so ALLOWED is again
   policy-correct.
5. **3612** (fraud → ALLOWED). "Account Sales Managers $80-$130,000/yr", a commission-only
   sales role. The model applied the brief's own edge case ("commission-only sales roles
   are ALLOWED if the compensation structure is stated plainly"). Here the policy and the
   gold label disagree.
6. **3174** (legit → VIOLATING, §1+§4+§5). The only false VIOLATING in 300 legitimate
   postings. Evidence: *"starting your own business requires capital investment both
   initially and on an ongoing basis"* and *"Serious income goal of $100k plus p.a"* for
   15–20 h/week. Arguably policy-correct: §1 forbids requiring payment to join, even though
   EMSCAD calls the posting legitimate.
7. **2278** (fraud → NEEDS_REVIEW §5 under v1, ALLOWED in the v2 shadow run). The posting
   text is identical in both runs, and §6 did not apply. This is run-to-run inconsistency
   on the same §5 judgement (see §5 below).
8. **177 / 569** (legit → NEEDS_REVIEW §5). "English Teacher Abroad": the employer is never
   named and the profile is one generic line. Wrong only under flagged scoring; under §5 as
   written, review is the right routing.
9. **863** (legit → NEEDS_REVIEW §5). A staffing-agency posting for an unnamed "Leading
   global retailer". Correct under §5 as written, and it shows the cost of §5: agency
   postings will fill the review queue.

**Where the misses cluster (development data, no eval tuning).**
- On the 1,000 teacher-labeled development postings (the distillation teacher run, §9), 149 frauds were labeled
  ALLOWED.
- 63 of them share their description opening with at least 3 other postings in the
  dataset, and 58 of those clusters are mostly fraudulent. Examples: "Aker Solutions",
  "AECOM" and "Expro" postings copied word for word under other titles and cities.
  These are cloned listings impersonating real employers.
- Duplication alone is not a fraud signal: 157 of the 500 legitimate postings are also in
  such clusters, because agencies reuse text.
- The fix is a policy section (impersonation or a cloned listing) plus a tool that finds
  the original listing. A prompt cannot see the rest of the marketplace.
- The router partly learns these clusters from the gold labels, which is also why it scores
  higher against gold than the agent does.

**Other illustrative cases (not eval errors).**
- **3191** (eval, fraud, VIOLATING §4+§5) is a true positive. Quotes: *"earn up to $2500
  and more per week"*, *"You do NOT need any special skills to get started"*, and no
  identifiable employer.
- **258** (development set, legit, VIOLATING §2+§5, after the prompt fix) asks applicants to
  *"Make a quick video and post it to YouTube"*. A literal reading of §2 supports it; most
  analysts would not call it harvesting. The quote is real but the reading is harsh. That
  is the kind of error verification cannot catch (DESIGN.md §4).

## 3. The prompt fix

### 3.1 Change

The policy was **not** changed. One instruction was added to the prompt: weak
signal guidance limits VIOLATING; when a review-only section names a condition
that the facts block confirms (for example "no company profile"), report it,
unless the posting itself satisfies the section (for example, it names and
describes the employer). Also `ESCALATE_BELOW_CONFIDENCE` was lowered from 0.7
to 0.6: Haiku reports 0.5–0.6 even on clear ALLOWED cases, which sent a third of
traffic to Opus without changing outcomes.

The exact change, in step 4 of the instructions (`core/labeler/labeling/prompt.py`).
`ESCALATE_BELOW_CONFIDENCE` went from 0.7 to 0.6 at the same time.

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
 5. Hints come from simple pattern matching and are often wrong. Use them to know
```

**Later rewording.** The two quoted examples ("no company profile", "names and
describes the employer") paraphrased §5 inside the fixed instructions, against the rule
that nothing section-specific lives in prompts. They were replaced by generic wording:

```diff
-   They are exactly what review-only sections are for: when a review-only
-   section names a condition (for example "no company profile") and the facts
-   block shows it is true, report that section with the fact, unless the
-   posting text itself clearly satisfies what the section asks for (for
-   example, it names and describes the employer). Policy guidance that missing
+   They are exactly what review-only sections are for: when a review-only
+   section's text names a condition that the facts block shows is true,
+   report that section with the fact, unless the posting text itself clearly
+   satisfies what the section asks for. Policy guidance that missing
```

The §3.2 numbers below were measured with the first wording. §1.1 (the post-hoc eval)
runs the rewritten one. Two later code changes also touch step 4. Neither changes
behaviour under v1:
- "and never the sole basis for a VIOLATING label: that needs at least one verified
  quote" restates in the prompt a rule the code enforces anyway.
- "(only a fact the section lists as accepted, when it lists any)" applies only to
  policies that declare `accepts_facts`, which v1 does not.

### 3.2 Checked on dev postings only

**A. The same 16 dev postings (8 fraud, 8 legit) before and after:**

| | before | after |
|---|---|---|
| fraud flagged (VIOLATING or NEEDS_REVIEW) | 6 / 8 | **7 / 8** |
| legit VIOLATING | 0 | 0 |
| legit NEEDS_REVIEW | 0 | 1 |
| escalated to tier 2 | 31% | **6%** |
| cost / 1k (CLI token counts × list price, §6) | $6.72 | **$1.94** |
| latency p50 / p95 | 6.4 s / 14.7 s | **5.5 s / 7.6 s** |

**B. 10 dev postings with an empty company profile (5 fraud, 5 legit), after:**
§5 was applied on 9 of 10. The tenth (**7144**, fraud) names and describes the
employer ("Expro") in the description, so ALLOWED is correct under the new
instruction. Fraud: 2 VIOLATING, 2 NEEDS_REVIEW, 1 ALLOWED. Legit: 4
NEEDS_REVIEW, 1 VIOLATING (case 258 above).

**Trade-off:** legitimate postings without a company profile now go to review
instead of straight through. That is what §5 says, and it raises the review
rate. If that load is too high, the fix is a policy decision (narrow §5), made
through a new version.

## 4. Evidence: does Haiku fabricate quotes, and does verification work?

Every quote in every stored model call (463 decisions, 633 calls: the full eval
run, the v2 sample and the dev checks):

| | Haiku 5.5 | Opus 5.5 |
|---|---|---|
| quotes returned | 381 | 81 |
| found verbatim | 355 | 75 |
| found after case / whitespace / quote-mark normalisation | 17 | 6 |
| found via the glued-sentence fallback | 1 | 0 |
| **rejected** | **8** | 0 |

The 8 rejections, all on §5 (so none changed a label: §5 also rests on the
absence facts):

| count | quote | what happened |
|---|---|---|
| 5 | *"location: GB, LND, London"* and similar | The model quoted the prompt's **metadata line**, label included. Not posting text: correctly rejected (and a location proves nothing for §5). |
| 2 | *"[company_profile]\n(empty)"*, *"[title]\nGraphic Designer"* | The model quoted the **prompt's own scaffolding**. Correctly rejected: exactly what the check is for. |
| 1 | *"We help teachers get safe & secure jobs abroad"* | The posting says `&amp;` (EMSCAD keeps HTML entities). **False rejection.** Fixed after the run: the matcher now decodes entities, and the stored span still covers the original `&amp;`. |

So **no invented posting content was observed in 462 quotes**; 7 quotes of
prompt scaffolding were caught, and the matcher caused one false rejection,
now fixed (both cases have tests). Normalisation was needed for 23 quotes that
would otherwise have been wrongly rejected.

The errors that did occur (§2) were omissions and harsh readings, not
fabrications. Verification is not designed to catch those; DESIGN.md §4 says
what does.

**v1 and `accepts_facts`.** `policies/v1.yaml` is kept verbatim from the brief, so it
has no `accepts_facts`, and under v1 §5 may rest on any absence fact. That is the
behaviour of versions written before the field existed. Two rules still hold under v1:
VIOLATING always needs a verified quote, and §5 is the only review-only section, so facts
alone can never combine into a block. In the `v1@current` run, 162 of the 163 decisions
with §5 have a §5 quote. The remaining one rests on `no_company_profile`. None rests on
another fact such as `no_company_logo` or `no_salary`. Restricting §5 to
`no_company_profile` is a new version, not an edit to v1; v2 and v3 already do it.

## 5. Policy v2 demo

`policies/v2.yaml` adds §6 *Vague home-based clerical work* (review only) and
one edge case ("office-based or clearly specified clerical roles at a named
employer do not match §6"). No code changes: the new section reaches the
prompt, the schema's section enum, the label aggregation and the UI (filter,
color, policy diff) from the document.

**Where §6 applies: 12 dev postings** picked by title to exercise it (6
home-based clerical frauds, 6 office clerical jobs at named employers), same
code and prompt, v1 vs v2 (`make demo-v2-dev`):

| | v1 | v2 |
|---|---|---|
| 1408 *Data Entry Admin/Clerical Positions - Work From Home* (fraud) | NEEDS_REVIEW §5 | **VIOLATING §5+§6** |
| 17584 *Online Typist From Home* (fraud) | NEEDS_REVIEW §5 | **VIOLATING §5+§6** |
| 17573 *Home Based Typist* (fraud) | VIOLATING §2 §4 §5 | VIOLATING §2 §4 §5 **§6** |
| 17657 *Data Entry / Typist Work At Home* (fraud) | VIOLATING §4 §5 | VIOLATING §4 §5 **§6** |
| 17537, 17630 *Data Entry* (fraud) | NEEDS_REVIEW §5 | NEEDS_REVIEW §5 (no home-based claim: §6 not cited) |
| 6 office clerical roles at named employers (legit) | ALLOWED | ALLOWED (the new edge case holds) |

Two postings move from review to VIOLATING, which is the policy's own
combination rule at work (§5 "unless combined with another section"), and
none of the 6 legitimate clerical jobs is touched. §6 evidence is a verbatim
quote, e.g. *"by performing online data entry work, and other types of work
from home duties"* (17584).

**On the eval set** (20-posting shadow run, `make demo-v2`), §6 matched
nothing: none of those 20 is a vague home-based clerical job. Four postings
still changed label between the v1 and v2 runs, all on §5 (2278 and 177:
§5 → ALLOWED; 1663 and 2740: ALLOWED → §5). These flips are model
inconsistency, not the policy change, which is why DESIGN.md §5 adds a
consistency metric: a shadow-run diff must be read against a same-policy rerun
baseline.

**Re-labeling existing items.** Labels are keyed by `(job_id,
policy_version)`, so v2 never overwrites v1. `POST /api/policies/v2/relabel`
with scope `recent` (last N days), `previous` (everything labeled under the
published version) or `eval` runs as a background job; in production it goes
through batch inference and only re-labels postings the change can affect
(DESIGN.md §2).

Process note: while choosing what v2 should add, phrase counts were printed on
the eval set once. The choice was made from dev-set statistics, but because the
eval set was looked at, v2's eval numbers are not a clean held-out estimate. v2 is
a behaviour demo, not a quality claim.

### 5.1 v3: a policy fix found on eval, checked on dev

§1.1 showed that §5, applied as written, sends 89 of 300 legitimate eval postings to
review. `policies/v3.yaml` narrows §5 to postings that never say who is hiring. Two
rules keep the eval set clean:
- **The text is a general rule.** It was not fitted to individual eval cases.
- **Demonstration and measurement use development postings.** The six demo
  postings (README) are dev ids picked from the teacher's v1 labels, with no model
  call: legitimate postings with no profile that name the employer, and frauds with
  no profile that do not.

A v3 run on the eval set would not be held out. The v3 numbers belong to a dev
sample of no-profile postings: legitimate postings leaving review, and frauds still
caught. That run is not done yet.

## 6. Cost and latency

Measured via the CLI, so both are pessimistic. Full eval run (400 postings,
first prompt, concurrency 6 on a laptop):

| | tier 1 (Haiku, 400 calls) | tier 2 (Opus, 151 calls) |
|---|---|---|
| output tokens / call (incl. thinking) | 824 | 583 |
| latency / call (mean) | 5.7 s | 7.5 s |
| CLI-reported tokens billed at list price, per call | $0.00066 | $0.0209 |

- **Latency per posting:** p50 5.7 s, p95 17.4 s, max 24.7 s. All under the 60 s
  target, with process start-up (≈1 s per call) included. Escalated postings
  are the p95.
- **Cost as measured: $8.55 per 1,000 postings** ($3.42 for the 400). This
  overstates API cost: the CLI writes ≈1.8k tokens to cache on every call
  (its own wrapper plus the posting, with a 1-hour TTL) where the API would
  read the cached system prompt (1,980 tokens on Haiku 5.5's tokenizer, measured on the one
  API call) and bill the posting (≈300–800 tokens) as plain input.
- **API estimate from the measured output tokens:** tier 1 ≈ $0.50 per 1,000
  calls; tier 2 ≈ $15 per 1,000 calls. At this run's 37.8% escalation that is
  **≈ $6.2 per 1,000 postings**. At the current code's 13.5% escalation on the same
  eval set (§1.1), it is **≈ $2.55**, or ≈ $1.53 with Sonnet as tier 2. The ≈ 6% seen
  on a 16-posting dev sample (§3.2) gives ≈ $1.4, for reference. Escalation is the cost lever, as DESIGN.md
  §3 predicts. Opus is ≈30× Haiku per call.

## 7. Router: 1,000 balanced training rows instead of 17,480

The router is now trained on **1,000 development rows, 500 fraudulent and 500
legitimate** (fixed seed), instead of all 17,480 dev rows (766 fraudulent,
4.4%). The eval set is excluded from both. `uv run python -m scripts.router_compare`
trains both and scores them on the eval set. It also replays the escalation rule
from the stored run: each decision keeps its tier-1 answer and its other
escalation reasons, so the share that would reach tier 2 can be recomputed with
either router's score. That needs no model call. With the original router, the
replay gives 37.8%, exactly the measured rate, which checks the method.

| router trained on | AP | ROC AUC | P @ 0.5 | R @ 0.5 | escalation (replayed) | missed frauds it flags |
|---|---|---|---|---|---|---|
| all 17,480 dev rows (used for §1) | 0.976 | 0.992 | 0.92 | 0.89 | 37.8% | 50 / 56 |
| **1,000 balanced (now the default)** | 0.950 | 0.980 | 0.82 | 0.93 | 39.2% | 51 / 56 |

- **For routing, 1,000 rows are close to 17,480.** Ranking drops a little (AP
  0.976 → 0.950). Recall at the 0.5 threshold goes up and precision goes down,
  because a balanced sample makes the model more willing to call fraud. The
  practical effect is about 1.4 more points of escalation, ≈ $0.2 per 1,000
  postings at Opus tier-2 prices.
- **What it buys.** Training takes seconds instead of minutes. It is the
  label budget a policy-labeled golden set would realistically have (DESIGN.md
  §5). It is also less exposed to EMSCAD near-duplicates: with 17,480 rows,
  43 of the 100 eval frauds have a near-identical description in training.
- **Agent quality is unchanged.** The router never labels. §1's headline was
  measured with the 17,480-row router and is not re-run.
- `uv run python -m scripts.data router 0` trains on all dev rows again.

## 8. Few-shot examples: tried on dev, not adopted

**What was tried.**
- Five worked examples were picked from Claude's verified findings on the 1,000-row
  development sample. Each is an excerpt around verified quotes: §1+§4+§5, §2+§4+§5,
  §2+§3+§5, a §5-only NEEDS_REVIEW, and an ALLOWED commission-only sales role (the brief's
  edge case).
- They are stored in the policy document, as `policies/v1.1.yaml` (v1 + `examples:`).
  Adopting them is therefore a normal policy change.

**How it was measured.**
- 60 held-out development postings, 30 fraudulent and 30 legitimate. None is in the
  1,000-row sample or the eval set.
- `scripts/fewshot.py compare` labeled them with Haiku → Opus via the CLI.

| prompt | flagged P | flagged R | strict P | strict R | review rate | escalation | $ / 1k (CLI tokens) |
|---|---|---|---|---|---|---|---|
| v1, no examples | 0.83 | **0.63** | 1.00 | 0.27 | 25% | **18%** | **3.6** |
| v1.1, 5 examples | 0.85 | 0.57 | 1.00 | 0.27 | 20% | 27% | 5.7 |

**Verdict: not adopted.**
- The examples bought nothing on this sample. Two fewer frauds were flagged and one fewer
  legitimate posting went to review.
- Escalation rose, because Haiku reported lower confidence with longer context. That made
  it cost more.
- With 60 postings, two postings is within noise. A real decision needs the
  policy-labeled golden set (DESIGN.md §5).
- v1.1 stays a draft. The mechanism (versioned examples, verified quotes only, shadow-run
  before publishing) is in place if a better set of examples turns up.

## 9. Distilled small model (nice to have): measured, kept as an option

**Teacher.**
- The production cascade (Haiku → Opus, current prompt) labeled the same **1,000 balanced
  development postings** the router trains on: 500 fraudulent, 500 legitimate, never the
  eval set. 20% were escalated, there were 0 failures, and it cost ≈ $4.5 in CLI-reported
  tokens.
- The training targets are the teacher's **verified** findings only: quotes that passed
  verification and sections that survived it.
- Committed in `data/distill/`: `teacher.json.gz` (decisions with traces), and
  `train.jsonl.gz` / `valid.jsonl.gz` (897 / 99 after dropping over-long postings).

**Student.**
- Qwen2.5-1.5B-Instruct (Apache-2.0), 4-bit, with a LoRA adapter: 5.3M trainable
  parameters, 0.34% of the model.
- Trained with MLX on an M2 Max: 900 iterations in ≈ 75 min, loss masked to the answer only.
- Validation loss went from 1.69 to 0.56.
- The adapter is committed: `data/distill/adapter/adapters.safetensors`, 21 MB.
- It reads the same system prompt and posting as Claude, and its output goes through the
  same quote verification and aggregation.

**On the 400 eval postings** (no escalation, the student alone, `make slm-serve` + `make slm-eval`).
The last column is recomputed from the stored traces of the §1.1 run (`v1@current`):

| | Qwen2.5-1.5B base | **Qwen2.5-1.5B + LoRA (distilled)** | Haiku → Opus, current code (§1.1) |
|---|---|---|---|
| strict P / R | 0.00 / 0.00 | **0.92 / 0.12** | 0.96 / 0.22 |
| flagged P / R / F1 | 0.27 / 0.59 / 0.37 | **0.49 / 0.68 / 0.57** | 0.45 / 0.73 / 0.56 |
| false VIOLATING (of 300 legit) | 1 | **1** | 1 |
| review rate | 55% | **32%** | 35% |
| quotes rejected by verification | **60%** (611 of 1,013) | **4%** (11 of 261) | 2% (8 of 394: Haiku 8 of 381, Opus 0 of 13) |
| invalid JSON (→ NEEDS_REVIEW, tier=failed) | 2 | 2 | 0 |
| latency p50 / p95 | 1.5 s / 2.5 s | **1.6 s / 3.1 s** | 5.9 s / 14.3 s |
| cost per 1,000 postings | 0 (own hardware) | **0** (own hardware) | ≈ $2.55 at API prices |

What this shows:
- **Distillation works for the review queue.** On "flagged" (what an analyst sees), the
  1.5B student matches the Claude cascade: F1 0.57 vs 0.56. It is 3–4× faster and costs
  nothing per call.
- **It is weaker where precision matters most.** It reaches VIOLATING half as often (strict
  recall 0.12 vs 0.22). It finds §5 reliably but finds the second section that makes a
  posting VIOLATING less often.
- **Verification is what makes a small model safe to try.** The base model invented quotes
  in 60% of its findings. Verification threw them out, so a bad model degrades into
  NEEDS_REVIEW instead of wrong enforcement. After distillation, rejected quotes fall to 4%,
  close to Haiku's 2%.
- **Not adopted as the default**, for two reasons:
  - It learned policy v1 from v1 examples. Under a new version (v2's §6) it will mostly
    repeat v1 habits, and it would miss with confidence, which does not trigger
    escalation.
  - At 200k postings a day, Haiku costs ≈ $3k a month. Self-hosting only pays at about
    10× volume (DESIGN.md §3, option E).
- **How it would be used:**
  - Tier 1 = student and tier 2 = Claude, chosen on the Settings page, with no code change.
  - Each policy change re-runs the teacher on the dev sample (≈ 1,200 calls) and retrains
    (≈ 75 min) as part of draft → shadow → publish.
  - A guard escalates everything while the published version differs from the version the
    adapter was trained on.

## 10. Reproduce

```bash
make eval                                   # full 400, published policy, resumable
LLM_PROVIDER=claude_cli make eval           # same, without an API key
make demo-v2                                # draft v2 → shadow-run on eval → compare → publish
make demo-v2-dev                            # v1 vs v2 on 12 dev postings where §6 applies
uv run python -m scripts.run_eval --version v1 --tag current --provider claude_cli   # §1.1 post-hoc
uv run python -m scripts.router_compare     # §7, no model calls
LLM_PROVIDER=claude_cli uv run python -m scripts.fewshot compare   # §8, 60 dev postings
```

§9 needs Apple silicon (MLX) and runs on the host, not in Docker. It uses the committed
adapter, so no teacher run or training is needed. Use two terminals:

```bash
make slm-serve    # terminal 1: downloads the 4-bit base once (~1 GB), then serves :8090
make slm-eval     # terminal 2: waits until :8090 answers, then labels the 400
```

Raw outputs: `data/eval/` (ids, per-run JSON); every decision with its trace is
in the UI.
