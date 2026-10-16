#!/usr/bin/env bash
# Build the web interface (if needed) and start CPTEA on http://localhost:8000 without Docker.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt && touch backend/.venv/.installed
fi
# rebuild the web interface when it is missing or older than its sources (e.g. after git pull)
if [ ! -f frontend/dist/index.html ] || \
   [ -n "$(find frontend/src frontend/index.html frontend/package.json frontend/vite.config.js -newer frontend/dist/index.html -print -quit)" ]; then
  echo "Building the web interface…"
  (cd frontend && { [ -d node_modules ] && [ node_modules -nt package-lock.json ] || npm ci; } && npm run build)
fi
# keep Python packages in step with requirements.txt
if [ backend/requirements.txt -nt backend/.venv/.installed ]; then
  backend/.venv/bin/pip install -q -r backend/requirements.txt && touch backend/.venv/.installed
fi
[ -f .env ] && set -a && . ./.env && set +a
export CPTEA_DATA_DIR="${CPTEA_DATA_DIR:-$ROOT/data}" CPTEA_STATIC_DIR="$ROOT/frontend/dist"
mkdir -p "$CPTEA_DATA_DIR"
cd backend
exec .venv/bin/uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" --workers 1
