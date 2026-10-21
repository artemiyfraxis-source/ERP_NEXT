"""Purchasing ↔ Procurement: «Оформить» a Kaiten invoice in a request for quotes (Roller_Buyer kaiten-rfq.ts orderFromKaiten).

Roller made its own order from the invoice. In ERP the procurement line is the order, and it moves only through its own
commands, so the purchasing side never writes procurement tables: after the choice has filled ПОСТАВЩИК / КОЛ-ВО /
ЕД.ИЗМ (факт) (ProcurementLinesBridge), this port runs the steps a buyer would run on «Лист закупки» —
`build_supplier_requests` («Сформировать запрос»: № ЗАКАЗА, «Жду счет»), `upload_invoice_file` (the invoice PDF into
procurement's files) and `invoice_supplier_order` (one invoice for the whole order: СЧЕТ, № / ДАТА СЧЕТА, ОЖИД. ДАТА,
«В оплату»). Every rule of those commands holds — own lines only, the stage, the versions, the pay gate."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.procurement.application import (
    BuildSupplierRequestsCommand,
    ConcurrentProcurementUpdate,
    InvoiceSupplierOrderCommand,
    OrderLineRef,
    OrderLinesMismatch,
    UnitOfWork as ProcurementUnitOfWork,
    UploadInvoiceFileCommand,
    build_supplier_requests,
    invoice_supplier_order,
    upload_invoice_file,
)
from app.modules.procurement.domain import FACTS_EDITABLE, ProcurementStatus, ProcurementValidationError
from app.modules.purchasing.rfq import LineChanged, LineRefused
from app.shared.identity import Actor


@dataclass(frozen=True)
class FormedOrder:
    order_number: str
    line_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class FormedOrders:
    orders: tuple[FormedOrder, ...]
    skipped: tuple[tuple[UUID, str], ...]  # (procurement line id, why it was left out)


def _refused(error: Exception) -> Exception:
    if isinstance(error, OrderLinesMismatch):
        return LineRefused(str(error))
    if isinstance(error, (ConcurrentProcurementUpdate, IntegrityError, StaleDataError)):
        return LineChanged("Строку закупки изменили — обновите")
    if isinstance(error, KeyError):
        return LineRefused(str(error.args[0]) if error.args else "Строка закупки не найдена")
    return LineRefused(str(error))


class KaitenOrderBridge:
    def __init__(self, procurement_uow_factory: Callable[[], ProcurementUnitOfWork]):
        self.procurement_uow_factory = procurement_uow_factory

    def blockers(self, line_ids: Iterable[UUID], actor: Actor) -> dict[UUID, str]:
        """What would stop «Сформировать запрос» once the choice has filled supplier and quantity: the line's stage,
        its buyer, no ТОЧКА ОТГРУЗКИ, an open replacement. Lines missing here can go."""
        out: dict[UUID, str] = {}
        with self.procurement_uow_factory() as uow:
            for line_id in dict.fromkeys(line_ids):
                line = uow.procurement.get(line_id)
                if line is None:
                    out[line_id] = "строка закупки не найдена"
                elif line.buyer is None or line.buyer.id != actor.id:
                    out[line_id] = "строка другого закупщика"
                elif line.status not in FACTS_EDITABLE and line.status is not ProcurementStatus.AWAITING_INVOICE:
                    out[line_id] = f"строка уже «{line.status.value}»"
                elif line.status in FACTS_EDITABLE and not (line.delivery_point or "").strip():
                    out[line_id] = "не заполнена ТОЧКА ОТГРУЗКИ — запрос поставщику не сформировать"
                elif line.replacement_open:
                    out[line_id] = "есть незакрытая замена (нет решения «Разрешено»)"
        return out

    def form_orders(self, line_ids: Iterable[UUID], actor: Actor, now: datetime) -> FormedOrders:
        """«Сформировать запрос» for these lines only: one order per supplier and task type, the lines «Жду счет»."""
        wanted = list(dict.fromkeys(line_ids))
        try:
            result = build_supplier_requests(BuildSupplierRequestsCommand(actor=actor, now=now, line_ids=wanted),
                                              self.procurement_uow_factory())
        except (ProcurementValidationError, ConcurrentProcurementUpdate, IntegrityError, StaleDataError, KeyError) as error:
            raise _refused(error) from error
        return FormedOrders(
            orders=tuple(FormedOrder(order.order_number, tuple(order.line_ids)) for order in result.orders),
            skipped=tuple((skip.line_id, skip.reason) for skip in result.skipped),
        )

    def attach_invoice(
        self, order_number: str, line_ids: Iterable[UUID], *, file_name: str, content: bytes, invoice_number: str,
        invoice_date: date, expected_arrival: date, actor: Actor, now: datetime,
    ) -> str:
        """The invoice file into procurement's files, then one invoice for the whole order → «В оплату». The link."""
        ids = list(dict.fromkeys(line_ids))
        try:
            uploaded = upload_invoice_file(UploadInvoiceFileCommand(
                line_id=ids[0], file_name=file_name, content_type="application/pdf", content=content, actor=actor, now=now,
            ), self.procurement_uow_factory())
            with self.procurement_uow_factory() as uow:
                refs = []
                for line_id in ids:
                    line = uow.procurement.get(line_id)
                    if line is None:
                        raise KeyError("Строка закупки не найдена")
                    refs.append(OrderLineRef(line_id, line.version))
            invoice_supplier_order(InvoiceSupplierOrderCommand(
                order_number=order_number, invoice_url=uploaded.url, expected_arrival=expected_arrival, lines=refs,
                actor=actor, now=now, invoice_number=invoice_number, invoice_date=invoice_date,
            ), self.procurement_uow_factory())
        except (ProcurementValidationError, ConcurrentProcurementUpdate, IntegrityError, StaleDataError, KeyError) as error:
            raise _refused(error) from error
        return uploaded.url
