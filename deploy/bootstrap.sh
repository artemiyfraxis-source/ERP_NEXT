#!/usr/bin/env bash
# First start on a fresh Ubuntu 22.04/24.04 VPS, as root. Idempotent: re-running keeps the existing .env.
#   bash /opt/erp/app/deploy/bootstrap.sh [domain]
# Without a domain the site gets <ip-with-dashes>.sslip.io, which Caddy can obtain a certificate for.
set -euo pipefail
export ERP_HOME=${ERP_HOME:-/opt/erp}
here=$(cd "$(dirname "$0")" && pwd)

if ! command -v docker >/dev/null; then
  apt-get update && apt-get install -y ca-certificates curl
  curl -fsSL https://get.docker.com | sh
fi
# Docker Hub is unreliable from Russian networks: try the provider mirrors first, Hub last.
if [ ! -f /etc/docker/daemon.json ]; then
  cat > /etc/docker/daemon.json <<'JSON'
{ "registry-mirrors": ["https://dockerhub.timeweb.cloud", "https://mirror.gcr.io"],
  "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
JSON
  systemctl restart docker
fi

if command -v ufw >/dev/null; then
  ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null
fi
timedatectl set-timezone Europe/Moscow 2>/dev/null || true

mkdir -p "$ERP_HOME"/{config/secrets,supplymap,import,backups}
chmod 700 "$ERP_HOME/config"
chown 10001 "$ERP_HOME/import"  # the container user writes the Google snapshots there
chmod 755 "$ERP_HOME/config/secrets"
if [ ! -f "$ERP_HOME/config/.env" ]; then
  domain=${1:-}
  if [ -z "$domain" ]; then
    ip=$(curl -fsS4 https://api.ipify.org || hostname -I | awk '{print $1}')
    domain="${ip//./-}.sslip.io"
  fi
  sed -e "s|^ERP_DOMAIN=.*|ERP_DOMAIN=$domain|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" \
      "$here/.env.example" > "$ERP_HOME/config/.env"
  chmod 600 "$ERP_HOME/config/.env"
fi
for f in "$ERP_HOME"/config/secrets/*.json; do [ -f "$f" ] && chown 10001 "$f" && chmod 600 "$f"; done
[ -f "$ERP_HOME/config/secrets/google-service-account.json" ] || \
  echo "!! Положи ключ Google в $ERP_HOME/config/secrets/google-service-account.json (без него «Из расчёта» ответит 503)"

"$here/erp.sh" up -d --build
"$here/erp.sh" ps
echo "Сайт: https://$(grep ^ERP_DOMAIN= "$ERP_HOME/config/.env" | cut -d= -f2)"
