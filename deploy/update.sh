#!/usr/bin/env bash
# Roll out a new code snapshot: backup first, then rebuild and restart (migrations run on api start).
# push.ps1 unpacks the new tree into $ERP_HOME/app.new and calls this script from there.
set -euo pipefail
export ERP_HOME=${ERP_HOME:-/opt/erp}
new=$(cd "$(dirname "$0")/.." && pwd)
if docker compose ls -q 2>/dev/null | grep -qx erp; then
  "$ERP_HOME/app/deploy/erp.sh" backup-now
fi
if [ "$new" != "$ERP_HOME/app" ]; then
  rm -rf "$ERP_HOME/app.prev"
  [ -d "$ERP_HOME/app" ] && mv "$ERP_HOME/app" "$ERP_HOME/app.prev"
  mv "$new" "$ERP_HOME/app"
fi
cd "$ERP_HOME/app/deploy"
./erp.sh build api
./erp.sh up -d
for i in $(seq 1 60); do
  if ./erp.sh exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" 2>/dev/null; then
    echo "OK: $(cat "$ERP_HOME/app/REVISION" 2>/dev/null)"; exit 0
  fi
  sleep 3
done
echo "API не поднялся: ./erp.sh logs api. Откат кода: push.ps1 -Ref <прежний коммит> (прежний в $ERP_HOME/app.prev/REVISION)" >&2
exit 1
