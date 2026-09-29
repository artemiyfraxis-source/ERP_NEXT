#!/usr/bin/env bash
# Real data into the production database — the server twin of scripts/load-real-data.ps1. Google is only read.
#   deploy/load-real-data.sh base    # admin, staff (+PINs), references mirror, techcard library, projects
#   deploy/load-real-data.sh stock [--yes]  # ЦАР/ЖУК/СПИСАНИЕ → lots; without --yes only the report. Rerun = top-up:
#       new lots are created, lots ERP never moved follow Google («догрузка из Google»), moved lots are reported
#   deploy/load-real-data.sh kd        # КД проектов с Google Диска в базу (Диск только читается)
#   deploy/load-real-data.sh projects  # re-read projects (idempotent: nothing is duplicated)
#   deploy/load-real-data.sh techcards # ТК: выданные в SUPPLYMAP → ERP, остальным сборкам — черновики генератора
#       (отгруженные и ХОЛД не трогаются; повтор собирает только то, чего ещё нет)
# Secrets the owner reads afterwards: $ERP_HOME/config/admin.txt, $ERP_HOME/config/staff-pins.txt (chmod 600).
set -euo pipefail
export ERP_HOME=${ERP_HOME:-/opt/erp}
cd "$(dirname "$0")"
key=/run/erp-secrets/google-service-account.json
[ -f "$ERP_HOME/config/secrets/google-service-account.json" ] || { echo "нет ключа Google в $ERP_HOME/config/secrets" >&2; exit 2; }
admin_file="$ERP_HOME/config/admin.txt"
if [ ! -f "$admin_file" ]; then
  printf 'login=admin\npassword=%s\n' "$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)" > "$admin_file"
  chmod 600 "$admin_file"
fi
login=$(sed -n 's/^login=//p' "$admin_file"); password=$(sed -n 's/^password=//p' "$admin_file")
cli() { ./erp.sh cli "$@"; }
ok_or_report() { local code=$?; [ $code -le 1 ] || exit $code; }   # exit 1 = «есть расхождения, см. отчёт»

case "${1:-}" in
  base)
    if ! ./erp.sh run --rm -e ERP_NEW_USER_PASSWORD="$password" api python -m app.identity_admin create-user \
        --login "$login" --email "$login@erp.local" --name 'Администратор ERP' --department 'Руководство' --role admin; then
      echo "(admin уже есть — пропускаю)"
    fi
    cli python -m app.modules.identity.import_staff --apply --source /opt/supplymap | tee "$ERP_HOME/config/staff-pins.txt"
    chmod 600 "$ERP_HOME/config/staff-pins.txt"
    cli python -m app.modules.references.load_mirror --source /opt/supplymap
    cli python -m app.modules.techcards.seed_library
    "$0" projects  # in-process API call: the login cookie stays inside the container, so Secure is off there only
    "$0" kd
    "$0" techcards
    ;;
  projects)
    cli sh -c "python ../scripts/projects_import.py snapshot --key $key --out /app/data/import/projects.json && \
               ERP_COOKIE_SECURE=0 python ../scripts/projects_import.py apply /app/data/import/projects.json --login '$login' --password '$password'" || ok_or_report
    ;;
  kd)  # старые КД с Google Диска → в базу (Диск только читается); повтор докачивает недостающее
    cli python -m app.modules.techcards.import_kd_from_drive --apply || ok_or_report
    ;;
  techcards)
    cli python -m app.workflows.techcards_all supplymap || ok_or_report
    cli python -m app.workflows.techcards_all generate || ok_or_report
    ;;
  stock)
    if [ "${2:-}" != "--yes" ]; then
      cli sh -c "python ../scripts/stock_export_google.py --key $key --out /app/data/import && \
                 python -m app.modules.warehouse.import_stock --dir /app/data/import --dry-run" || ok_or_report
      echo "Это была сверка без записи. Записать: $0 stock --yes"
    else
      cli sh -c "python ../scripts/stock_export_google.py --key $key --out /app/data/import && \
                 python -m app.modules.warehouse.import_stock --dir /app/data/import" || ok_or_report
    fi
    ;;
  *) echo "usage: $0 base|projects|kd|techcards|stock [--yes]" >&2; exit 2 ;;
esac
