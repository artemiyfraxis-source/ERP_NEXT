#!/usr/bin/env bash
# Restore the database from a dump — into a NEW database first; production switches over only after the checks:
#   deploy/restore.sh /opt/erp/backups/daily/erp_2026-10-10_030001.dump
#   deploy/restore.sh /opt/erp/backups/pre-update/erp_2026-10-06_020013-final.dump   # state right before a migration
#  1. flock backups/.lock: no nightly/pre-update dump and no rotation runs meanwhile, a second restore waits
#     (ERP_RESTORE_LOCK_WAIT, 900 s);
#  2. the dump's .sha256 must match (a dump without one — taken before 10.10.2026 — is pinned by its hash now);
#     pg_restore --list must read it; its alembic revision and row count per table are read from the dump itself;
#  3. CREATE DATABASE erp_restore_<ts>; the pinned hash is checked right before and right after
#     pg_restore --exit-on-error into it;
#  4. checks of erp_restore_<ts>: alembic_version is the dump's, the same tables as the dump, the same row count in
#     every table, app.db_integrity snapshot reads it (status/restore/<ts>/restored.json);
#     any failure up to here: erp_restore_<ts> is dropped, production was never stopped;
#  5. switch: stop api+worker → safety dump of the database being replaced, backups/pre-restore/erp_<ts>.dump + .sha256
#     (exact state, nothing writes any more) → ONE transaction: erp → erp_prev_<ts>, erp_restore_<ts> → erp.
#     A failure here: nothing renamed, api+worker start again on the old database;
#  6. the restored revision is the migration head of erp-next:latest → api+worker start and /api/health?db=1 must
#     name it, else the rename is rolled back and the old database runs again. Another revision: api+worker stay
#     stopped and what to do is printed (the code of that revision has to come back first).
# erp_prev_<ts> stays until the owner drops it. Exit: 0 restored and running; 1 not restored, production as before;
# 2 usage; 3 restored, api/worker stopped (revision ≠ code); 4 rollback of the switch failed — read the message.
# Result: status/restore-result.json; files of the run: status/restore/<ts>/. deploy/README.md «Ручное восстановление».
set -euo pipefail
dump=${1:?usage: restore.sh <file.dump>}
export ERP_HOME=${ERP_HOME:-/opt/erp}
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
erp=./erp.sh
backups=$ERP_HOME/backups
ts=$(date +%Y%m%d_%H%M%S)
restore_db=erp_restore_$ts
prev_db=erp_prev_$ts
work=$ERP_HOME/status/restore/$ts
result=$ERP_HOME/status/restore-result.json
lock_wait=${ERP_RESTORE_LOCK_WAIT:-900}
health_py=$(bash "$here/rehearse.sh" health-script)

case "$dump" in
  "$backups/"*) ;;
  *) echo "копия должна лежать в $backups" >&2; exit 2 ;;
esac
case "$dump" in *"/../"* | *"/./"*) echo "путь к копии без /../ и /./" >&2; exit 2 ;; esac
if [ ! -f "$dump" ] || [ ! -s "$dump" ]; then echo "нет файла копии $dump" >&2; exit 2; fi
rel=${dump#"$backups/"}

step=start created=0 stopped=0 switched=0 finished=0 pinned="" dump_rev=""
mkdir -p "$work"

json_str() {
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; s=${s//$'\r'/}; s=${s//$'\t'/ }
  printf '"%s"' "$s"
}
write_result() {  # ok message
  local tmp=$result.tmp
  {
    printf '{"ok": %s, "step": %s, "message": %s, "dump": %s, "sha256": %s, "revision": %s' "$1" "$(json_str "$step")" \
      "$(json_str "$2")" "$(json_str "$dump")" "$(json_str "$pinned")" "$(json_str "$dump_rev")"
    printf ', "previous_db": %s, "run": %s, "at": %s}\n' "$(json_str "$([ "$switched" = 1 ] && echo "$prev_db")")" \
      "$(json_str "$work")" "$(json_str "$(date '+%d.%m.%Y %H:%M:%S')")"
  } > "$tmp" && mv -f "$tmp" "$result"
}
say() { echo "== $*"; }
psql_db() {  # database sql... → tuples only, unaligned, '|' between columns
  local db=$1; shift
  local args=() sql
  for sql in "$@"; do args+=(-c "$sql"); done
  "$erp" exec -T db psql -X -q -U erp -d "$db" -v ON_ERROR_STOP=1 -tA "${args[@]}" | tr -d '\r'   # CR: psql of a Windows test run
}
drop_restore_db() {
  [ "$created" = 1 ] || return 0
  psql_db postgres "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$restore_db' AND pid <> pg_backend_pid()" \
    "DROP DATABASE IF EXISTS $restore_db" >/dev/null 2>&1 \
    && created=0 || echo "не удалена временная база $restore_db — удалить: ./erp.sh exec db dropdb -U erp $restore_db" >&2
}
undo() {  # before the switch: production as it was
  drop_restore_db
  if [ "$stopped" = 1 ]; then "$erp" start api worker || echo "api/worker не запустились: ./erp.sh up -d" >&2; stopped=0; fi
}
fail() {  # message — production as before the restore
  finished=1
  undo
  write_result false "$1"
  echo "ВОССТАНОВЛЕНИЕ НЕ ВЫПОЛНЕНО ($step): $1 — рабочая база не изменена" >&2
  exit 1
}
# shellcheck disable=SC2317,SC2329  # the EXIT trap calls it
on_exit() {
  local code=$?
  [ "$finished" = 0 ] && [ "$code" != 0 ] || return 0
  if [ "$switched" = 0 ]; then
    undo
    write_result false "сбой на шаге $step (код $code); рабочая база не изменена"
    echo "ВОССТАНОВЛЕНИЕ НЕ ВЫПОЛНЕНО ($step): сбой (код $code) — рабочая база не изменена" >&2
  else
    write_result false "сбой на шаге $step (код $code) после переключения: erp — восстановленная база, прежняя — $prev_db"
    echo "СБОЙ ПОСЛЕ ПЕРЕКЛЮЧЕНИЯ ($step, код $code): база erp — восстановленная, прежняя — $prev_db; api/worker: ./erp.sh ps" >&2
  fi
}
trap on_exit EXIT

hash_of() { sha256sum < "$1" | cut -d' ' -f1; }
pinned_ok() { [ "$(hash_of "$dump")" = "$pinned" ]; }
rename_pair() {  # from1 to1 from2 to2 — both renames in one transaction (all or nothing), 3 tries
  local try
  for try in 1 2 3; do
    psql_db postgres "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$1', '$3') AND pid <> pg_backend_pid()" \
      >/dev/null || true
    if "$erp" exec -T db psql -X -q -U erp -d postgres -v ON_ERROR_STOP=1 -1 \
         -c "ALTER DATABASE $1 RENAME TO $2" -c "ALTER DATABASE $3 RENAME TO $4"; then
      return 0
    fi
    echo "переименование баз не прошло (попытка $try)" >&2
    sleep 2
  done
  return 1
}
databases() { psql_db postgres "SELECT datname FROM pg_database WHERE datname IN ('erp', '$prev_db', '$restore_db') ORDER BY 1" | tr '\n' ' '; }

# 1. Lock.
step=lock
command -v flock >/dev/null 2>&1 || fail "нет flock (util-linux) — без блокировки не восстанавливаю"
exec 9>> "$backups/.lock"
say "жду блокировку копий ($backups/.lock, до $lock_wait с)"
flock -w "$lock_wait" 9 || fail "идёт копирование или другое восстановление дольше $lock_wait с"

# 2. The source.
step=checksum
pinned=$(hash_of "$dump")
if [ -f "$dump.sha256" ]; then
  [ "$(cut -d' ' -f1 < "$dump.sha256")" = "$pinned" ] \
    || fail "контрольная сумма $dump не сходится с $dump.sha256 — копия повреждена"
  say "контрольная сумма сходится: $pinned"
else
  echo "ВНИМАНИЕ: у копии нет .sha256 (снята до 10.10.2026) — проверяю читаемость, sha256 сейчас: $pinned" >&2
fi
step=up
if ! "$erp" exec -T db pg_isready -q -U erp -d postgres >/dev/null 2>&1 || ! "$erp" exec -T backup true >/dev/null 2>&1; then
  "$erp" up -d db backup >/dev/null || fail "не поднялись база и контейнер копий"
  for _ in $(seq 1 30); do
    "$erp" exec -T db pg_isready -q -U erp -d postgres >/dev/null 2>&1 && break
    sleep 2
  done
fi
step="read-dump"
"$erp" exec -T backup pg_restore --list "/backups/$rel" | tr -d '\r' > "$work/toc.txt" || fail "копия не читается (pg_restore --list)"
awk '$4 == "TABLE" && $5 == "public" { print $6 }' "$work/toc.txt" | LC_ALL=C sort -u > "$work/dump-tables.txt"
[ -s "$work/dump-tables.txt" ] || fail "в копии нет таблиц"
"$erp" exec -T backup pg_restore --data-only -f - "/backups/$rel" | tr -d '\r' | awk '
  incopy && $0 == "\\." { print "R", table, rows; incopy = 0; next }
  incopy { rows++; if (table == "alembic_version") print "V", $0; next }
  /^COPY .* FROM stdin;$/ { table = $2; sub(/^public\./, "", table); gsub(/"/, "", table); rows = 0; incopy = 1 }
' > "$work/dump-data.txt" || fail "данные копии не читаются (pg_restore --data-only)"
dump_rev=$(awk '$1 == "V" { print $2 }' "$work/dump-data.txt")
if [ -z "$dump_rev" ] || [ "$(printf '%s\n' "$dump_rev" | wc -l)" -ne 1 ]; then
  fail "в копии не одна ревизия alembic_version: '${dump_rev//$'\n'/ }'"
fi
awk 'NR == FNR { if ($1 == "R") rows[$2] = $3; next } { print $1, ($1 in rows ? rows[$1] : "нет-данных") }' \
  "$work/dump-data.txt" "$work/dump-tables.txt" | LC_ALL=C sort > "$work/dump-counts.txt"
say "копия: ревизия $dump_rev, таблиц $(wc -l < "$work/dump-tables.txt"), строк $(awk '{ s += $2 } END { print s + 0 }' "$work/dump-counts.txt")"

step=disk
db_kb=$(psql_db postgres "SELECT pg_database_size('erp') / 1024" 2>/dev/null | tr -cd 0-9 || true)
docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
if [ -n "$db_kb" ] && [ -n "$docker_root" ] && [ -d "$docker_root" ]; then
  avail_kb=$(df -Pk "$docker_root" | awk 'NR == 2 { print $4 }')
  need_kb=$((db_kb * 12 / 10 + 512 * 1024))
  [ -z "$avail_kb" ] || [ "$avail_kb" -ge "$need_kb" ] \
    || fail "мало места под вторую базу: свободно $((avail_kb / 1024)) МБ в $docker_root, нужно $((need_kb / 1024)) МБ"
fi

# 3. Into a new database.
step=create
psql_db postgres "CREATE DATABASE $restore_db" >/dev/null || fail "не создана база $restore_db"
created=1
step=restore
pinned_ok || fail "копия изменилась после проверки контрольной суммы"
say "pg_restore в $restore_db (рабочая база erp работает дальше)"
"$erp" exec -T backup pg_restore --no-owner --exit-on-error -d "$restore_db" "/backups/$rel" \
  || fail "pg_restore в $restore_db не прошёл"
pinned_ok || fail "копия изменилась во время восстановления"

# 4. Checks.
step=verify
rev_rows=$(psql_db "$restore_db" "SELECT version_num FROM alembic_version") || fail "в $restore_db не читается alembic_version"
[ "$rev_rows" = "$dump_rev" ] || fail "ревизия $restore_db '${rev_rows//$'\n'/ }' не равна ревизии копии $dump_rev"
psql_db "$restore_db" "SELECT tablename FROM pg_tables WHERE schemaname = 'public'" | LC_ALL=C sort -u > "$work/restored-tables.txt" \
  || fail "не прочитан список таблиц $restore_db"
if ! cmp -s "$work/dump-tables.txt" "$work/restored-tables.txt"; then
  fail "таблицы $restore_db не совпали с копией: $(diff "$work/dump-tables.txt" "$work/restored-tables.txt" | grep '^[<>]' | head -5 | tr '\n' ' ')"
fi
grep -q -v -x '[a-z0-9_]*' "$work/restored-tables.txt" && fail "в $restore_db таблица с необычным именем: $(grep -v -x '[a-z0-9_]*' "$work/restored-tables.txt" | head -1)"
count_sql=""
while read -r table; do
  count_sql="${count_sql:+$count_sql UNION ALL }SELECT '$table ' || count(*) FROM public.\"$table\""
done < "$work/restored-tables.txt"
psql_db "$restore_db" "$count_sql" | LC_ALL=C sort > "$work/restored-counts.txt" || fail "не посчитаны строки $restore_db"
if ! cmp -s "$work/dump-counts.txt" "$work/restored-counts.txt"; then
  fail "число строк $restore_db не совпало с копией: $(diff "$work/dump-counts.txt" "$work/restored-counts.txt" | grep '^[<>]' | head -6 | tr '\n' ' ')"
fi
say "проверено: ревизия $dump_rev, таблицы и число строк в каждой — как в копии"
chown 10001 "$work" 2>/dev/null || true
code=0
# shellcheck disable=SC2016  # expanded inside the container
"$erp" run --rm --no-deps -T -v "$work:/integrity" api sh -c '
  python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(\"app.db_integrity\") else 3)" || exit 3
  ERP_DATABASE_URL="${ERP_DATABASE_URL%/*}/$1" exec python -m app.db_integrity snapshot --out /integrity/restored.json
' sh "$restore_db" || code=$?
case "$code" in
  0) ;;
  3) echo "ВНИМАНИЕ: в образе нет app.db_integrity — снимок восстановленной базы не снят" >&2 ;;
  *) fail "app.db_integrity не прочитал $restore_db (код $code)" ;;
esac

# 5. Switch.
step=stop
stopped=1
"$erp" stop api worker || fail "не остановились api и worker"
step="safety-dump"
safety=$backups/pre-restore/erp_$ts.dump
if "$erp" exec -T -e BACKUP_LOCK_HELD=1 backup bash -s -- --pre-restore "$ts" "$rel" < "$here/backup-loop.sh" \
   && [ -s "$safety" ] && [ "$(cut -d' ' -f1 < "$safety.sha256" 2>/dev/null)" = "$(hash_of "$safety")" ]; then
  say "копия заменяемой базы: $safety"
elif [ "${ERP_RESTORE_WITHOUT_SAFETY_DUMP:-0}" = 1 ]; then
  echo "ВНИМАНИЕ: копия заменяемой базы не снята (ERP_RESTORE_WITHOUT_SAFETY_DUMP=1); прежняя база останется как $prev_db" >&2
else
  fail "копия заменяемой базы не снята — без неё не переключаю (если база не читается: ERP_RESTORE_WITHOUT_SAFETY_DUMP=1, прежняя всё равно останется как $prev_db)"
fi
step=switch
pinned_ok || fail "копия изменилась до переключения"
if ! rename_pair erp "$prev_db" "$restore_db" erp; then
  now=$(databases)
  case " $now " in
    *" erp "*) ;;
    *)  # not expected (one transaction), but production never stays without a database named erp
      "$erp" exec -T db psql -X -q -U erp -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $prev_db RENAME TO erp" || true ;;
  esac
  fail "базы не переименованы (было: $now, сейчас: $(databases))"
fi
created=0 switched=1
now_rev=$(psql_db erp "SELECT version_num FROM alembic_version" || true)
say "переключено: erp — восстановленная база (ревизия $now_rev), прежняя — $prev_db"

# 6. Start.
step=start
head=$(docker run --rm --network none erp-next:latest alembic heads 2>/dev/null | awk 'NR == 1 { print $1 }' || true)
if [ -z "$head" ] || [ "$now_rev" != "$head" ]; then
  finished=1
  write_result false "база восстановлена на ревизии $now_rev, код на ревизии ${head:-?}: API и worker не запущены"
  echo "База восстановлена из $dump на ревизии ${now_rev:-?}, а код на ревизии ${head:-?}: API и worker НЕ запущены." >&2
  echo "Вернуть код той версии, что была при копии (push.ps1 -Ref <коммит из app.prev/REVISION>)," >&2
  echo "или накатить миграции по протоколу (копия → репетиция → сверка): bash $ERP_HOME/app/deploy/update.sh" >&2
  echo "Прежняя база — $prev_db (вернуть её: deploy/README.md «Ручное восстановление»)." >&2
  exit 3
fi
"$erp" start api worker || true
healthy=0
for _ in $(seq 1 "${ERP_HEALTH_TRIES:-60}"); do
  if printf '%s\n' "$health_py" | "$erp" exec -T api python - "$head" >/dev/null 2>&1; then healthy=1; break; fi
  sleep 3
done
if [ "$healthy" = 1 ]; then
  finished=1
  write_result true "восстановлено из $dump (ревизия $now_rev); прежняя база — $prev_db"
  say "восстановлено из $dump (ревизия $now_rev). Прежняя база $prev_db оставлена; удалить, когда не нужна:"
  echo "   ./erp.sh exec db dropdb -U erp $prev_db"
  exit 0
fi

step=rollback
echo "восстановленная база не поднялась (/api/health?db=1) — возвращаю прежнюю" >&2
"$erp" stop api worker || true
if rename_pair erp "$restore_db" "$prev_db" erp; then
  switched=0 finished=1
  "$erp" start api worker || true
  write_result false "API не поднялся на восстановленной базе — вернул прежнюю; восстановленная оставлена как $restore_db"
  echo "ВОССТАНОВЛЕНИЕ ОТМЕНЕНО: API не ответил на восстановленной базе, работает прежняя. Восстановленная — $restore_db (./erp.sh exec db dropdb -U erp $restore_db)." >&2
  exit 1
fi
finished=1
write_result false "API не поднялся на восстановленной базе, и вернуть прежнюю не удалось: базы $(databases)"
echo "КРИТИЧНО: API не поднялся на восстановленной базе, и переименование назад не прошло. Базы: $(databases)." >&2
echo "Прежняя база — $prev_db. Вернуть руками: deploy/README.md «Ручное восстановление»." >&2
exit 4
