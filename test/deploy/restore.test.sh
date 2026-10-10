#!/usr/bin/env bash
# deploy/restore.sh against a stubbed docker with a fake database server (test/deploy/stubs-restore): the source dump
# keeps its checksum, the restore goes into a new database, production is stopped only after every check, any failure
# before the switch leaves the old database and running api, a failed health check renames back.
# The real PostgreSQL drill is test/deploy/restore-drill.test.sh.
#   bash test/deploy/restore.test.sh
set -uo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
stubs=$(cd "$(dirname "$0")/stubs" && pwd)
rstubs=$(cd "$(dirname "$0")/stubs-restore" && pwd)
work=$(mktemp -d)
trap '[ -n "${KEEP_WORK:-}" ] && echo "kept $work" || rm -rf "$work"' EXIT
passed=0 failed=0
mkdir -p "$work/bin"
if ! command -v flock >/dev/null 2>&1; then   # Git Bash on Windows: the lock itself is covered by backup.test.sh on CI
  printf '#!/usr/bin/env bash\nexit 0\n' > "$work/bin/flock"; chmod +x "$work/bin/flock"
fi

setup() {  # name — production erp at 0088_x (with a table the old dump lacks); a daily dump at 0087_w
  home=$work/$1
  mkdir -p "$home/app" "$home/config" "$home/status" "$home/backups" "$home/stub/dbs/erp" "$home/stub/dump"
  cp -R "$repo/deploy" "$home/app/deploy"
  : > "$home/config/.env"
  echo 0088_x > "$home/stub/dbs/erp/rev"
  printf 'alembic_version 1\napi_tokens 4\nitems 5\norders 2\n' > "$home/stub/dbs/erp/counts"
  echo 0087_w > "$home/stub/dump/rev"
  printf 'alembic_version 1\nitems 3\norders 2\n' > "$home/stub/dump/counts"
  echo running > "$home/stub/api"
  : > "$home/docker.log"
  unset STUB_RESTORE_RC STUB_MUTATE_SOURCE STUB_COUNT_SKEW STUB_INTEGRITY_RC STUB_SAFETY_FAIL STUB_RENAME_FAIL STUB_HEALTH_RC
  export ERP_HOME=$home STUB_STATE=$home/stub STUB_LOG=$home/docker.log STUB_HEAD=0087_w ERP_HEALTH_TRIES=1
  BACKUP_ROOT=$home/backups BACKUP_MIN_FREE_MB=0 PATH="$stubs:$work/bin:$PATH" bash "$repo/deploy/backup-loop.sh" --now >/dev/null 2>&1
  dump=$(find "$home/backups/daily" -name '*.dump' | head -1)
  dump_hash=$(sha256sum < "$dump" | cut -d' ' -f1)
}
run_restore() {  # [dump]
  PATH="$rstubs:$stubs:$work/bin:$PATH" bash "$home/app/deploy/restore.sh" "${1:-$dump}" > "$home/out.log" 2>&1
  rc=$?
}
check() {
  local what=$1; shift
  if "$@"; then passed=$((passed + 1)); else
    failed=$((failed + 1)); echo "FAIL [$case_name] $what (rc=$rc)"
    echo "--- out.log"; tail -15 "$home/out.log"; echo "--- docker.log"; tail -20 "$home/docker.log"
    echo "--- dbs"; ls "$home/stub/dbs"; echo "---"
  fi
}
has() { grep -q -F -- "$1" "$home/docker.log"; }
hasnt() { ! has "$1"; }
line_of() { grep -n -F -- "$1" "$home/docker.log" | head -1 | cut -d: -f1; }
before() { local a b; a=$(line_of "$1"); b=$(line_of "$2"); [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]; }
db_rev() { cat "$home/stub/dbs/$1/rev"; }
# shellcheck disable=SC2012  # names of our own fake databases
dbs() { ls -1 "$home/stub/dbs" | tr '\n' ' '; }
only_erp() { [ "$(dbs)" = "erp " ]; }
api_is() { [ "$(cat "$home/stub/api")" = "$1" ]; }
source_intact() { [ "$(sha256sum < "$dump" | cut -d' ' -f1)" = "$dump_hash" ] \
  && (cd "$(dirname "$dump")" && sha256sum -c --status "$(basename "$dump").sha256"); }
result_has() { grep -q -F -- "$1" "$home/status/restore-result.json"; }
untouched() {  # production as before: old revision, api running, no other database, never stopped
  [ "$(db_rev erp)" = 0088_x ] && only_erp && api_is running && hasnt "stop api worker"
}

case_name="newest daily, same revision as the code"
setup ok
run_restore
check "exit 0" [ "$rc" = 0 ]
check "source dump keeps its checksum" source_intact
check "erp is the restored database" [ "$(db_rev erp)" = 0087_w ]
check "the restored one has exactly the dump's tables" [ "$(cut -d' ' -f1 "$home/stub/dbs/erp/counts" | tr '\n' ' ')" = "alembic_version items orders " ]
check "the previous database kept as erp_prev_*" bash -c "ls '$home/stub/dbs' | grep -q '^erp_prev_' && [ \"\$(cat '$home'/stub/dbs/erp_prev_*/rev)\" = 0088_x ]"
check "no temporary database left" bash -c "! ls '$home/stub/dbs' | grep -q '^erp_restore_'"
check "safety dump in pre-restore/ with a valid .sha256" bash -c "cd '$home/backups/pre-restore' && f=\$(ls *.dump) && sha256sum -c --status \"\$f.sha256\""
check "daily dumps untouched" [ "$(find "$home/backups/daily" -name '*.dump' | wc -l)" -eq 1 ]
check "restored into the new database while production ran" before "pg_restore --no-owner --exit-on-error -d erp_restore_" "stop api worker"
check "checks before stopping" before "' || count(*) FROM public." "stop api worker"
check "integrity snapshot before stopping" before "api sh -c" "stop api worker"
check "safety dump after stopping" before "stop api worker" "--pre-restore"
check "switch after the safety dump" before "--pre-restore" "RENAME TO"
check "one transaction for both renames" has "-1 -c ALTER DATABASE erp RENAME TO erp_prev_"
check "started after the switch" before "RENAME TO" "start api worker"
check "api running" api_is running
check "result ok" result_has '"ok": true'
check "integrity snapshot saved" bash -c "ls '$home'/status/restore/*/restored.json >/dev/null"

case_name="checksum does not match"
setup badsum
echo "0000  $(basename "$dump")" > "$dump.sha256"
run_restore
check "exit 1" [ "$rc" = 1 ]
check "nothing created" hasnt "CREATE DATABASE"
check "production untouched" untouched
check "result names the step" result_has '"step": "checksum"'

case_name="pg_restore fails half-way"
setup halfway
export STUB_RESTORE_RC=1
run_restore
check "exit 1" [ "$rc" = 1 ]
check "production untouched" untouched
check "the partial database dropped" has "DROP DATABASE IF EXISTS erp_restore_"
check "no safety dump needed" [ ! -d "$home/backups/pre-restore" ]
check "source dump keeps its checksum" source_intact
check "result step restore" result_has '"step": "restore"'

case_name="source changes during pg_restore"
setup mutate
export STUB_MUTATE_SOURCE=1
run_restore
check "exit 1" [ "$rc" = 1 ]
check "production untouched" untouched
check "says the dump changed" grep -q 'изменилась' "$home/out.log"

case_name="row counts differ"
setup skew
export STUB_COUNT_SKEW=1
run_restore
check "exit 1" [ "$rc" = 1 ]
check "production untouched" untouched
check "result step verify" result_has '"step": "verify"'

case_name="integrity snapshot fails"
setup integrity
export STUB_INTEGRITY_RC=1
run_restore
check "exit 1" [ "$rc" = 1 ]
check "production untouched" untouched

case_name="safety dump fails"
setup safety
export STUB_SAFETY_FAIL=1
run_restore
check "exit 1" [ "$rc" = 1 ]
check "old database still erp, nothing renamed" [ "$(db_rev erp)" = 0088_x ] && only_erp && hasnt "RENAME TO"
check "api started again" before "stop api worker" "start api worker" && api_is running

case_name="rename fails"
setup rename
export STUB_RENAME_FAIL=1
run_restore
check "exit 1" [ "$rc" = 1 ]
check "old database still erp, temporary dropped" [ "$(db_rev erp)" = 0088_x ] && only_erp
check "api started again" api_is running

case_name="older dump than the code (0087 into 0088)"
setup older
export STUB_HEAD=0088_x
run_restore
check "exit 3" [ "$rc" = 3 ]
check "erp holds exactly the dump" [ "$(db_rev erp)" = 0087_w ] && ! grep -q api_tokens "$home/stub/dbs/erp/counts"
check "api and worker stay stopped" api_is stopped
check "previous database kept" bash -c "ls '$home/stub/dbs' | grep -q '^erp_prev_'"
check "says what to do" grep -q 'push.ps1 -Ref' "$home/out.log"

case_name="health red after the switch"
setup health
export STUB_HEALTH_RC=1
run_restore
check "exit 1" [ "$rc" = 1 ]
check "old database back as erp" [ "$(db_rev erp)" = 0088_x ]
check "restored one kept aside" bash -c "ls '$home/stub/dbs' | grep -q '^erp_restore_' && ! ls '$home/stub/dbs' | grep -q '^erp_prev_'"
check "api running again" api_is running
check "result names rollback" result_has '"step": "rollback"'

case_name="dump without .sha256 (before 10.10.2026)"
setup nosum
rm -f "$dump.sha256"
run_restore
check "exit 0" [ "$rc" = 0 ]
check "warns" grep -q 'нет .sha256' "$home/out.log"
check "restored" [ "$(db_rev erp)" = 0087_w ]

case_name="path outside backups"
setup outside
cp "$dump" "$work/elsewhere.dump"
run_restore "$work/elsewhere.dump"
check "exit 2" [ "$rc" = 2 ]
check "nothing called" [ ! -s "$home/docker.log" ]
run_restore "$home/backups/daily/../daily/$(basename "$dump")"
check "/../ refused" [ "$rc" = 2 ]

echo "restore.test.sh: $passed passed, $failed failed"
[ "$failed" = 0 ]
