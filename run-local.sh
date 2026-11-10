#!/usr/bin/env bash
# Run the API, the worker and the Vite dev server against the database configured in backend/.env.
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f backend/.env ]]; then
  echo 'Create backend/.env from backend/.env.example first.' >&2
  exit 1
fi
command -v uv >/dev/null
command -v npm >/dev/null
(cd backend && uv sync && uv run alembic upgrade head)
(cd frontend && npm ci)
api_pid=''
worker_pid=''
cleanup() {
  [[ -z "$api_pid" ]] || kill "$api_pid" 2>/dev/null || true
  [[ -z "$worker_pid" ]] || kill "$worker_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
(cd backend && exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log) &
api_pid=$!
(cd backend && exec uv run python -m app.worker) &
worker_pid=$!
echo 'Lumelia Outreach: http://127.0.0.1:5173 (Ctrl+C stops every process).'
(cd frontend && npm run dev)
