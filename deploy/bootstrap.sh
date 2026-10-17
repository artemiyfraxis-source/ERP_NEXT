#!/usr/bin/env bash
# First start on a fresh Ubuntu 22.04/24.04/26.04 machine, as root. Idempotent: re-running keeps the existing .env.
#   bash /opt/erp/app/deploy/bootstrap.sh [lan|<domain>]
# lan (default): the shop-floor server, site at http://<LAN IP>; from outside through Tailscale (HTTPS, *.ts.net).
# <domain>: a public machine, Caddy gets a Let's Encrypt certificate for it.
set -euo pipefail
export ERP_HOME=${ERP_HOME:-/opt/erp}
here=$(cd "$(dirname "$0")" && pwd)

if ! command -v docker >/dev/null; then
  apt-get update && apt-get install -y ca-certificates curl
  # get.docker.com lags behind new Ubuntu releases (26.04 on the shop-floor server): fall back to Ubuntu's own packages
  # and drop the half-added Docker apt source so later `apt-get update` stays clean.
  if ! curl -fsSL https://get.docker.com | sh; then
    rm -f /etc/apt/sources.list.d/docker.list /etc/apt/sources.list.d/docker.sources
    apt-get update && apt-get install -y docker.io
    apt-get install -y docker-compose-v2 || apt-get install -y docker-compose-plugin || apt-get install -y docker-compose
  fi
fi
docker compose version >/dev/null || { echo "!! docker compose не установился" >&2; exit 1; }
systemctl enable --now docker   # containers restart:unless-stopped come back after a power cut
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

mkdir -p "$ERP_HOME"/{config/secrets,supplymap,import,backups,status}
chmod 700 "$ERP_HOME/config"
chown 10001 "$ERP_HOME/import"  # the container user writes the Google snapshots there
chmod 755 "$ERP_HOME/config/secrets"
if [ ! -f "$ERP_HOME/config/.env" ]; then
  domain=${1:-lan}
  secure=1
  publish=127.0.0.1:8443
  if [ "$domain" = lan ]; then domain=:80; secure=0; else publish=443; fi
  sed -e "s|^ERP_DOMAIN=.*|ERP_DOMAIN=$domain|" -e "s|^ERP_COOKIE_SECURE=.*|ERP_COOKIE_SECURE=$secure|" \
      -e "s|^ERP_HTTPS_PUBLISH=.*|ERP_HTTPS_PUBLISH=$publish|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" \
      "$here/.env.example" > "$ERP_HOME/config/.env"
  chmod 600 "$ERP_HOME/config/.env"
fi
for f in "$ERP_HOME"/config/secrets/*.json; do [ -f "$f" ] && chown 10001 "$f" && chmod 600 "$f"; done
[ -f "$ERP_HOME/config/secrets/google-service-account.json" ] || \
  echo "!! Положи ключ Google в $ERP_HOME/config/secrets/google-service-account.json (без него «Из расчёта» ответит 503)"

# First install (no database volume yet): the API creates the schema itself on its first start. On a machine that
# already has a database the API never migrates on start — new migrations go through update.sh (backup → rehearsal).
if docker volume inspect erp_pgdata >/dev/null 2>&1; then
  echo "База уже есть: миграции не накатываются при старте (только через update.sh)"
  "$here/erp.sh" up -d --build
else
  ERP_AUTO_MIGRATE=1 "$here/erp.sh" up -d --build
fi
"$here/erp.sh" ps
if grep -q '^ERP_DOMAIN=:80' "$ERP_HOME/config/.env"; then
  echo "Сайт в сети цеха: http://$(hostname -I | awk '{print $1}')"
  if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
    tailscale serve --bg 80 >/dev/null && echo "Снаружи (Tailscale): https://$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  fi
else
  echo "Сайт: https://$(grep ^ERP_DOMAIN= "$ERP_HOME/config/.env" | cut -d= -f2)"
fi
