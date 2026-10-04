"""Брак штучных позиций спецификации — 0 (технолог Василий 04.10: «пока даём без брака»).

С 04.10 новая штучная строка набора получает брак 0 % (`default_scrap`), а «на партию» у штук округляется вверх до
целого. Строки, заведённые раньше, несут прежнее умолчание 5 % — с округлением вверх они выдавали бы лишние штуки.
Здесь у штучных компонентов (тип не Кабель/Провод/ТУТ/Шлейф — `unit_for_type`) брак ровно 5 становится 0; другое
значение брака технолог ставил руками — его не трогаем, метражные позиции тоже. Решено агентом — проверить.
Откат пустой: прежние 5 % от ручных уже не отличить."""

import json
from decimal import Decimal
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0084_kit_piece_scrap_zero"
down_revision: str | None = "0083_procurement_sheet_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LENGTH_TYPES = {"Кабель", "Провод", "ТУТ", "Шлейф"}  # requests.domain.ZPK_UNIT_BY_TYPE (метры)


def _is_old_piece_default(component: dict) -> bool:
    if str(component.get("type") or "").strip() in _LENGTH_TYPES:
        return False
    try:
        return Decimal(str(component.get("scrap") or "0")) == Decimal("5")
    except ArithmeticError:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    kitsets = sa.table("techcard_kitsets", sa.column("project", sa.String()), sa.column("assemblies", sa.JSON()))
    for project, assemblies in bind.execute(sa.select(kitsets.c.project, kitsets.c.assemblies)).all():
        data = json.loads(assemblies) if isinstance(assemblies, str) else assemblies
        changed = False
        for assembly in data or []:
            for component in assembly.get("components") or []:
                if _is_old_piece_default(component):
                    component["scrap"] = "0"
                    changed = True
        if changed:
            bind.execute(kitsets.update().where(kitsets.c.project == project).values(assemblies=data))


def downgrade() -> None:
    pass
