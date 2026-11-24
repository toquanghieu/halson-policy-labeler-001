# The worker service: consumes labels-live / labels-backfill (ECS service "workers" in the design).
# Ships core/ + worker/ only: no FastAPI, no UI. Also runs the one-shot compose `init` job
# (migrations, data load, router), which is why scripts/ and the eval ids are copied in.
# Build from the repo root: docker build -f worker/Dockerfile .
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
# Dependencies first (cached layer): the lock needs every member's pyproject, not its code.
COPY pyproject.toml uv.lock ./
COPY core/pyproject.toml core/
COPY backend/pyproject.toml backend/
COPY worker/pyproject.toml worker/
RUN uv sync --frozen --no-dev --package labeler-worker --no-install-workspace
COPY core/ core/
COPY worker/ worker/
COPY scripts/ scripts/
COPY policies/ policies/
COPY data/eval/eval_job_ids.txt data/eval/eval_job_ids.txt
RUN uv sync --frozen --no-dev --package labeler-worker
ENV PATH="/app/.venv/bin:$PATH" DATA_DIR=/app/data POLICIES_DIR=/app/policies PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app /app/data
USER app
EXPOSE 9100
STOPSIGNAL SIGTERM
CMD ["python", "-m", "labeler_worker"]
