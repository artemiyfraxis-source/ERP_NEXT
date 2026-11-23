"""Запись в прод 23.11.2026 — БД.КОАКС «КСВ» разъёмов 800К. КБ1 (N-111/5D ×6 строк, RP-SMA-male 5D-FB) по каталогу
Радиолаб (владелец 23.11: «вот от чего отталкиваться можно»). Было «<2» продавца kroks.ru. edit_record по id, старое
значение сверяется; «Источник данных» дополняется заметкой. Режимы: dry | apply."""
import json, os, sys
from uuid import UUID
from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import EditRecordCommand, FieldEdit, edit_record
from app.modules.references.domain import TechOperationTab
from app.shared.clock import moscow_now
from app.shared.identity import Actor
sys.stdout.reconfigure(encoding="utf-8")
CELLS = json.loads('[{"id": "c0efe4e6-a001-46dc-af48-ed6853a1f433", "article": "N-111/5D", "old": "<2 (до 6 ГГц)", "new": "≤1,3 (прямой; ориентир — Радиолаб, серия N, 0–11 ГГц)", "note": "; 23.11.2026 (владелец): КСВ — по каталогу Радиолаб (radiolab.ru), «Электрические характеристики» серии N: «К.С.В.н: прямой 1.3 макс.; угловой 1.5 макс.», 50 Ом, 0–11 ГГц; данных изготовителя нет, прежнее значение — в журнале"}, {"id": "548580b7-65d6-4469-8195-9e71c229de64", "article": "N-111/5D", "old": "<2 (до 6 ГГц)", "new": "≤1,3 (прямой; ориентир — Радиолаб, серия N, 0–11 ГГц)", "note": "; 23.11.2026 (владелец): КСВ — по каталогу Радиолаб (radiolab.ru), «Электрические характеристики» серии N: «К.С.В.н: прямой 1.3 макс.; угловой 1.5 макс.», 50 Ом, 0–11 ГГц; данных изготовителя нет, прежнее значение — в журнале"}, {"id": "fe4be6e3-46ba-477f-b931-fbee308d973b", "article": "N-111/5D", "old": "<2 (до 6 ГГц)", "new": "≤1,3 (прямой; ориентир — Радиолаб, серия N, 0–11 ГГц)", "note": "; 23.11.2026 (владелец): КСВ — по каталогу Радиолаб (radiolab.ru), «Электрические характеристики» серии N: «К.С.В.н: прямой 1.3 макс.; угловой 1.5 макс.», 50 Ом, 0–11 ГГц; данных изготовителя нет, прежнее значение — в журнале"}, {"id": "ca4a1005-b141-4eec-b046-09a5643a0796", "article": "N-111/5D", "old": "<2 (до 6 ГГц)", "new": "≤1,3 (прямой; ориентир — Радиолаб, серия N, 0–11 ГГц)", "note": "; 23.11.2026 (владелец): КСВ — по каталогу Радиолаб (radiolab.ru), «Электрические характеристики» серии N: «К.С.В.н: прямой 1.3 макс.; угловой 1.5 макс.», 50 Ом, 0–11 ГГц; данных изготовителя нет, прежнее значение — в журнале"}, {"id": "0ac53963-0f34-4897-8b7c-841bd2c25a4c", "article": "N-111/5D", "old": "<2 (до 6 ГГц)", "new": "≤1,3 (прямой; ориентир — Радиолаб, серия N, 0–11 ГГц)", "note": "; 23.11.2026 (владелец): КСВ — по каталогу Радиолаб (radiolab.ru), «Электрические характеристики» серии N: «К.С.В.н: прямой 1.3 макс.; угловой 1.5 макс.», 50 Ом, 0–11 ГГц; данных изготовителя нет, прежнее значение — в журнале"}, {"id": "a124ae44-07de-4cc8-95cb-d4303925cfc8", "article": "N-111/5D", "old": "<2 (до 6 ГГц)", "new": "≤1,3 (прямой; ориентир — Радиолаб, серия N, 0–11 ГГц)", "note": "; 23.11.2026 (владелец): КСВ — по каталогу Радиолаб (radiolab.ru), «Электрические характеристики» серии N: «К.С.В.н: прямой 1.3 макс.; угловой 1.5 макс.», 50 Ом, 0–11 ГГц; данных изготовителя нет, прежнее значение — в журнале"}, {"id": "85509668-bebf-451b-9d51-28260e65bf73", "article": "RP-SMA-male 5D-FB", "old": "<2", "new": "≤1,27 до 6 ГГц (1,15 + 0,02·f; ориентир — Радиолаб, серия SMA)", "note": "; 23.11.2026 (владелец): КСВ — по каталогу Радиолаб (radiolab.ru), «Электрические характеристики» серии SMA: «КСВ: прямой 1.15 макс. + 0.02 f ГГц — с гибким кабелем», 0–18 ГГц; на 6 ГГц 1,27; данных изготовителя нет, прежнее значение — в журнале"}]')
mode = sys.argv[1] if len(sys.argv) > 1 else "dry"
assert mode in ("dry", "apply")
actor = Actor(id=UUID(int=0), name="КСВ разъёмов по каталогу Радиолаб 23.11.2026", department="System")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
with factory() as uow:
    recs = {str(r.id): r for r in uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)}
todo = done = conflict = missing = failed = 0
for c in CELLS:
    rec = recs.get(c["id"])
    if rec is None:
        missing += 1; print("  нет строки", c["id"]); continue
    f = rec.fields
    if str(f.get("Артикул") or "") != c["article"]:
        conflict += 1; print("  другой артикул", c["id"], f.get("Артикул")); continue
    vswr, src = str(f.get("КСВ") or ""), str(f.get("Источник данных") or "")
    if vswr == c["new"]:
        done += 1; continue
    if vswr != c["old"]:
        conflict += 1; print("  КСВ уже другой", c["id"], vswr); continue
    todo += 1
    if mode == "apply":
        try:
            edit_record(EditRecordCommand(rec.id, (FieldEdit("КСВ", vswr, c["new"]), FieldEdit("Источник данных", src, src + c["note"])),
                                          actor, moscow_now()), factory())
        except Exception as error:
            failed += 1; print("  ошибка", c["id"], error)
print("mode", mode, "к записи", todo, "уже записано", done, "конфликт", conflict, "нет строки", missing, "ошибок", failed)
