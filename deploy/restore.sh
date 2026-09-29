#!/usr/bin/env bash
# Restore the database from a dump:  deploy/restore.sh /opt/erp/backups/daily/erp_2026-10-01_0300.dump
# Takes a safety dump first, stops api/worker, replaces the whole database, starts them again.
set -euo pipefail
dump=${1:?usage: restore.sh <file.dump>}
export ERP_HOME=${ERP_HOME:-/opt/erp}
cd "$(dirname "$0")"
case "$dump" in "$ERP_HOME/backups/"*) ;; *) echo "dump must be under $ERP_HOME/backups" >&2; exit 2 ;; esac
./erp.sh backup-now
./erp.sh stop api worker
./erp.sh exec -T backup pg_restore --clean --if-exists --no-owner -d erp "/backups/${dump#"$ERP_HOME/backups/"}"
./erp.sh start api worker
echo "restored from $dump"
