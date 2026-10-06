#!/usr/bin/env bash
# backend/docker-entrypoint.sh: uvicorn starts only on a database at the migration head (real alembic + SQLite through
# the backend Python), or after `alembic upgrade head` with ERP_AUTO_MIGRATE=1. uvicorn and alembic are stubs.
#   bash test/deploy/entrypoint.test.sh
set -uo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
passed=0 failed=0

py=""
for candidate in "$repo/backend/.venv/bin/python" "$repo/backend/.venv/Scripts/python.exe" python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import alembic, sqlalchemy" >/dev/null 2>&1; then
    py=$candidate; break
  fi
done
if [ -z "$py" ]; then echo "entrypoint.test.sh: SKIP — no Python with alembic (backend/.venv or pip install)"; exit 0; fi

bin=$work/bin; mkdir -p "$bin"
printf '#!/usr/bin/env bash\nexec "%s" "$@"\n' "$py" > "$bin/python"
# shellcheck disable=SC2016  # $* and $STUB_LOG belong to the stubs
{ printf '#!/usr/bin/env bash\necho "uvicorn $*" >> "$STUB_LOG"\n' > "$bin/uvicorn"
  printf '#!/usr/bin/env bash\necho "alembic $*" >> "$STUB_LOG"\n' > "$bin/alembic"; }
chmod +x "$bin/python" "$bin/uvicorn" "$bin/alembic"
head=$(cd "$repo/backend" && "$py" -c "from alembic.config import Config; from alembic.script import ScriptDirectory; print(ScriptDirectory.from_config(Config('alembic.ini')).get_heads()[0])")

db_url() {  # file → SQLAlchemy URL the backend Python understands (a Windows path under Git Bash)
  local path=$1
  command -v cygpath >/dev/null 2>&1 && path=$(cygpath -m "$path")
  printf 'sqlite:///%s' "$path"
}
make_db() {  # file [revision] — a SQLite file with alembic_version (or without it when no revision)
  "$py" -c "
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('CREATE TABLE IF NOT EXISTS unrelated (id INTEGER)')
if len(sys.argv) > 2:
    connection.execute('CREATE TABLE alembic_version (version_num VARCHAR(128) NOT NULL PRIMARY KEY)')
    connection.execute('INSERT INTO alembic_version VALUES (?)', (sys.argv[2],))
connection.commit()
" "$(command -v cygpath >/dev/null 2>&1 && cygpath -w "$1" || echo "$1")" "${@:2}"
}
run_entrypoint() {  # extra env assignments...
  export STUB_LOG=$work/calls.log; : > "$STUB_LOG"
  out=$(cd "$repo/backend" && env PATH="$bin:$PATH" PYTHONIOENCODING=utf-8 "$@" sh "$repo/backend/docker-entrypoint.sh" 2>&1)
  rc=$?
}
check() {
  local what=$1; shift
  if "$@"; then passed=$((passed + 1)); else failed=$((failed + 1)); echo "FAIL [$case_name] $what"; echo "$out"; fi
}
called() { grep -q -F -- "$1" "$STUB_LOG"; }
not_called() { ! called "$1"; }
says() { printf '%s' "$out" | grep -q -F -- "$1"; }

case_name="database at head"
make_db "$work/head.db" "$head"
run_entrypoint ERP_DATABASE_URL="$(db_url "$work/head.db")"
check "exit 0" [ "$rc" = 0 ]
check "uvicorn started" called "uvicorn app.main:app"
check "no migration" not_called "alembic"

case_name="database behind the code"
make_db "$work/old.db" "0001_older_revision"
run_entrypoint ERP_DATABASE_URL="$(db_url "$work/old.db")"
check "refuses" [ "$rc" != 0 ]
check "uvicorn not started" not_called "uvicorn"
check "no migration" not_called "alembic"
check "Russian error names both revisions" says "ревизия базы 0001_older_revision не совпадает с ревизией кода $head"

case_name="empty database"
make_db "$work/empty.db"
run_entrypoint ERP_DATABASE_URL="$(db_url "$work/empty.db")"
check "refuses" [ "$rc" != 0 ]
check "uvicorn not started" not_called "uvicorn"
check "explains the first install" says "ERP_AUTO_MIGRATE=1"

case_name="ERP_AUTO_MIGRATE=1"
run_entrypoint ERP_AUTO_MIGRATE=1 ERP_DATABASE_URL="$(db_url "$work/empty.db")"
check "exit 0" [ "$rc" = 0 ]
check "migrates first" called "alembic upgrade head"
check "then starts uvicorn" called "uvicorn app.main:app"

case_name="no database url"
run_entrypoint ERP_DATABASE_URL=
check "refuses" [ "$rc" != 0 ]
check "uvicorn not started" not_called "uvicorn"

echo "entrypoint.test.sh: $passed passed, $failed failed"
[ "$failed" = 0 ]
