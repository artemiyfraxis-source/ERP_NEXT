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
mkdir -p "$ERP_HOME/status"
./erp.sh build api
./erp.sh up -d
for i in $(seq 1 60); do
  if ./erp.sh exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" 2>/dev/null; then
    # То, чего нет в SUPPLYMAP: шаблоны карт ERP и правки справочников (идемпотентно, ручные правки не трогает).
    ./erp.sh cli python -m app.modules.techcards.seed_library --erp-only || echo "Шаблоны карт ERP не записаны" >&2
    ./erp.sh cli python -m app.modules.references.erp_corrections || echo "Правки справочников не записаны" >&2
    ./erp.sh cli python -m app.modules.identity.issue_logins --out /app/data/import/logins.csv || echo "Логины не выданы" >&2
    # The auto-update timer and command come from this repo too: refresh them when they are installed.
    if [ -f /etc/systemd/system/erp-auto-update.timer ]; then
      bash "$ERP_HOME/app/deploy/install-auto-update.sh" >/dev/null || echo "Таймер автообновления не обновлён" >&2
    fi
    echo "OK: $(cat "$ERP_HOME/app/REVISION" 2>/dev/null)"; exit 0
  fi
  sleep 3
done
echo "API не поднялся: ./erp.sh logs api. Откат кода: push.ps1 -Ref <прежний коммит> (прежний в $ERP_HOME/app.prev/REVISION)" >&2
exit 1
