"""Purchasing ↔ Procurement: the RFQ reads the buyer's procurement lines and writes the chosen supplier back.

Requests for quotes (app.modules.purchasing.rfq) never touch procurement tables. They see a line through
`ProcurementLines` (a read model: what to ask for, how much, the calc price with VAT) and hand the decision back
here, where it becomes an ordinary «Заполнить данные» save — `save_procurement_facts` with the line's version, so the
procurement rules (own line only, «В работе»/«Готов к запросу» only, the stage following the facts, the supplier
map learning) stay in one place."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.procurement.application import (
    ConcurrentProcurementUpdate,
    SaveProcurementFactsCommand,
    SupplierDirectory,
    UnitOfWork as ProcurementUnitOfWork,
    save_procurement_facts,
)
from app.modules.procurement.domain import (
    FACT_PRICE_VAT,
    FACTS_EDITABLE,
    ProcurementFacts,
    ProcurementLine,
    ProcurementValidationError,
)
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing.rfq import LineChanged, LineRefused, LineView
from app.shared.identity import Actor

_CHUNK = 500


def budget_unit_gross(line: ProcurementLine) -> Decimal | None:
    """ЦЕНА С НДС (Расчет); without it the net calc price plus the fact VAT rate (22 %) — решено агентом."""
    if line.calculated_price_with_vat is not None:
        return line.calculated_price_with_vat
    if line.calculated_price is not None:
        return (line.calculated_price * (100 + FACT_PRICE_VAT) / 100).quantize(Decimal("0.000001"), ROUND_HALF_UP)
    return None


def line_view(line: ProcurementLine) -> LineView:
    return LineView(
        id=line.id,
        legacy_id=line.legacy_id,
        status=line.status.value,
        version=line.version,
        buyer_id=line.buyer.id if line.buyer else None,
        editable=line.status in FACTS_EDITABLE,
        name=line.calculated_name,
        article=line.design_article,
        to_buy=line.to_buy,
        unit=line.calculated_unit,
        need_by=line.planned_date,
        budget_unit_gross=budget_unit_gross(line),
        supplier=line.supplier,
        hint=" ".join(part for part in (line.item_type, line.category, line.calculated_name) if part),
        actual_quantity=line.actual_quantity,
        actual_unit=line.actual_unit,
        project=line.project_reference,
        item_type=line.item_type,
    )


class ProcurementLinesBridge:
    def __init__(
        self,
        procurement_uow_factory: Callable[[], ProcurementUnitOfWork],
        suppliers: SupplierDirectory | None = None,
    ):
        self.procurement_uow_factory = procurement_uow_factory
        self.suppliers = suppliers

    def mine(self, actor: Actor) -> list[LineView]:
        """The buyer's own lines on the desk («В работе» / «Готов к запросу»)."""
        with self.procurement_uow_factory() as uow:
            lines = list(uow.procurement.by_buyer(actor, 10_000))
        return [line_view(line) for line in lines if line.status in FACTS_EDITABLE]

    def get_many(self, line_ids: Iterable[UUID]) -> dict[UUID, LineView]:
        wanted = list(dict.fromkeys(line_ids))
        found: dict[UUID, LineView] = {}
        with self.procurement_uow_factory() as uow:
            session = getattr(uow, "session", None)
            # One query for all: the repository's get() below then reads the session's identity map (the rows are
            # held here so it cannot drop them) instead of one SELECT per line.
            held = [
                row for start in range(0, len(wanted), _CHUNK) for row in session.scalars(
                    select(ProcurementLineRow).where(ProcurementLineRow.id.in_(wanted[start:start + _CHUNK])))
            ] if session is not None and len(wanted) > 1 else []
            for line_id in wanted:
                line = uow.procurement.get(line_id)
                if line is not None:
                    found[line_id] = line_view(line)
        del held
        return found

    def apply_choice(
        self,
        line_id: UUID,
        *,
        supplier: str,
        quantity: Decimal,
        unit: str,
        expected_version: int,
        actor: Actor,
        now: datetime,
    ) -> LineView:
        """ПОСТАВЩИК, КОЛ-ВО (факт), ЕД.ИЗМ (факт) := the decision. A line changed since it was read → LineChanged."""
        command = SaveProcurementFactsCommand(
            line_id=line_id,
            facts=ProcurementFacts(supplier=supplier, actual_quantity=quantity, actual_unit=unit),
            expected_version=expected_version,
            actor=actor,
            now=now,
        )
        try:
            line = save_procurement_facts(command, self.procurement_uow_factory(), self.suppliers)
        except ConcurrentProcurementUpdate as error:
            raise LineChanged("Строку закупки изменили — обновите") from error
        except (IntegrityError, StaleDataError) as error:
            raise LineChanged("Строку закупки изменили — обновите") from error
        except ProcurementValidationError as error:
            raise LineRefused(str(error)) from error
        except KeyError as error:
            raise LineRefused("Строка закупки не найдена") from error
        return line_view(line)
