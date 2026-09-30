"""Разовый снос всех проектов ERP — владелец 30.09.2026: «все проекты снесем и буду по одному тебе давать, чтобы
я проверял». Дальше проекты приходят по одному: список `data/projects.txt`, загрузка `scripts/projects_import.py load`.

    python -m app.workflows.projects_wipe [--dry-run]

Сносится всё, что принадлежит проектам: карточки наборов (состав, спецификация), ТК с ревизиями и эскизами, реестр
и журнал плана, задания цеха со всем, что на них записано (операции, смены, выпуск, коробки, брак, ОТК), готовая
продукция и отгрузки. Не трогается: склад (партии ЦАР/ЖУК, списания), запросы на комплектацию и выдачи — они держат
резерв и списания партий, поэтому остатки не меняются; заявки, закупки, приёмка, справочники, сотрудники; КД проектов
(вернётся в карточку, когда проект загрузят снова, как после «Удалить проект»); нумерация заданий, коробок и
отгрузок (номера не повторятся).

Снос делается один раз: метка `projects_wiped` в `operations_job_marks` лежит в самой базе, так что база, возвращённая
из копии, снесётся снова. SQLite-база перед сносом копируется рядом (`<база>.before-projects-wipe.bak`). Всё в одной
транзакции: ошибка — ничего не удалено. Код выхода: 0 — сделано или уже было; 2 — нет базы.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, Table, delete, func, select
from sqlalchemy.orm import Session

from app.modules.operations.infrastructure import OperationsJobMarkRow, SqlAlchemyJobMarks
from app.modules.production.plan import normalize_project_number
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import Base, SqlAlchemyAuditRepository
from app.workflows.kitset_names import QUEUE_KEY as NAME_QUEUE_KEY


WIPED_KEY = "projects_wiped"
LOADED_KEY = "projects_loaded"  # номера, загруженные из Google по списку data/projects.txt (projects_import.py load)

# Таблицы проектов. Удаляются в обратном порядке зависимостей (metadata.sorted_tables), так что порядок здесь
# свободный. Ни одна оставляемая таблица не ссылается на эти (tests/workflows/test_projects_wipe.py).
WIPED_TABLES = frozenset((
    # карточки наборов и техкарты
    "techcard_kitsets", "techcard_projects", "techcard_revisions", "techcard_operation_sketches",
    # реестр и журнал плана
    "production_plan_projects", "production_plan_events",
    # задания цеха и всё, что на них записано
    "production_tasks", "production_operations", "production_operation_operators", "production_work_sessions",
    "production_work_inputs", "production_work_material_inputs", "production_boxes", "production_outputs",
    "production_consumption", "production_material_consumption", "production_serials",
    "production_finished_goods_receipts", "production_defects", "production_defect_decisions",
    "production_qc_inspections", "production_qc_instruments", "production_qc_decisions",
    # готовая продукция и отгрузки
    "finished_goods", "shipments", "shipment_lines", "shipment_boxes", "shipping_files", "shipping_project_addressees",
))

# Что печатается в итоге: таблица → подпись.
REPORTED = (
    ("techcard_kitsets", "карточек проектов"),
    ("techcard_projects", "ТК"),
    ("techcard_revisions", "ревизий ТК"),
    ("production_plan_projects", "строк реестра"),
    ("production_plan_events", "записей журнала"),
    ("production_tasks", "заданий цеха"),
    ("finished_goods", "строк готовой продукции"),
    ("shipments", "отгрузок"),
)


def wiped_tables() -> list[Table]:
    """Таблицы проектов — дети раньше родителей."""
    import app.database  # noqa: F401 — таблицы всех модулей попадают в metadata

    tables = {table.name: table for table in Base.metadata.sorted_tables}
    missing = sorted(WIPED_TABLES - tables.keys())
    if missing:
        raise RuntimeError(f"Нет таблиц: {', '.join(missing)}")
    return [table for table in reversed(Base.metadata.sorted_tables) if table.name in WIPED_TABLES]


def wiped_at(engine: Engine) -> str | None:
    """Когда проекты сносились (метка), None — ещё не сносились."""
    with Session(engine) as session:
        value = SqlAlchemyJobMarks(session).get(WIPED_KEY)
    if value is None:
        return None
    try:
        return str(json.loads(value).get("at") or value)
    except (ValueError, AttributeError):
        return value


def count_rows(engine: Engine) -> dict[str, int]:
    with Session(engine) as session:
        return {table.name: int(session.scalar(select(func.count()).select_from(table)) or 0)
                for table in wiped_tables()}


def backup_sqlite(engine: Engine, now: datetime) -> Path | None:
    """Копия SQLite-базы рядом с ней (API остановлен, но копия всё равно через backup — целостная). Не SQLite — None."""
    url = engine.url
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    source = Path(url.database).resolve()
    target = source.with_name(f"{source.name}.before-projects-wipe.bak")
    if target.exists():
        target = source.with_name(f"{source.name}.before-projects-wipe-{now:%Y%m%d-%H%M%S}.bak")
    src, dst = sqlite3.connect(source), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def wipe_projects(engine: Engine, *, actor: Actor, now: datetime, backup: Path | None = None) -> dict[str, int] | None:
    """Снести проекты одной транзакцией и поставить метку. None — метка уже стоит, ничего не тронуто."""
    tables = wiped_tables()
    with Session(engine) as session:
        marks = SqlAlchemyJobMarks(session)
        if marks.get(WIPED_KEY) is not None:
            return None
        counts = {table.name: int(session.scalar(select(func.count()).select_from(table)) or 0) for table in tables}
        for table in tables:
            session.execute(delete(table))
        # Очередь добора наименований и список загруженных — про снесённые карточки.
        session.execute(delete(OperationsJobMarkRow).where(OperationsJobMarkRow.key.in_((NAME_QUEUE_KEY, LOADED_KEY))))
        marks.put(WIPED_KEY, json.dumps({
            "at": now.isoformat(timespec="seconds"), "backup": str(backup or ""), "counts": counts,
        }, ensure_ascii=False), now)
        SqlAlchemyAuditRepository(session).add(AuditEvent(
            id=uuid.uuid4(), aggregate_id=actor.id, event_type="projects.wiped", actor=actor, occurred_at=now,
            details={**{name: str(count) for name, count in counts.items()}, "backup": str(backup or "")},
        ))
        session.commit()
    return counts


def loaded_projects(engine: Engine) -> set[str]:
    """Номера, уже загруженные по списку (нормализованные): повторно не загружаются, удалённые в ERP не возвращаются."""
    with Session(engine) as session:
        value = SqlAlchemyJobMarks(session).get(LOADED_KEY)
    try:
        items = json.loads(value) if value else []
    except ValueError:
        items = []
    return {normalize_project_number(item) for item in items if isinstance(item, str)} if isinstance(items, list) else set()


def mark_loaded(engine: Engine, projects: Iterable[str], now: datetime) -> None:
    with Session(engine) as session:
        marks = SqlAlchemyJobMarks(session)
        try:
            current = json.loads(marks.get(LOADED_KEY) or "[]")
        except ValueError:
            current = []
        items = [str(item) for item in current] if isinstance(current, list) else []
        for project in projects:
            key = normalize_project_number(project)
            if key and key not in items:
                items.append(key)
        marks.put(LOADED_KEY, json.dumps(items, ensure_ascii=False), now)
        session.commit()


def summary(counts: dict[str, int]) -> str:
    return ", ".join(f"{label} {counts.get(name, 0)}" for name, label in REPORTED)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Разовый снос всех проектов ERP (склад и комплектация не трогаются).")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать, что будет снесено")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine

    engine = build_engine(url)
    try:
        done = wiped_at(engine)
        if done is not None:
            print(f"Проекты уже снесены ({done}) — пропускаю. Новые проекты — по списку data/projects.txt.")
            return 0
        counts = count_rows(engine)
        if args.dry_run:
            print(f"Будет снесено (пробный прогон): {summary(counts)}")
            return 0
        now = moscow_now()
        backup = backup_sqlite(engine, now) if any(counts.values()) else None
        if backup is not None:
            print(f"Копия базы до сноса: {backup}")
        actor = Actor(id=uuid.uuid4(), name="снос проектов (владелец 30.09)", department="")
        counts = wipe_projects(engine, actor=actor, now=now, backup=backup)
        if counts is None:
            print("Проекты уже снесены — пропускаю.")
            return 0
        print(f"Снесено: {summary(counts)}.")
        print("Не тронуты: склад, запросы на комплектацию и выдачи, заявки, закупки, приёмка, КД проектов.")
        print("Новые проекты — по одному, списком data/projects.txt.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
