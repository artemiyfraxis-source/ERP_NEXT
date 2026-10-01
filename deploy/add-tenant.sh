#!/usr/bin/env bash
# A second project on the shop-floor server as its own Linux user (owner 01.10.2026: «разделить серв… сделать ещё
# одного пользователя»). As root:
#   bash /opt/erp/app/deploy/add-tenant.sh <user> <public-key-file> [CPU% (default 50% of cores)] [memory (default 25%)]
# The user gets: a home and /opt/<user>; SSH by key only (password locked); NO sudo and NO docker group (the docker
# group is root in disguise); its own rootless Docker (own daemon, images, containers, volumes — cannot see ERP's);
# CPU and memory caps on its whole systemd slice, so nothing it runs can starve ERP; /opt/<user>/backups on the RAID.
# ERP stays closed to it: /opt/erp is 750 root. Ports below 1024 stay with ERP's Caddy — the project uses e.g. 8081.
# Idempotent: rerun to add another key or change the caps.
set -euo pipefail
user=${1:?usage: add-tenant.sh <user> <public-key-file> [CPU%] [memory]}
key=${2:?нужен файл с публичным SSH-ключом}
ERP_HOME=${ERP_HOME:-/opt/erp}
cores=$(nproc)
cpu=${3:-$((cores * 50))%}
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
mem=${4:-$((mem_kb / 4 / 1024))M}
say() { echo "== $*"; }

# 1. User, key-only login, no admin rights.
if ! id "$user" >/dev/null 2>&1; then say "создаю пользователя $user"; useradd -m -s /bin/bash "$user"; fi
for group in sudo docker adm lxd; do gpasswd -d "$user" "$group" >/dev/null 2>&1 || true; done
rm -f "/etc/sudoers.d/$user"
passwd -l "$user" >/dev/null
home=$(getent passwd "$user" | cut -d: -f6)
install -d -m 700 -o "$user" -g "$user" "$home/.ssh"
touch "$home/.ssh/authorized_keys"
while read -r line; do
  [ -n "$line" ] && ! grep -qxF "$line" "$home/.ssh/authorized_keys" && echo "$line" >> "$home/.ssh/authorized_keys"
done < "$key"
chown "$user:$user" "$home/.ssh/authorized_keys"; chmod 600 "$home/.ssh/authorized_keys"
uid=$(id -u "$user")

# 2. ERP closed, the project's own folder open.
chown root:root "$ERP_HOME"; chmod 750 "$ERP_HOME"
install -d -m 750 -o "$user" -g "$user" "/opt/$user"

# 3. Backups on the RAID (mounted at $ERP_HOME/backups by prepare-host.sh): a subfolder bound into /opt/<user>/backups.
if findmnt -n "$ERP_HOME/backups" >/dev/null; then
  install -d -m 750 -o "$user" -g "$user" "$ERP_HOME/backups/_tenants/$user" "/opt/$user/backups"
  line="$ERP_HOME/backups/_tenants/$user /opt/$user/backups none bind,nofail,x-systemd.requires-mounts-for=$ERP_HOME/backups 0 0"
  grep -qF "/opt/$user/backups " /etc/fstab || echo "$line" >> /etc/fstab
  findmnt -n "/opt/$user/backups" >/dev/null || { systemctl daemon-reload; mount "/opt/$user/backups"; }
  say "копии проекта: /opt/$user/backups (RAID)"
else
  install -d -m 750 -o "$user" -g "$user" "/opt/$user/backups"
  say "!! RAID не смонтирован — копии проекта пока на SSD (/opt/$user/backups)"
fi

# 4. Caps for everything the user runs (its rootless dockerd lives in user-<uid>.slice).
mkdir -p "/etc/systemd/system/user-$uid.slice.d"
cat > "/etc/systemd/system/user-$uid.slice.d/50-limits.conf" <<EOF
[Slice]
CPUQuota=$cpu
MemoryMax=$mem
TasksMax=4096
EOF
systemctl daemon-reload
say "лимиты $user: CPU $cpu (ядер $cores), память $mem"

# 5. Rootless Docker for the user.
apt-get install -y uidmap dbus-user-session slirp4netns >/dev/null
command -v rootlesskit >/dev/null || apt-get install -y docker-ce-rootless-extras >/dev/null 2>&1 \
  || apt-get install -y rootlesskit >/dev/null
setup=$(command -v dockerd-rootless-setuptool.sh || ls /usr/share/docker.io/contrib/dockerd-rootless-setuptool.sh 2>/dev/null || true)
[ -n "$setup" ] || { echo "!! нет dockerd-rootless-setuptool.sh — rootless Docker не поставлен" >&2; exit 1; }
grep -q "^$user:" /etc/subuid || usermod --add-subuids 200000-265535 --add-subgids 200000-265535 "$user"
# Ubuntu 24.04+ forbids unprivileged user namespaces unless AppArmor allows the binary.
rk=$(readlink -f "$(command -v rootlesskit)")
profile=/etc/apparmor.d/$(echo "${rk#/}" | tr / .)
if [ -d /etc/apparmor.d ] && [ ! -f "$profile" ]; then
  cat > "$profile" <<EOF
abi <abi/4.0>,
include <tunables/global>
"$rk" flags=(unconfined) {
  userns,
  include if exists <local/$(basename "$profile")>
}
EOF
  systemctl reload apparmor 2>/dev/null || apparmor_parser -r "$profile" || true
fi
loginctl enable-linger "$user"
for _ in $(seq 20); do [ -d "/run/user/$uid" ] && break; sleep 1; done
sudo -iu "$user" env XDG_RUNTIME_DIR="/run/user/$uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
  "$setup" install --skip-iptables
grep -q DOCKER_HOST "$home/.bashrc" || echo "export DOCKER_HOST=unix:///run/user/$uid/docker.sock" >> "$home/.bashrc"
sudo -iu "$user" env XDG_RUNTIME_DIR="/run/user/$uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
  systemctl --user enable docker >/dev/null 2>&1 || true
sudo -iu "$user" env DOCKER_HOST="unix:///run/user/$uid/docker.sock" docker info --format 'rootless docker: {{.ServerVersion}}, {{.SecurityOptions}}'
say "готово: ssh $user@$(hostname -I | awk '{print $1}'), папка /opt/$user, ERP ему не видна"
