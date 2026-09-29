#!/usr/bin/env bash
# Nightly pg_dump at BACKUP_HOUR (server time): /backups/daily keeps KEEP_DAILY dumps, Sunday's copy goes to
# /backups/weekly (KEEP_WEEKLY), the copy of the 1st of a month goes to /backups/monthly (KEEP_MONTHLY, 36:
# traceability records are kept 3 years — SUPPLYMAP OwnerTick.gs BACKUP_KEEP_MONTHS, owner 14.09.2026). Files (УПД, счета, КД-реестр) live in the database, so the dump is the whole ERP.
set -u
mkdir -p /backups/daily /backups/weekly /backups/monthly
dump() {
  local stamp file
  stamp=$(date +%Y-%m-%d_%H%M)
  file=/backups/daily/erp_${stamp}.dump
  if pg_dump -Fc -f "$file.part" && mv "$file.part" "$file"; then
    echo "$(date -Is) backup ok: $file ($(du -h "$file" | cut -f1))"
    [ "$(date +%u)" = 7 ] && cp "$file" /backups/weekly/
    [ "$(date +%d)" = 01 ] && cp "$file" /backups/monthly/
    ls -1t /backups/daily/*.dump 2>/dev/null | tail -n +$((KEEP_DAILY + 1)) | xargs -r rm -f
    ls -1t /backups/weekly/*.dump 2>/dev/null | tail -n +$((KEEP_WEEKLY + 1)) | xargs -r rm -f
    ls -1t /backups/monthly/*.dump 2>/dev/null | tail -n +$((KEEP_MONTHLY + 1)) | xargs -r rm -f
  else
    rm -f "$file.part"; echo "$(date -Is) BACKUP FAILED" >&2
  fi
}
[ "${1:-}" = "--now" ] && { dump; exit; }
while true; do
  now=$(date +%s); next=$(date -d "today ${BACKUP_HOUR}:00" +%s)
  [ "$next" -le "$now" ] && next=$(date -d "tomorrow ${BACKUP_HOUR}:00" +%s)
  sleep $((next - now)); dump
done
