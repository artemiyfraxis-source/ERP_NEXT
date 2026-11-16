"""Запись в прод 16.11.2026 — «Наконечники»: колонки «От мм2» / «До мм2» = mm2From / mm2To там, где они разошлись после
правки сечений 16.11 (правка карточки по ключу не обновила колонку, экран читает колонку). Режимы: dry | apply."""
import os, sys
from uuid import UUID
from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import EditRecordCommand, FieldEdit, edit_record
from app.modules.references.domain import TechOperationTab
from app.shared.clock import moscow_now
from app.shared.identity import Actor
sys.stdout.reconfigure(encoding="utf-8")
mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
actor = Actor(id=UUID(int=0), name="Сечения наконечников: колонка = ключ 16.11.2026", department="System")
f = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
with f() as uow:
    rows = list(uow.tech_operations.list(tab=TechOperationTab.LUG, query="", limit=100_000))
n = 0
for r in rows:
    g = lambda k: str(r.fields.get(k) or "")
    edits = tuple(FieldEdit(label, g(label), g(key)) for label, key in (("От мм2", "mm2From"), ("До мм2", "mm2To")) if g(label) != g(key))
    if not edits:
        continue
    n += 1
    print(" ", g("terArticle") or g("Артикул контакта (BAG)"), [(e.field, e.old, e.value) for e in edits])
    if mode == "apply":
        edit_record(EditRecordCommand(r.id, edits, actor, moscow_now()), f())
print("mode", mode, "rows", n)
