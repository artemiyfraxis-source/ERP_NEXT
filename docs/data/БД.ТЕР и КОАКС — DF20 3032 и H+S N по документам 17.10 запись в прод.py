"""Запись в прод 17.10.2026 (поздний вечер) — по документам производителя («документация превыше», владелец 17.10).
Журнал и откат — «БД.ТЕР и КОАКС — DF20 3032 и H+S N по документам 17.10.md».

1. БД.ТЕР, Hirose DF20F-3032SCFA: «Высота обжима проводника» «30 AWG: 0,48–0,52» — это строка 30 AWG таблицы обжима
   другого контакта, DF20F-2830SCFA (ETAD-H1079-00). У 3032 (исполнения 04 / 06 / 44) Crimp Condition Table на hirose.com
   нет, его Crimp Quality Standard ETAD-H0929-00 высоты не даёт (ширина ствола у 3032 0,67–0,72, у 2830 0,67–0,74 — стволы
   разные). Ячейка очищается, причина — в «Источник высоты обжима».
2. БД.КОАКС, Huber+Suhner 25_N-50-3-9/133_NE и 25_N-50-2-14/133_NE стояли на RG-223. Лист данных H+S: 3-9 — Suitable
   cables SUCOFORM_141, MULTIFLEX_141 (H+S: «Handformable (RG402)»), 2-14 — SUCOFORM_86, SR_86_TP_M17 («(RG405)»).
   Ни склад, ни комплекты, ни техкарты на эти строки не ссылаются (поиск по всем таблицам прода 17.10). «Провод» → RG-402 /
   RG-405 (как у прочих разъёмов на эти кабели, напр. 11_SMA-50-2-65/119_NE), L1 — по инструкции H+S (жила 3 / 5,4 мм,
   диэлектрик и внешний проводник заподлицо), L2 и L3 по аналогу Amphenol для RG-223 убраны.

Путь — `_edit_in` со сверкой старого значения каждой ячейки (поменяли после снимка — отказ, не пишется). Строка — по ID
из снимка прода 17.10 (сверяется и артикул).
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл
  ... python - dry   < этот файл   → всё «уже так»
"""
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 17.10.2026", department="System")
REASON = ("17.10.2026: по документам производителя — Hirose DF20 (ETAD-H1079-00 / ETAD-H0929-00), Huber+Suhner "
          "(листы данных 25_N-50-3-9/133_N, 25_N-50-2-14/133_N)")

DF20_SRC_OLD = (
    "нет данных производителя: hirose.com API-документы для CL0686-0043-9-04 и -44 не содержат crimping_condition_table "
    "(только drawing_2d/spec_sheet/product_manual_guideline/harness_procedure); старый query-URL documenttype=CrimpCondition "
    "для DF20/AP105-DF20-2830S (TAD-P-0506, documentid=D144285_en) отдаёт 404 на всём сайте — документ снят с публикации "
    "(проверено 2026-09-29); 16.10.2026, обжим по AWG — уточнения из ячеек: 2,9–4,4 — диапазон на все сечения: 2,9 Н у "
    "32 AWG, 4,4 Н у 30 AWG | 17.10.2026: hirose.com: у DF20F-3032SCFA (исполнения 04, 06, 44) Crimp Condition Table нет — "
    "она есть только у DF20F-2830SCFA (ETAD-H1079-00, AP105-DF20-2830S); контакт 3032 снят с производства 29.11.2025. "
    "Значение 30 AWG в ячейке совпадает со строкой таблицы 2830 (другой контакт) — проверить."
)
DF20_SRC_NEW = (
    "нет данных производителя: у DF20F-3032SCFA (исполнения 04, 06, 44) Crimp Condition Table на hirose.com нет "
    "(документы CL0686-0043-9-xx: drawing_2d, spec_sheet, harness_procedure, crimping_quality_standard); контакт снят "
    "с производства 29.11.2025. 17.10.2026: высота «30 AWG: 0,48–0,52» удалена — это строка AB (UL1571 30 AWG 7/0.102) "
    "таблицы ETAD-H1079-00 другого контакта, DF20F-2830SCFA; Crimp Quality Standard ETAD-H0929-00 (общий для DF20 2830 / "
    "3032 / 36) высоты не даёт, ширина ствола по жиле у 3032 0,67–0,72, у 2830 0,67–0,74 — стволы разные; каталог DF20 "
    "(B268, прим. 4): аппликатор AP105-DF20-2830S годится и для 3032, но высоту под 3032 Hirose не публикует. "
    "https://www.hirose.com/api/v1/products/CL0686-0042-6-44/documents/crimping_condition_table/content?lang=EN ; "
    "https://www.hirose.com/api/v1/products/CL0686-0043-9-44/documents/crimping_quality_standard/content?lang=EN"
)
COAX_SRC_OLD = ("L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-5373 bulkhead jack (RG-55/142/223) — "
                "https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-female flange на RG-223")
HS = ("https://www.hubersuhner.com/en/shop/product/connectors-adapters/coaxial-connectors/{}/"
      "coaxial-connector-n-50-ohm-straight-panel-cable-jack-female-flange-mount")
ROWS = [
    {"id": "ad91fd36-1e51-44b2-8dee-163a2979e695", "article": "DF20F-3032SCFA", "key": "terArticle", "edits": {
        "Высота обжима проводника , мм": ("30 AWG: 0,48–0,52", ""),
        "Источник высоты обжима": (DF20_SRC_OLD, DF20_SRC_NEW),
    }},
    {"id": "fb4d8994-4e78-4654-a121-d4bc619b9329", "article": "N-50-3-9/133 NE", "key": "Артикул", "edits": {
        "Провод": ("RG-223", "RG-402"),
        "L1": ("4,6", "3"),
        "L2": ("8", ""),
        "L3": ("16,3", ""),
        "Источник данных": (COAX_SRC_OLD, (
            "Кабель и L1: Huber+Suhner — лист данных 25_N-50-3-9/133_N (item 22543952): Suitable cables SUCOFORM_141, "
            "MULTIFLEX_141 (H+S: «Handformable (RG402)»); инструкция 0000350737 rev. D: жила 3 мм, диэлектрик и внешний "
            "проводник заподлицо, пайка жилы и экрана. До 17.10.2026 стоял RG-223 с L1–L3 по аналогу Amphenol 82-5373. "
            + HS.format("22543952"))),
    }},
    {"id": "223531e2-b526-46b4-a8ab-fa28ce61302a", "article": "N-50-2-14/133 NE", "key": "Артикул", "edits": {
        "Провод": ("RG-223", "RG-405"),
        "L1": ("4,6", "5,4"),
        "L2": ("8", ""),
        "L3": ("16,3", ""),
        "Источник данных": (COAX_SRC_OLD, (
            "Кабель и L1: Huber+Suhner — лист данных 25_N-50-2-14/133_N (item 22641303): Suitable cables SUCOFORM_86, "
            "SR_86_TP_M17 (H+S: «Handformable (RG405)»); инструкция 0000182188 rev. G: зачистка 5,4 мм, диэлектрик и "
            "внешний проводник заподлицо, пайка жилы и экрана. До 17.10.2026 стоял RG-223 с L1–L3 по аналогу Amphenol "
            "82-5373. " + HS.format("22641303"))),
    }},
]

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
done = same = refused = 0
for row in ROWS:
    with factory() as uow:
        record = uow.tech_operations.get(UUID(row["id"]))
        if record is None:
            refused += 1
            print(f"   нет записи: {row['article']}")
            continue
        fields = record.fields
        article = str(fields.get(row["key"]) or "").strip()
        if article != row["article"]:
            refused += 1
            print(f"   отказ: артикул «{article}», ждали «{row['article']}»")
            continue
        edits = row["edits"]
        if all(str(fields.get(name, "")).strip() == new.strip() for name, (_, new) in edits.items()):
            same += 1
            print(f"   уже так: {row['article']}")
            continue
        try:
            before, _ = _edit_in(record.id, tuple(FieldEdit(name, old, new) for name, (old, new) in edits.items()), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            refused += 1
            print(f"   отказ: {row['article']} ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": list(edits), "reason": REASON}, uow)
        if mode == "apply":
            uow.commit()
        done += 1
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: {row['article']}: {', '.join(edits)}")
print(f"ИТОГО {len(ROWS)}: {'проверено' if mode == 'dry' else 'записано'} {done}, уже так {same}, отказ {refused}")
