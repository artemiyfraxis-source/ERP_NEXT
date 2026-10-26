#!/usr/bin/env bash
# Rehearsal of the operation-template write on a throwaway copy of the production DB (live-data protocol).
# Run on the server as: sudo -n bash /tmp/tpl/tpl-rehearse.sh   (expects /tmp/tpl/{apply_templates.py,payload.json,check.py} (папка docs/data/operation-templates-2026-10-25))
set -euo pipefail
cd /opt/erp/app/deploy
export ERP_CANDIDATE_IMAGE=erp-next:latest
compose=(docker compose -p erp-tplrehearsal -f docker-compose.rehearsal.yml)
teardown() { "${compose[@]}" down -v --remove-orphans --timeout 5 >/dev/null 2>&1 || true; }
teardown; trap teardown EXIT

dump=$(ls -t /opt/erp/backups/intraday/*.dump /opt/erp/backups/pre-update/*.dump /opt/erp/backups/daily/*.dump 2>/dev/null | head -1)
echo "== дамп: $dump ($(stat -c %y "$dump" | cut -c1-16))"
"${compose[@]}" up -d db >/dev/null
for _ in $(seq 1 60); do
  "${compose[@]}" exec -T -e PGPASSWORD=rehearsal db pg_isready -q -h 127.0.0.1 -U erp -d erp >/dev/null 2>&1 && break; sleep 2
done
"${compose[@]}" exec -T -e PGPASSWORD=rehearsal db pg_restore -h 127.0.0.1 -U erp -d erp --no-owner --no-acl < "$dump"
run() { "${compose[@]}" run --rm -T -e PYTHONPATH=/app/backend -v /tmp/tpl:/tpl api "$@"; }
echo "== до записи"
run python /tpl/check.py
echo "== запись шаблонов на копии (--apply)"
run python /tpl/apply_templates.py /tpl/payload.json --apply
echo "== после записи"
run python /tpl/check.py
echo "== пересчёт ЭТК на копии (dry-run)"
run python -m app.workflows.etk_refresh --dry-run 2>&1 | tail -40
echo "== репетиция окончена, копия удаляется"
