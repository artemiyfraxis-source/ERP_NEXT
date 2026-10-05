#!/usr/bin/env bash
# Roll out a new code snapshot on the live server under the owner's rule
#   бэкап → прогон на копии → сверка количеств строк → только потом прод.
# auto-update.sh / push.ps1 unpack the new tree into $ERP_HOME/app.new and run this script from there.
#  1. free disk ≥ ERP_MIN_FREE_PCT (20%) for backups and Docker, else refuse;
#  2. build the new image as erp-next:candidate (production keeps running erp-next:latest);
#  3. ALWAYS a pre-update dump: backups/pre-update/erp_<stamp>.dump + .sha256 (own rotation, 10), stack brought up if down;
#  4. migrations the new version brings = its alembic head vs alembic_version of production;
#     none → no rehearsal; DESTRUCTIVE = True in one of them and its id not in status/migration-approved → stop
#     («нужна отмашка владельца»); otherwise rehearse.sh on a copy of the dump (red → stop, production untouched);
#  5. switch the code; with migrations: stop api+worker → second dump (…-final, the exact pre-migration state) →
#     snapshot → alembic upgrade head → snapshot → compare on production (rehearse.sh migrate-script);
#  6. candidate becomes erp-next:latest, `up -d`, /api/health?db=1 must name the head revision;
#  7. status/update-result.json {ok, step, message, revision, …} for the banner; exit 1 on any stop.
# The API never migrates on start (backend/docker-entrypoint.sh): it refuses a database that is not at its head.
set -euo pipefail
export ERP_HOME=${ERP_HOME:-/opt/erp}
unset ERP_AUTO_MIGRATE   # compose default 0: production never auto-migrates on start
new=$(cd "$(dirname "$0")/.." && pwd)
prod=$ERP_HOME/app
status_dir=$ERP_HOME/status
result=$status_dir/update-result.json
approved_file=$status_dir/migration-approved
pre_dir=$ERP_HOME/backups/pre-update
candidate=erp-next:candidate latest=erp-next:latest previous=erp-next:previous
min_free=${ERP_MIN_FREE_PCT:-20}
stamp=$(date +%Y-%m-%d_%H%M%S)
code_rev=$(cat "$new/REVISION" 2>/dev/null || echo "?")
# Scripts read now: the directories move in step 5.
backup_sh=$(cat "$new/deploy/backup-loop.sh")
migrate_sh=$(bash "$new/deploy/rehearse.sh" migrate-script)
health_py=$(bash "$new/deploy/rehearse.sh" health-script)
rehearse="$new/deploy/rehearse.sh"

step=start head="" db_rev="" approve="" finished=0
mkdir -p "$status_dir"

json_str() {  # a JSON string literal of $1
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; s=${s//$'\r'/}; s=${s//$'\t'/ }
  printf '"%s"' "$s"
}
write_result() {  # ok(true|false) message
  local tmp=$result.tmp
  {
    printf '{"ok": %s, "step": %s, "message": %s, "revision": %s' "$1" "$(json_str "$step")" "$(json_str "$2")" \
      "$(json_str "$code_rev")"
    printf ', "migration": %s, "db_revision": %s, "approve": %s, "at": %s}\n' "$(json_str "$head")" \
      "$(json_str "$db_rev")" "$(json_str "$approve")" "$(json_str "$(date '+%d.%m.%Y %H:%M')")"
  } > "$tmp"
  chmod 644 "$tmp"; mv "$tmp" "$result"
}
fail() {  # message — the last line of the log is the banner's reason (auto-update.sh)
  finished=1
  write_result false "$1"
  echo "ОБНОВЛЕНИЕ ОСТАНОВЛЕНО ($step): $1" >&2
  exit 1
}
on_exit() {
  local code=$?
  if [ "$finished" = 0 ] && [ "$code" != 0 ]; then
    write_result false "сбой на шаге $step (код $code), журнал: $status_dir/update-last.log"
    echo "ОБНОВЛЕНИЕ ОСТАНОВЛЕНО ($step): сбой (код $code)" >&2
  fi
}
trap on_exit EXIT

free_pct() { df -P "$1" | awk 'NR == 2 { if ($2 > 0) print int($4 * 100 / $2); else print 0 }'; }
wait_db() {  # erp.sh
  local _
  for _ in $(seq 1 30); do
    "$1" exec -T db pg_isready -q -U erp -d erp >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}
db_revision() {  # erp.sh → the single alembic_version row, or exit 1
  local rows
  rows=$("$1" exec -T db psql -U erp -d erp -tAc 'SELECT version_num FROM alembic_version') || return 1
  rows=$(printf '%s\n' "$rows" | sed '/^[[:space:]]*$/d')
  [ -n "$rows" ] && [ "$(printf '%s\n' "$rows" | wc -l)" -eq 1 ] || return 1
  printf '%s' "$rows" | tr -d '[:space:]'
}
pre_update_dump() {  # erp.sh name → verified backups/pre-update/erp_<name>.dump
  printf '%s\n' "$backup_sh" | "$1" exec -T backup bash -s -- --pre-update "$2" || return 1
  [ -s "$pre_dir/erp_$2.dump" ] || return 1
  (cd "$pre_dir" && sha256sum -c --status "erp_$2.dump.sha256")
}
healthy() {  # erp.sh head → 0 when /api/health?db=1 names the head (3 minutes)
  local _
  for _ in $(seq 1 "${ERP_HEALTH_TRIES:-60}"); do
    printf '%s\n' "$health_py" | "$1" exec -T api python - "$2" 2>/dev/null && return 0
    sleep 3
  done
  printf '%s\n' "$health_py" | "$1" exec -T api python - "$2" >&2 || true
  return 1
}

echo "== $(date -Is) обновление до $code_rev (протокол: копия → репетиция → сверка → прод)"
[ -f "$prod/deploy/erp.sh" ] || fail "нет $prod — первая установка идёт через bootstrap.sh"

# 1. Disk.
step=disk
mkdir -p "$pre_dir"
docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
for path in "$ERP_HOME/backups" ${docker_root:+"$docker_root"}; do
  [ -d "$path" ] || continue
  free=$(free_pct "$path")
  if [ -z "$free" ] || [ "$free" -lt "$min_free" ]; then
    fail "мало места на диске: свободно ${free:-?}% в $path (нужно не меньше $min_free%) — обновление не начато"
  fi
done

# 2. Candidate image (production keeps running the old one).
step=build
docker build -t "$candidate" -f "$new/Dockerfile" "$new" || fail "новая версия не собралась — сервер работает на прежней"

# 3. Pre-update backup, always.
step=backup
prod_erp=$prod/deploy/erp.sh
"$prod_erp" up -d db backup || fail "не поднялись база и копирование — обновление не начато"
wait_db "$prod_erp" || fail "база не отвечает — обновление не начато"
pre_update_dump "$prod_erp" "$stamp" || fail "копия базы перед обновлением не снята или не прошла проверку sha256"
dump=$pre_dir/erp_$stamp.dump
echo "== копия базы: $dump"

# 4. What the new version brings.
step=plan
db_rev=$(db_revision "$prod_erp") || fail "не прочитана ревизия базы (alembic_version)"
plan=$(docker run --rm -i --network none "$candidate" python - "$db_rev" <<'PY'
import sys
from alembic.config import Config
from alembic.script import ScriptDirectory

script = ScriptDirectory.from_config(Config("alembic.ini"))
heads = script.get_heads()
if len(heads) != 1:
    print("error в новой версии несколько голов миграций: " + ", ".join(heads))
    sys.exit(0)
head, current = heads[0], sys.argv[1]
print("head", head)
if current == head:
    sys.exit(0)
try:
    script.get_revision(current)
    pending = [rev for rev in script.iterate_revisions(head, current) if rev.revision != current]
except Exception:
    print("unknown", current)
    sys.exit(0)
for rev in reversed(pending):
    print("pending", rev.revision, 1 if getattr(rev.module, "DESTRUCTIVE", False) else 0)
PY
) || fail "не прочитаны миграции новой версии"
head=$(printf '%s\n' "$plan" | awk '$1 == "head" { print $2 }')
if printf '%s\n' "$plan" | grep -q '^error '; then fail "$(printf '%s\n' "$plan" | sed -n 's/^error //p')"; fi
if printf '%s\n' "$plan" | grep -q '^unknown '; then
  fail "база на ревизии $db_rev, которой нет в новой версии (база новее кода?) — обновление не начато"
fi
[ -n "$head" ] || fail "не определена голова миграций новой версии"
pending=$(printf '%s\n' "$plan" | awk '$1 == "pending" { print $2 }')
destructive=$(printf '%s\n' "$plan" | awk '$1 == "pending" && $3 == 1 { print $2 }')
[ -n "$pending" ] || [ "$db_rev" = "$head" ] || fail "база на $db_rev, голова новой версии $head, а списка миграций нет — обновление не начато"

if [ -z "$pending" ]; then
  migrate=0
  echo "== новых миграций нет (база на $head) — репетиция не нужна"
else
  migrate=1
  echo "== новые миграции: $(printf '%s\n' "$pending" | tr '\n' ' ')"
  step=approval
  missing=""
  for rev in $destructive; do
    if [ ! -f "$approved_file" ] || ! tr -s ' \t\r,;' '\n' < "$approved_file" | grep -qxF -- "$rev"; then
      missing="${missing:+$missing }$rev"
    fi
  done
  if [ -n "$missing" ]; then
    approve=$missing
    fail "нужна отмашка владельца: миграция $missing помечена DESTRUCTIVE (удаляет или переписывает данные) — не применена, сервер работает на прежней версии"
  fi
  step=rehearsal
  step_file=$(mktemp)
  if ! REHEARSAL_STEP_FILE=$step_file ERP_CANDIDATE_IMAGE=$candidate bash "$rehearse" "$dump" "$head"; then
    why=$(tr '\t' ' ' < "$step_file" 2>/dev/null || true); rm -f "$step_file"
    fail "репетиция на копии базы не прошла${why:+ — $why}; прод не тронут"
  fi
  rm -f "$step_file"
fi

# 5. Code switch (from here production changes).
step=switch
if [ "$new" != "$prod" ]; then
  rm -rf "$ERP_HOME/app.prev"
  mv "$prod" "$ERP_HOME/app.prev"
  mv "$new" "$prod"
fi
cd "$prod/deploy"
erp=./erp.sh
had_latest=0
docker image inspect "$latest" >/dev/null 2>&1 && had_latest=1
promote() {  # keep the running image as :previous, the candidate becomes :latest
  if [ "$had_latest" = 1 ]; then docker tag "$latest" "$previous"; fi
  docker tag "$candidate" "$latest"
}
demote() {  # back to the image that ran before
  if [ "$had_latest" = 1 ]; then docker tag "$previous" "$latest"; fi
}

if [ "$migrate" = 1 ]; then
  step=stop
  "$erp" stop api worker || { "$erp" start api worker || true; fail "не остановились api и worker — миграции не начаты"; }
  step=final-backup
  if ! pre_update_dump "$erp" "${stamp}-final"; then
    "$erp" start api worker || true
    fail "вторая копия базы (перед миграцией) не снята — миграции не начаты, работает прежняя версия"
  fi
  final_dump=$pre_dir/erp_${stamp}-final.dump
  step=migrate
  promote
  mkdir -p "$status_dir/integrity"
  chown 10001 "$status_dir/integrity" 2>/dev/null || true
  code=0
  printf '%s\n' "$migrate_sh" | "$erp" run --rm --no-deps -T -v "$status_dir/integrity:/integrity" \
    -e INTEGRITY_DIR=/integrity api sh -s || code=$?
  if [ "$code" != 0 ]; then
    case "$code" in 20) step=integrity-missing ;; 21) step=snapshot-before ;; 23) step=snapshot-after ;; 24) step=compare ;; esac
    now_rev=$(db_revision "$erp" || echo "?")
    if [ "$now_rev" = "$db_rev" ]; then
      demote
      "$erp" start api worker || true
      fail "миграции на сервере не прошли (код $code); база не изменилась ($db_rev), работает прежняя версия"
    fi
    fail "база уже изменена ($now_rev), но проверка после миграции не прошла (код $code). API и worker остановлены. Восстановить: cd $prod/deploy && ./restore.sh $final_dump, затем откат кода (deploy/README.md)"
  fi
  echo "== миграции на сервере применены, сверка без расхождений (снимки: $status_dir/integrity)"
else
  promote
fi

# 6. Start the new version.
step=start
if ! "$erp" up -d || ! healthy "$erp" "$head"; then
  if [ "$migrate" = 0 ]; then
    demote
    "$erp" up -d || true
    fail "новая версия не ответила /api/health?db=1 — вернул прежний образ, база не менялась"
  fi
  fail "новая версия не ответила /api/health?db=1 после миграции; база на $head. Журнал: ./erp.sh logs api. Восстановить базу: ./restore.sh $final_dump"
fi

# То, чего нет в SUPPLYMAP: шаблоны карт ERP и правки справочников (идемпотентно, ручные правки не трогает).
step=post
"$erp" cli python -m app.modules.techcards.seed_library --erp-only || echo "Шаблоны карт ERP не записаны" >&2
"$erp" cli python -m app.modules.references.erp_corrections || echo "Правки справочников не записаны" >&2
"$erp" cli python -m app.modules.identity.issue_logins --out /app/data/import/logins.csv || echo "Логины не выданы" >&2
# The auto-update timer and command come from this repo too: refresh them when they are installed.
if [ -f /etc/systemd/system/erp-auto-update.timer ]; then
  bash "$prod/deploy/install-auto-update.sh" >/dev/null || echo "Таймер автообновления не обновлён" >&2
fi
step=finished
finished=1
if [ "$migrate" = 1 ]; then
  write_result true "установлена $code_rev; миграции до $head прошли репетицию и сверку"
else
  write_result true "установлена $code_rev; новых миграций нет"
fi
echo "OK: $code_rev"
