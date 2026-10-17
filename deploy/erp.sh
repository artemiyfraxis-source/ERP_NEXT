#!/usr/bin/env bash
# Wrapper for every production command:  deploy/erp.sh <compose args>   e.g.
#   deploy/erp.sh ps | logs -f api | cli python -m app.modules.warehouse.import_stock --dry-run
# `cli ...` runs a one-off command in a fresh api container (migrations, imports, identity_admin).
#   deploy/erp.sh cli python -m app.workflows.purge_project_tails 719К.2 [--yes]   # tails of deleted projects
#   (dry run without --yes)
set -euo pipefail
export ERP_HOME=${ERP_HOME:-/opt/erp}
cd "$(dirname "$0")"
compose=(docker compose --env-file "$ERP_HOME/config/.env" -f docker-compose.yml)
case "${1:-}" in
  cli) shift; exec "${compose[@]}" run --rm api "$@" ;;
  backup-now) exec "${compose[@]}" exec backup bash /backup-loop.sh --now ;;
  *) exec "${compose[@]}" "$@" ;;
esac
