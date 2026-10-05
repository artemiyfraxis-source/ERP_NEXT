#!/usr/bin/env bash
# Restore the database from a dump:
#   deploy/restore.sh /opt/erp/backups/daily/erp_2026-10-01_0300.dump
#   deploy/restore.sh /opt/erp/backups/pre-update/erp_2026-10-06_020013-final.dump   # state right before a migration
# Checks the dump's .sha256 when there is one, takes a safety dump first, stops api/worker, replaces the whole database.
# api/worker start again only when the restored database is at the migration head of the running image: the API
# refuses any other revision (backend/docker-entrypoint.sh), see deploy/README.md «Ручное восстановление».
set -euo pipefail
dump=${1:?usage: restore.sh <file.dump>}
export ERP_HOME=${ERP_HOME:-/opt/erp}
cd "$(dirname "$0")"
case "$dump" in "$ERP_HOME/backups/"*) ;; *) echo "dump must be under $ERP_HOME/backups" >&2; exit 2 ;; esac
[ -s "$dump" ] || { echo "нет файла копии $dump" >&2; exit 2; }
if [ -f "$dump.sha256" ]; then
  (cd "$(dirname "$dump")" && sha256sum -c --status "$(basename "$dump").sha256") \
    || { echo "контрольная сумма $dump не сходится — копия повреждена, восстановление не начато" >&2; exit 2; }
fi
./erp.sh backup-now
./erp.sh stop api worker
./erp.sh exec -T backup pg_restore --clean --if-exists --no-owner -d erp "/backups/${dump#"$ERP_HOME/backups/"}"
db_rev=$(./erp.sh exec -T db psql -U erp -d erp -tAc 'SELECT version_num FROM alembic_version' | tr -d '[:space:]')
head=$(docker run --rm --network none erp-next:latest alembic heads 2>/dev/null | awk 'NR == 1 { print $1 }')
if [ -n "$head" ] && [ "$db_rev" = "$head" ]; then
  ./erp.sh start api worker
  echo "restored from $dump (ревизия $db_rev)"
  exit 0
fi
echo "База восстановлена из $dump на ревизии ${db_rev:-?}, а код на ревизии ${head:-?}: API и worker НЕ запущены." >&2
echo "Вернуть код той версии, что была при копии (push.ps1 -Ref <коммит из app.prev/REVISION>)," >&2
echo "или накатить миграции по протоколу (копия → репетиция → сверка): bash $ERP_HOME/app/deploy/update.sh" >&2
exit 1
