#!/usr/bin/env bash
# Out-of-schedule update requested from anywhere, not only from the owner's PC (owner 01.10.2026: «сделай так, чтобы не
# только от этого ПК»). A systemd timer runs this every 2 minutes; it only reads GitHub. A request is a new commit on the
# branch `update-request`, pushed by the GitHub Actions button «Обновить сервер» (.github/workflows/update-server.yml —
# GitHub web or phone app) or by any Claude thread: `gh workflow run update-server.yml`. A new request = `erp-update now`.
set -uo pipefail
export ERP_HOME=${ERP_HOME:-/opt/erp}
REPO=${ERP_GIT_REPO:-git@github.com:artemiyfraxis-source/ERP_NEXT.git}
key=$ERP_HOME/config/github-deploy-key
seen=$ERP_HOME/status/update-request.seen
[ -f "$key" ] || exit 0
export GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
req=$(git ls-remote "$REPO" refs/heads/update-request 2>/dev/null | cut -f1)
[ -n "$req" ] || exit 0
# First run only remembers the current request, so installing this never triggers an update by itself.
if [ ! -f "$seen" ]; then echo "$req" > "$seen"; exit 0; fi
[ "$(cat "$seen")" = "$req" ] && exit 0
echo "$req" > "$seen"
echo "запрос обновления $req"
# Approval of a destructive migration rides in the request commit message («approve=<rev>»). Read it only from the
# update-request branch commit itself; a request without it clears any earlier approval.
approved=""
tmp=$(mktemp -d)
if git init -q --bare "$tmp" 2>/dev/null && git -C "$tmp" fetch -q --depth=1 "$REPO" refs/heads/update-request 2>/dev/null; then
  approved=$(git -C "$tmp" log -1 --format=%B FETCH_HEAD 2>/dev/null | grep -Eo 'approve=[0-9a-z_]+' | head -n1 | cut -d= -f2)
fi
rm -rf "$tmp"
mkdir -p "$ERP_HOME/status"
printf '%s
' "$approved" > "$ERP_HOME/status/migration-approved"
[ -n "$approved" ] && echo "разрешена миграция $approved"
touch "$ERP_HOME/update-now"
systemctl start --no-block erp-auto-update.service
