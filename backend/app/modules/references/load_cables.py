"""Структурная база кабелей: разбор монтажных проводов и карточки коаксиальных кабелей из файла.

    ERP_DATABASE_URL=... python -m app.modules.references.load_cables [--file путь] [--dry-run]
    ERP_DATABASE_URL=... python -m app.modules.references.load_cables --hookups [--file путь] [--fill-empty] [--dry-run]
    ERP_DATABASE_URL=... python -m app.modules.references.load_cables --coax-vocabulary [--file путь] [--dry-run]

Все шаги идемпотентны (сервер запускает их при каждом обновлении, `deploy/update.sh`):
1. Монтажные провода БД.ПРОВ без структуры получают её, если их сечение есть в «Сечения AWG ↔ мм²» (то же правило,
   что применила миграция 0089); провод со структурой не трогается. Остальные печатаются — их разбирает владелец.
2. Карточки коаксиальных кабелей из `backend/data/cables/coax-cables.json`: карточка с тем же названием (ключ
   генератора: без регистра, пробелов и дефисов) переписывается из файла, если он говорит другое, новая добавляется,
   карточка, которой в файле больше нет, удаляется. Исправили данные — положили новый файл и запустили снова; новой
   миграции не нужно.
3. Полные названия и алиасы карточек из `backend/data/cables/coax-articles.json` (`--articles путь`): марка и её
   написания, полное название и артикул каждого предложения поставщика с этим самым кабелем (не «по семейству» и не
   `variant` — предложение, в названии которого материалы: «5D-FB CCA PE AL PE» — другой кабель, своя строка БД.ПРОВ).
   Генератор ЭТК и каталог редактора жгута находят по любому из них те же D1–D3. Имя, которое уже у другого кабеля
   или которое в файле у двух кабелей, не загружается — печатается с причиной; марка без карточки — тоже.
Второй запуск с теми же файлами ничего не меняет.

Формат файла: {"verified": bool, "cables": [{name, construction{core,dielectric,shield,jacket: {material, d_mm}},
connectors[{family, max_ghz, note}], vna_span{from_mhz, to_ghz, narrowing[{connector, to_ghz}], note},
markers[{freq_mhz | "peak", vswr_max, attenuation_db_m}], attenuation[{freq_mhz, value | nominal | max, unit,
ref_length_m, temp_c, interpolated, source}], fc_ghz, notes_ru, sources[], changed[], confidence, параметры
(impedance_ohm, velocity_pct, shielding_db, bend_static_mm, bend_dynamic_mm, temp_min_c, temp_max_c, voltage_max_v,
weight_g_m)}]}. Одна запись — один кабель. КСВН в файле — норма приёмки (предложено), не значение из datasheet.

`--hookups` — только база монтажных проводов из `backend/data/cables/hookup-wires.json` (`importers.import_hookups`,
формат там же): недостающие сечения добавляются, размеры серий сопоставляются со строками БД.ПРОВ по серии и
сечению, ячейки и структура обновляются на месте, недостающие размеры создаются; ничего не переименовывается и не
удаляется. Запускается руками (в `deploy/update.sh` не входит); второй запуск ничего не меняет. `--fill-empty` —
только пустые ячейки: заполненная не трогается, сечения и строки не создаются, шаг 3 не считается.

`--coax-vocabulary` — один раз, руками (в `deploy/update.sh` не входит): ячейки «Тип провода», «Изоляция», «Экран»
коаксиальных строк БД.ПРОВ получают слова словаря `backend/data/cables/coax-vocabulary.json` (записи «БД.ПРОВ» его
`map`). Переписывается только ячейка, в которой ещё старый текст; строка помечается правленной в ERP (перезагрузка
зеркала её не трогает). Ячейка, которую успели поменять, печатается и остаётся как есть; второй запуск ничего не меняет.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.shared.identity import Actor

from .application import apply_coax_vocabulary, import_coax_cards, import_coax_names, structure_hookups
from .domain import ReferenceValidationError
from .importers import ImportReport, import_hookups

ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")
DEFAULT_FILE = Path(__file__).resolve().parents[3] / "data" / "cables" / "coax-cables.json"
HOOKUPS_FILE = DEFAULT_FILE.with_name("hookup-wires.json")
ARTICLES_FILE = DEFAULT_FILE.with_name("coax-articles.json")
VOCABULARY_FILE = DEFAULT_FILE.with_name("coax-vocabulary.json")


def read_seed(path: Path = DEFAULT_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Монтажные провода и коаксиальные кабели → ERP")
    parser.add_argument("--file", type=Path, default=None, help=f"по умолчанию {DEFAULT_FILE.name} ({HOOKUPS_FILE.name} с --hookups, "
                                                                     f"{VOCABULARY_FILE.name} с --coax-vocabulary)")
    parser.add_argument("--dry-run", action="store_true", help="проверить и посчитать, ничего не записывая")
    parser.add_argument("--hookups", action="store_true", help="только база монтажных проводов из hookup-wires.json")
    parser.add_argument("--fill-empty", action="store_true", help="с --hookups: писать только в пустые ячейки")
    parser.add_argument("--articles", type=Path, default=ARTICLES_FILE, help=f"полные названия и артикулы, по умолчанию {ARTICLES_FILE.name}")
    parser.add_argument("--coax-vocabulary", action="store_true",
                        help=f"один раз: ячейки коаксиальных строк БД.ПРОВ словами {VOCABULARY_FILE.name}")
    args = parser.parse_args(argv)
    from app.database import build_engine, build_references_uow_factory

    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL", file=sys.stderr)
        return 1
    factory = build_references_uow_factory(build_engine(database_url))
    now = datetime.now(timezone.utc)
    mode = "проверка, ничего не записано" if args.dry_run else "записано"
    if args.hookups:
        try:
            report = import_hookups(factory(), read_seed(args.file or HOOKUPS_FILE), ACTOR, now, dry_run=args.dry_run,
                                    fill_empty=args.fill_empty)
        except (OSError, ValueError, ReferenceValidationError) as error:
            print(f"База монтажных проводов не загружена: {error}", file=sys.stderr)
            return 1
        print(hookup_summary(report, mode))
        return 0
    if args.coax_vocabulary:
        try:
            result = apply_coax_vocabulary(read_seed(args.file or VOCABULARY_FILE), ACTOR, now, factory(), dry_run=args.dry_run)
        except (OSError, ValueError, ReferenceValidationError) as error:
            print(f"Словарь коаксиальных кабелей не применён: {error}", file=sys.stderr)
            return 1
        print(vocabulary_summary(result, mode))
        return 0
    try:
        wires = structure_hookups(ACTOR, now, factory, dry_run=args.dry_run)
        coax = import_coax_cards(read_seed(args.file or DEFAULT_FILE), ACTOR, now, factory(), dry_run=args.dry_run)
        # A dry run wrote no cards: the names are planned over the cards the base has now.
        names = import_coax_names(read_seed(args.articles), ACTOR, now, factory(), dry_run=args.dry_run)
    except (OSError, ValueError, ReferenceValidationError) as error:
        print(f"Структурная база кабелей не загружена: {error}", file=sys.stderr)
        return 1
    print(f"Монтажные провода ({mode}): разобрано сейчас {wires['created']}, было {wires['structured_before']}, "
          f"осталось владельцу {len(wires['left'])}" + "".join(f"\n  — {line}" for line in wires["left"]))
    print(f"Коаксиальные кабели ({mode}): в файле {coax['cards']}, новых {coax['created']}, "
          f"обновлено {coax['updated']}, удалено {coax['removed']}")
    print(names_summary(names, mode))
    return 0


def names_summary(report: dict, mode: str) -> str:
    lines = [
        f"Названия и артикулы коаксиальных кабелей ({mode}): кабелей в файле {report['cables']}, имён у карточек "
        f"{report['aliases']}, добавлено {report['aliases_added']}, убрано {report['aliases_removed']}, полных названий "
        f"изменено {report['full_names_changed']}; предложений «по семейству» (не имя кабеля) {report['family_only']}, "
        f"другого кабеля (материалы в названии) {report['variants']}",
    ]
    if report["no_card"]:
        lines.append(f"  нет карточки ({len(report['no_card'])}), имена ждут её: " + ", ".join(report["no_card"]))
    if report["refused"]:
        lines.append(f"  не загружено ({len(report['refused'])}):")
        lines += [f"     — {item}" for item in report["refused"]]
    return "\n".join(lines)


def vocabulary_summary(report: dict, mode: str) -> str:
    lines = [f"Словарь коаксиальных кабелей → БД.ПРОВ ({mode}): ячеек в словаре {report['cells']}, "
             f"переписано {len(report['rewritten'])}, не тронуто {len(report['problems'])}, "
             f"марок нет в БД.ПРОВ {len(report['missing'])}"]
    lines += [f"  — {item}" for item in report["rewritten"]]
    if report["problems"]:
        lines.append("  не тронуто (ячейку уже поменяли):")
        lines += [f"     — {item}" for item in report["problems"]]
    if report["missing"]:
        lines.append("  марок нет в БД.ПРОВ: " + ", ".join(report["missing"]))
    return "\n".join(lines)


def hookup_summary(report: ImportReport, mode: str) -> str:
    def block(title: str, items) -> list[str]:
        items = list(items)
        return [title, *(f"     — {item}" for item in items)] if items else []

    lines = [
        f"База монтажных проводов ({mode}{', только пустые ячейки' if report.fill_empty else ''}):",
        f"1. Сечения: в файле {report.gauges_in_file}, добавлено {len(report.gauges_added)}, "
        f"расходятся с таблицей {len(report.gauge_mismatches)} (не изменены)",
        *block("   добавлены:", report.gauges_added), *block("   расходятся:", report.gauge_mismatches),
        f"2. Размеры: в файле {report.sizes_in_file}, найдено строк БД.ПРОВ {report.rows_matched}, "
        f"обновлено {len(report.updated)}, без изменений {report.unchanged}, создано {len(report.created)}, "
        f"отклонено {len(report.refused)}; структура: создана {report.hookups_created}, обновлена {report.hookups_updated}",
        *block("   созданы:", report.created),
        *block("   обновлены:", (f"{item['key']}: " + "; ".join(f"{name} «{old}» → «{new}»" for name, (old, new) in item["changes"].items())
                                 for item in report.updated)),
        *block("   отклонены:", (f"{item['key']}: {item['reason']}" for item in report.refused)),
        "3. Ушли из монтажных в «прочие»: не считается — загрузка класс строк не меняет" if report.fill_empty else
        f"3. Ушли из монтажных в «прочие» (марка не из базы монтажных): {len(report.moved_out_of_montage)}",
        *block("   марки:", report.moved_out_of_montage),
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
