#!/usr/bin/env bash
# Restore drill on a REAL, THROWAWAY PostgreSQL 16 (CI job `restore-drill`): deploy/backup-loop.sh and deploy/restore.sh
# with real pg_dump/pg_restore/psql and the real migrations; only docker is a stub (test/deploy/stubs-pg/docker).
#  1. a dump at head-1 restored into an installation at head → exactly the head-1 schema and rows, api left stopped
#     (exit 3), the newer database kept as erp_prev_*, then `alembic upgrade head` and the integrity compare pass;
#  2. the newest daily at head restored over changed data → its rows back, its checksum unchanged, the safety dump holds
#     the replaced state, api running;
#  3. a dump whose pg_restore fails half-way (GRANT to a role that no longer exists) → production database unchanged,
#     no temporary database left, api never stopped.
#   RESTORE_DRILL=1 PGHOST=localhost PGPORT=5432 PGUSER=erp PGPASSWORD=erp bash test/deploy/restore-drill.test.sh
# It DROPS every database named erp or erp_* on that server: only a throwaway server on localhost is accepted.
set -uo pipefail
if [ "${RESTORE_DRILL:-}" != 1 ]; then
  echo "restore-drill.test.sh: SKIP — RESTORE_DRILL=1 and PGHOST/PGPORT/PGUSER/PGPASSWORD of a throwaway PostgreSQL 16"
  exit 0
fi
case "${PGHOST:-}" in localhost | 127.0.0.1) ;; *) echo "restore-drill.test.sh: only PGHOST=localhost (throwaway server)" >&2; exit 1 ;; esac
repo=$(cd "$(dirname "$0")/../.." && pwd)
stubs=$(cd "$(dirname "$0")/stubs-pg" && pwd)
work=$(mktemp -d)
passed=0 failed=0
for tool in psql pg_dump pg_restore sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || { echo "restore-drill.test.sh: no $tool" >&2; exit 1; }
done
py=""
for candidate in "$repo/backend/.venv/bin/python" "$repo/backend/.venv/Scripts/python.exe" python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import alembic, sqlalchemy, psycopg" >/dev/null 2>&1; then
    py=$candidate; break
  fi
done
[ -n "$py" ] || { echo "restore-drill.test.sh: no Python with alembic and psycopg" >&2; exit 1; }
mkdir -p "$work/bin"
printf '#!/usr/bin/env bash\nexec "%s" "$@"\n' "$py" > "$work/bin/python"
chmod +x "$work/bin/python"
if ! command -v flock >/dev/null 2>&1; then   # Git Bash on Windows; the lock is covered by backup.test.sh on CI
  printf '#!/usr/bin/env bash\nexit 0\n' > "$work/bin/flock"; chmod +x "$work/bin/flock"
fi
export PATH="$stubs:$work/bin:$PATH"
export DRILL_BACKEND=$repo/backend DRILL_URL_BASE="postgresql+psycopg://$PGUSER:$PGPASSWORD@$PGHOST:${PGPORT:-5432}"
export ERP_HOME=$work/home STUB_STATE=$work/home/stub STUB_LOG=$work/home/docker.log
export ERP_HEALTH_TRIES=3 BACKUP_MIN_FREE_MB=0 BACKUP_LOCK_WAIT=5 ERP_RESTORE_LOCK_WAIT=5
mkdir -p "$ERP_HOME/app" "$ERP_HOME/config" "$ERP_HOME/status" "$ERP_HOME/backups" "$STUB_STATE"
cp -R "$repo/deploy" "$ERP_HOME/app/deploy"
: > "$ERP_HOME/config/.env"; : > "$STUB_LOG"; echo running > "$STUB_STATE/api"

q() { psql -X -q -v ON_ERROR_STOP=1 -tA -d "$@" | tr -d '\r'; }
drop_all() {
  local db
  for db in $(q postgres -c "SELECT datname FROM pg_database WHERE datname = 'erp' OR datname LIKE 'erp\_%'"); do
    q postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$db'" >/dev/null
    q postgres -c "DROP DATABASE \"$db\"" >/dev/null
  done
  q postgres -c "DROP ROLE IF EXISTS drill_ghost" >/dev/null 2>&1 || true
}
trap 'drop_all; rm -rf "$work"' EXIT
alembic() { (cd "$repo/backend" && ERP_DATABASE_URL="$DRILL_URL_BASE/erp" python -m alembic "$@"); }
schema() {  # db → its schema, without comments and the per-dump \restrict keys
  pg_dump --schema-only --no-owner --no-privileges -d "$1" | grep -v -E '^(--|\\(un)?restrict|$)'
}
same_schema() {  # file db — prints the difference when there is one
  schema "$2" > "$work/schema-now.sql"
  cmp -s "$1" "$work/schema-now.sql" || { diff "$1" "$work/schema-now.sql" | head -20; return 1; }
}
# Catalog objects by name and kind. pg_dump text alone can't compare a migrated database with a restored one:
# PostgreSQL deparses `col IN (…)` on varchar differently after a dump/restore round trip.
objects() {
  q "$1" -c "
    SELECT 'column ' || table_name || '.' || column_name || ' ' || data_type || ' ' || is_nullable || ' ' || coalesce(column_default, '')
      FROM information_schema.columns WHERE table_schema = 'public'
    UNION ALL SELECT 'constraint ' || conrelid::regclass::text || ' ' || conname || ' ' || contype::text || ' ' || condeferrable::text || condeferred::text || convalidated::text
      FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'public'
    UNION ALL SELECT 'index ' || tablename || ' ' || indexname FROM pg_indexes WHERE schemaname = 'public'
    UNION ALL SELECT 'sequence ' || sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public'
    UNION ALL SELECT 'trigger ' || event_object_table || ' ' || trigger_name FROM information_schema.triggers WHERE trigger_schema = 'public'
    UNION ALL SELECT 'routine ' || routine_name FROM information_schema.routines WHERE routine_schema = 'public'
    UNION ALL SELECT 'view ' || table_name FROM information_schema.views WHERE table_schema = 'public'
    UNION ALL SELECT 'type ' || typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
      WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd')" | LC_ALL=C sort
}
same_objects() {  # file db
  objects "$2" > "$work/objects-now.txt" && [ -s "$1" ] || return 1
  cmp -s "$1" "$work/objects-now.txt" || { diff "$1" "$work/objects-now.txt" | head -20; return 1; }
}
counts() {  # db → «table rows» lines
  local sql="" table
  for table in $(q "$1" -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"); do
    sql="${sql:+$sql UNION ALL }SELECT '$table ' || count(*) FROM public.\"$table\""
  done
  q "$1" -c "$sql" | LC_ALL=C sort
}
revision() { q "$1" -c "SELECT version_num FROM alembic_version"; }
backup_now() {  # → path of the new daily dump
  local before
  before=$(find "$ERP_HOME/backups/daily" -name '*.dump' 2>/dev/null | sort)
  BACKUP_ROOT=$ERP_HOME/backups PGDATABASE=erp bash "$repo/deploy/backup-loop.sh" --now >/dev/null 2>&1 || return 1
  comm -13 <(printf '%s\n' "$before") <(find "$ERP_HOME/backups/daily" -name '*.dump' | sort) | head -1
}
hash_of() { sha256sum < "$1" | cut -d' ' -f1; }
run_restore() {
  : > "$STUB_LOG"
  bash "$ERP_HOME/app/deploy/restore.sh" "$1" > "$work/out.log" 2>&1
  rc=$?
}
check() {
  local what=$1; shift
  if "$@"; then passed=$((passed + 1)); else
    failed=$((failed + 1)); echo "FAIL [$case_name] $what (rc=${rc:-})"
    echo "--- out.log"; tail -20 "$work/out.log" 2>/dev/null; echo "--- databases"; q postgres -c "SELECT datname FROM pg_database ORDER BY 1"
  fi
}
databases() { q postgres -c "SELECT datname FROM pg_database WHERE datname LIKE 'erp%' ORDER BY 1" | tr '\n' ' '; }
prev_db() { q postgres -c "SELECT datname FROM pg_database WHERE datname LIKE 'erp\_prev\_%' ORDER BY 1 DESC LIMIT 1"; }

echo "== restore drill on $PGHOST:${PGPORT:-5432}"
drop_all
q postgres -c "CREATE DATABASE erp" >/dev/null
head=$(alembic heads | awk 'NR == 1 { print $1 }')
down_of() { (cd "$repo/backend" && python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; print(ScriptDirectory.from_config(Config('alembic.ini')).get_revision('$1').down_revision)"); }
# The drill needs a dump older than the code in schema, not only in data: a data-only head (0107_stage_ladder) leaves
# the objects as they were, so step back to the nearest revision the head's schema differs from.
previous=$(down_of head)
for _ in 1 2 3 4 5 6 7 8; do
  alembic upgrade "$previous" >/dev/null 2>&1 || { echo "alembic upgrade $previous failed" >&2; exit 1; }
  objects erp > "$work/objects-probe.txt"
  alembic upgrade head >/dev/null 2>&1 || { echo "alembic upgrade head failed" >&2; exit 1; }
  cmp -s "$work/objects-probe.txt" <(objects erp) || break
  previous=$(down_of "$previous")
  q postgres -c "DROP DATABASE erp" >/dev/null && q postgres -c "CREATE DATABASE erp" >/dev/null
done
q postgres -c "DROP DATABASE erp" >/dev/null && q postgres -c "CREATE DATABASE erp" >/dev/null
alembic upgrade "$previous" >/dev/null 2>&1 || { echo "alembic upgrade $previous failed" >&2; exit 1; }
(cd "$repo/backend" && ERP_DATABASE_URL="$DRILL_URL_BASE/erp" python -m tests.migrations.seed_any_revision >/dev/null) \
  || { echo "seed failed" >&2; exit 1; }
objects erp > "$work/objects-old.txt"
counts erp > "$work/counts-old.txt"
dump_old=$(backup_now) || { echo "backup at $previous failed" >&2; exit 1; }
hash_old=$(hash_of "$dump_old")
# Reference: the same dump restored by hand into an empty database — what an exact restore must give.
q postgres -c "CREATE DATABASE erp_drillref" >/dev/null
pg_restore --no-owner -d erp_drillref "$dump_old" || { echo "reference restore failed" >&2; exit 1; }
schema erp_drillref > "$work/schema-old.sql"
q postgres -c "DROP DATABASE erp_drillref" >/dev/null
alembic upgrade head >/dev/null 2>&1 || { echo "alembic upgrade head failed" >&2; exit 1; }
objects erp > "$work/objects-new.txt"

case_name="dump at $previous into an installation at $head"
check "the newer installation really differs" bash -c "! cmp -s '$work/objects-old.txt' '$work/objects-new.txt'"
run_restore "$dump_old"
check "exit 3: restored, api stays stopped (code is at $head)" [ "$rc" = 3 ]
check "exactly the old schema (as a clean restore of the dump)" same_schema "$work/schema-old.sql" erp
check "the same objects as the installation at $previous" same_objects "$work/objects-old.txt" erp
check "exactly the old rows" cmp -s "$work/counts-old.txt" <(counts erp)
check "revision $previous" [ "$(revision erp)" = "$previous" ]
check "newer database kept at $head" [ "$(revision "$(prev_db)")" = "$head" ]
check "no temporary database left" bash -c "! printf '%s' '$(databases)' | grep -q erp_restore_"
check "api stopped" [ "$(cat "$STUB_STATE/api")" = stopped ]
check "source dump keeps its checksum" [ "$(hash_of "$dump_old")" = "$hash_old" ]
(cd "$repo/backend" && ERP_DATABASE_URL="$DRILL_URL_BASE/erp" python -m app.db_integrity snapshot --out "$work/before.json" >/dev/null)
alembic upgrade head >/dev/null 2>&1; up=$?
(cd "$repo/backend" && ERP_DATABASE_URL="$DRILL_URL_BASE/erp" python -m app.db_integrity snapshot --out "$work/after.json" >/dev/null)
check "alembic upgrade head over the restored database" [ "$up" = 0 ]
check "integrity compare after the upgrade" bash -c "cd '$repo/backend' && python -m app.db_integrity compare '$work/before.json' '$work/after.json' >/dev/null"
echo running > "$STUB_STATE/api"

case_name="newest daily at $head over changed data"
counts erp > "$work/counts-b.txt"
dump_b=$(backup_now) || { echo "backup B failed" >&2; exit 1; }
hash_b=$(hash_of "$dump_b")
leaf=$(q erp -c "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version' AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.confrelid = c.oid) ORDER BY c.relname" \
  | while read -r t; do [ "$(q erp -c "SELECT count(*) FROM public.\"$t\"")" -gt 0 ] && { echo "$t"; break; }; done)
check "a table to change" [ -n "$leaf" ]
q erp -c "DELETE FROM public.\"$leaf\"" >/dev/null
run_restore "$dump_b"
check "exit 0" [ "$rc" = 0 ]
check "rows of the dump back" cmp -s "$work/counts-b.txt" <(counts erp)
check "source dump keeps its checksum" [ "$(hash_of "$dump_b")" = "$hash_b" ] \
  && (cd "$(dirname "$dump_b")" && sha256sum -c --status "$(basename "$dump_b").sha256")
check "api running" [ "$(cat "$STUB_STATE/api")" = running ]
safety=$(find "$ERP_HOME/backups/pre-restore" -name '*.dump' | sort | tail -1)
check "safety dump with a valid .sha256" bash -c "cd '$(dirname "$safety")' && sha256sum -c --status '$(basename "$safety").sha256'"
check "safety dump holds the replaced state ($leaf empty)" \
  [ "$(pg_restore --data-only -t "$leaf" -f - "$safety" | awk '/^COPY /{c=1;next} /^\\\.$/{c=0} c{n++} END{print n+0}')" = 0 ]
check "replaced database kept with $leaf empty" [ "$(q "$(prev_db)" -c "SELECT count(*) FROM public.\"$leaf\"")" = 0 ]

case_name="pg_restore fails half-way"
q postgres -c "CREATE ROLE drill_ghost" >/dev/null
q erp -c "GRANT SELECT ON public.\"$leaf\" TO drill_ghost" >/dev/null
dump_c=$(backup_now) || { echo "backup C failed" >&2; exit 1; }
q erp -c "REVOKE SELECT ON public.\"$leaf\" FROM drill_ghost" >/dev/null
q postgres -c "DROP ROLE drill_ghost" >/dev/null
check "three dumps, three different files" [ "$(find "$ERP_HOME/backups/daily" -name '*.dump' | wc -l)" -eq 3 ]
counts erp > "$work/counts-c.txt"
schema erp > "$work/schema-c.sql"
dbs_before=$(databases)
run_restore "$dump_c"
check "exit 1" [ "$rc" = 1 ]
check "failed in pg_restore" grep -q '(restore)' "$work/out.log"
check "production rows unchanged" cmp -s "$work/counts-c.txt" <(counts erp)
check "production schema unchanged" same_schema "$work/schema-c.sql" erp
check "no temporary database left" [ "$(databases)" = "$dbs_before" ]
check "api never stopped" bash -c "! grep -q 'stop api worker' '$STUB_LOG'" && [ "$(cat "$STUB_STATE/api")" = running ]

echo "restore-drill.test.sh: $passed passed, $failed failed"
[ "$failed" = 0 ]
