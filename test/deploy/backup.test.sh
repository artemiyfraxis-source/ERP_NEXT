#!/usr/bin/env bash
# deploy/backup-loop.sh against stubbed pg_dump/pg_restore (test/deploy/stubs): unique names that are never reused,
# a .sha256 for every dump (daily, weekly, monthly, pre-update, pre-restore), no replacement of an existing dump,
# rotation that spares the dump being restored, the status file and the freshness healthcheck, the free-space check,
# the lock shared with restore.sh (only where flock exists — the server and CI; Git Bash on Windows has none).
#   bash test/deploy/backup.test.sh
set -uo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
stubs=$(cd "$(dirname "$0")/stubs" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
passed=0 failed=0
real_date=$(command -v date)

# `date` stub: STUB_WEEKDAY / STUB_MONTHDAY answer +%u / +%d (Sunday, the 1st), everything else is the real date.
mkdir -p "$work/bin"
cat > "$work/bin/date" <<SH
#!/usr/bin/env bash
case "\${1:-}" in
  +%u) [ -n "\${STUB_WEEKDAY:-}" ] && { echo "\$STUB_WEEKDAY"; exit 0; } ;;
  +%d) [ -n "\${STUB_MONTHDAY:-}" ] && { echo "\$STUB_MONTHDAY"; exit 0; } ;;
esac
exec "$real_date" "\$@"
SH
chmod +x "$work/bin/date"

setup() {  # name — fresh BACKUP_ROOT
  root=$work/$1
  mkdir -p "$root"
  unset STUB_PG_DUMP_FAIL STUB_WEEKDAY STUB_MONTHDAY BACKUP_LOCK_HELD
  export BACKUP_ROOT=$root BACKUP_MIN_FREE_MB=0 BACKUP_LOCK_WAIT=1 STUB_WEEKDAY=3 STUB_MONTHDAY=15
}
run() {  # args...
  out=$(PATH="$work/bin:$stubs:$PATH" bash "$repo/deploy/backup-loop.sh" "$@" 2>&1)
  rc=$?
}
check() {
  local what=$1; shift
  if "$@"; then passed=$((passed + 1)); else
    failed=$((failed + 1)); echo "FAIL [$case_name] $what (rc=$rc)"; echo "$out"
    find "$root" -type f | sort; cat "$root/backup-status.json" 2>/dev/null
  fi
}
count() { find "$root/$1" -maxdepth 1 -name '*.dump' 2>/dev/null | wc -l | tr -d ' '; }
sha_ok() {  # every dump in dir has a matching .sha256
  local d=$root/$1 f
  for f in "$d"/*.dump; do
    [ -e "$f" ] || return 1
    (cd "$d" && sha256sum -c --status "$(basename "$f").sha256") || return 1
  done
}
status() { sed -n "s/^  \"$1\": //p" "$root/backup-status.json" | sed 's/,$//; s/^"//; s/"$//'; }
no_parts() { [ -z "$(find "$root" -name '*.part')" ]; }

case_name="two backups within one minute"
setup twice
run --now; rc1=$rc
run --now
check "both exit 0" [ "$rc1" = 0 ] && [ "$rc" = 0 ]
check "two different archives" [ "$(count daily)" = 2 ]
check "each with a matching .sha256" sha_ok daily
check "status ok" [ "$(status ok)" = true ]
last=$(status last_success_file)
check "status names an existing daily dump" [ -s "$root/$last" ] && [ "${last%%/*}" = daily ]
check "status has its size and sha256" [ "$(status last_success_size_bytes)" -gt 0 ] \
  && [ "$(status last_success_sha256)" = "$(sha256sum < "$root/$last" | cut -d' ' -f1)" ]
check "no partial files" no_parts
check "healthy" bash -c "PATH='$work/bin:$stubs:$PATH' BACKUP_ROOT='$root' bash '$repo/deploy/backup-loop.sh' --health >/dev/null"

case_name="sunday and the 1st"
setup weekly
export STUB_WEEKDAY=7 STUB_MONTHDAY=01
run --now
run --now
check "exit 0" [ "$rc" = 0 ]
check "one weekly copy a day, with .sha256" [ "$(count weekly)" = 1 ] && sha_ok weekly
check "one monthly copy a day, with .sha256" [ "$(count monthly)" = 1 ] && sha_ok monthly

case_name="pg_dump fails"
setup dumpfail
run --now
good=$(status last_success_file)
export STUB_PG_DUMP_FAIL=1
run --now
check "exit 1" [ "$rc" = 1 ]
check "no new archive, nothing partial" [ "$(count daily)" = 1 ] && no_parts
check "status ok:false, exit code and reason" [ "$(status ok)" = false ] && [ "$(status last_exit_code)" = 1 ] \
  && grep -q 'pg_dump' "$root/backup-status.json"
check "last success kept" [ "$(status last_success_file)" = "$good" ]
check "failures counted" [ "$(status consecutive_failures)" = 1 ]
run --health
check "unhealthy" [ "$rc" = 1 ]
unset STUB_PG_DUMP_FAIL
run --now
check "next success resets the counter" [ "$(status consecutive_failures)" = 0 ] && [ "$(status ok)" = true ]

case_name="freshness"
setup fresh
run --health
check "no status file: unhealthy" [ "$rc" = 1 ]
run --now
run --health
check "fresh: healthy" [ "$rc" = 0 ]
sed -i 's/^  "last_success_epoch": .*/  "last_success_epoch": 1000,/' "$root/backup-status.json"
run --health
check "older than 26 h: unhealthy" [ "$rc" = 1 ]
check "says how old" bash -c "printf '%s' \"\$1\" | grep -q 'старше 26 ч'" _ "$out"

case_name="low disk"
setup disk
export BACKUP_MIN_FREE_MB=999999999
run --now
check "exit 1" [ "$rc" = 1 ]
check "nothing written" [ "$(count daily)" = 0 ] && no_parts
check "status says why" grep -q 'мало места' "$root/backup-status.json"

case_name="pre-update: an existing name is never replaced"
setup preupdate
run --pre-update 2026-10-10_020000
check "first exit 0" [ "$rc" = 0 ]
check "with .sha256" sha_ok pre-update
before=$(sha256sum < "$root/pre-update/erp_2026-10-10_020000.dump")
run --pre-update 2026-10-10_020000
check "same name refused" [ "$rc" = 1 ]
check "the existing dump untouched" [ "$(sha256sum < "$root/pre-update/erp_2026-10-10_020000.dump")" = "$before" ] && sha_ok pre-update
check "status says it exists" grep -q 'уже есть' "$root/backup-status.json"
run --pre-update 'bad/name'
check "bad name refused" [ "$rc" = 2 ]

case_name="pre-restore rotation spares the dump being restored"
setup prerestore
mkdir -p "$root/pre-restore"
for i in 01 02 03; do
  f=$root/pre-restore/erp_2026-01-$i.dump
  echo "PGDMP old" > "$f"; (cd "$root/pre-restore" && sha256sum "$(basename "$f")" > "$(basename "$f").sha256")
  touch -d "2026-01-$i" "$f"
done
export KEEP_PRE_RESTORE=2
run --pre-restore 20261010_120000 pre-restore/erp_2026-01-01.dump
unset KEEP_PRE_RESTORE
check "exit 0" [ "$rc" = 0 ]
check "the protected oldest one kept" [ -e "$root/pre-restore/erp_2026-01-01.dump" ]
check "others rotated as usual" [ ! -e "$root/pre-restore/erp_2026-01-02.dump" ] && [ ! -e "$root/pre-restore/erp_2026-01-02.dump.sha256" ]
check "new safety dump with .sha256" [ -e "$root/pre-restore/erp_20261010_120000.dump" ] && sha_ok pre-restore
check "daily untouched" [ ! -d "$root/daily" ]
run --pre-restore 20261010_120001 ../etc/passwd
check "protected path with .. refused" [ "$rc" = 2 ]

if command -v flock >/dev/null 2>&1; then
  case_name="lock held by restore.sh"
  setup lock
  : > "$root/.lock"
  # -o: only flock itself holds the lock, so killing it frees the lock (the orphaned sleep holds nothing)
  flock -o "$root/.lock" sleep 30 >/dev/null 2>&1 &
  holder=$!
  sleep 1
  run --now
  check "dump waits, then gives up (exit 75)" [ "$rc" = 75 ]
  check "nothing written" [ "$(count daily)" = 0 ]
  check "status says busy" grep -q 'занято' "$root/backup-status.json"
  BACKUP_LOCK_HELD=1 run --pre-restore 20261010_130000 ""
  check "the restore's own safety dump runs under its lock" [ "$rc" = 0 ]
  kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null
  run --now
  check "free lock: dump runs" [ "$rc" = 0 ] && [ "$(count daily)" = 1 ]
else
  echo "backup.test.sh: lock cases skipped — no flock here (the server and CI have it)"
fi

echo "backup.test.sh: $passed passed, $failed failed"
[ "$failed" = 0 ]
