# Casebook: policy-grounded labeling for job postings

An agent labels each job posting against a written, versioned recruitment-fraud policy,
with verbatim evidence. Analysts inspect decisions and edit and publish policy versions in a
web UI. The marketplace uses a JSON API.

| Document | What it is |
|---|---|
| **[DESIGN.md](DESIGN.md)** | Part A: the production design, ~4 pages (long form: [docs/design-details.md](docs/design-details.md)) |
| **[RESULTS.md](RESULTS.md)** | Metrics on the fixed 400-posting eval set, error analysis, cost, latency (long form: [docs/results-details.md](docs/results-details.md)) |
| [docs/reference.md](docs/reference.md) | Every API call, the repository layout, re-labeling and rollback, the optional local model |
| `policies/` | The policy as data. `v1.yaml` is the brief's text, verbatim; `v2.yaml` adds §6; `v3.yaml` narrows §5 |

**The core** is the agent (`core/labeler/labeling/`), versioned policies (shadow-run,
compare, publish, roll back), the eval, the analyst UI, the JSON API and `docker compose up`.
**Optional, off by default:** OpenAI and OpenAI-compatible providers, the distilled local
model, Prometheus and Grafana, the Postman collection and the AWS Secrets Manager adapter.
Each one is a single adapter behind a port.

## Run it

```bash
cp .env.example .env          # optional: ANTHROPIC_API_KEY here, or later on the Settings page
docker compose up --build     # http://localhost:8000
```

This starts the production architecture with local stand-ins for the AWS services
(DESIGN.md §6): the API and UI, 2 workers, Postgres (for Aurora), ElasticMQ (SQS) and
SeaweedFS (S3). On first start, a one-shot `init` job:
- migrates the database;
- downloads EMSCAD (CC0, ~50 MB);
- trains the router;
- loads the two measured eval runs from `data/seed/`, so RESULTS.md's numbers show in the UI
  without re-running.

This takes about a minute.

**The API key can be added later, on the Settings page.** Until then the workers run but
take no messages: the header shows "2 waiting" and the reason, and queued postings stay
queued. They start within seconds of the key being saved, with no restart. That holds for
every Settings change; only code and `.env` changes need `docker compose up -d --build`.

**Without an API key.**
- `LLM_PROVIDER=fake` in `.env` runs everything with a scripted stand-in, for clicking
  through the UI.
- With [Claude Code](https://claude.com/claude-code) logged in, the real agent runs
  through the local `claude` CLI. That is how RESULTS.md was produced. The CLI lives on the
  host, so run the worker there:

```bash
make infra     # postgres, elasticmq, seaweedfs, init; writes .env.homelab
make api       # API + UI on the host (or keep the api container)
make worker    # a worker on the host with LLM_PROVIDER=claude_cli
```

**Without Docker** (needs `uv` and Node 20): one process with SQLite, an in-memory queue and
the worker inside the API.

```bash
make setup     # deps, data load, router training, UI build
make dev       # http://localhost:8000
```

## The analyst UI

| Tab | What it does |
|---|---|
| **Messages** | Postings with no decision yet: import JSON or CSV, pick some, *Label*. On a fresh start the eval set is already labeled (seeded), so *all* lists the rest of the dataset. |
| **Work** | What is queued, being labeled or failed, per version; queue depth, workers, open jobs (*Re-send*, *Cancel*). |
| **Decisions** | Labeled postings, filtered by label, section and tier; evidence highlighted in the text, the decision trail, and the same posting under other versions side by side. |
| **Policy** | Edit drafts, shadow-run them on the eval set, see the impact, publish with an optional backfill, roll back. |
| **Evaluation** | Metrics on the fixed set for any version or eval run (`v1`, `v1@current`). |
| **Settings** | Provider and model per tier, effort, escalation thresholds, backfill window, API keys (write-only) with *Test connection*. |

## The evaluation

```bash
make eval      # labels the 400 eval postings under the published policy; resumable
```

It prints the metrics and writes `data/eval/main-<version>.json`. The **Evaluation** page shows
the same numbers.

## The policy-change demo

`policies/v2.yaml` adds **§6 Vague home-based clerical work** (review only). Nothing in the
code knows about §6.

```bash
make demo-v2       # v2 as a draft → shadow-run on the eval set → diff → publish
make demo-v2-dev   # v1 vs v2 on 12 dev postings where §6 applies (RESULTS.md §5)
```

To do the same in the UI:
1. On **Policy**, open v1, click *New version from this*, edit, then *Save draft*.
2. Click *Shadow-run on eval set*. v1 keeps serving meanwhile.
3. The *Impact* panel lists every posting whose decision changed.
4. Click *Publish…*. The dialog offers to re-label the last N days and shows how many
   postings that is.

After that, §6 appears as a filter in **Decisions**, with its quotes in their own colour.

**v3, the review-load trade-off.** `policies/v3.yaml` narrows §5. An empty profile is no
longer "unverifiable" when the text names the employer. Demo it on six **dev** postings, so
the eval set stays held out (RESULTS.md §5.1):
1. Save v3 as a draft.
2. Run `POST /api/policies/v3/relabel {"scope": "ids", "ids": ["3707", "16716", "8531", "5530", "17652", "17580"]}`.
   That is 6 model calls.
3. Open e.g. `#/queue?id=3707&cmp=v3`.

What to expect:
- **3707, 16716, 8531** are legitimate postings that name their employer. They should leave
  review.
- **5530, 17652, 17580** are frauds that never say who is hiring. §5 should still catch them.

Rollback, comparing versions and how re-labeling works: [docs/reference.md](docs/reference.md#re-labeling-rollback-and-comparing-versions).

## API

Interactive docs are at `/docs`. `postman/` holds a collection with every endpoint, a
description per request and 57 example responses captured from the app (`make postman`).
The calls the marketplace needs:

| | |
|---|---|
| `POST /api/postings` | label one posting and wait for the decision (a worker labels it; 202 + job id if it takes over 50 s) |
| `POST /api/postings/batch` | put up to 5,000 on the live queue; returns a job id |
| `GET /api/results/{job_id}` | the decision under the published policy |
| `GET /api/events?after=` | `PostingLabeled` events (outbox) to act on |

```bash
curl -s localhost:8000/api/postings -H 'content-type: application/json' -d '{
  "job_id": "demo-1", "title": "Payment Processing Agent",
  "description": "Receive client payments into your personal account and forward them via Western Union. Earn $3,000 per week, no experience needed."
}' | jq '{label, policy_sections, evidence, rationale, confidence}'
```

Policies, re-labels, jobs, settings and the work view: [docs/reference.md](docs/reference.md#api-every-main-call).

## Layout

Three Python packages in one uv workspace, next to the frontend. The API and the worker
never import each other.

| Folder | Package | What it is | Image |
|---|---|---|---|
| `core/` | `labeler` | Shared: agent, policy, evidence, DB, queue, storage, LLM providers, use cases | in both |
| `backend/` | `labeler_api` | HTTP API (FastAPI) and the UI host; only enqueues work | `backend/Dockerfile` |
| `worker/` | `labeler_worker` | Queue consumer, scaled on its own | `worker/Dockerfile` |
| `frontend/` | | React analyst UI | built into the API image |

Inside `core/labeler/`, every external system is a package under `infra/` (queue, storage,
secrets, llm), each with a `port.py` contract and one `*_adapter.py` per implementation. The domain
uses only ports, `bootstrap.py` picks the adapters, and `tests/test_architecture.py` enforces both.
`scripts/` holds data loading, the eval runner and the demos; `tests/` is one suite for all
three packages. [The file-by-file tree](docs/reference.md#layout).

## Quality checks and configuration

`make lint` runs ruff (with security rules), mypy, eslint and tsc. `make test` runs 137 tests
at 91% line coverage, with no key needed; the repository tests run on SQLite and on a real
Postgres container. CI runs both, plus a Docker build and a gitleaks secret scan.

Settings come from environment variables (`.env.example`). Models, thresholds and the backfill
window can also be changed on the **Settings** page, which every worker reads within ~5 s.
No secrets are in the repo.
