# The API service: FastAPI + the analyst UI on port 8000 (ECS service "api" in the design).
# Ships core/ + backend/ only. It enqueues work; the worker image consumes it
# (EMBEDDED_WORKER=false here, and the worker package is not installed).
# Build from the repo root: docker build -f backend/Dockerfile .

# --- UI build ---
FROM node:20.19-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npx vite build

# --- API ---
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
COPY core/pyproject.toml core/
COPY backend/pyproject.toml backend/
COPY worker/pyproject.toml worker/
RUN uv sync --frozen --no-dev --package labeler-api --no-install-workspace
COPY core/ core/
COPY backend/ backend/
COPY policies/ policies/
COPY data/eval/eval_job_ids.txt data/eval/eval_job_ids.txt
RUN uv sync --frozen --no-dev --package labeler-api
COPY --from=ui /ui/dist /app/frontend/dist
ENV PATH="/app/.venv/bin:$PATH" DATA_DIR=/app/data POLICIES_DIR=/app/policies PYTHONUNBUFFERED=1 EMBEDDED_WORKER=false
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app /app/data
USER app
EXPOSE 8000
CMD ["uvicorn", "labeler_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
