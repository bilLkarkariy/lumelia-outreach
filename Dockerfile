# One image for the API, the worker and the one-off jobs (migrations, demo seed).
# The API process also serves the compiled UI, so a deployment is a single origin.
FROM node:22.22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14.1-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /usr/local/bin/uv
# pg_dump and psql back the encrypted backup and its restore check (scripts/backup.py).
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY backend/scripts ./scripts
COPY --from=frontend /build/dist ./static
RUN useradd --uid 10001 --create-home lumelia && chown -R lumelia:lumelia /app
USER lumelia
ENV PATH="/app/.venv/bin:$PATH"
CMD ["sh", "-c", "exec uvicorn app.site:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
