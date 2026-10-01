#!/usr/bin/env bash
# One-time setup of the server auto-update (deploy/auto-update.sh), as root. Idempotent.
#   bash /opt/erp/app/deploy/install-auto-update.sh
# Creates the read-only deploy key and prints its public half: add it once at
#   https://github.com/artemiyfraxis-source/ERP_NEXT/settings/keys/new  (title erp-server, «Allow write access» OFF)
# then installs the systemd timer (2:00 and 6:00 MSK) and the `erp-update` command:
#   erp-update now     # update right away (a minute of downtime — better not in the middle of the shift)
#   erp-update status  # last run, revision, banner
set -euo pipefail
export ERP_HOME=${ERP_HOME:-/opt/erp}
key=$ERP_HOME/config/github-deploy-key
mkdir -p "$ERP_HOME/status"
if [ ! -f "$key" ]; then
  ssh-keygen -q -t ed25519 -N '' -C "erp-server deploy (read-only)" -f "$key"
fi
chmod 600 "$key"

cat > /etc/systemd/system/erp-auto-update.service <<UNIT
[Unit]
Description=ERP auto-update from GitHub main
After=network-online.target docker.service
Wants=network-online.target
[Service]
Type=oneshot
Environment=ERP_HOME=$ERP_HOME
ExecStart=/bin/bash $ERP_HOME/app/deploy/auto-update.sh
TimeoutStartSec=45min
UNIT
cat > /etc/systemd/system/erp-auto-update.timer <<UNIT
[Unit]
Description=ERP auto-update before and after the shift
[Timer]
OnCalendar=*-*-* 02:00:00
OnCalendar=*-*-* 06:00:00
Persistent=false
[Install]
WantedBy=timers.target
UNIT
cat > /usr/local/bin/erp-update <<CMD
#!/usr/bin/env bash
case "\${1:-status}" in
  now) touch $ERP_HOME/update-now && systemctl start erp-auto-update.service; journalctl -u erp-auto-update -n 2 --no-pager -o cat ;;
  status) echo "версия: \$(cat $ERP_HOME/app/REVISION)"; systemctl list-timers erp-auto-update.timer --no-pager | head -2
          [ -f $ERP_HOME/status/update-status.json ] && cat $ERP_HOME/status/update-status.json && echo
          tail -5 $ERP_HOME/status/update-last.log 2>/dev/null ;;
  *) echo "usage: erp-update now|status" ;;
esac
CMD
chmod 755 /usr/local/bin/erp-update
# Out-of-schedule requests from GitHub (button «Обновить сервер»), see deploy/update-request.sh.
cat > /etc/systemd/system/erp-update-request.service <<UNIT
[Unit]
Description=ERP update request from GitHub (branch update-request)
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
Environment=ERP_HOME=$ERP_HOME
ExecStart=/bin/bash $ERP_HOME/app/deploy/update-request.sh
UNIT
cat > /etc/systemd/system/erp-update-request.timer <<UNIT
[Unit]
Description=ERP: check GitHub for an update request every 2 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now erp-auto-update.timer erp-update-request.timer >/dev/null

export GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
if git ls-remote git@github.com:artemiyfraxis-source/ERP_NEXT.git main >/dev/null 2>&1; then
  echo "Автообновление включено: GitHub доступен, обновление в 2:00 и 6:00, вручную — erp-update now."
else
  echo "Автообновление включено, но GitHub пока не пускает. Добавь ключ (только чтение) один раз:"
  echo "  https://github.com/artemiyfraxis-source/ERP_NEXT/settings/keys/new"
  cat "$key.pub"
fi
