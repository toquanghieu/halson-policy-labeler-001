# Three ways to run (README "Run it"):
#   make up                  full stack in Docker: Postgres, ElasticMQ (SQS), SeaweedFS (S3), api, 2 workers
#   make infra + api/worker  infrastructure in Docker, API and/or workers on the host (e.g. claude CLI provider)
#   make dev                 one process, no services: SQLite, in-memory queue, embedded worker
#
# Layout: core/ (shared package `labeler`), backend/ (API, `labeler_api`), worker/ (`labeler_worker`),
# one uv workspace at the root; every target runs from here.
.PHONY: help setup data ui dev up down observability infra api worker migrate import-legacy lint test postman eval demo-v2 demo-v2-dev distill-teacher distill-data slm-train slm-serve slm-eval

ENV_FILE ?= .env
HOMELAB_ENV ?= .env.homelab
load_env = set -a; [ -f $(ENV_FILE) ] && . ./$(ENV_FILE); set +a
load_homelab = set -a; [ -f $(ENV_FILE) ] && . ./$(ENV_FILE); . ./$(HOMELAB_ENV); set +a

help:
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

# ---- containers -------------------------------------------------------------------
up: ## full stack in Docker on http://localhost:8000 (scale workers: docker compose up --scale worker=4)
	docker compose up --build -d
	@echo "UI/API http://localhost:8000 · queue browser http://localhost:9325 · S3 http://localhost:8333"

observability: ## + Prometheus (9090) and Grafana (3000)
	docker compose --profile observability up --build -d

down: ## stop everything (data volumes kept; `docker compose down -v` to wipe)
	docker compose --profile observability down

infra: ## only Postgres, ElasticMQ, SeaweedFS (+ one-shot init), for host-run api/worker
	docker compose up --build -d postgres elasticmq seaweedfs init
	@[ -f $(HOMELAB_ENV) ] || cp .env.homelab.example $(HOMELAB_ENV)

api: ## API + UI on the host against `make infra` (no embedded worker)
	$(load_homelab) && uv run uvicorn labeler_api.main:app --port 8000

worker: ## one worker on the host against `make infra`; run several for more throughput
	$(load_homelab) && uv run python -m labeler_worker

# ---- single process, no services ----------------------------------------------------
setup: ## install deps, load data, train router, build UI (for `make dev`)
	uv sync
	cd frontend && npm ci
	$(MAKE) data ui

data:
	$(load_env) && uv run python -m scripts.init

ui:
	cd frontend && npx vite build

dev: ## API + UI + embedded worker on http://localhost:8000, SQLite + in-memory queue
	$(load_env) && uv run uvicorn labeler_api.main:app --port 8000

# ---- database -----------------------------------------------------------------------
migrate: ## apply Alembic migrations to DATABASE_URL
	$(load_env) && uv run alembic -c core/alembic.ini upgrade head

import-legacy: ## copy data/labeler.db (pre-Postgres runs) into DATABASE_URL
	$(load_env) && uv run python -m scripts.import_legacy

# ---- quality ----------------------------------------------------------------------
lint: ## ruff + mypy (core, backend, worker, scripts, tests), eslint + tsc (frontend)
	uvx ruff check . && uvx ruff format --check . && uvx mypy core/labeler backend/labeler_api worker/labeler_worker
	cd frontend && npx eslint . && npx tsc -b

test: ## unit + integration tests (Postgres tests use Docker, skipped without it)
	uv run pytest -q

postman: ## regenerate postman/ (every request + real example responses, captured in-process)
	uv run python -m scripts.postman_collection

# ---- evaluation -------------------------------------------------------------------
eval: ## label the 400 eval postings under the published policy (in-process, resumable)
	$(load_env) && uv run python -m scripts.run_eval

demo-v2: ## create v2 as a draft, shadow-run it on the eval set, publish, compare
	$(load_env) && uv run python -m scripts.policy_demo

demo-v2-dev: ## v1 vs v2 on 12 dev postings picked to exercise §6 (no publish)
	$(load_env) && uv run python -m scripts.policy_demo_dev

# ---- distilled small model (nice-to-have; Apple silicon, MLX) -----------------------
SLM_BASE ?= mlx-community/Qwen2.5-1.5B-Instruct-4bit
SLM_ADAPTER ?= data/distill/adapter
MLX = uvx --python 3.12 --from mlx-lm

distill-teacher: ## Claude labels the 1,000 balanced dev rows (claude CLI; ~1-1.5k calls, resumable)
	$(load_env) && LLM_PROVIDER=claude_cli uv run python -m scripts.distill teacher

distill-data: ## teacher labels (committed data/distill/teacher.json.gz) -> {train,valid}.jsonl(.gz)
	$(load_env) && uv run python -m scripts.distill dataset

slm-train: ## LoRA fine-tune of $(SLM_BASE) on data/distill (MLX, local GPU; the trained adapter is already committed)
	uv run python -m scripts.distill unpack && $(MLX) mlx_lm.lora --model $(SLM_BASE) --train --data data/distill \
	  --adapter-path $(SLM_ADAPTER) --mask-prompt --num-layers 16 --batch-size 2 --iters 900 \
	  --learning-rate 1e-4 --max-seq-length 4096 --grad-checkpoint --steps-per-eval 150 --seed 7

slm-serve: ## serve the student on http://localhost:8090/v1 (OpenAI-style chat completions)
	$(MLX) mlx_lm.server --model $(SLM_BASE) --adapter-path $(SLM_ADAPTER) --port 8090 --temp 0

slm-eval: ## the student alone on the 400 eval postings (needs `make slm-serve` running; waits up to 5 min for it)
	@for i in $$(seq 1 150); do curl -sf http://localhost:8090/v1/models >/dev/null && break; \
	  [ $$i = 150 ] && { echo "no model server on :8090 - start 'make slm-serve' first"; exit 1; }; sleep 2; done
	$(load_env) && uv run python -m scripts.run_eval --tag slm --provider slm \
	  --tier1 slm-qwen2.5-1.5b-distilled --no-escalation --concurrency 1
