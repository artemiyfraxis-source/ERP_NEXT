"""Бумажная УПД в данных отправки проекта (владелец, 14.10.2026).

«Данные отправки» (Приёмка с производства, Отгрузка): получатель, адрес, телефон, вариант УПД; если УПД бумажная —
её можно приложить заранее, пока КБ ещё в цехе. Новая отправка проекта с бумажной УПД берёт этот файл себе.

Что делает миграция:
- `shipping_project_addressees.upd_url` — ссылка на приложенный файл УПД (VARCHAR(1024) NOT NULL DEFAULT '');
- `shipping_files` (то же хранилище файлов УПД, второго нет): файл может принадлежать карточке проекта, а не
  отправке — `shipment_id` становится NULL-able, новая колонка `project` (VARCHAR(255) NOT NULL DEFAULT '') и CHECK:
  у файла есть отправка или проект.

Существующие строки не меняются (новые колонки пустые, у старых файлов отправка есть). Печатается сверка: строк в
каждой таблице до и после. DESTRUCTIVE = False. Откат: файлы карточек, которые уже взяла отправка, переходят к ней;
остальные файлы карточек удаляются (старый код не знает файлов без отправки); печатается, сколько каких.
"""

from typing import Sequence
from uuid import UUID

from alembic import op
import sqlalchemy as sa


revision: str = "0125_addressee_upd_file"
down_revision: str | None = "0124_levels_pm_storekeeper"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

ADDRESSEES = "shipping_project_addressees"
FILES = "shipping_files"
SHIPMENTS = "shipments"
OWNER_CHECK = "ck_shipping_files_owner"
FILE_URL = "/api/shipping/files/"


def _count(table: str) -> int:
    return int(op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def upgrade() -> None:
    before = {table: _count(table) for table in (ADDRESSEES, FILES)}
    with op.batch_alter_table(ADDRESSEES) as batch:
        batch.add_column(sa.Column("upd_url", sa.String(1024), nullable=False, server_default=""))
    with op.batch_alter_table(FILES) as batch:
        batch.add_column(sa.Column("project", sa.String(255), nullable=False, server_default=""))
        batch.alter_column("shipment_id", existing_type=sa.Uuid(), nullable=True)
        batch.create_check_constraint(OWNER_CHECK, "shipment_id IS NOT NULL OR project <> ''")
    after = {table: _count(table) for table in before}
    print("0125_addressee_upd_file: строк было → стало: " + ", ".join(
        f"{table} {before[table]} → {after[table]}" for table in before
    ))


def downgrade() -> None:
    bind = op.get_bind()
    files = sa.table(FILES, sa.column("id", sa.Uuid()), sa.column("shipment_id", sa.Uuid()))
    shipments = sa.table(SHIPMENTS, sa.column("id", sa.Uuid()), sa.column("upd_url", sa.String()))
    moved = dropped = 0
    for file_id in bind.execute(sa.select(files.c.id).where(files.c.shipment_id.is_(None))).scalars().all():
        url = f"{FILE_URL}{UUID(str(file_id))}"
        owner = bind.execute(sa.select(shipments.c.id).where(shipments.c.upd_url == url).limit(1)).scalar()
        if owner is not None:
            bind.execute(sa.update(files).where(files.c.id == file_id).values(shipment_id=owner))
            moved += 1
        else:
            bind.execute(sa.delete(files).where(files.c.id == file_id))
            dropped += 1
    with op.batch_alter_table(FILES) as batch:
        batch.drop_constraint(OWNER_CHECK, type_="check")
        batch.alter_column("shipment_id", existing_type=sa.Uuid(), nullable=False)
        batch.drop_column("project")
    with op.batch_alter_table(ADDRESSEES) as batch:
        batch.drop_column("upd_url")
    print(f"0125_addressee_upd_file откат: файлов карточек передано отправкам {moved}, удалено {dropped}")
