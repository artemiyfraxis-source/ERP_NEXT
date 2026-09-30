#!/usr/bin/env bash
# Linux/cloud twin of run-demo.ps1: fresh SQLite demo base, API serving the built frontend.
#   bash scripts/demo/run-demo.sh            # seeds backend/demo.db, API on 8021, leaves it running
# Login preview / Preview-2026!. Then screenshots: node scripts/demo/shoot.mjs
set -euo pipefail
PORT="${ERP_API_PORT:-8021}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND="$ROOT/backend"
PY="$BACKEND/.venv/bin/python"
[ -x "$PY" ] || { python3.12 -m venv "$BACKEND/.venv" || python3 -m venv "$BACKEND/.venv"; }
"$PY" -m pip install -q -r "$BACKEND/requirements.lock.txt" httpx
(cd "$ROOT/frontend" && { [ -d node_modules ] || npm ci; } && npx vite build >/dev/null)
cd "$BACKEND"
rm -f demo.db
export ERP_DATABASE_URL="sqlite+pysqlite:///demo.db" ERP_COOKIE_SECURE=0 DEMO_BASE="http://127.0.0.1:$PORT"
"$PY" -m alembic upgrade head
printf 'Preview-2026!\nPreview-2026!\n' | "$PY" -m app.identity_admin create-user --login preview \
  --email preview@example.com --name 'Артемий' --department 'Руководство' --role admin
"$PY" -m app.modules.references.load_mirror --source "${SUPPLYMAP_SOURCE_REPOSITORY:-$ROOT/../SUPPLYMAP}"
"$PY" -m app.modules.techcards.seed_library
nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" > "$BACKEND/demo-api.log" 2>&1 &
echo $! > "$BACKEND/demo-api.pid"
for _ in $(seq 60); do curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 1; done
"$PY" "$ROOT/scripts/demo/seed_demo.py"
echo "ERP demo: http://127.0.0.1:$PORT  (preview / Preview-2026!, API pid $(cat "$BACKEND/demo-api.pid"), stop: kill \$(cat backend/demo-api.pid))"
