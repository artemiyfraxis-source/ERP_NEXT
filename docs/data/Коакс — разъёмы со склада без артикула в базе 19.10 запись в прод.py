"""Запись в прод 19.10.2026 — БД.КОАКС: разъёмы со склада ЦАР, которых нет в базе (владелец 19.10: «2.92-JB2G-F, 3.5-JB2AG,
2.92mm male 086, 3.5mm male 086, SMA-JB2C, SMA-JW2CG, SMA-JWB3-F-1, SMA-JR, SMB-KB2, SMP-KW2 R/A RG178 — это тоже, только
выделить из этого именно артикул»). Список: docs/data/«Склад — коаксиальные разъёмы vs БД.КОАКС 19.10.csv».

Артикул = только номер детали из текста партии; провод и исполнение — в свои колонки. «086» = полужёсткий кабель .086″ = RG-405
(так назван в самой базе и у Hengliti: «接086线缆», «接086/RG405»). Один ряд на пару «артикул + провод».
Заполнено только то, что подтверждает документ или сама партия; остальное пусто (L1–L3, HEX, тип пина/экрана, частота,
производитель, цена). Не записано (номера детали нет, не выдумывать): «2.92mm male 086» (ГРН OLD.26.0000157) и
«3.5mm male 086» (OLD.26.0000158); а также SMA-JW2CG (OLD.26.0000070) и SMA-JR (OLD.26.0000128) —
в партии нет провода, а ключ строки «артикул + провод» без него не принимается (угадывать провод нельзя). Заглубление контакта — только у прямых и только общим значением серии
(radiolab / MIL-STD-348B), как у уже записанных строк.
Режимы: dry | apply. Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл
  ... python - dry   < этот файл   → всё «без изменений»
Откат: удалить строки по ключу (артикул × провод) в «Базе данных» → БД.КОАКС; список — рядом, в .csv с тем же именем."""
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Разъёмы со склада 19.10.2026", department="System")
SMA_DEPTH = ("0,38…1,14", "radiolab.ru, SMA, вилка: E = 0,38…1,14 мм от торца корпуса до контакта; стандарт SMA (MIL-C-39012, "
             "IEC 169-15), общий для серии; https://www.radiolab.ru/ru/attach_file/smaradiolab.pdf; 19.10.2026")
SMB_DEPTH = ("0,36…1,19", "MIL-STD-348B рис. 311-1 (SMB, штырь): C 3,33…3,58 (REF → торец корпуса) − D 2,39…2,97 (REF → кончик штыря) = "
             "0,36…1,19 мм; radiolab.ru SMB, розетка: D 3,33…3,58, H ≤ 2,97 — совпадает; "
             "https://www.radiolab.ru/ru/attach_file/smbradiolab.pdf; 19.10.2026")
HL = ("Hengliti (hltchips.com): «B2» в артикулах серий ISMA/SMP = кабель 086/RG405 (HL-ISMA-JB2 id=841 «接086线缆», "
      "HL-SMP-KB2 id=409 «接086/RG405»); ")
NOSHEET = "документа производителя на этот артикул не найдено — в базе только то, что на партии склада; "


def row(art, kind, wire, series, gender, angle, name, lots, shop, depth=None, link="", extra=""):
    fields = {
        "coaxArticle": art, "coaxWire": wire, "coaxType": kind, "Серия": series, "Пол": gender, "Исполнение": angle,
        "Наименование": name, "Ссылка": link,
        "Примечание": f"склад ЦАР: {lots}; текст партии «{shop}»",
        "Источник данных": f"склад ЦАР, текст партии «{shop}»: артикул, пол, исполнение" + (", провод" if wire else "") + "; "
                           + extra + NOSHEET + "19.10.2026",
    }
    if depth:
        fields["Заглубление контакта, мм"], fields["Заглубление контакта, источник"] = depth
    return {key: value for key, value in fields.items() if value != "" or key in ("coaxArticle", "coaxWire")}


ROWS = [
    row("2.92-JB2G-F", "2.92mm-male", "RG-405", "2.92mm", "штекер", "прямой",
        "Разъём 2.92-JB2G-F (2,92 мм, штекер, под кабель RG-405 / .086)", "ГРН OLD.26.0000069, ячейка 18C2C", "2.92-JB2G-F", extra=HL),
    row("3.5-JB2AG", "3.5mm-male", "RG-405", "3.5mm", "штекер", "прямой",
        "Разъём 3.5-JB2AG (3,5 мм, штекер, под кабель RG-405 / .086)", "ГРН OLD.26.0000072, ячейка 18C1E", "3.5-JB2AG", extra=HL),
    row("SMA-JB2C", "SMA-male", "RG-405", "SMA", "штекер", "прямой",
        "Разъём SMA-JB2C (SMA, штекер, под кабель RG-405 / .086)", "ГРН OLD.26.0000066 и 067, ячейки 18C1C, 18C1D", "SMA-JB2C",
        depth=SMA_DEPTH, extra=HL),
    row("SMA-JWB3-F-1", "SMA-male R/A", "RG-402", "SMA", "штекер", "угловой",
        "Разъём SMA-JWB3-F-1 (SMA, штекер, угловой, под кабель RG-402)", "ГРН OLD.26.0000068, ячейка 18E6B", "SMA-JWB3-F-1"),
    row("SMB-KB2", "SMB-female", "RG-405", "SMB", "гнездо", "прямой",
        "Разъём SMB-KB2 (SMB, розетка, под кабель RG-405 / .086)", "ГРН OLD.26.0000174, ячейка 18F6C", "SMB-KB2",
        depth=SMB_DEPTH, link="https://tejte.com/smb-kb2-kwb2",
        extra="tejte.com/smb-kb2-kwb2: «SMB-KB2 SMB female connector … Suitable for 086/RG405 and other cables»; " + HL),
    row("SMP-KW2", "SMP-female R/A", "RG-178", "SMP", "гнездо", "угловой",
        "Разъём SMP-KW2 (SMP, розетка, угловой, под кабель RG-178)", "ГРН OLD.26.0000173, ячейка 18F6B", "SMP-KW2 R/A RG178"),
]

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def call(dry_run: bool):
    return import_rows("coax", factory(), [ImportRowIn(fields=fields, old=None) for fields in ROWS], ACTOR, now,
                       dry_run=dry_run, source="erp").model_dump()


data = call(True)
print("== dry:", {k: (len(v) if isinstance(v, list) else v) for k, v in data.items()})
for key in ("created", "updated", "refused"):
    for item in data.get(key) or []:
        print("  ", key, item)
if mode == "apply":
    if len(data["created"]) != len(ROWS) or data["refused"]:
        raise SystemExit("СТОП — ожидалось создать ровно новые строки, отказов 0; ничего не записано")
    data = call(False)
    print("== apply:", {k: (len(v) if isinstance(v, list) else v) for k, v in data.items()})
