#!/usr/bin/env bash
# Nightly pg_dump at BACKUP_HOUR (server time): /backups/daily keeps KEEP_DAILY dumps, Sunday's copy goes to
# /backups/weekly (KEEP_WEEKLY). Files (УПД, счета, КД-реестр) live in the database, so the dump is the whole ERP.
set -u
mkdir -p /backups/daily /backups/weekly
dump() {
  local stamp file
  stamp=$(date +%Y-%m-%d_%H%M)
  file=/backups/daily/erp_${stamp}.dump
  if pg_dump -Fc -f "$file.part" && mv "$file.part" "$file"; then
    echo "$(date -Is) backup ok: $file ($(du -h "$file" | cut -f1))"
    [ "$(date +%u)" = 7 ] && cp "$file" /backups/weekly/
    ls -1t /backups/daily/*.dump 2>/dev/null | tail -n +$((KEEP_DAILY + 1)) | xargs -r rm -f
    ls -1t /backups/weekly/*.dump 2>/dev/null | tail -n +$((KEEP_WEEKLY + 1)) | xargs -r rm -f
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
