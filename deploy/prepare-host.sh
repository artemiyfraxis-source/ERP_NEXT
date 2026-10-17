#!/usr/bin/env bash
# One-time host preparation of the shop-floor server, as root, before bootstrap.sh. Idempotent; push.ps1 runs it on
# the first install (and with -Prepare). Each step only acts when needed and prints what it did.
#   bash /opt/erp/app/deploy/prepare-host.sh [<static IPv4/prefix>]   # default: the address the machine has now
# 1. removes the microk8s snap (the installer's "featured snaps" screen offers it; it would fight Docker for ports);
# 2. pins the current DHCP address as static (netplan), with an automatic rollback if the network does not come back;
# 3. grows the root LV to the whole volume group (the installer gives / only 100 GB of the SSD);
# 4. mounts the empty Intel RST RAID volume at /opt/erp/backups for the nightly dumps. A volume that already has
#    data is NOT formatted — it is mounted read-only at /mnt/old-raid and listed, the decision stays with a human;
# 5. announces the host name on the shop LAN (mDNS + LLMNR): http://erp-server and http://erp-server.local.
set -uo pipefail
ERP_HOME=${ERP_HOME:-/opt/erp}
say() { echo "== $*"; }

# 1. microk8s
if command -v snap >/dev/null && snap list microk8s >/dev/null 2>&1; then
  say "удаляю microk8s"; snap remove --purge microk8s
fi

# 2. static IP
iface=$(ip -4 route show default | awk '{print $5; exit}')
gw=$(ip -4 route show default | awk '{print $3; exit}')
addr=${1:-$(ip -4 -o addr show dev "$iface" scope global | awk '{print $4; exit}')}
static=/etc/netplan/60-erp-static.yaml
if [ -n "$iface" ] && [ -n "$gw" ] && [ -n "$addr" ] && [ ! -f "$static" ]; then
  dns=$(resolvectl dns "$iface" 2>/dev/null | awk '{for (i = 4; i <= NF; i++) printf "%s, ", $i}')
  say "закрепляю $addr на $iface (шлюз $gw)"
  cat > "$static" <<YAML
# ERP server: fixed LAN address (deploy/prepare-host.sh). Overrides the installer's DHCP file for this interface.
network:
  version: 2
  ethernets:
    $iface:
      dhcp4: false
      addresses: [$addr]
      routes: [{to: default, via: $gw}]
      nameservers: {addresses: [${dns}1.1.1.1]}
YAML
  chmod 600 "$static"
  # Rollback in 2 minutes unless the gateway answers after the apply (survives this ssh session dying).
  systemd-run --quiet --unit=erp-netplan-rollback --on-active=120 \
    /bin/sh -c "mv $static /root/60-erp-static.yaml.failed && netplan apply"
  netplan apply
  sleep 5
  if ping -c 3 -W 2 "$gw" >/dev/null; then
    systemctl stop erp-netplan-rollback.timer 2>/dev/null; say "IP закреплён: $addr"
  else
    say "!! шлюз не отвечает — через 2 минуты вернётся DHCP"
  fi
fi

# 3. root LV
root_dev=$(findmnt -no SOURCE /)
if lvs "$root_dev" >/dev/null 2>&1; then
  vg=$(lvs --noheadings -o vg_name "$root_dev" | tr -d ' ')
  # LC_ALL=C: a Russian locale prints «828,46», and the integer test silently skipped the extension (01.10.2026)
  free=$(LC_ALL=C vgs --noheadings --units g --nosuffix -o vg_free "$vg" | tr -d ' ')
  if [ "${free%.*}" -ge 1 ]; then say "расширяю / на ${free} ГБ"; lvextend -r -l +100%FREE "$root_dev"; fi
fi
df -h / | tail -1

# 4. RAID for backups
command -v mdadm >/dev/null || apt-get install -y mdadm
ls /dev/md[0-9]* >/dev/null 2>&1 || mdadm --assemble --scan || true
raid=""
for md in /dev/md[0-9]*; do
  [ -b "$md" ] || continue
  level=$(mdadm --detail "$md" 2>/dev/null | awk -F': ' '/Raid Level/ {print $2}')
  case "$level" in raid1|raid5|raid6|raid10) raid=$md ;; esac   # an imsm "container" has no level
done
mkdir -p "$ERP_HOME/backups"
if [ -z "$raid" ]; then
  say "!! RAID-массив не найден — копии пока на SSD ($ERP_HOME/backups)"
elif findmnt -n "$ERP_HOME/backups" >/dev/null; then
  say "копии уже на $(findmnt -no SOURCE "$ERP_HOME/backups")"
else
  fstype=$(blkid -p -o value -s TYPE "$raid" 2>/dev/null)
  parts=$(lsblk -nro NAME "$raid" | tail -n +2 | wc -l)
  label=$(blkid -o value -s LABEL "$raid" 2>/dev/null)
  if [ -z "$fstype" ] && [ "$parts" -eq 0 ]; then
    say "размечаю пустой $raid под копии"; mkfs.ext4 -q -L erp-backups -m 0 "$raid"; label=erp-backups
  fi
  if [ "$label" = erp-backups ]; then
    uuid=$(blkid -o value -s UUID "$raid")
    grep -q "$uuid" /etc/fstab || \
      echo "UUID=$uuid $ERP_HOME/backups ext4 defaults,nofail,x-systemd.device-timeout=30 0 2" >> /etc/fstab
    if [ -n "$(ls -A "$ERP_HOME/backups")" ]; then
      say "!! $ERP_HOME/backups не пуст — монтирование отложено, перенесите файлы вручную"
    else
      systemctl daemon-reload; mount "$ERP_HOME/backups" && say "копии на $raid ($(df -h "$ERP_HOME/backups" | awk 'NR==2 {print $2}'))"
    fi
    mdadm --detail --scan | while read -r line; do
      grep -qF "$line" /etc/mdadm/mdadm.conf || echo "$line" >> /etc/mdadm/mdadm.conf
    done
    update-initramfs -u >/dev/null 2>&1 || true
    systemctl enable --now mdmonitor 2>/dev/null || true
  else
    mkdir -p /mnt/old-raid
    say "!! на $raid уже есть данные (${fstype:-разделы: $parts}) — не трогаю, только смотрю"
    lsblk -f "$raid"
    mount -o ro "$raid" /mnt/old-raid 2>/dev/null || mount -o ro "${raid}p1" /mnt/old-raid 2>/dev/null || true
    findmnt -n /mnt/old-raid >/dev/null && ls -la /mnt/old-raid | head -30 && umount /mnt/old-raid
  fi
fi
grep -E '^md|blocks|resync|recovery' /proc/mdstat || true

# 5. The name on the shop LAN: http://erp-server and http://erp-server.local (mDNS + LLMNR), only for the local subnet.
mkdir -p /etc/systemd/resolved.conf.d
printf '[Resolve]\nMulticastDNS=yes\nLLMNR=yes\n' > /etc/systemd/resolved.conf.d/erp-lan-name.conf
if [ -n "$iface" ]; then
  for net in /run/systemd/network/*"$iface".network; do
    [ -f "$net" ] || continue
    mkdir -p "/etc/systemd/network/$(basename "$net").d"
    printf '[Network]\nMulticastDNS=yes\nLLMNR=yes\n' > "/etc/systemd/network/$(basename "$net").d/erp-lan-name.conf"
  done
  networkctl reload 2>/dev/null || true
fi
systemctl restart systemd-resolved
if command -v ufw >/dev/null && [ -n "$addr" ]; then
  subnet=$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "$addr")
  ufw allow from "$subnet" to any port 5353,5355 proto udp >/dev/null
fi
say "имя в сети цеха: http://$(hostname) и http://$(hostname).local"
exit 0
