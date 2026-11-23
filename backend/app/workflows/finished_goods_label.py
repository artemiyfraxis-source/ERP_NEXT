"""Этикетка ГРН акта приёмки ГП (владелец 22.10.2026): кнопка «Печать» у ГРН «ГП.YY.NNNNNNN» в «Приёмке с производства».

Акт приёмки живёт в Shipping, очередь печати этикеток — в Warehouse; связывает их этот сценарий (межмодульный код только
здесь, как у остальных `workflows/`): берёт акт и КБ из Shipping, собирает `GrnLabelData` и ставит задание в ту же
очередь, откуда его забирает агент печати склада. Задание — `kind="lot"`, `lot_id` = id акта (в очереди это просто
ссылка без внешнего ключа; партий склада у ГП нет, схема не меняется). Рисуется по шаблону этикетки вида «intake»
конструктора этикеток (владелец 23.10.2026): сохранённому для текущего формата или встроенному."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.shipping.infrastructure import FinishedGoodsActRow, SqlAlchemyFinishedGoodsRepository
from app.modules.warehouse.labels.layout import GrnLabelData
from app.modules.warehouse.labels.template import LabelContent, intake_content
from app.modules.warehouse.labels_application import PrintResult, label_settings, print_label

GOODS_WAREHOUSE = "ГП"  # склад готовой продукции — вторая часть строки «место · склад» на этикетке
NOT_FOUND = "Акт приёмки с таким ГРН не найден"


@dataclass(frozen=True)
class ActLabel:
    act_id: UUID
    data: GrnLabelData
    project: str = ""
    item_index: str = ""
    registry_fields: dict = field(default_factory=dict)

    @property
    def content(self) -> LabelContent:
        """The values of the «intake» label template (the designer's fields of «Приёмка с производства»)."""
        data = self.data
        if self.registry_fields:
            if not set(self.registry_fields) <= {item.name for item in fields(GrnLabelData)}:
                raise ValueError("Печать нового ГРН требует шаблонов категорий ГРН; обновите модуль печати")
            data = replace(data, **self.registry_fields)
        return intake_content(data, self.project, self.item_index)


def _quantity(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001")).normalize(), "f")


def act_label_data(sessions: Callable[[], Session], grn: str) -> ActLabel:
    """Что написано на этикетке акта: ГРН, «проект КБ» в строке артикула, наименование, штуки акта (за вычетом откатов),
    дата приёмки, место · «ГП». KeyError — такого ГРН у актов приёмки нет."""
    with sessions() as session:
        row = session.scalar(select(FinishedGoodsActRow).where(FinishedGoodsActRow.grn == grn.strip()))
        if row is None:
            raise KeyError(NOT_FOUND)
        repo = SqlAlchemyFinishedGoodsRepository(session)
        good = repo.get(row.finished_good_id)
        if good is None:
            raise KeyError(NOT_FOUND)
        act = next((item for item in repo.acts([good.id]).get(good.id, ()) if item.id == row.id), None)
        quantity = act.net if act is not None else Decimal(row.quantity)
        return ActLabel(row.id, GrnLabelData(
            grn=row.grn, sku=f"{good.project} {good.item_index}", item_name=good.name or good.article,
            quantity=_quantity(quantity), unit=good.unit, manufacturer_batch="", received_on=row.received_at,
            place=(row.location or good.location or "").strip(), warehouse=GOODS_WAREHOUSE,
        ), good.project, good.item_index, {
            "category": row.grn_category or "", "grn_id": row.grn_id, "grn_number": row.grn,
            "quality_state": row.quality_state, "quality_basis": row.quality_basis or "",
            "context": row.grn_context or {},
        } if row.grn_contract_version is not None else {})


def print_act_label(sessions: Callable[[], Session], warehouse_uow_factory: Callable[[], object], grn: str, copies: int,
                    now: datetime, *, created_by: str) -> PrintResult:
    label = act_label_data(sessions, grn)
    return print_label(
        warehouse_uow_factory(), label.content, label_settings(warehouse_uow_factory()), copies, now,
        kind="lot", lot_id=label.act_id, created_by=created_by,
    )
