#!/usr/bin/env bash
# The in-container script of deploy/rehearse.sh (migrate-script), run by a real Python against a fake `app` package:
# no app.db_integrity → exit 20 before any migration (the rehearsal FAILS, never skips); snapshot → upgrade → snapshot →
# compare in this order; a compare that finds differences → exit 24. alembic is a stub.
#   bash test/deploy/rehearse.test.sh
set -uo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
passed=0 failed=0

py=""
for candidate in "$repo/backend/.venv/bin/python" "$repo/backend/.venv/Scripts/python.exe" python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys" >/dev/null 2>&1; then py=$candidate; break; fi
done
[ -n "$py" ] || { echo "rehearse.test.sh: SKIP — no Python"; exit 0; }
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

bin=$work/bin; mkdir -p "$bin"
printf '#!/usr/bin/env bash\nexec "%s" "$@"\n' "$py" > "$bin/python"
# shellcheck disable=SC2016  # $*, $STUB_LOG and $FAKE_UPGRADE_RC belong to the stub
printf '#!/usr/bin/env bash\necho "alembic $*" >> "$STUB_LOG"\nexit "${FAKE_UPGRADE_RC:-0}"\n' > "$bin/alembic"
chmod +x "$bin/python" "$bin/alembic"

fake_app() {  # dir with_integrity(0|1)
  mkdir -p "$1/app"; : > "$1/app/__init__.py"
  if [ "$2" = 1 ]; then
    cat > "$1/app/db_integrity.py" <<'PY'
import os, sys
with open(os.environ["STUB_LOG_NATIVE"], "a", encoding="utf-8") as log:
    log.write("db_integrity " + " ".join(sys.argv[1:2]) + "\n")
if sys.argv[1] == "snapshot":
    with open(sys.argv[sys.argv.index("--out") + 1], "w", encoding="utf-8") as out:
        out.write("{}")
elif sys.argv[1] == "compare":
    sys.exit(int(os.environ.get("FAKE_COMPARE_RC", "0")))
PY
  fi
}
run_script() {  # app-dir env...
  local dir=$1; shift
  export STUB_LOG=$work/calls.log; : > "$STUB_LOG"
  mkdir -p "$work/integrity"
  out=$(bash "$repo/deploy/rehearse.sh" migrate-script \
    | (cd "$dir" && env PATH="$bin:$PATH" STUB_LOG_NATIVE="$(native "$STUB_LOG")" INTEGRITY_DIR="$(native "$work/integrity")" \
       PYTHONIOENCODING=utf-8 "$@" sh -s) 2>&1)
  rc=$?
}
check() {
  local what=$1; shift
  if "$@"; then passed=$((passed + 1)); else failed=$((failed + 1)); echo "FAIL [$case_name] $what (rc=$rc)"; echo "$out"; cat "$STUB_LOG"; fi
}
calls() { tr -d '\r' < "$STUB_LOG" | tr '\n' '|'; }   # Python on Windows writes CRLF

case_name="no app.db_integrity"
fake_app "$work/without" 0
run_script "$work/without"
check "exit 20" [ "$rc" = 20 ]
check "no migration attempted" [ ! -s "$STUB_LOG" ]

case_name="green"
fake_app "$work/with" 1
run_script "$work/with"
check "exit 0" [ "$rc" = 0 ]
check "snapshot → upgrade → snapshot → compare" \
  [ "$(calls)" = "db_integrity snapshot|alembic upgrade head|db_integrity snapshot|db_integrity compare|" ]
check "snapshots written to INTEGRITY_DIR" [ -s "$work/integrity/before.json" ] && [ -s "$work/integrity/after.json" ]

case_name="compare finds differences"
run_script "$work/with" FAKE_COMPARE_RC=1
check "exit 24" [ "$rc" = 24 ]

case_name="migration fails"
run_script "$work/with" FAKE_UPGRADE_RC=1
check "exit 22" [ "$rc" = 22 ]
check "no after-snapshot or compare" [ "$(calls)" = "db_integrity snapshot|alembic upgrade head|" ]

echo "rehearse.test.sh: $passed passed, $failed failed"
[ "$failed" = 0 ]
