#!/usr/bin/env bash
# Nightly pg_dump at BACKUP_HOUR (server time): /backups/daily keeps KEEP_DAILY dumps, Sunday's copy goes to
# /backups/weekly (KEEP_WEEKLY), the copy of the 1st of a month goes to /backups/monthly (KEEP_MONTHLY, 36:
# traceability records are kept 3 years — SUPPLYMAP OwnerTick.gs BACKUP_KEEP_MONTHS, owner 14.09.2026). Files (УПД, счета, КД-реестр) live in the database, so the dump is the whole ERP.
#   --now                 one daily dump right away (erp.sh backup-now).
#   --pre-update <name>   the dump update.sh takes before every update: /backups/pre-update/erp_<name>.dump + .sha256,
#                         checked with pg_restore --list, own rotation (KEEP_PRE_UPDATE, 10) — the daily ones are untouched.
#                         update.sh pipes THIS file into the backup container (bash -s), so the mode works even while the
#                         running container still mounts the previous version of the script.
set -u
root=${BACKUP_ROOT:-/backups}
KEEP_DAILY=${KEEP_DAILY:-14}; KEEP_WEEKLY=${KEEP_WEEKLY:-8}; KEEP_MONTHLY=${KEEP_MONTHLY:-36}
KEEP_PRE_UPDATE=${KEEP_PRE_UPDATE:-10}

keep_newest() {  # dir count: delete all but the newest <count> dumps (and their .sha256)
  local f
  # shellcheck disable=SC2012  # names are our own erp_<stamp>.dump, no spaces or newlines
  ls -1t "$1"/*.dump 2>/dev/null | tail -n +$(($2 + 1)) | while IFS= read -r f; do rm -f -- "$f" "$f.sha256"; done
}

dump() {
  local stamp file
  mkdir -p "$root/daily" "$root/weekly" "$root/monthly"
  stamp=$(date +%Y-%m-%d_%H%M)
  file=$root/daily/erp_${stamp}.dump
  if pg_dump -Fc -f "$file.part" && mv "$file.part" "$file"; then
    echo "$(date -Is) backup ok: $file ($(du -h "$file" | cut -f1))"
    [ "$(date +%u)" = 7 ] && cp "$file" "$root/weekly/"
    [ "$(date +%d)" = 01 ] && cp "$file" "$root/monthly/"
    keep_newest "$root/daily" "$KEEP_DAILY"
    keep_newest "$root/weekly" "$KEEP_WEEKLY"
    keep_newest "$root/monthly" "$KEEP_MONTHLY"
  else
    rm -f "$file.part"; echo "$(date -Is) BACKUP FAILED" >&2
    return 1
  fi
}

pre_update() {
  local name=${1:-} dir=$root/pre-update base
  case "$name" in ''|*[!A-Za-z0-9_.-]*) echo "pre-update: имя копии — только буквы, цифры, _ . -" >&2; return 2 ;; esac
  base=erp_${name}.dump
  mkdir -p "$dir"
  if ! pg_dump -Fc -f "$dir/$base.part"; then
    rm -f "$dir/$base.part"; echo "$(date -Is) PRE-UPDATE BACKUP FAILED: pg_dump" >&2; return 1
  fi
  if ! pg_restore --list "$dir/$base.part" >/dev/null; then
    rm -f "$dir/$base.part"; echo "$(date -Is) PRE-UPDATE BACKUP FAILED: копия не читается (pg_restore --list)" >&2; return 1
  fi
  mv "$dir/$base.part" "$dir/$base"
  (cd "$dir" && sha256sum "$base" > "$base.sha256") || { echo "PRE-UPDATE BACKUP FAILED: sha256" >&2; return 1; }
  keep_newest "$dir" "$KEEP_PRE_UPDATE"
  echo "$(date -Is) pre-update backup ok: $dir/$base ($(du -h "$dir/$base" | cut -f1))"
}

case "${1:-}" in
  --now) dump; exit ;;
  --pre-update) pre_update "${2:-}"; exit ;;
esac
while true; do
  now=$(date +%s); next=$(date -d "today ${BACKUP_HOUR}:00" +%s)
  [ "$next" -le "$now" ] && next=$(date -d "tomorrow ${BACKUP_HOUR}:00" +%s)
  sleep $((next - now)); dump
done
