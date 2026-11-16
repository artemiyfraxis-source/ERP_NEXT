"""Запись в прод 16.11.2026 — БД «Наконечники»: сечения мм² (52 ячейки, 26 строк) по каталогам производителей.
Владелец 16.11: «сечения все есть в артикулах» — заполнять. Путь — правка карточки (edit_record, как в «Базе данных»),
ключ — артикул REEL или BAG; со старым значением: изменённое после выгрузки — отказ. Режимы: dry | apply."""
import json, os, sys
from uuid import UUID
from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import EditRecordCommand, FieldEdit, edit_record
from app.modules.references.domain import TechOperationTab
from app.shared.clock import moscow_now
from app.shared.identity import Actor
sys.stdout.reconfigure(encoding="utf-8")
EDITS = json.loads('{"НШВ 16-12": [["mm2From", "", "16"], ["mm2To", "", "16"]], "НШВ 25-16": [["mm2From", "", "25"], ["mm2To", "", "25"]], "НШВИ 0.25-8": [["mm2From", "0,2", "0,25"], ["mm2To", "0,2", "0,25"]], "НШВИ 1.0-8": [["mm2From", "0,75", "1"], ["mm2To", "0,75", "1"]], "НШВИ 1.0-12": [["mm2From", "0,75", "1"], ["mm2To", "0,75", "1"]], "ПМ 16-6": [["mm2From", "", "16"], ["mm2To", "", "16"]], "ПМ 16-8": [["mm2From", "", "16"], ["mm2To", "", "16"]], "ПМ 16-10": [["mm2From", "", "16"], ["mm2To", "", "16"]], "ПМ 25-8": [["mm2From", "", "25"], ["mm2To", "", "25"]], "ПМ 25-10": [["mm2From", "", "25"], ["mm2To", "", "25"]], "ПМ 35-8": [["mm2From", "", "35"], ["mm2To", "", "35"]], "ПМ 35-10": [["mm2From", "", "35"], ["mm2To", "", "35"]], "ПМ 50-8": [["mm2From", "", "50"], ["mm2To", "", "50"]], "ПМ 50-10": [["mm2From", "", "50"], ["mm2To", "", "50"]], "ПМ 70-10": [["mm2From", "", "70"], ["mm2To", "", "70"]], "ПМ 95-10": [["mm2From", "", "95"], ["mm2To", "", "95"]], "ПМ 120-10": [["mm2From", "", "120"], ["mm2To", "", "120"]], "ПМ 150-12": [["mm2From", "", "150"], ["mm2To", "", "150"]], "ПМ 185-12": [["mm2From", "", "185"], ["mm2To", "", "185"]], "РПИ-М 2.5 (4.8)": [["mm2From", "2,5", "1,5"], ["mm2To", "4", "2,5"]], "РПИ-М 2.5 (6.3)": [["mm2From", "2,5", "1,5"], ["mm2To", "4", "2,5"]], "РПИ-М(н) 2,5-(6,3)": [["mm2From", "2,5", "1,5"], ["mm2To", "4", "2,5"]], "FLVDDF1.25-187A-5": [["mm2From", "0,35", "0,25"], ["mm2To", "1,5", "1,65"]], "FLVDDF1.25-187A-8": [["mm2From", "0,35", "0,25"], ["mm2To", "1,5", "1,65"]], "FLVDDF1.25-250A": [["mm2From", "0,35", "0,25"], ["mm2To", "1,5", "1,65"]], "60294-1": [["mm2From", "0,35", "0,32"], ["mm2To", "0,75", "0,82"]]}')
mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
assert mode in ("dry", "apply")
actor = Actor(id=UUID(int=0), name="Сечения наконечников по каталогам 16.11.2026", department="System")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
with factory() as uow:
    rows = list(uow.tech_operations.list(tab=TechOperationTab.LUG, query="", limit=100_000))
by_article = {}
for r in rows:
    for key in {str(r.fields.get("terArticle", "")).strip(), str(r.fields.get("Артикул контакта (BAG)", "")).strip()} - {""}:
        by_article.setdefault(key, []).append(r)
ok = stale = missing = done = 0
for article, cells in EDITS.items():
    found = by_article.get(article, [])
    if len(found) != 1:
        print("  нет/несколько строк:", article, len(found)); missing += 1; continue
    rec = found[0]
    cur = {f: str(rec.fields.get(f, "") or "") for f, _, _ in cells}
    if all(cur[f] == new for f, _, new in cells):
        done += 1; continue
    if any(cur[f] != old for f, old, _ in cells):
        print("  изменилось:", article, cur); stale += 1; continue
    ok += 1
    if mode == "apply":
        edit_record(EditRecordCommand(rec.id, tuple(FieldEdit(f, old, new) for f, old, new in cells), actor, moscow_now()), factory())
print("mode", mode, "к записи", ok, "уже записано", done, "изменилось", stale, "не найдено", missing)
