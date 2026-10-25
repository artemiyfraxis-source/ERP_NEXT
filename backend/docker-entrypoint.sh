#!/bin/sh
# API start in the container (Dockerfile CMD). Migrations are NOT applied here any more: deploy/update.sh applies them
# after a backup and a rehearsal on a copy of the database. The API starts only when the database is exactly at the
# migration head of this image; otherwise it stops with a Russian error (the old code never runs on a newer schema,
# the new code never runs on an older one).
#   ERP_AUTO_MIGRATE=1 — first install only (deploy/bootstrap.sh on a fresh database): migrate, then start.
#   ERP_API_WORKERS=N — N uvicorn worker processes (deploy/docker-compose.yml sets 8; unset → 1, as before). With N > 1
#     a worker is replaced after ERP_API_MAX_REQUESTS requests (+ up to 10 % jitter, so they never all restart at
#     once), answers the supervisor's health ping within 30 s (a long JSON answer holds the GIL) and gets 20 s to finish
#     its requests on a stop. deploy/README.md, «Ядра и память».
# One-off commands (`erp.sh cli ...`, the worker) replace this CMD and are not gated.
set -eu   # workdir is /app/backend (Dockerfile WORKDIR): alembic.ini is here

if [ "${ERP_AUTO_MIGRATE:-0}" = 1 ]; then
  echo "ERP_AUTO_MIGRATE=1: накатываю миграции перед стартом (первая установка)"
  alembic upgrade head
else
  python - <<'PY'
import os
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

heads = sorted(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
url = os.environ.get("ERP_DATABASE_URL", "")
if not url:
    sys.exit("API не запущен: не задан ERP_DATABASE_URL")
engine = create_engine(url, pool_pre_ping=True)
try:
    with engine.connect() as connection:
        if not inspect(connection).has_table("alembic_version"):
            sys.exit(
                "API не запущен: в базе нет схемы ERP (таблицы alembic_version). Первая установка — "
                "deploy/bootstrap.sh (ERP_AUTO_MIGRATE=1)."
            )
        current = sorted(row[0] for row in connection.execute(text("SELECT version_num FROM alembic_version")))
except SystemExit:
    raise
except Exception as error:  # the database is not reachable yet: the restart policy tries again
    sys.exit(f"API не запущен: база недоступна ({type(error).__name__}: {error})")
finally:
    engine.dispose()
if current != heads:
    sys.exit(
        "API не запущен: ревизия базы " + (", ".join(current) or "пусто") + " не совпадает с ревизией кода "
        + ", ".join(heads) + ". Миграции накатывает только deploy/update.sh (копия → репетиция → сверка); "
        "если код откатили после миграции — верните новую версию или восстановите копию из backups/pre-update "
        "(deploy/README.md, «Ручное восстановление»)."
    )
print("ревизия базы совпадает с кодом: " + ", ".join(heads))
PY
fi
whole() {  # name value → the value when it is a whole number ≥ 1, else stop with a Russian error
  case "$2" in
    '' | *[!0-9]*) echo "API не запущен: $1=$2 — нужно целое число от 1" >&2; exit 1 ;;
  esac
  [ "$2" -ge 1 ] || { echo "API не запущен: $1=$2 — нужно целое число от 1" >&2; exit 1; }
  printf '%s' "$2"
}
workers=$(whole ERP_API_WORKERS "${ERP_API_WORKERS:-1}")
if [ "$workers" -gt 1 ]; then
  max_requests=$(whole ERP_API_MAX_REQUESTS "${ERP_API_MAX_REQUESTS:-10000}")
  echo "API: процессов $workers, замена процесса после $max_requests запросов"
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' \
    --workers "$workers" --limit-max-requests "$max_requests" --limit-max-requests-jitter $((max_requests / 10)) \
    --timeout-worker-healthcheck 30 --timeout-graceful-shutdown 20
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
