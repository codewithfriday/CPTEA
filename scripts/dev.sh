#!/usr/bin/env bash
# Development: API with auto-reload on :8000 and the Vite dev server on :5173 (proxies /api).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export CPTEA_DATA_DIR="${CPTEA_DATA_DIR:-$ROOT/data}"
mkdir -p "$CPTEA_DATA_DIR"
(cd "$ROOT/backend" && python3 -m uvicorn app.main:app --reload --port 8000) &
API=$!
trap 'kill $API' EXIT
cd "$ROOT/frontend" && npm run dev
