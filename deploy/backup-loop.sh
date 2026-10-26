#!/usr/bin/env bash
# Nightly pg_dump at BACKUP_HOUR (server time): /backups/daily keeps KEEP_DAILY dumps (7 — owner 26.10.2026: «бекап
# недельной давности»; how long records live — УПД/счета 3 years, техкарты/документация 5 — is the database's job, not
# the backups'). Sunday's copy goes to /backups/weekly and the 1st's to /backups/monthly only when KEEP_WEEKLY /
# KEEP_MONTHLY > 0 (both 0 by default; 0 also clears what those folders hold). Files (УПД, счета, КД-реестр) live in the
# database, so the dump is the whole ERP.
# Daytime dumps (owner, 10.10.2026: lose at most 3 hours of shop work, copies stay on the server): at every hour of
# BACKUP_INTRADAY_HOURS (9 12 15 18 21) into /backups/intraday, the newest KEEP_INTRADAY (15, about three days) kept;
# no weekly/monthly copies from them. An empty BACKUP_INTRADAY_HOURS turns them off.
#   --now                 one daily dump right away (erp.sh backup-now).
#   --pre-update <name>   the dump update.sh takes before every update: /backups/pre-update/erp_<name>.dump + .sha256,
#                         own rotation (KEEP_PRE_UPDATE, 10) — the daily ones are untouched. An existing name is refused.
#                         update.sh pipes THIS file into the backup container (bash -s), so the mode works even while the
#                         running container still mounts the previous version of the script.
#   --pre-restore <name> <protected>   the safety dump restore.sh takes of the database it is about to replace:
#                         /backups/pre-restore/erp_<name>.dump + .sha256 (KEEP_PRE_RESTORE, 10); <protected> (relative to
#                         /backups) is the dump being restored — rotation never deletes it. restore.sh holds the lock.
#   --health              compose healthcheck: 0 when the last attempt succeeded and the last good dump is younger than
#                         BACKUP_MAX_AGE_HOURS (26).
# Every dump: a name that is never reused (date, time to the second, -2/-3… when taken), checked with pg_restore --list,
# its .sha256 written before it appears under its name, never replacing a file. Weekly/monthly copies carry the .sha256.
# Every dump and every rotation runs under flock /backups/.lock (restore.sh takes the same lock on the host), so a
# restore never sees its source replaced or rotated away, and two dumps never run at once (BACKUP_LOCK_WAIT, 3600 s).
# Before a dump: free space ≥ max(BACKUP_MIN_FREE_MB (1024), 2 × the newest dump), else the dump fails.
# The result of every attempt goes to /backups/backup-status.json: last success (time, kind, file, size, sha256),
# last attempt, its result and exit code, failures in a row. A failed nightly dump is retried every
# BACKUP_RETRY_MINUTES (60) until the next scheduled one; on start, a stale or missing last success means a dump now.
set -u
root=${BACKUP_ROOT:-/backups}
KEEP_DAILY=${KEEP_DAILY:-7}; KEEP_WEEKLY=${KEEP_WEEKLY:-0}; KEEP_MONTHLY=${KEEP_MONTHLY:-0}
KEEP_PRE_UPDATE=${KEEP_PRE_UPDATE:-10}; KEEP_PRE_RESTORE=${KEEP_PRE_RESTORE:-10}
KEEP_INTRADAY=${KEEP_INTRADAY:-15}
INTRADAY_HOURS=${BACKUP_INTRADAY_HOURS-9 12 15 18 21}
MAX_AGE_HOURS=${BACKUP_MAX_AGE_HOURS:-26}
MIN_FREE_MB=${BACKUP_MIN_FREE_MB:-1024}
RETRY_MINUTES=${BACKUP_RETRY_MINUTES:-60}
LOCK_WAIT=${BACKUP_LOCK_WAIT:-3600}
status_file=$root/backup-status.json
made=""      # the dump write_dump just made
reason=""    # why the last step failed (Russian, for the status file)

stamp_now() { date '+%Y-%m-%dT%H:%M:%S%z'; }
log() { echo "$(stamp_now) $*"; }

json_str() {  # a JSON string literal of $1
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/ }; s=${s//$'\r'/}; s=${s//$'\t'/ }
  printf '"%s"' "$s"
}
field() {  # name → a number or plain string field of the status file (our own one-key-per-line format)
  [ -f "$status_file" ] || return 0
  sed -n "s/^  \"$1\": //p" "$status_file" | head -1 | sed 's/,$//; s/^"//; s/"$//'
}
write_status() {  # ok kind result code — keeps the last-success fields unless this attempt succeeded
  local ok=$1 kind=$2 result=$3 code=$4 tmp=$status_file.tmp
  local s_at s_epoch s_kind s_file s_size s_sha fails
  if [ "$ok" = true ]; then
    s_at=$(stamp_now); s_epoch=$(date +%s); s_kind=$kind; s_file=${made#"$root/"}
    s_size=$(stat -c %s "$made" 2>/dev/null || wc -c < "$made"); s_sha=$(cut -d' ' -f1 < "$made.sha256")
    fails=0
  else
    s_at=$(field last_success_at); s_epoch=$(field last_success_epoch); s_kind=$(field last_success_kind)
    s_file=$(field last_success_file); s_size=$(field last_success_size_bytes); s_sha=$(field last_success_sha256)
    fails=$(field consecutive_failures | tr -cd 0-9); fails=$(( ${fails:-0} + 1 ))
  fi
  {
    printf '{\n'
    printf '  "ok": %s,\n' "$ok"
    printf '  "last_success_at": %s,\n' "$(json_str "$s_at")"
    printf '  "last_success_epoch": %s,\n' "${s_epoch:-0}"
    printf '  "last_success_kind": %s,\n' "$(json_str "$s_kind")"
    printf '  "last_success_file": %s,\n' "$(json_str "$s_file")"
    printf '  "last_success_size_bytes": %s,\n' "${s_size:-0}"
    printf '  "last_success_sha256": %s,\n' "$(json_str "$s_sha")"
    printf '  "last_attempt_at": %s,\n' "$(json_str "$(stamp_now)")"
    printf '  "last_attempt_kind": %s,\n' "$(json_str "$kind")"
    printf '  "last_result": %s,\n' "$(json_str "$result")"
    printf '  "last_exit_code": %s,\n' "$code"
    printf '  "consecutive_failures": %s,\n' "$fails"
    printf '  "max_age_hours": %s\n' "$MAX_AGE_HOURS"
    printf '}\n'
  } > "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$status_file"
}

keep_newest() {  # dir count [protected file]: delete all but the newest <count> dumps (and their .sha256)
  local f
  # shellcheck disable=SC2012  # names are our own erp_<stamp>.dump, no spaces or newlines
  ls -1t "$1"/*.dump 2>/dev/null | tail -n +$(($2 + 1)) | while IFS= read -r f; do
    if [ -n "${3:-}" ] && [ "$f" -ef "$3" ]; then continue; fi
    rm -f -- "$f" "$f.sha256"
  done
}

space_ok() {  # dir → 0 when free space there ≥ max(MIN_FREE_MB, 2 × the newest dump)
  local need_kb newest size avail_kb
  need_kb=$((MIN_FREE_MB * 1024))
  # shellcheck disable=SC2012  # our own names
  newest=$(ls -1t "$root"/daily/*.dump "$root"/intraday/*.dump "$root"/pre-update/*.dump 2>/dev/null | head -1)
  if [ -n "$newest" ]; then
    size=$(stat -c %s "$newest" 2>/dev/null || wc -c < "$newest")
    [ $(( (size / 1024 + 1) * 2 )) -gt "$need_kb" ] && need_kb=$(( (size / 1024 + 1) * 2 ))
  fi
  avail_kb=$(df -Pk "$1" 2>/dev/null | awk 'NR == 2 { print $4 }')
  [ -n "$avail_kb" ] || return 0
  if [ "$avail_kb" -lt "$need_kb" ]; then
    reason="мало места в $1: свободно $((avail_kb / 1024)) МБ, нужно не меньше $((need_kb / 1024)) МБ"
    return 1
  fi
}

write_dump() {  # dir stem exact(0|1) → <dir>/<stem>.dump + .sha256, never replacing anything; sets $made
  local dir=$1 stem=$2 exact=$3 base n=1 part hash
  made=""
  mkdir -p "$dir" || { reason="не создан каталог $dir"; return 1; }
  base=$stem.dump
  while [ -e "$dir/$base" ] || [ -e "$dir/$base.sha256" ]; do
    if [ "$exact" = 1 ]; then reason="копия $dir/$base уже есть — не заменяю"; return 1; fi
    n=$((n + 1)); base=$stem-$n.dump
  done
  space_ok "$dir" || return 1
  part=$dir/.$base.part
  if ! pg_dump -Fc -f "$part"; then rm -f -- "$part"; reason="pg_dump завершился с ошибкой"; return 1; fi
  if ! pg_restore --list "$part" >/dev/null; then
    rm -f -- "$part"; reason="копия не читается (pg_restore --list)"; return 1
  fi
  hash=$(sha256sum < "$part" | cut -d' ' -f1)
  if [ -z "$hash" ] || ! printf '%s  %s\n' "$hash" "$base" > "$dir/$base.sha256"; then
    rm -f -- "$part" "$dir/$base.sha256"; reason="не записан .sha256"; return 1
  fi
  mv -n -- "$part" "$dir/$base" 2>/dev/null
  if [ -e "$part" ] || [ ! -s "$dir/$base" ]; then
    rm -f -- "$part" "$dir/$base.sha256"; reason="копия $dir/$base не встала на место"; return 1
  fi
  made=$dir/$base
}

locked() {  # cmd... — under the backup lock (restore.sh holds it on the host; BACKUP_LOCK_HELD=1 = caller has it)
  if [ -n "${BACKUP_LOCK_HELD:-}" ]; then "$@"; return; fi
  mkdir -p "$root"
  if ! command -v flock >/dev/null 2>&1; then
    log "ВНИМАНИЕ: нет flock — копия без блокировки" >&2
    "$@"; return
  fi
  (
    flock -w "$LOCK_WAIT" 9 || exit 75   # the caller records «занято»
    "$@"
  ) 9>> "$root/.lock"
}

copy_once() {  # kind(weekly|monthly) — one copy per day, with its .sha256
  local dir=$root/$1
  mkdir -p "$dir"
  # shellcheck disable=SC2012  # our own names
  if ls -1 "$dir"/erp_"$(date +%Y-%m-%d)"_*.dump >/dev/null 2>&1; then return 0; fi
  cp -n -- "$made" "$dir/" && cp -n -- "$made.sha256" "$dir/"
}

daily_locked() {
  local code=0
  write_dump "$root/daily" "erp_$(date +%Y-%m-%d_%H%M%S)" 0 || code=1
  if [ "$code" = 0 ]; then
    [ "$KEEP_WEEKLY" -gt 0 ] && [ "$(date +%u)" = 7 ]       && { copy_once weekly || log "ВНИМАНИЕ: воскресная копия не записана" >&2; }
    [ "$KEEP_MONTHLY" -gt 0 ] && [ "$(date +%d)" = 01 ]       && { copy_once monthly || log "ВНИМАНИЕ: копия за 1-е число не записана" >&2; }
    keep_newest "$root/daily" "$KEEP_DAILY"
    keep_newest "$root/weekly" "$KEEP_WEEKLY"
    keep_newest "$root/monthly" "$KEEP_MONTHLY"
    write_status true daily ok 0
    log "backup ok: $made ($(du -h "$made" | cut -f1)), sha256 $(cut -d' ' -f1 < "$made.sha256")"
  else
    write_status false daily "$reason" 1
    log "BACKUP FAILED: $reason" >&2
  fi
  return "$code"
}
daily() {
  local code=0
  locked daily_locked || code=$?
  if [ "$code" = 75 ]; then
    reason="занято (восстановление или другая копия) дольше $LOCK_WAIT с"
    write_status false daily "$reason" 75; log "BACKUP FAILED: $reason" >&2
  fi
  return "$code"
}

intraday_locked() {
  if ! write_dump "$root/intraday" "erp_$(date +%Y-%m-%d_%H%M%S)" 0; then
    write_status false intraday "$reason" 1; log "INTRADAY BACKUP FAILED: $reason" >&2
    return 1
  fi
  keep_newest "$root/intraday" "$KEEP_INTRADAY"
  write_status true intraday ok 0
  log "intraday backup ok: $made ($(du -h "$made" | cut -f1))"
}
intraday() {
  local code=0
  locked intraday_locked || code=$?
  if [ "$code" = 75 ]; then
    reason="занято (восстановление или другая копия) дольше $LOCK_WAIT с"
    write_status false intraday "$reason" 75; log "INTRADAY BACKUP FAILED: $reason" >&2
  fi
  return "$code"
}

next_at() {  # hour → epoch of its next occurrence after now ($now)
  local t
  t=$(date -d "today $1:00" +%s)
  [ "$t" -le "$now" ] && t=$(date -d "tomorrow $1:00" +%s)
  echo "$t"
}

named_locked() {  # kind dir keep name protected
  local kind=$1 dir=$2 keep=$3 name=$4 protected=${5:-}
  if ! write_dump "$dir" "erp_$name" 1; then
    write_status false "$kind" "$reason" 1
    log "$(printf '%s' "$kind" | tr '[:lower:]' '[:upper:]') BACKUP FAILED: $reason" >&2
    return 1
  fi
  keep_newest "$dir" "$keep" "${protected:+$root/$protected}"
  write_status true "$kind" ok 0
  log "$kind backup ok: $made ($(du -h "$made" | cut -f1))"
}
named() {  # kind dir keep name [protected]
  local code=0
  case "$4" in ''|*[!A-Za-z0-9_.-]*) echo "$1: имя копии — только буквы, цифры, _ . -" >&2; return 2 ;; esac
  case "${5:-}" in *..*) echo "$1: защищённый путь без '..'" >&2; return 2 ;; esac
  locked named_locked "$@" || code=$?
  if [ "$code" = 75 ]; then
    reason="занято (восстановление или другая копия) дольше $LOCK_WAIT с"
    write_status false "$1" "$reason" 75; log "$1 BACKUP FAILED: $reason" >&2
  fi
  return "$code"
}

fresh() {  # 0 when the last good dump is younger than MAX_AGE_HOURS
  local epoch
  epoch=$(field last_success_epoch | tr -cd 0-9)
  [ -n "$epoch" ] && [ "$epoch" -gt 0 ] && [ $(( $(date +%s) - epoch )) -le $((MAX_AGE_HOURS * 3600)) ]
}
health() {
  if [ ! -s "$status_file" ]; then echo "нет $status_file: ни одной копии ещё не снято"; return 1; fi
  if ! grep -q '^  "ok": true,' "$status_file"; then
    echo "последняя попытка копии не удалась: $(field last_attempt_at), $(sed -n 's/^  "last_result": //p' "$status_file")"
    return 1
  fi
  if ! fresh; then
    echo "последняя удачная копия старше $MAX_AGE_HOURS ч: $(field last_success_at)"; return 1
  fi
  echo "копия свежая: $(field last_success_at), $(field last_success_file)"
}

case "${1:-}" in
  --now) daily; exit ;;
  --pre-update) named pre-update "$root/pre-update" "$KEEP_PRE_UPDATE" "${2:-}"; exit ;;
  --pre-restore) named pre-restore "$root/pre-restore" "$KEEP_PRE_RESTORE" "${2:-}" "${3:-}"; exit ;;
  --health) health; exit ;;
esac
failed=0
if ! fresh; then
  log "последней удачной копии нет или она старше $MAX_AGE_HOURS ч — копия сейчас"
  daily || failed=1
fi
while true; do
  now=$(date +%s); next=$(next_at "$BACKUP_HOUR"); kind=daily
  for h in $INTRADAY_HOURS; do
    [ "$h" = "$BACKUP_HOUR" ] && continue
    t=$(next_at "$h"); [ "$t" -lt "$next" ] && { next=$t; kind=intraday; }
  done
  # A failed nightly dump is retried; a failed daytime one just waits for the next slot.
  if [ "$failed" = 1 ] && [ $((now + RETRY_MINUTES * 60)) -lt "$next" ]; then next=$((now + RETRY_MINUTES * 60)); kind=daily; fi
  sleep $((next - now))
  failed=0
  if [ "$kind" = daily ]; then daily || failed=1; else intraday || true; fi
done
