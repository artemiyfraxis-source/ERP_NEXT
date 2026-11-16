"""Запись в прод 16.11.2026 — БД «Наконечники»: очистка «Высота обжима изоляции, мм» у 4 артикулов (владелец 16.11:
«очисти высоту обжима у этих четырёх») — значения не подтверждены документами производителя. Режимы: dry | apply."""
import os, sys
from uuid import UUID
from app.database import build_engine, build_references_uow_factory
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor
sys.stdout.reconfigure(encoding="utf-8")
OLD = {
    "19003-0129": "22 AWG: 1,65; 20 AWG: 2,50; 18 AWG: 3,35",
    "19003-0135": "16 AWG: 4,85; 14 AWG: 4,85",
    "60294-1": "22 AWG: 2,60; 20 AWG: 3,20; 18 AWG: 3,80",
    "45 7373 8528-01": "20 AWG: 2,20; 18 AWG: 2,45; 16 AWG: 2,85",
}
mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
assert mode in ("dry", "apply")
actor = Actor(id=UUID(int=0), name="Очистка неподтверждённой высоты обжима изоляции 16.11.2026", department="System")
rows = [ImportRowIn(fields={"terArticle": a, "terInsCrimpHeight": ""}, old={"terInsCrimpHeight": v}) for a, v in OLD.items()]
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
rep = import_rows("lug", factory(), rows, actor, moscow_now(), dry_run=(mode != "apply"))
print("mode", mode, "created", len(rep.created), "updated", len(rep.updated), "unchanged", rep.unchanged, "refused", len(rep.refused))
for r in rep.refused: print("  refused", r)
for u in rep.updated: print("  upd", u)
