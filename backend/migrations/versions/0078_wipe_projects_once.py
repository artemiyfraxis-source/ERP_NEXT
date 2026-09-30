"""Разовый снос всех проектов (владелец, 30.09.2026: «все проекты снесем и буду по одному тебе давать»; дальше
проекты заводит он сам в ERP — «Создать проект из расчёта» — или по одному через чат).

Сносится всё, что принадлежит проектам: карточки наборов (состав, спецификация), ТК с ревизиями и эскизами, реестр
и журнал плана, задания цеха со всем, что на них записано, готовая продукция и отгрузки. Не трогаются: склад
(партии ЦАР/ЖУК, списания), запросы на комплектацию и выдачи — их строки держат РЕЗЕРВ партий и их списания, так
что остатки не меняются; заявки, закупки, приёмка, справочники, сотрудники; КД проектов (вернётся в карточку, когда
проект заведут снова, как после «Удалить проект»); нумерация заданий, коробок и отгрузок.

Миграция — потому что автообновление на ПК владельца запускает прежнюю версию update-local.ps1 и только она
подтягивает новый код: шаг в скрипте сработал бы лишь при следующем обновлении, миграция — сразу, один раз.
SQLite-база перед сносом копируется рядом (<база>.before-projects-wipe.bak); на пустой базе копии нет.
Итог — метка `projects_wiped` в operations_job_marks. Обратно не откатывается: данные — в копии."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0078_wipe_projects_once"
down_revision: str | None = "0077_production_operation_shares"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Дети раньше родителей: RESTRICT на PostgreSQL проверяется сразу, не в конце транзакции.
TABLES = (
    "production_qc_decisions", "production_qc_instruments", "production_qc_inspections",
    "production_defect_decisions", "production_defects", "production_finished_goods_receipts",
    "production_material_consumption", "production_consumption", "production_serials", "production_outputs",
    "production_work_material_inputs", "production_work_inputs", "production_work_sessions", "production_boxes",
    "production_operation_operators", "production_operations", "production_tasks",
    "production_plan_events", "production_plan_projects",
    "shipment_lines", "shipment_boxes", "shipping_files", "shipments", "finished_goods", "shipping_project_addressees",
    "techcard_operation_sketches", "techcard_revisions", "techcard_projects", "techcard_kitsets",
)
# Метки про снесённые карточки: очередь добора наименований набора.
STALE_MARKS = ("tc_name_queue",)
REPORTED = (
    ("techcard_kitsets", "карточек проектов"), ("techcard_projects", "ТК"), ("production_plan_projects", "строк реестра"),
    ("production_plan_events", "записей журнала"), ("production_tasks", "заданий цеха"),
    ("finished_goods", "строк готовой продукции"), ("shipments", "отгрузок"),
)


def _backup(bind, now: datetime) -> str:
    url = bind.engine.url
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return ""
    source = Path(url.database).resolve()
    target = source.with_name(f"{source.name}.before-projects-wipe.bak")
    if target.exists():
        target = source.with_name(f"{source.name}.before-projects-wipe-{now:%Y%m%d-%H%M%S}.bak")
    src, dst = sqlite3.connect(source, timeout=30), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return str(target)


def upgrade() -> None:
    bind = op.get_bind()
    counts = {name: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {name}")).scalar() or 0) for name in TABLES}
    now = datetime.now(timezone.utc)
    backup = _backup(bind, now) if any(counts.values()) else ""
    # Исправление выпуска ссылается на исправленный выпуск той же таблицы.
    op.execute(sa.text("UPDATE production_outputs SET correction_of_id = NULL WHERE correction_of_id IS NOT NULL"))
    for name in TABLES:
        op.execute(sa.text(f"DELETE FROM {name}"))
    marks = sa.table("operations_job_marks", sa.column("key", sa.String), sa.column("value", sa.Text),
                     sa.column("marked_at", sa.DateTime(timezone=True)))
    op.execute(marks.delete().where(marks.c.key.in_(STALE_MARKS + ("projects_wiped",))))
    op.bulk_insert(marks, [{"key": "projects_wiped", "marked_at": now, "value": json.dumps(
        {"at": now.isoformat(timespec="seconds"), "backup": backup, "counts": counts}, ensure_ascii=False,
    )}])
    _say("Снос проектов (владелец 30.09): " + ", ".join(f"{label} {counts[name]}" for name, label in REPORTED)
         + (f"; копия базы до сноса: {backup}" if backup else ""))


def _say(text: str) -> None:
    """Итог в вывод миграции. Вывод в трубу на Windows (автообновление из планировщика) идёт в кодировке ANSI:
    символ вне неё не должен откатывать уже сделанный снос вместе со всей миграцией."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def downgrade() -> None:
    """Снесённое не возвращается миграцией — только из копии базы."""
