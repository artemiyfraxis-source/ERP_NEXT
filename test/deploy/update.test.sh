#!/usr/bin/env bash
# deploy/update.sh against a stubbed docker (test/deploy/stubs): which production commands run in which case.
#   bash test/deploy/update.test.sh
set -uo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
stubs=$(cd "$(dirname "$0")/stubs" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
passed=0 failed=0

setup() {  # name db_rev plan — fresh ERP_HOME: app (running, old1234) + app.new (new5678)
  home=$work/$1
  mkdir -p "$home/app" "$home/app.new" "$home/status" "$home/backups" "$home/config" "$home/stub"
  cp -R "$repo/deploy" "$home/app/deploy"; echo old1234 > "$home/app/REVISION"
  cp -R "$repo/deploy" "$home/app.new/deploy"; echo new5678 > "$home/app.new/REVISION"
  : > "$home/config/.env"
  printf '%s\n' "$2" > "$home/stub/db_rev"
  : > "$home/docker.log"
  unset STUB_REHEARSAL_MIGRATE_RC STUB_PROD_MIGRATE_RC STUB_PROD_MIGRATE_COMMITS STUB_HEALTH_RC STUB_BUILD_RC
  export ERP_HOME=$home STUB_STATE=$home/stub STUB_LOG=$home/docker.log STUB_PLAN=$3
  export STUB_HEAD
  STUB_HEAD=$(printf '%s\n' "$3" | awk '$1 == "head" { print $2 }')
  export ERP_MIN_FREE_PCT=0 ERP_HEALTH_TRIES=1
}
run_update() {
  # The script text is passed with -c ($0 = its path, as when run from a file): Windows refuses to move app.new while
  # bash holds the file open; on the Linux server the plain `bash app.new/deploy/update.sh` behaves the same.
  local script=$ERP_HOME/app.new/deploy/update.sh
  PATH="$stubs:$PATH" bash -c "$(cat "$script")" "$script" > "$ERP_HOME/out.log" 2>&1
  rc=$?
}
log() { cat "$ERP_HOME/docker.log"; }
prod_calls() { grep -v -- '-p erp-rehearsal' "$ERP_HOME/docker.log" | grep '^compose ' || true; }
line_of() { grep -n -F -- "$1" "$ERP_HOME/docker.log" | head -1 | cut -d: -f1; }
result() { cat "$ERP_HOME/status/update-result.json" 2>/dev/null; }

check() {  # description condition...
  local what=$1; shift
  if "$@"; then passed=$((passed + 1)); else
    failed=$((failed + 1)); echo "FAIL [$case_name] $what"
    echo "--- out.log"; tail -15 "$ERP_HOME/out.log"; echo "--- docker.log"; tail -25 "$ERP_HOME/docker.log"; echo "---"
  fi
}
has() { log | grep -q -F -- "$1"; }
hasnt() { ! has "$1"; }
prod_has() { prod_calls | grep -q -F -- "$1"; }
prod_hasnt() { ! prod_has "$1"; }
result_has() { result | grep -q -F -- "$1"; }
before() { local a b; a=$(line_of "$1"); b=$(line_of "$2"); [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]; }
dump_ok() { local d=$ERP_HOME/backups/pre-update n; n=$(find "$d" -name "erp_*$1.dump" | head -1)
  [ -n "$n" ] && [ -s "$n" ] && (cd "$d" && sha256sum -c --status "$(basename "$n").sha256"); }
no_dump() { [ -z "$(find "$ERP_HOME/backups/pre-update" -name "erp_*$1.dump" 2>/dev/null)" ]; }
rev_is() { [ "$(cat "$ERP_HOME/$1/REVISION")" = "$2" ]; }

case_name="no new revision"
setup norev 0088_x "head 0088_x"
run_update
check "exit 0" [ "$rc" = 0 ]
check "candidate image built" has "build -t erp-next:candidate"
check "pre-update backup taken with a valid sha256" dump_ok "[0-9]"
check "rehearsal skipped" hasnt "erp-rehearsal"
check "api and worker not stopped" prod_hasnt "stop api worker"
check "no migration run" prod_hasnt "api sh -s"
check "no final dump" no_dump "-final"
check "candidate promoted to latest" has "tag erp-next:candidate erp-next:latest"
check "running image kept as previous" before "tag erp-next:latest erp-next:previous" "tag erp-next:candidate erp-next:latest"
check "stack restarted" prod_has "up -d"
check "code switched" rev_is app new5678
check "previous code kept" rev_is app.prev old1234
check "result ok:true" result_has '"ok": true'

case_name="rehearsal red"
setup red 0088_x $'head 0089_y\npending 0089_y 0'
export STUB_REHEARSAL_MIGRATE_RC=24
run_update
check "exit 1" [ "$rc" = 1 ]
check "pre-update backup taken" dump_ok "[0-9]"
check "rehearsal ran" has "-p erp-rehearsal"
check "rehearsal torn down" has "-p erp-rehearsal -f"
check "rehearsal project removed with volumes" bash -c "grep -F -- '-p erp-rehearsal' '$ERP_HOME/docker.log' | tail -1 | grep -q 'down -v'"
check "prod api/worker never stopped" prod_hasnt "stop api worker"
check "prod migration never run" prod_hasnt "api sh -s"
check "prod never restarted" bash -c "! grep -v -- '-p erp-rehearsal' '$ERP_HOME/docker.log' | grep -E '(^| )up -d$'"
check "candidate not promoted" hasnt "tag erp-next:candidate erp-next:latest"
check "code not switched" rev_is app old1234
check "result ok:false, step rehearsal, names the failed step" \
  bash -c "grep -q '\"ok\": false' '$ERP_HOME/status/update-result.json' && grep -q '\"step\": \"rehearsal\"' '$ERP_HOME/status/update-result.json' && grep -q 'compare' '$ERP_HOME/status/update-result.json'"

case_name="integrity module missing in the new version"
setup nointegrity 0088_x $'head 0089_y\npending 0089_y 0'
export STUB_REHEARSAL_MIGRATE_RC=20
run_update
check "exit 1" [ "$rc" = 1 ]
check "prod untouched" prod_hasnt "stop api worker"
check "result names integrity-missing" result_has "integrity-missing"

case_name="destructive without approval"
setup destr 0088_x $'head 0089_y\npending 0089_y 1'
run_update
check "exit 1" [ "$rc" = 1 ]
check "pre-update backup taken" dump_ok "[0-9]"
check "rehearsal not run" hasnt "erp-rehearsal"
check "prod untouched" prod_hasnt "stop api worker"
check "candidate not promoted" hasnt "tag erp-next:candidate erp-next:latest"
check "code not switched" rev_is app old1234
check "result asks for the owner's approval" result_has "нужна отмашка владельца"
check "result names the revision to approve" result_has '"approve": "0089_y"'

case_name="destructive approved"
setup approved 0088_x $'head 0089_y\npending 0089_y 1'
printf 'other_rev\n0089_y\n' > "$ERP_HOME/status/migration-approved"
run_update
check "exit 0" [ "$rc" = 0 ]
check "rehearsal ran" has "-p erp-rehearsal"
check "rehearsal before stopping prod" before "-p erp-rehearsal" "stop api worker"
check "final dump while api is stopped" before "stop api worker" "-final"
check "final dump taken" dump_ok "-final"
check "migration after the final dump" before "-final" "INTEGRITY_DIR=/integrity api sh -s"
check "candidate promoted before the migration" before "tag erp-next:candidate erp-next:latest" "INTEGRITY_DIR=/integrity api sh -s"
check "started after the migration" bash -c "grep -n -E '(^| )up -d$' '$ERP_HOME/docker.log' | tail -1 | cut -d: -f1 | { read -r up; [ \"\$up\" -gt $(line_of "INTEGRITY_DIR=/integrity api sh -s") ]; }"
check "result ok:true" result_has '"ok": true'

case_name="prod migration failed, database unchanged"
setup migfail 0088_x $'head 0089_y\npending 0089_y 0'
export STUB_PROD_MIGRATE_RC=22
run_update
check "exit 1" [ "$rc" = 1 ]
check "previous image back as latest" has "tag erp-next:previous erp-next:latest"
check "old api/worker started again" prod_has "start api worker"
check "result step migrate" result_has '"step": "migrate"'

case_name="prod check failed after the migration committed"
setup migcommitted 0088_x $'head 0089_y\npending 0089_y 0'
export STUB_PROD_MIGRATE_RC=24 STUB_PROD_MIGRATE_COMMITS=1
run_update
check "exit 1" [ "$rc" = 1 ]
check "api/worker stay stopped" prod_hasnt "start api worker"
check "previous image not restored over the migrated database" hasnt "tag erp-next:previous erp-next:latest"
check "result step compare, tells how to restore" bash -c "grep -q '\"step\": \"compare\"' '$ERP_HOME/status/update-result.json' && grep -q 'restore.sh' '$ERP_HOME/status/update-result.json'"

case_name="health red without migrations"
setup healthred 0088_x "head 0088_x"
export STUB_HEALTH_RC=1
run_update
check "exit 1" [ "$rc" = 1 ]
check "previous image back as latest" has "tag erp-next:previous erp-next:latest"
check "result step start" result_has '"step": "start"'

case_name="database revision unknown to the new code"
setup unknown 0090_z $'head 0088_x\nunknown 0090_z'
run_update
check "exit 1" [ "$rc" = 1 ]
check "rehearsal not run" hasnt "erp-rehearsal"
check "candidate not promoted" hasnt "tag erp-next:candidate erp-next:latest"
check "code not switched" rev_is app old1234

case_name="low disk"
setup disk 0088_x "head 0088_x"
export ERP_MIN_FREE_PCT=101
run_update
check "exit 1" [ "$rc" = 1 ]
check "nothing built" hasnt "build"
check "no backup" no_dump ""
check "result step disk" result_has '"step": "disk"'

case_name="pre-update backup rotation"
setup rotation 0088_x "head 0088_x"
mkdir -p "$ERP_HOME/backups/pre-update"
for i in 01 02 03 04 05 06 07 08 09 10 11; do
  f=$ERP_HOME/backups/pre-update/erp_2026-01-$i.dump
  echo old > "$f"; echo x > "$f.sha256"; touch -d "2026-01-$i" "$f"
done
run_update
check "exit 0" [ "$rc" = 0 ]
check "keeps 10 dumps" [ "$(find "$ERP_HOME/backups/pre-update" -name '*.dump' | wc -l)" -eq 10 ]
check "oldest dropped with its sha256" bash -c "[ ! -e '$ERP_HOME/backups/pre-update/erp_2026-01-01.dump' ] && [ ! -e '$ERP_HOME/backups/pre-update/erp_2026-01-01.dump.sha256' ]"
check "daily backups untouched" [ ! -d "$ERP_HOME/backups/daily" ]

echo "update.test.sh: $passed passed, $failed failed"
[ "$failed" = 0 ]
