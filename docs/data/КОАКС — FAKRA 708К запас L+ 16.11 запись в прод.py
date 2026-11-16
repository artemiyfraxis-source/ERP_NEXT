"""Запись в прод 16.11.2026 — запас кабеля на заход в разъём (L+) для FAKRA проекта 708К. (чертёж 122664/AA).

Владелец 16.11.2026: «резка + запас в разъёмы», «запас тоже реши сам». Длина резки = L по КД + L+ стороны А + L+ стороны В
(генератор КОАКС, PR #854). Размера «торец корпуса → конец кабеля» в документах Rosenberger нет (MA_59V059, MA_59V065,
чертежи 59K13O-1M4A4 / 59K25H-1M4A4; MA_59V111 на корпус 59Z163-003-Z не найдена) — значения ОЦЕНОЧНЫЕ, решение
агента, уточнить по первому образцу (допуск L по чертежу 0…+10 мм):
  59K13O-1M4A4 × RTK 031 (в корпусе 59Z163-003-Z с уплотнителем 59K14B-1M4/50 и крышкой 59Z163-000/51) — L+ 30 мм;
  59K25H-1M4A4 × RTK 031 (в корпусе 59Z006-T00-Z)                                                      — L+ 25 мм.

Только пустые ячейки: old = {"L+": ""} — заполненная ячейка вернётся «refused», ничего не перезапишется.

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → обе ячейки «refused» (уже записаны)
Откат: очистить «L+» у двух строк (журнал CSV рядом).
"""
import os
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.importers import import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")

ROWS = [
    ("59K13O-1M4A4", "RTK 031", "30"),
    ("59K25H-1M4A4", "RTK 031", "25"),
]


def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
    if mode not in {"dry", "apply"}:
        raise SystemExit("режим: dry | apply")
    factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
    rows = [ImportRowIn(fields={"coaxArticle": art, "coaxWire": wire, "L+": value}, old={"L+": ""}) for art, wire, value in ROWS]
    report = import_rows("coax", factory(), rows, ACTOR, moscow_now(), dry_run=mode != "apply", source="erp").model_dump()
    print(mode, report)


main()
