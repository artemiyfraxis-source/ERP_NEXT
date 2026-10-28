"""Права «Сканирование документов» / «Печать документов» и скан на выбранный сканер (владелец, 28.10.2026: «Сканировать
документы в базу может ограниченное количество операторов… каждый, кому доступна эта функция, может воспользоваться
своим устройством. Также печать отдельных документов доступна ограниченному количеству операторов (по должности)»).

- Матрица «Доступы» (`identity_level_access`) получает две новые клетки, никто ничего не теряет:
  `scan` = edit — каждому уровню, у которого сегодня «Склад» или «Приёмка» на правке (сканировал УПД именно он: кнопки
  «Склад → Поставка» и «Приёмка → Принять по УПД» стояли за правкой этих разделов); `print` = edit — каждому уровню, у которого записана хоть одна клетка. Только недостающие клетки
  (`ON CONFLICT DO NOTHING`): уже записанная не трогается, повторный запуск ничего не меняет. Личные исключения не
  добавляются; печатается, у скольких людей правка склада была только личным исключением (им «Сканирование» выдают
  в «Доступах», если нужно). Дальше владелец сужает оба права в «Доступах».
- `warehouse_scan_jobs.target_scanner` — сканер, выбранный человеком (WIA DeviceID или имя, как агент его прислал);
  NULL — агент берёт первый свой сканер. Сканер бывает только у задания с адресатом-агентом (CHECK) и не пустой.
- Печатается сверка: строк матрицы было → стало, добавлено клеток scan / print.

DESTRUCTIVE = False. Откат убирает клетки и личные исключения `scan` / `print` (старый код этих разделов не знает) и
колонку `target_scanner`.

Ревизия встаёт после текущей головы; при слиянии с параллельной 0158 меняется только `down_revision`.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0159_scan_print_access"
down_revision: str | None = "0158_lot_maker_supplier_articles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

SCAN, PRINT, EDIT = "scan", "print", "edit"


def _scalar(connection, sql: str, **params) -> int:
    return int(connection.execute(sa.text(sql).bindparams(**params)).scalar() or 0)


def _cells(connection, section: str) -> int:
    return _scalar(connection, "SELECT COUNT(*) FROM identity_level_access WHERE section = :section", section=section)


def upgrade() -> None:
    connection = op.get_bind()
    rows_before = _scalar(connection, "SELECT COUNT(*) FROM identity_level_access")
    scan_before, print_before = _cells(connection, SCAN), _cells(connection, PRINT)

    connection.execute(sa.text("""
        INSERT INTO identity_level_access (level, section, mode)
        SELECT DISTINCT level, :scan, :edit FROM identity_level_access
        WHERE section IN ('warehouse', 'receiving') AND mode = :edit
        ON CONFLICT (level, section) DO NOTHING
    """).bindparams(scan=SCAN, edit=EDIT))
    connection.execute(sa.text("""
        INSERT INTO identity_level_access (level, section, mode)
        SELECT DISTINCT level, :print, :edit FROM identity_level_access WHERE section <> :print
        ON CONFLICT (level, section) DO NOTHING
    """).bindparams(print=PRINT, edit=EDIT))

    # People whose «Склад» / «Приёмка» on edit is a personal exception over a level without `scan`: they scanned
    # before, the level cell does not give it to them now (no exceptions are added — the owner grants it in «Доступы»).
    personal = _scalar(connection, """
        SELECT COUNT(DISTINCT u.id) FROM identity_user_level_access a JOIN identity_users u ON u.id = a.user_id
        WHERE a.section IN ('warehouse', 'receiving') AND a.mode = 'edit' AND u.active = :active
          AND u.access_level NOT IN ('superadmin', 'admin')
          AND NOT EXISTS (SELECT 1 FROM identity_level_access l
                          WHERE l.level = u.access_level AND l.section = :scan AND l.mode = :edit)
    """, active=True, scan=SCAN, edit=EDIT)

    with op.batch_alter_table("warehouse_scan_jobs") as batch:
        batch.add_column(sa.Column("target_scanner", sa.String(300), nullable=True))
        batch.create_check_constraint(
            "ck_warehouse_scan_jobs_target_scanner",
            "target_scanner IS NULL OR (target_scanner <> '' AND target_machine_id IS NOT NULL)",
        )

    rows_after = _scalar(connection, "SELECT COUNT(*) FROM identity_level_access")
    print(f"0159_scan_print_access: строк матрицы было {rows_before}, стало {rows_after}; "
          f"добавлено клеток scan {_cells(connection, SCAN) - scan_before}, print {_cells(connection, PRINT) - print_before}; "
          f"людей с правкой склада / приёмки только личным исключением (scan не получили): {personal}")


def downgrade() -> None:
    connection = op.get_bind()
    with op.batch_alter_table("warehouse_scan_jobs") as batch:
        batch.drop_constraint("ck_warehouse_scan_jobs_target_scanner", type_="check")
        batch.drop_column("target_scanner")
    removed = 0
    for table in ("identity_level_access", "identity_user_level_access"):
        removed += connection.execute(
            sa.text(f"DELETE FROM {table} WHERE section IN (:scan, :print)").bindparams(scan=SCAN, print=PRINT)
        ).rowcount or 0
    print(f"0159_scan_print_access (откат): удалено клеток и исключений scan / print: {removed}")
