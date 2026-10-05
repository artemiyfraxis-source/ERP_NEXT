#!/usr/bin/env bash
# All deploy-script tests: shellcheck (or `bash -n` when shellcheck is missing) + test/deploy/*.test.sh.
#   bash test/deploy/run.sh            SHELLCHECK=/path/to/shellcheck bash test/deploy/run.sh
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
scripts=(deploy/update.sh deploy/rehearse.sh deploy/backup-loop.sh deploy/restore.sh deploy/bootstrap.sh
         backend/docker-entrypoint.sh test/deploy/run.sh test/deploy/update.test.sh test/deploy/entrypoint.test.sh
         test/deploy/rehearse.test.sh test/deploy/stubs/docker test/deploy/stubs/pg_dump test/deploy/stubs/pg_restore)
status=0
cd "$repo" || exit 1
shellcheck=${SHELLCHECK:-$(command -v shellcheck || true)}
if [ -n "$shellcheck" ]; then
  "$shellcheck" -x "${scripts[@]}" && echo "shellcheck: ${#scripts[@]} scripts clean" || status=1
else
  echo "shellcheck not found — syntax only (bash -n / sh -n)"
  for f in "${scripts[@]}"; do
    case "$f" in backend/docker-entrypoint.sh) sh -n "$f" ;; *) bash -n "$f" ;; esac || status=1
  done
fi
for t in "$here"/*.test.sh; do
  bash "$t" || status=1
done
exit "$status"
