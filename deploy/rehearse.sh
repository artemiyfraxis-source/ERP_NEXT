#!/usr/bin/env bash
# Update rehearsal on a copy of the production database (owner's rule: бэкап → прогон на копии → сверка → только потом прод).
#   rehearse.sh <dump> <head-revision>   update.sh calls it when the new version brings migrations; exit 0 = green.
#   rehearse.sh migrate-script           prints the in-container script update.sh also runs on production.
#   rehearse.sh health-script            prints the Python probe for /api/health?db=1 (update.sh uses it too).
# A run: throwaway compose project `erp-rehearsal` (docker-compose.rehearsal.yml: postgres:16 on an anonymous volume,
# internal network, no secrets, no worker) → pg_restore of the fresh pre-update dump → app.db_integrity snapshot →
# alembic upgrade head → snapshot → compare → the new API against the copy: /api/health?db=1 must name the head
# revision, then read GETs as a temporary admin created in the copy. The project is always torn down (trap).
# Failure: exit 1, the last line names the step; with REHEARSAL_STEP_FILE set, «step<TAB>message» goes there too.
# Production is never touched here: every docker command below targets the `erp-rehearsal` project.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)

migrate_script() {
  # Runs inside a container of the NEW image (sh, workdir /app/backend). Exit code = the failed step (see step_name).
  cat <<'SH'
set -u
D=${INTEGRITY_DIR:-/tmp}
python -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("app.db_integrity") else 1)' \
  || { echo "в этой версии нет модуля app.db_integrity — сверка невозможна" >&2; exit 20; }
echo "== снимок базы до миграции"
python -m app.db_integrity snapshot --out "$D/before.json" || exit 21
echo "== alembic upgrade head"
alembic upgrade head || exit 22
echo "== снимок базы после миграции"
python -m app.db_integrity snapshot --out "$D/after.json" || exit 23
echo "== сверка до/после"
python -m app.db_integrity compare "$D/before.json" "$D/after.json" || exit 24
echo "== сверка без расхождений"
SH
}

health_script() {
  # python - <head>: exit 0 only when /api/health?db=1 answers 200 and names the head revision.
  cat <<'PY'
import sys, urllib.request
head = sys.argv[1]
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/health?db=1", timeout=5) as response:
        status, body = response.status, response.read().decode("utf-8", "replace")
except Exception as error:  # connection refused while starting, HTTP 5xx, ...
    print(f"/api/health?db=1: {error}", file=sys.stderr)
    sys.exit(1)
if status != 200 or head not in body:
    print(f"/api/health?db=1: {status} {body[:300]} — нет ревизии {head}", file=sys.stderr)
    sys.exit(2)
print(f"/api/health?db=1: 200, ревизия {head}")
PY
}

checks_script() {
  # Read GETs as the temporary admin of the copy (login + cookie, as the browser does). Never writes business data.
  cat <<'PY'
import json, os, sys, urllib.error, urllib.request
base = "http://127.0.0.1:8000"
login, password = os.environ["CHECK_LOGIN"], os.environ["CHECK_PASSWORD"]

def call(path, data=None, cookie=""):
    request = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if cookie:
        request.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null"), response.headers
    except urllib.error.HTTPError as error:
        sys.exit(f"{'POST' if data is not None else 'GET'} {path}: HTTP {error.code} {error.read()[:300]!r}")
    except Exception as error:
        sys.exit(f"{path}: {error}")

status, body, _ = call("/api/auth/login-staff")
if not isinstance(body, dict) or "staff" not in body:
    sys.exit(f"GET /api/auth/login-staff: нет списка staff ({status})")
print(f"ok GET /api/auth/login-staff: {len(body['staff'])} сотрудников")
status, body, headers = call("/api/auth/login", json.dumps({"login": login, "password": password}).encode())
cookie = "; ".join(item.split(";", 1)[0] for item in headers.get_all("Set-Cookie") or [])
if not cookie:
    sys.exit("POST /api/auth/login: сервер не выдал cookie сессии")
status, body, _ = call("/api/auth/me", cookie=cookie)
print(f"ok GET /api/auth/me: {status}")
status, body, _ = call("/api/stock?limit=5", cookie=cookie)
if not isinstance(body, dict) or "lots" not in body:
    sys.exit(f"GET /api/stock: нет списка lots ({status})")
print(f"ok GET /api/stock: {body.get('total')} партий")
PY
}

case "${1:-}" in
  migrate-script) migrate_script; exit 0 ;;
  health-script) health_script; exit 0 ;;
esac

dump=${1:?usage: rehearse.sh <dump> <head-revision> | migrate-script | health-script}
head=${2:?usage: rehearse.sh <dump> <head-revision>}
export ERP_CANDIDATE_IMAGE=${ERP_CANDIDATE_IMAGE:-erp-next:candidate}
compose=(docker compose -p erp-rehearsal -f "$here/docker-compose.rehearsal.yml")
check_login=erp-rehearsal-check
check_password=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')

teardown() { "${compose[@]}" down -v --remove-orphans --timeout 5 >/dev/null 2>&1 || true; }
fail() {  # step message...
  local step=$1; shift
  echo "-- журнал копии (последние строки):" >&2
  "${compose[@]}" logs --no-color --tail 40 api db >&2 2>/dev/null || true
  [ -n "${REHEARSAL_STEP_FILE:-}" ] && printf '%s\t%s\n' "$step" "$*" > "$REHEARSAL_STEP_FILE"
  echo "РЕПЕТИЦИЯ НЕ ПРОШЛА ($step): $*" >&2
  exit 1
}
step_name() {
  case "$1" in
    20) echo "integrity-missing" ;; 21) echo "snapshot-before" ;; 22) echo "migrate" ;;
    23) echo "snapshot-after" ;; 24) echo "compare" ;; *) echo "migrate" ;;
  esac
}

[ -s "$dump" ] || fail backup "нет копии базы $dump"
teardown   # leftovers of an interrupted run
trap teardown EXIT
trap 'exit 130' INT TERM

echo "== репетиция: копия базы из $(basename "$dump"), новая версия $ERP_CANDIDATE_IMAGE, голова $head"
"${compose[@]}" up -d db || fail start "не поднялась временная база"
ready=0
for _ in $(seq 1 60); do
  # TCP, not the socket: during initdb the image runs a temporary server on the socket only.
  if "${compose[@]}" exec -T -e PGPASSWORD=rehearsal db pg_isready -q -h 127.0.0.1 -U erp -d erp >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 2
done
[ "$ready" = 1 ] || fail start "временная база не ответила за 2 минуты"

echo "== pg_restore копии"
"${compose[@]}" exec -T -e PGPASSWORD=rehearsal db \
  pg_restore -h 127.0.0.1 -U erp -d erp --no-owner --no-acl < "$dump" || fail restore "копия базы не восстановилась (pg_restore)"

code=0
migrate_script | "${compose[@]}" run --rm -T -e INTEGRITY_DIR=/tmp api sh -s || code=$?
if [ "$code" != 0 ]; then
  fail "$(step_name "$code")" "миграции или сверка на копии не прошли (код $code)"
fi

echo "== временный пользователь проверки в копии"
"${compose[@]}" run --rm -T -e ERP_NEW_USER_PASSWORD="$check_password" api \
  python -m app.identity_admin create-user --login "$check_login" --email "$check_login@erp.local" \
  --name 'Проверка обновления' --department 'Руководящий состав' --level admin >/dev/null \
  || fail checks "не создался временный пользователь в копии"

echo "== новая версия на копии"
"${compose[@]}" up -d --no-deps api || fail health "новая версия не запустилась на копии"
healthy=0
for _ in $(seq 1 60); do
  if health_script | "${compose[@]}" exec -T api python - "$head" 2>/dev/null; then healthy=1; break; fi
  sleep 3
done
if [ "$healthy" = 0 ]; then
  health_script | "${compose[@]}" exec -T api python - "$head" >&2 || true
  fail health "новая версия на копии не ответила /api/health?db=1 с ревизией $head за 3 минуты"
fi
checks_script | "${compose[@]}" exec -T -e CHECK_LOGIN="$check_login" -e CHECK_PASSWORD="$check_password" api python - \
  || fail checks "чтение данных новой версией на копии не прошло"
echo "== репетиция прошла: миграции до $head на копии, сверка без расхождений, API отвечает"
