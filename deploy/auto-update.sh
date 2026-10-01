#!/usr/bin/env bash
# Server auto-update (owner 01.10.2026: «обновления давай утром и вечером или вручную»). A systemd timer runs this at
# 2:00 and 6:00 MSK (owner 01.10: «давай обновы в 6 и в 2 ночи»; nightly backup is 3:00); `erp-update now` runs it at once. Install it once with
# deploy/install-auto-update.sh. Each run:
#   1. fetches main from GitHub with the read-only deploy key ($ERP_HOME/config/github-deploy-key);
#   2. nothing new → clears a stale failure banner and exits;
#   3. unpacks the commit into app.new and runs update.sh (backup first, build, migrations, restart, health check);
#   4. failure → the previous code is started again and the ERP shows the red banner «Обновление не установилось» with
#      the tail of the log ($ERP_HOME/status/update-status.json, served by Caddy). A failed commit is retried at the
#      next two runs, then left until the next commit (`erp-update now` always tries). The database is NOT rolled back automatically: the
#      backup taken in step 4 is in backups/daily (restore.sh) if a migration of the failed version broke data.
# Full log of the last run: $ERP_HOME/status/update-last.log; history: journalctl -u erp-auto-update.
set -uo pipefail
export ERP_HOME=${ERP_HOME:-/opt/erp}
REPO=${ERP_GIT_REPO:-git@github.com:artemiyfraxis-source/ERP_NEXT.git}
key=$ERP_HOME/config/github-deploy-key
src=$ERP_HOME/src
status_dir=$ERP_HOME/status
status=$status_dir/update-status.json
state=$status_dir/auto-update.state
log=$status_dir/update-last.log
mkdir -p "$status_dir"; chmod 755 "$status_dir"
[ -f "$key" ] || { echo "нет ключа $key — автообновление не настроено (install-auto-update.sh)"; exit 0; }
export GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
exec 9>"$status_dir/auto-update.lock"; flock -n 9 || exit 0

write_status() {  # kind reason retry logfile
  python3 - "$status" "$1" "$2" "$3" "$4" <<'PY'
import json, sys, datetime
path, kind, reason, retry, logfile = sys.argv[1:6]
try:
    tail = [l.rstrip("\n") for l in open(logfile, encoding="utf-8", errors="replace")][-15:]
except OSError:
    tail = []
body = {"ok": False, "kind": kind, "at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
        "reason": reason, "retry": retry or None, "log": tail}
open(path + ".tmp", "w", encoding="utf-8").write(json.dumps(body, ensure_ascii=False))
PY
  mv "$status.tmp" "$status"; chmod 644 "$status"
}
clear_status() { [ "$(python3 -c "import json;print(json.load(open('$status')).get('kind',''))" 2>/dev/null)" = "${1:-}" ] && rm -f "$status"; }

# 1. Fetch.
fetch_log=$status_dir/fetch.log
if [ ! -d "$src/.git" ]; then git clone -q --branch main "$REPO" "$src" >"$fetch_log" 2>&1 || true; fi
if ! git -C "$src" fetch -q origin main >"$fetch_log" 2>&1; then
  write_status fetch "сервер не достучался до GitHub" "" "$fetch_log"
  exit 1
fi
[ -f "$status" ] && clear_status fetch
full=$(git -C "$src" rev-parse origin/main)
current=$(cat "$ERP_HOME/app/REVISION" 2>/dev/null || echo none)
# 2. Up to date (REVISION may be a short sha written by push.ps1).
if [ -n "$current" ] && [ "${full#"$current"}" != "$full" ]; then
  [ -f "$status" ] && clear_status update
  rm -f "$ERP_HOME/update-now"
  echo "уже последняя версия: $current"; exit 0
fi

# Retry budget for a failing commit: 3 scheduled runs; `erp-update now` ($ERP_HOME/update-now) always tries.
read -r failed_rev attempts last_try 2>/dev/null < "$state" || { failed_rev=""; attempts=0; last_try=0; }
[ "$failed_rev" = "$full" ] || attempts=0
if [ "$attempts" -ge 3 ] && [ ! -f "$ERP_HOME/update-now" ]; then exit 0; fi
rm -f "$ERP_HOME/update-now"

# 3. Update.
short=$(git -C "$src" rev-parse --short=7 "$full")
echo "== $(date -Is) обновление $current → $short" | tee "$log"
rm -rf "$ERP_HOME/app.new"; mkdir -p "$ERP_HOME/app.new"
git -C "$src" archive "$full" | tar -x -C "$ERP_HOME/app.new"
echo "$short" > "$ERP_HOME/app.new/REVISION"
if bash "$ERP_HOME/app.new/deploy/update.sh" >>"$log" 2>&1; then
  rm -f "$state"; [ -f "$status" ] && clear_status update
  echo "OK: $short"; exit 0
fi

# 4. Failure: back to the previous code.
attempts=$((attempts + 1)); echo "$full $attempts $(date +%s)" > "$state"
reason=$(grep -v '^\s*$' "$log" | tail -1)
if [ -d "$ERP_HOME/app.prev" ] && [ "$(cat "$ERP_HOME/app/REVISION" 2>/dev/null)" = "$short" ]; then
  echo "== откат на $(cat "$ERP_HOME/app.prev/REVISION")" >>"$log"
  rm -rf "$ERP_HOME/app.failed"; mv "$ERP_HOME/app" "$ERP_HOME/app.failed"; mv "$ERP_HOME/app.prev" "$ERP_HOME/app"
  (cd "$ERP_HOME/app/deploy" && ./erp.sh build api && ./erp.sh up -d) >>"$log" 2>&1
fi
retry=""; [ "$attempts" -lt 3 ] && retry="повтор при следующем обновлении ($attempts из 3), сразу: erp-update now"
write_status update "${reason:-версия $short не поднялась}" "$retry" "$log"
exit 1
