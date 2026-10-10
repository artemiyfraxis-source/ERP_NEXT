"""Структурная база кабелей: разбор монтажных проводов и карточки коаксиальных кабелей из файла.

    ERP_DATABASE_URL=... python -m app.modules.references.load_cables [--file путь] [--dry-run]

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

ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")
DEFAULT_FILE = Path(__file__).resolve().parents[3] / "data" / "cables" / "coax-cables.json"


def read_seed(path: Path = DEFAULT_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Монтажные провода и коаксиальные кабели → ERP")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--dry-run", action="store_true", help="проверить и посчитать, ничего не записывая")
    args = parser.parse_args(argv)
    from app.database import build_engine, build_references_uow_factory

    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL", file=sys.stderr)
        return 1
    factory = build_references_uow_factory(build_engine(database_url))
    now = datetime.now(timezone.utc)
    mode = "проверка, ничего не записано" if args.dry_run else "записано"
    try:
        wires = structure_hookups(ACTOR, now, factory, dry_run=args.dry_run)
        coax = import_coax_cards(read_seed(args.file), ACTOR, now, factory(), dry_run=args.dry_run)
    except (OSError, ValueError, ReferenceValidationError) as error:
        print(f"Структурная база кабелей не загружена: {error}", file=sys.stderr)
        return 1
    print(f"Монтажные провода ({mode}): разобрано сейчас {wires['created']}, было {wires['structured_before']}, "
          f"осталось владельцу {len(wires['left'])}" + "".join(f"\n  — {line}" for line in wires["left"]))
    print(f"Коаксиальные кабели ({mode}): в файле {coax['cards']}, новых {coax['created']}, "
          f"обновлено {coax['updated']}, удалено {coax['removed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
