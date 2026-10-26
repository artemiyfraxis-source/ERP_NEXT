"""Запись в прод — «Частота до, ГГц» коаксиальных кабелей (владелец 25.10.2026: одно поле частоты, как у разъёмов; по
нему и полоса нормы КСВН, `coax_vswr.cable_max_ghz`). Запускать ПОСЛЕ выпуска ветки ref-columns-drop-1025: до неё у
списка «Коаксиальные кабели» нет столбца «Частота до, ГГц», и загрузка отказывает.

Что делает: каждая строка БД.ПРОВ списка «Коаксиальные кабели» с ПУСТОЙ «Частота до, ГГц» получает первое, что есть:
  1. рабочая частота её карточки «Коаксиальные кабели» (`max_operating_ghz` — номинал по datasheet/стандарту);
  2. иначе верх «Диапазона частот» карточки — конец диапазона VNA (`span_to_ghz`, с его основанием `span_note`);
  3. иначе ничего (строка в отчёте «не заполнено»).
Карточка — только СВОЯ (по марке или её другому названию), карточка семейства («как у RG-58») не берётся: это догадка.
Частота среза (f_c, TE11) не берётся никогда. Источник каждого значения дописывается в «Источник данных» строки
(«25.10.2026: Частота до 3 ГГц — рабочая частота карточки RG-316 (3 (MIL M17/113))»).

Путь — import_rows("prov") по натуральному ключу «Марка» (у коакса цвета нет), старые значения обеих ячеек сверяются
(old: «Частота до» = "", «Источник данных» — как прочитано): поменяли после чтения — отказ строки, ничего не затёрто.

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → «заполнить: 0»
"""
import os
import sys
from decimal import Decimal

from app.database import build_engine, build_references_uow_factory
from app.modules.references.cable_structure import card_for
from app.modules.references.domain import CABLE_MAX_GHZ_FIELD, TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
SOURCE = "Источник данных"
DAY = "25.10.2026"


def shown(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def pick(card) -> tuple[str, str] | None:
    """(value, why) — the rated operating frequency first, else the top of the VNA span; never the cut-off."""
    if card.max_operating_ghz is not None and card.max_operating_ghz > 0:
        note = f" ({card.max_operating_note})" if card.max_operating_note else ""
        return shown(card.max_operating_ghz), f"рабочая частота карточки {card.name}{note}"
    if card.span_to_ghz is not None and card.span_to_ghz > 0:
        note = f"; {card.span_note}" if card.span_note else ""
        return shown(card.span_to_ghz), f"верх «Диапазона частот» карточки {card.name} (диапазон VNA{note})"
    return None


mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()

with factory() as uow:
    records = [record for record in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000)
               if record.cable_kind == "coax"]
    cards = {card.name_key: card for card in uow.cable_structure.cards()}

rows, filled, skipped = [], 0, []
for record in records:
    mark = str(record.fields.get("Марка") or record.display_text).strip()
    if str(record.fields.get(CABLE_MAX_GHZ_FIELD) or "").strip():
        filled += 1
        continue
    card, exact = card_for(cable_key(mark), cards)
    found = pick(card) if card is not None and exact else None
    if found is None:
        skipped.append(f"{mark}: {'нет своей карточки' if not exact else 'у карточки нет рабочей частоты и диапазона'}")
        continue
    value, why = found
    old_source = str(record.fields.get(SOURCE) or "")
    note = f"{DAY}: Частота до {value} ГГц — {why}"
    rows.append({"fields": {"Марка": mark, CABLE_MAX_GHZ_FIELD: value, SOURCE: f"{old_source}; {note}" if old_source.strip() else note},
                 "old": {CABLE_MAX_GHZ_FIELD: "", SOURCE: old_source}})
    print(f"   {mark}: {value} ГГц — {why}")

print(f"== коаксиальных строк {len(records)}: уже заполнено {filled}, заполнить {len(rows)}, без источника {len(skipped)}")
for line in skipped:
    print("   не заполнено:", line)


def call(dry_run: bool):
    return import_rows("prov", factory(), [ImportRowIn(fields=r["fields"], old=r["old"]) for r in rows], ACTOR, now,
                       dry_run=dry_run, source="erp").model_dump()


if rows:
    data = call(True)
    if data["created"]:  # a fill must find its row
        print(f"СТОП — создано бы {len(data['created'])} новых строк; ничего не записано")
        raise SystemExit(1)
    if not dry:
        data = call(False)
    print(f"== «Частота до»: обновлено {len(data['updated'])}, без изменений {data['unchanged']}, отказ {len(data['refused'])}")
    for item in data["refused"][:30]:
        print("   отказ:", item)
print("ИТОГО", "(проверка, ничего не записано)" if dry else "(записано)")
