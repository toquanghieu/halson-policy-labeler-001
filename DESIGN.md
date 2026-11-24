# DESIGN — Agentic policy labeling for job postings

Part A: the production design for 200k postings/day (5× peaks), a label within
60 s for 95% of postings, weekly policy changes with 30-day re-labels, and two
consumers (analysts in a UI, the marketplace via API). The repo (Part B) runs
the same shape with local stand-ins for each AWS service (§6). The long form,
with every table and argument, is [docs/design-details.md](docs/design-details.md).

## Five decisions

1. **The model proposes, code decides.** The LLM reports matching sections with
   quotes. Code verifies every quote against the posting, drops sections without
   proof, and computes the label from each section's `effect` in the policy.
   VIOLATING always needs at least one verified quote.
2. **The policy is a versioned document, not a prompt.** Sections, effects
   (`violating` / `review_only`) and edge cases live in YAML. The prompt, the
   output schema (enum of section ids) and the combination rule come from it at
   run time, so publishing a version is a data change.
3. **Labels are keyed by `(job_id, policy_version)`.** Versions never overwrite
   each other: shadow-run, compare, publish, roll back by re-publishing.
4. **Cheap model on everything, strong model only where it pays**, on explicit,
   logged triggers.
5. **Failure is a label.** A model error, malformed output or a missed deadline
   makes the posting `NEEDS_REVIEW` with confidence 0 and a reason. Nothing is
   silently dropped or allowed.

## 1. Architecture

```mermaid
flowchart LR
  M[Marketplace] -->|PostingCreated| EB[(EventBridge)]
  M -->|GET /results/:id| API
  API -->|one posting, caller waits| QS[[SQS labels-sync + DLQ]] --> W
  EB --> QL[[SQS labels-live + DLQ]]
  API[Label API · ECS Fargate] --> QL
  API --> PG[(Aurora Postgres)]
  QL --> W[Workers · ECS Fargate<br/>autoscaled on queue age]
  QB[[SQS labels-backfill + DLQ]] --> W
  W --> R[Router, in-process]
  W -->|tier 1 / tier 2| LLM[Claude on Bedrock<br/>Haiku → Opus/Sonnet]
  W --> PG
  W --> S3[(S3: traces, models)]
  W -->|PostingLabeled| EB
  PUB[Policy published] --> SF[Step Functions] --> BATCH[Bedrock batch] --> PG
  SF --> QB
  PG --> UI[Analyst UI · CloudFront]
  W --> CW[CloudWatch + alarms]
```

| Component | Service | Why |
|---|---|---|
| Ingest | EventBridge → **SQS** (DLQ after 3 receives): `sync` > `live` > `backfill` | Absorbs the 5× peak; a waiting caller is never behind a batch; poison messages become visible, not retried forever. |
| API | **ALB + ECS Fargate** (FastAPI) | Submit, results, policy management. Only enqueues. |
| Workers | **ECS Fargate**, scaled on `ApproximateAgeOfOldestMessage` | LLM calls are I/O-bound: one task holds ~50 calls in flight. No GPUs. |
| Models | **Claude on Bedrock** | Data stays in the account, IAM auth, batch at half price, provisioned throughput. |
| Router | TF-IDF + logistic regression, **in the worker** | Sub-millisecond; decides who gets a second opinion, never the label. |
| State | **Aurora Postgres** | Policies, labels, jobs; sections are a table, so analyst filters are indexed SQL. |
| Traces | **S3** (+ Athena) | Full prompts and outputs are large and rarely read; Postgres keeps the key. |
| Backfill | **Step Functions + Bedrock batch** | Not latency-bound: half price, own quota, never on the live path. |
| Secrets | **Secrets Manager** (KMS) | Read by the task role, rotated, audited in CloudTrail. With Bedrock there is no model key at all. |

**One posting:** event → SQS → worker → hints + router → tier 1 → verify → maybe
tier 2 → label and `PostingLabeled` event in one transaction (published version
only) → ack. A 240 s budget per posting, under the 300 s visibility timeout, means
a posting is never worked on twice. [Secrets](docs/design-details.md#secrets-api-keys).

## 2. Scaling

**Peak.** 200k/day is 2.3/s; 5× is ~12/s. At ~6 s per tier-1 call (measured
p50, RESULTS.md §6), peak needs ~70 calls in flight: 2–3 tasks. Scaling on
queue *age* keeps queueing under ~20 s, inside the 60 s target.

**The real limit is tokens, not compute.** ~2k input tokens per posting, most of it
the cached policy and instructions (1,980 tokens on Haiku 5.5, measured), plus a few
hundred output: ~1.5–2M tokens/min at peak. The Bedrock quota is the first thing
to negotiate.

**Backfill after a policy change** (30 days ≈ 6M postings, weekly):

1. **Never on the live path.** New postings use the new version at once; the
   backfill runs on batch inference behind it.
2. **Shadow first.** Run the draft on the eval set and a sample of recent
   traffic (minutes, a few dollars); publish with that diff in hand.
3. **Re-label only what can change.** A version that only adds a `violating`
   section cannot change a posting already VIOLATING. Flips *out of* VIOLATING
   go to analysts, not auto-unblocked. (Designed, not built.)

**At 10× (2M/day)** the model quota breaks first (~15–20M tokens/min): provisioned
throughput, then a **distilled small model** on own GPUs as tier 1. 60M re-labels per
change no longer fit a week of batch quota, so rule 3 becomes mandatory. Postgres,
workers and the API scale fine.

**The human queue breaks before any machine does.** §5 alone sends 89 of 300
legitimate eval postings to review (RESULTS.md §1.1). At EMSCAD's 4.8% fraud rate,
that is **~31% of traffic, ~60k postings a day**, about 1,000 analyst-hours at a
minute a case. The levers:

- narrow §5 by policy (`policies/v3.yaml`, measured on dev data);
- order the queue by router risk and sample the tail: a score ≥ 0.3 kept 50 of 51
  frauds in review and moved 49 of 140 cases out of it on the eval run;

- send §5-only cases back to the poster to verify the employer;
- tune the thresholds on the Settings page, shadow-running each change.

[Details §2](docs/design-details.md#the-human-queue).

## 3. Cost

First-party list prices per 1M tokens (in/out): Haiku 5.5 $0.10/$0.50, Sonnet 5.5
$2/$10, Opus 5.5 $4/$20. With token counts **measured** on the 400-posting run,
1,000 calls cost **Haiku ≈ $0.50, Sonnet ≈ $7.6, Opus ≈ $15.2**. Month = 6M postings;
weekly backfill = 4 × 6M at batch price (−50%). Bedrock has its own price list, and
cache reads on the API are not measured yet:
[details §3](docs/design-details.md#prices-and-what-is-measured).

| Option | per 1k | live / month | + weekly backfill |
|---|---|---|---|
| A. Opus on everything | ≈ $15 | ≈ $91k | +$183k |
| B. Sonnet on everything | ≈ $7.6 | ≈ $46k | +$91k |
| C. Haiku → Opus, 38% escalated (first prompt) | ≈ $6.3 | ≈ $38k | +$76k |
| **C. Haiku → Opus, 13.5% escalated (current code)** | ≈ $2.55 | ≈ $15k | +$31k |
| **C′. Haiku → Sonnet, 13.5%** (recommended) | ≈ $1.53 | ≈ $9.2k | +$18k |
| D. Haiku only | ≈ $0.50 | ≈ $3.0k | +$6k |
| E. Self-hosted distilled SLM + hosted escalation | GPU-bound | ≈ $2.5k fixed + escalations | mostly free |

Infrastructure (Fargate, Aurora, SQS, S3) is ~$1.5–3k/month.

- **Escalation drives cost.** Each point of escalation to Opus adds ≈ $0.15/1k.
- **Backfills can cost more than live traffic**, so batch pricing and "re-label only
  what can change" matter more than model choice.

- **Recommendation: C′.** The repo defaults to Opus because every measured number was
  taken with it. C′ is one Settings change plus a shadow-run.

**Model choice.** Haiku for every posting (structured JSON in ~5 s, no invented posting
text in 381 quotes, RESULTS.md §4); Opus or Sonnet for the ambiguous minority. A local
SLM is cheapest at scale but needs GPUs, policy-labeled data and retraining per policy
version (RESULTS.md §9).

## 4. Agent design

A **code-orchestrated workflow**, not an open-ended tool loop: all the evidence is
in the posting, so a free-roaming agent would add latency and variance, not
information. The "tools" run deterministically before the call.

```
posting ─┬─ hints   (regex: contact tokens, payment words, money movement, pay claims)
         ├─ facts   (absence flags: no company profile / logo / salary)
         └─ router  (risk score, never shown to the model)
   tier 1  ─ one structured call: policy + posting + facts + hints
             → findings[{section, quotes[], facts[], explanation}], insufficient_evidence, confidence
  validate ─ the reply is checked against the schema in code; malformed → retry once, then a failed label
   verify  ─ each quote must occur in the posting; facts must be true and accepted by the section;
             a `violating` section needs ≥ 1 verified quote
 aggregate ─ any `violating` section, or ≥ N sections (combine_to_violating) → VIOLATING,
             but only with ≥ 1 verified quote; one `review_only` section → NEEDS_REVIEW; else ALLOWED
 escalate? ─ confidence < 0.6 · section dropped · insufficient evidence · router ≥ 0.5 but ALLOWED
   tier 2  ─ same prompt, larger model, independent of tier 1's answer
```

**Policy use.** Nothing section-specific lives in code or in the fixed instructions.
The rendered policy is the cached system prompt, and the schema's `section` enum is
built from it, so a new section works at once. The combination rule
(`combine_to_violating`) and the facts a section may rest on (`accepts_facts`) are
policy fields.

**Evidence.** Code checks every quote (ignoring case, whitespace, entities and curly
quotes) and stores the span cut from the **original** text, so the UI highlights real
posting text. It costs no tokens and makes a cheaper model safe to try: fabrication
shows up as an alarmed rejection rate. It cannot check that a quote *means* what the
model claims, or see a section the model missed; escalation, review and audits cover
that. [Details §4](docs/design-details.md#4-agent-design).

**Uncertainty, three kinds:** the policy says review (§5), the model cannot decide
(`insufficient_evidence`), or it is unsure (low confidence → escalation; not yet calibrated).

**Debuggability.** Each decision stores facts, hints, router score, each tier's raw
output (malformed ones too), rejected quotes, escalation reasons, the policy hash,
tokens, cost and latency, shown in the UI as a timeline.

## 5. Evaluation and quality over time

**Offline.** The fixed 400 set with the full 3×2 confusion matrix, NEEDS_REVIEW scored
both ways; accuracy is never the headline. EMSCAD gold is not the policy (29 of 56 missed
frauds break no written rule, RESULTS.md §2), so production needs a **policy-labeled
golden set** (1–2k analyst-labeled postings with sections and evidence) and a
**consistency** check (label a sample 2–3×; disagreement routes to review).

**Production.** Resolved reviews plus a 0.5% random audit of ALLOWED/VIOLATING give
weekly precision, recall and calibration. **Drift alarms** (daily) cover the label
and section mix, review rate, escalation rate, quote rejection rate, router-vs-agent
disagreement and cost per posting. Escalation and rejection rates usually move
before any label metric does.

**Rollout of a policy or model:** draft → offline eval + posting-by-posting diff
(the UI's Impact panel) → shadow on live traffic for a day, sample flips by hand →
publish (a pointer change; rollback = re-publish) → backfill, with flips out of
VIOLATING sent to analysts.

## 6. What the repo builds

`docker compose up` runs the same architecture with stand-ins that speak the production
APIs: ElasticMQ, Postgres, SeaweedFS, an outbox for EventBridge, Prometheus and Grafana.
Models are chosen per tier on the Settings page; Bedrock is one more provider class. Not built: batch pricing for backfills (a separate queue, read only when
live is empty), worker autoscaling, the EventBridge relay, diff-aware backfill, the golden
set and calibration, authentication (Cognito for analysts, IAM for the marketplace) and an
audit log. [Full mapping](docs/design-details.md#6-what-the-repo-builds-vs-this-design).
