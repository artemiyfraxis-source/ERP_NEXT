"""Структурная база кабелей: разбор монтажных проводов и карточки коаксиальных кабелей из файла.

    ERP_DATABASE_URL=... python -m app.modules.references.load_cables [--file путь] [--dry-run]
    ERP_DATABASE_URL=... python -m app.modules.references.load_cables --hookups [--file путь] [--dry-run]

Оба шага идемпотентны (сервер запускает их при каждом обновлении, `deploy/update.sh`):
1. Монтажные провода БД.ПРОВ без структуры получают её, если их сечение есть в «Сечения AWG ↔ мм²» (то же правило,
   что применила миграция 0089); провод со структурой не трогается. Остальные печатаются — их разбирает владелец.
2. Карточки коаксиальных кабелей из `backend/data/cables/coax-cables.json`: карточка с тем же названием (ключ
   генератора: без регистра, пробелов и дефисов) переписывается из файла, новая добавляется, карточка, которой в
   файле больше нет, удаляется. Исправили данные — положили новый файл и запустили снова; новой миграции не нужно.

Формат файла: {"verified": bool, "cables": [{name, construction{core,dielectric,shield,jacket: {material, d_mm}},
connectors[{family, max_ghz, note}], vna_span{from_mhz, to_ghz, narrowing[{connector, to_ghz}], note},
markers[{freq_mhz | "peak", vswr_max, attenuation_db_m}], attenuation[{freq_mhz, value | nominal | max, unit,
ref_length_m, temp_c, interpolated, source}], fc_ghz, notes_ru, sources[], changed[], confidence, параметры
(impedance_ohm, velocity_pct, shielding_db, bend_static_mm, bend_dynamic_mm, temp_min_c, temp_max_c, voltage_max_v,
weight_g_m)}]}. Одна запись — один кабель. КСВН в файле — норма приёмки (предложено), не значение из datasheet.

`--hookups` — только база монтажных проводов из `backend/data/cables/hookup-wires.json` (`importers.import_hookups`,
формат там же): недостающие сечения добавляются, размеры серий сопоставляются со строками БД.ПРОВ по серии и
сечению, ячейки и структура обновляются на месте, недостающие размеры создаются; ничего не переименовывается и не
удаляется. Запускается руками (в `deploy/update.sh` не входит); второй запуск ничего не меняет.
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

from .application import import_coax_cards, structure_hookups
from .domain import ReferenceValidationError
from .importers import ImportReport, import_hookups

ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")
DEFAULT_FILE = Path(__file__).resolve().parents[3] / "data" / "cables" / "coax-cables.json"
HOOKUPS_FILE = DEFAULT_FILE.with_name("hookup-wires.json")


def read_seed(path: Path = DEFAULT_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Монтажные провода и коаксиальные кабели → ERP")
    parser.add_argument("--file", type=Path, default=None, help=f"по умолчанию {DEFAULT_FILE.name} ({HOOKUPS_FILE.name} с --hookups)")
    parser.add_argument("--dry-run", action="store_true", help="проверить и посчитать, ничего не записывая")
    parser.add_argument("--hookups", action="store_true", help="только база монтажных проводов из hookup-wires.json")
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
            report = import_hookups(factory(), read_seed(args.file or HOOKUPS_FILE), ACTOR, now, dry_run=args.dry_run)
        except (OSError, ValueError, ReferenceValidationError) as error:
            print(f"База монтажных проводов не загружена: {error}", file=sys.stderr)
            return 1
        print(hookup_summary(report, mode))
        return 0
    try:
        wires = structure_hookups(ACTOR, now, factory, dry_run=args.dry_run)
        coax = import_coax_cards(read_seed(args.file or DEFAULT_FILE), ACTOR, now, factory(), dry_run=args.dry_run)
    except (OSError, ValueError, ReferenceValidationError) as error:
        print(f"Структурная база кабелей не загружена: {error}", file=sys.stderr)
        return 1
    print(f"Монтажные провода ({mode}): разобрано сейчас {wires['created']}, было {wires['structured_before']}, "
          f"осталось владельцу {len(wires['left'])}" + "".join(f"\n  — {line}" for line in wires["left"]))
    print(f"Коаксиальные кабели ({mode}): в файле {coax['cards']}, новых {coax['created']}, "
          f"обновлено {coax['updated']}, удалено {coax['removed']}")
    return 0


def hookup_summary(report: ImportReport, mode: str) -> str:
    def block(title: str, items) -> list[str]:
        items = list(items)
        return [title, *(f"     — {item}" for item in items)] if items else []

    lines = [
        f"База монтажных проводов ({mode}):",
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
        f"3. Ушли из монтажных в «прочие» (марка не из базы монтажных): {len(report.moved_out_of_montage)}",
        *block("   марки:", report.moved_out_of_montage),
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
