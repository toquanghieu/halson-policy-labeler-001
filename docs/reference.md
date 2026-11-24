# Reference

The parts of the [README](../README.md) that are for looking things up: every API call, the
repository layout, how re-labeling and rollback work, the operations shortcuts, and the
optional distilled model. Paths are relative to the repository root.

## Operations

| | |
|---|---|
| `docker compose up --scale worker=4` | more workers; they share the queue |
| `make observability` | + Prometheus on :9090 (alert rules from DESIGN §5) and Grafana on :3000 |
| http://localhost:9325 | ElasticMQ's queue browser |
| `make down` | stop (volumes kept) |

## API, every main call

Interactive docs at `/docs`. A Postman collection with every endpoint, a
description per request (inputs, side effects, errors) and 57 saved example
responses (errors included) is in `postman/`: import
`policy-labeler.postman_collection.json` and `local.postman_environment.json`.
The examples are real responses captured from the app by `make postman`, so they
are regenerated, not hand-maintained. The main calls:

| | |
|---|---|
| `POST /api/postings` | label one posting and wait for the decision (a worker labels it; 202 + job id if it takes over 50 s) |
| `POST /api/postings/batch` | put up to 5,000 on the live queue; returns a job id |
| `POST /api/inbox/import` | store postings without labeling them (the Messages tab) |
| `GET /api/inbox?scope=inbox` | postings with no decision under the published policy: the 400 eval postings + imports |
| `POST /api/inbox/label` | label some (`ids`) or all of them (`all_in_scope`) on the live queue |
| `GET /api/jobs/{id}` | progress of a batch or re-label |
| `GET /api/results/{job_id}` | the decision under the published policy (`?policy_version=` for another, `?full=true` for evidence offsets and trace) |
| `GET/POST /api/policies`, `PUT /api/policies/{v}` | list, create a draft, edit a draft |
| `POST /api/policies/{v}/publish` | make a version live, or re-publish a retired one (rollback); `{"backfill": true, "days": 30}` also re-labels |
| `GET /api/policies/{v}/relabel/preview?days=30` | how many postings that re-label would touch |
| `POST /api/policies/{v}/relabel` | run a version (draft or published) over existing postings; scopes `eval`, `recent`, `previous`, `ids` |
| `GET /api/postings/{id}/versions?v=v1&v=v2` | one posting's full decision under several versions |
| `GET/PUT/DELETE /api/settings` | run-time settings: provider and model per tier, effort, escalation thresholds, backfill window |
| `GET /api/settings/providers`, `PUT/DELETE /api/settings/secrets/{name}`, `POST /api/settings/providers/{name}/test` | providers, write-only API keys, connection test |
| `GET /api/work?status=&version=` | postings queued, labeling or failed, with queue depth, workers and open jobs |
| `POST /api/jobs/{id}/cancel` | stop a batch or re-label; already-made labels stay |
| `GET /api/compare?a=v1&b=v2` | which postings changed decision between versions |
| `GET /api/events?after=` | `PostingLabeled` events (outbox) for the marketplace to act on |
| `GET /api/metrics?policy_version=` | eval-set metrics |

```bash
curl -s localhost:8000/api/postings -H 'content-type: application/json' -d '{
  "job_id": "demo-1", "title": "Payment Processing Agent",
  "description": "Receive client payments into your personal account and forward them via Western Union. Earn $3,000 per week, no experience needed."
}' | jq '{label, policy_sections, evidence, rationale, confidence, policy_version}'
```

## Re-labeling, rollback and comparing versions

**Rollback.** Open a retired version and click *Re-publish (roll back)…*. Its
labels are still stored, so the optional re-label only touches postings that
arrived after it was retired.

**One posting under several versions.** In a case, *Compare vN with* lists every
other version; tick one or more to see label, sections, quotes and rationale side
by side (columns that differ are outlined). A version that never saw the
posting shows *Label under vN*, which labels just that posting.

**Re-labeling existing items.** Labels are stored per `(job_id, policy_version)`,
so a new version never overwrites the old one. `POST /api/policies/{v}/relabel`
takes a scope: `eval`, `recent` (last N days, default 30), `previous`
(everything labeled under the published version) or `ids`. Publishing can start
the `recent` re-label itself (`{"backfill": true, "days": 30}`, or by default via
the *Backfill on publish* setting). Every posting in scope becomes a
message on the **backfill** queue, which workers read only when the live queue is
empty, so a 30-day re-label never delays new postings; progress is on
`GET /api/jobs/{id}`. Locally every EMSCAD posting counts as "recent", because
the dataset has no posting dates and `created_at` is when it was loaded: a
30-day re-label here means all ~17.5k postings, which the publish dialog shows
before you confirm. In production the same job goes through batch inference
and only re-labels what a change can affect; see DESIGN.md §2.

## Layout

Three Python services-and-libraries in one [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
(one lock file, one `.venv` at the root), next to the frontend:

| Folder | Package | What it is | Depends on | Image |
|---|---|---|---|---|
| `frontend/` | | React analyst UI | the API | built into the API image |
| `backend/` | `labeler_api` | HTTP API (FastAPI): routes, request bodies, errors, serves the UI. Only enqueues work. | `core` | `backend/Dockerfile` |
| `worker/` | `labeler_worker` | Queue consumer: its own service, scaled on its own | `core` | `worker/Dockerfile` |
| `core/` | `labeler` | Shared library: labeling agent, policy, evidence, DB, queue, storage, LLM providers, use cases | | in both images |

The API and the worker never import each other. The API image does not contain the
worker, and the worker image contains neither FastAPI nor the UI. The one exception is
`make dev`: a single process with `EMBEDDED_WORKER=true`, which runs the worker inside the
API through a lazy import.

Inside `core/`, layers with every external system behind a port (ports and adapters).
Dependencies point inward: `services` → `labeling` (pure domain code); `infra` holds the
adapters. `bootstrap.py` is the only place that picks them: SQLite or Postgres, in-memory
queue or SQS, disk or S3, fake / CLI / API model.

```
pyproject.toml      workspace root: members, dev tools, ruff / mypy / pytest config
backend/            labeler_api: the API service
  labeler_api/
    main.py         app factory: lifespan (embedded worker only in `make dev`), routers, error handlers, /metrics, UI
    routes/         labeling, inbox, analyst, policies, settings, ops (+ GET /api/work)
    deps.py         Depends: the service, the settings
    schemas.py      request bodies
    errors.py       domain errors -> HTTP status
    ui.py           serves frontend/dist
  Dockerfile        UI build + API image
worker/             labeler_worker: the queue consumer service
  labeler_worker/
    consumer.py     sync, then live, then backfill; bounded concurrency, lease per item, ack after store, DLQ path, waits without a usable key
    heartbeat.py    liveness rows (running / waiting + reason), so the API and UI know who is consuming
    __main__.py     `python -m labeler_worker`: signals, metrics endpoint, graceful drain
  Dockerfile        worker image (also runs the one-shot `init` job)
core/               labeler: shared by both services
  labeler/
    config.py       settings from env / .env (pydantic-settings, validated)
    errors.py       domain errors (NotFound, Conflict, InvalidInput, PolicyError)
    bootstrap.py    composition root: picks and wires DB, queue, object store, model provider
    services/
      labeling.py   use cases: label, batch (live queue), relabel (backfill queue), publish, rollback, compare, process_item
      settings.py   run-time settings (Settings page): env defaults + overrides from the database
    labeling/       the domain, no I/O
      agent.py      tier 1 → verify → aggregate → escalate? → tier 2; full trace
      policy.py     parse + validate the YAML, render it for the model, aggregate the label from section effects
      prompt.py     system prompt and JSON schema, both generated from the policy
      evidence.py   quote verification with offsets into the original text
      signals.py    regex hints and absence facts (pre-model "tools")
      router.py     TF-IDF + logistic regression risk score (routing only; 1,000 balanced dev rows)
      metrics.py    precision / recall / F1, confusion matrix, cost and latency summaries
      schemas.py    Posting, LabelResult, Label
    infra/          ports and adapters: one package per external system, each with port.py (the
                    contract, a typing.Protocol, standard library only) and one *_adapter.py per
                    implementation; tests/test_architecture.py enforces the rules
      queue/        port.py · sqs_adapter.py (SQS / ElasticMQ) · memory_adapter.py (in-process)
      storage/      port.py · s3_adapter.py (S3 / SeaweedFS) · local_adapter.py (a directory)
      secrets/      port.py · env_adapter.py · db_adapter.py (Fernet in the DB) · aws_adapter.py
                    (Secrets Manager) · cached.py (read cache shared by db and aws)
      llm/          port.py · anthropic_adapter.py · openai_compat_adapter.py (OpenAI, Ollama, Gemini…)
                    · slm_adapter.py (distilled student) · claude_cli_adapter.py (dev) · fake_adapter.py
                    (tests) · registry.py (picks one per tier with the current key and URL)
      db/           repository over SQLAlchemy Core (Postgres or SQLite), tables, Alembic migration runner
      telemetry.py  Prometheus metrics: decisions, escalations, quote verification, cost, latency, queue depth
  migrations/       Alembic revisions (alembic.ini beside them)
scripts/            init (migrate, load, train router), eval runner, policy demos, router_compare, fewshot, distill, Postman
tests/              one suite for all three packages: evidence, policy, agent, providers, secrets, repository
                    (SQLite + Postgres), queue + worker (liveness, waiting, cancel, requeue, status), storage, inbox,
                    settings, work view, API
deploy/             elasticmq.conf, prometheus.yml, alerts.yml, Grafana provisioning + dashboard
frontend/src/
  lib/              API client and types, formatting, policy context
  components/       case file, version compare, shared UI atoms
  pages/            Messages (inbox + import), Work (queued / labeling / failed), Decisions, Policy, Evaluation, Settings
```

## Optional: the distilled small model (Apple silicon only)

A 1.5B model (Qwen2.5-1.5B-Instruct + a LoRA adapter trained on Claude's labels of 1,000
dev postings) can serve as tier 1. Results are in RESULTS.md §9. It runs with MLX, so
it needs a Mac with Apple silicon. It does not run in Docker or on Linux, and nothing
else in the repo depends on it. The adapter is committed (`data/distill/adapter/`).

```bash
make slm-serve    # terminal 1: serves the student on http://localhost:8090/v1
make slm-eval     # terminal 2: waits for the server, then labels the 400 eval postings
```

To use it in the app, choose provider `slm` for tier 1 on the **Settings** page.
`make slm-train` retrains it (≈ 75 min on an M2 Max).
