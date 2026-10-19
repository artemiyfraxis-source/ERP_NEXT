"""Запись в прод 19.10.2026 — склад: артикул двух партий «2.92mm male 086» / «3.5mm male 086» → артикулы БД.КОАКС
2.92-JB2G-F / 3.5-JB2AG (владелец 19.10: «да, ты прав» — это те же изделия).

Только штатной операцией склада `edit_lot_identity` (версия партии, событие `warehouse.identity_edited` в журнал):
меняется ТОЛЬКО артикул; наименование и серия передаются как были; количества, движения, резерв не трогаются.
Запуск в одноразовом контейнере api: `python - dry` (проверка) или `python - apply`.
Откат: тот же скрипт с `rollback` возвращает прежние артикулы (сверка по текущему артикулу).
"""
import os
import sys
from app.database import build_engine, build_warehouse_uow_factory
from app.modules.warehouse.application import EditLotIdentityCommand, edit_lot_identity
from app.shared.clock import moscow_now
from app.shared.identity import Actor
from uuid import UUID
from sqlalchemy import text

MODE = sys.argv[1] if len(sys.argv) > 1 else "dry"
CHANGES = [("OLD.26.0000157", "2.92mm male 086", "2.92-JB2G-F"), ("OLD.26.0000158", "3.5mm male 086", "3.5-JB2AG")]
ACTOR = Actor(id=UUID(int=0), name="Исправление артикулов склада 19.10.2026", department="System")

engine = build_engine(os.environ["ERP_DATABASE_URL"])
factory = build_warehouse_uow_factory(engine)
with engine.connect() as c:
    rows = c.execute(text("select id, warehouse, grn, package, article, name, series, version, inbound, written_off, reserved "
                          "from warehouse_lots where grn in :g").bindparams(__import__('sqlalchemy').bindparam("g", expanding=True)),
                     {"g": [g for g, _, _ in CHANGES]}).mappings().all()
by_grn: dict[str, list] = {}
for r in rows:
    by_grn.setdefault(r["grn"], []).append(r)
for grn, old, new in CHANGES:
    if MODE == "rollback":
        old, new = new, old
    lots = by_grn.get(grn, [])
    if not lots:
        print(f"{grn}: партия не найдена — пропуск")
        continue
    for lot in lots:
        if (lot["article"] or "").strip() == new:
            print(f"{grn}/{lot['package']}: уже «{new}» — без изменений")
            continue
        if (lot["article"] or "").strip() != old:
            print(f"{grn}/{lot['package']}: СТОП — артикул «{lot['article']}», ожидался «{old}»")
            continue
        print(f"{grn}/{lot['package']} {lot['warehouse']}: «{old}» → «{new}» (наименование «{lot['name']}», серия «{lot['series']}», "
              f"приход {lot['inbound']}, списано {lot['written_off']}, резерв {lot['reserved']}, версия {lot['version']})")
        if MODE in ("apply", "rollback"):
            updated = edit_lot_identity(EditLotIdentityCommand(lot_id=lot["id"], expected_version=lot["version"], article=new,
                                                               name=lot["name"] or "", series=lot["series"] or "", actor=ACTOR,
                                                               now=moscow_now()), factory())
            print(f"   записано: артикул «{updated.article}», версия {updated.version}")
print("режим:", MODE)
