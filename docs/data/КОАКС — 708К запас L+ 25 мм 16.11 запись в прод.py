"""Запись в прод 16.11.2026 — запас кабеля на заход в разъём (L+) = 25 мм для остальных разъёмов 708К.

Владелец (технолог) 16.11.2026: «давай по стандарту 25 сделаем, на таких длинах норм будет». Длина резки = L по КД + L+ А + L+ В.
  AMK11D-1M4Z5-Z × RTK 031 (HFM, КБ3–10), 2FA1-NZSP-C30E2 × RTK 031 (Amphenol, КБ28–40),
  73403-6270 × LMR-100 и 901-9610-3 × LMR-100 (КБ11–14) — L+ 25 мм.
Только пустые ячейки (old = {"L+": ""}); строки должны уже быть (created в dry-прогоне = 0, иначе СТОП).
Порядок: dry → backup-now → apply → dry (все refused). Откат: очистить «L+» у четырёх строк (журнал CSV рядом).
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
    ("AMK11D-1M4Z5-Z", "RTK 031", "25"),
    ("2FA1-NZSP-C30E2", "RTK 031", "25"),
    ("73403-6270", "LMR-100", "25"),
    ("901-9610-3", "LMR-100", "25"),
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
