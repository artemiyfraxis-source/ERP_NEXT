"""«Заявки» закупщика ↔ the other modules: the one place the view meets them.

Procurement builds the view (`procurement.requests_view`) and never imports purchasing or the warehouse; this workflow
hands it, for a list of lines and on the caller's session (read-only, a few indexed reads per call):
- the lines' places in the requests for quotes (purchasing `rfq_line_states`) as `RfqState`;
- the order facts of «Счета и поставки» (`purchasing_order_facts`: handed over for payment, shipped) as `OrderState`;
- the free stock of each line's item: warehouse lots with something available (balance − reserve), the same part
  number (else the same name) and unit — RB «На складе: свободно N»;
- the last UPD purchase of the line's nomenclature item (matched as the request pool does; purchasing
  `line_purchases`).
And the request-for-quotes half of «Разделить» (`PurchasingRfqSplitter`), inside the procurement command's transaction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.procurement.domain import same_unit
from app.modules.procurement.price_history import article_key, name_key
from app.modules.procurement.requests_view import LastPurchase, OrderState, RfqState, ViewLine
from app.modules.purchasing import line_purchases, rfq_line_states, rfq_split
from app.modules.warehouse.infrastructure import StockLotRow
# The request pool's attribution of a procurement line to a nomenclature item (link, part number, names).
from app.workflows.purchasing_rfq_stats import _Nomenclature
from app.workflows.purchasing_order_facts import order_facts
from app.shared.events import AuditEvent
from app.shared.identity import Actor


def composition_projects(session: Session) -> list[dict]:
    """Saved evidence stays discoverable even before a buyer creates any purchase intent."""
    from app.modules.requests.infrastructure import CalcCompositionRow
    return [{"key": project, "kind": "project", "value": project, "lines": 0, "counts": {}}
            for project in session.scalars(select(CalcCompositionRow.project).order_by(CalcCompositionRow.project))]


def saved_composition(session: Session, project: str) -> dict:
    """Read saved full source + exact recorded request relations. Never fetch Google or create obligations."""
    from app.modules.requests.infrastructure import SqlAlchemyCompositionRepository, RequestRow
    from app.modules.procurement.infrastructure import ProcurementLineRow
    from app.shared.project_number import normalize_project_number

    project = normalize_project_number(project)
    snapshot = SqlAlchemyCompositionRepository(session).get(project)
    if snapshot is None:
        return {"project": project, "state": "missing", "version": 0, "source": None, "rows": []}
    ids = [UUID(row["request_id"]) for row in snapshot.rows if row["request_id"]]
    existing = set(session.scalars(select(RequestRow.id).where(RequestRow.id.in_(ids)))) if ids else set()
    procurement = {}
    if ids:
        for line_id, request_id in session.execute(select(
            ProcurementLineRow.id, ProcurementLineRow.source_request_id,
        ).where(ProcurementLineRow.source_request_id.in_(ids))):
            procurement.setdefault(str(request_id), []).append(str(line_id))
    lots = session.scalars(select(StockLotRow).where(
        StockLotRow.inbound - StockLotRow.written_off > 0,
    ).order_by(StockLotRow.warehouse, StockLotRow.grn, StockLotRow.package, StockLotRow.id)).all()
    by_article, by_name = {}, {}
    for lot in lots:
        if article_key(lot.article):
            by_article.setdefault(article_key(lot.article), []).append(lot)
        if name_key(lot.name):
            by_name.setdefault(name_key(lot.name), []).append(lot)
    rows = []
    for row in snapshot.rows:
        matched = by_article.get(article_key(row["name"]), [])
        how = "exact_article"
        if not matched:
            matched = by_name.get(name_key(row["name"]), [])
            how = "exact_name"
        matched = [lot for lot in matched if row.get("unit_source") in {"explicit", "derived_known"}
                   and row["unit"].strip() and lot.unit.strip()
                   and same_unit(row["unit"], lot.unit)]
        ambiguous = how == "exact_name" and len({article_key(lot.article) for lot in matched}) > 1
        request_id = row["request_id"] if row["request_id"] and UUID(row["request_id"]) in existing else None
        rows.append({
            **row, "request_id": request_id, "procurement_ids": sorted(procurement.get(request_id, [])),
            "relation": "recorded" if request_id else (
                "request_missing" if row["request_id"] else row.get("request_link_state", "unlinked")
            ),
            "lots": [{
                "id": str(lot.id), "grn": lot.grn, "package": lot.package, "warehouse": lot.warehouse,
                "article": lot.article, "name": lot.name, "unit": lot.unit,
                "balance": str(lot.inbound - lot.written_off), "reserved": str(lot.reserved),
                "free": str(lot.inbound - lot.written_off - lot.reserved),
                "role": "matching_stock", "match_source": how,
                "match_state": "ambiguous" if ambiguous else "exact",
            } for lot in matched],
        })
    return {
        "project": snapshot.project, "snapshot_id": str(snapshot.id), "state": "saved", "version": snapshot.version,
        "coverage": "full_source", "valid": all(not row["issues"] for row in rows),
        "source": {
            "kind": "google_calc", "id": snapshot.source_id,
            "url": f"https://docs.google.com/spreadsheets/d/{snapshot.source_id}/edit",
            "sheet": "ЗКП", "content_hash": snapshot.content_hash,
            "imported_at": snapshot.imported_at.isoformat(), "imported_by": str(snapshot.imported_by),
        }, "rows": rows,
    }


class CompositionRefreshError(RuntimeError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def refresh_composition(state, *, project, url, expected_version, actor, now):
    """Explicit source read; the command's own Requests UOW commits only snapshot + audit."""
    from app.modules.requests.application import CalcSheetError, extract_from_calc
    from app.modules.requests.composition import CompositionConflict, composition_seed, save_composition
    from app.modules.requests.domain import ValidationError
    from app.modules.requests.google_sheets import reader_from_env

    try:
        reader = getattr(state, "calc_spreadsheet_reader", None) or reader_from_env()
        seed = composition_seed(project, url, extract_from_calc(url, reader))
        with state.uow_factory() as uow:
            value = save_composition(seed, expected_version, actor, now, uow)
            uow.commit()
    except CompositionConflict as error:
        raise CompositionRefreshError(str(error), 409) from error
    except ValidationError as error:
        raise CompositionRefreshError(str(error), 422) from error
    except CalcSheetError as error:
        raise CompositionRefreshError(str(error), 503) from error
    return {"project": value.project, "version": value.version, "content_hash": value.content_hash}



def rfq_states(session: Session, line_ids: Iterable[UUID]) -> dict[UUID, RfqState]:
    return {
        line_id: RfqState(
            live=state.rfq_id is not None, rfq_id=state.rfq_id, rfq_number=state.rfq_number, quoted=state.quoted,
            decided=state.decided, requote=state.requote, requote_from=state.requote_from,
        )
        for line_id, state in rfq_line_states.states(session, line_ids).items()
    }


def order_states(session: Session, lines: Sequence[ViewLine]) -> dict[str, OrderState]:
    """№ ЗАКАЗА → what the orders board knows of it: «В оплате» and «В доставке» show in «Заявки» too."""
    facts = order_facts(session, [line.order_number for line in lines if line.order_number])
    return {
        number: OrderState(sent_to_pay=bool(entry["sent_to_pay_at"]), shipped=bool(entry["shipped_at"]))
        for number, entry in facts.items()
    }


def free_stock(session: Session, lines: Sequence[ViewLine]) -> dict[UUID, Decimal]:
    """Available quantity on the lots of each line's item; lines with nothing are left out."""
    if not lines:
        return {}
    L = StockLotRow
    lots = session.execute(
        select(L.article, L.name, L.unit, L.inbound, L.written_off, L.reserved).where(
            L.inbound - L.written_off - L.reserved > 0
        )
    ).all()
    by_article: dict[str, list[tuple[str, Decimal]]] = {}
    by_name: dict[str, list[tuple[str, Decimal]]] = {}
    for lot in lots:
        available = lot.inbound - lot.written_off - lot.reserved
        key = article_key(lot.article)
        if key:
            by_article.setdefault(key, []).append((lot.unit, available))
        name = name_key(lot.name)
        if name:
            by_name.setdefault(name, []).append((lot.unit, available))
    result: dict[UUID, Decimal] = {}
    for line in lines:
        key = article_key(line.article)
        pool = by_article.get(key, []) if key else []
        if not pool:
            pool = by_name.get(name_key(line.name), [])
        total = sum((available for unit, available in pool if same_unit(line.unit, unit)), Decimal("0"))
        if total > 0:
            result[line.id] = total
    return result


def _day(value: str) -> date:
    return date.fromisoformat(value[:10])


def last_purchases(
    session: Session, lines: Sequence[ViewLine], paid: Mapping[UUID, LastPurchase],
) -> dict[UUID, LastPurchase]:
    """The newest of the procurement's own paid lines (`paid`) and, the UPDs."""
    result = dict(paid)
    if not lines:
        return result
    nomenclature = _Nomenclature(session)
    wanted = []
    for line in lines:
        item_id, _how = nomenclature.match(line.id, line.article, line.name)
        if item_id is not None:
            wanted.append(line_purchases.Wanted(line.id, item_id, line.unit))
    for line_id, upd in line_purchases.last_purchases(session, wanted).items():
        on = _day(upd.date)
        known = result.get(line_id)
        if known is None or known.on < on:
            unit = next((line.unit for line in lines if line.id == line_id), None)
            result[line_id] = LastPurchase(price=upd.price, on=on, supplier=upd.supplier or None, unit=unit, source="upd",
                                           document_id=upd.document_id, document_number=upd.number)
    return result


class PurchasingRfqSplitter:
    """procurement's `RfqLineSplitter` port over purchasing's `rfq_split`, on the procurement unit of work's session."""

    def live_request(self, uow, line_id: UUID) -> str | None:
        return rfq_split.live_request(uow.session, line_id)

    def split(
        self, uow, *, line_id: UUID, new_line_id: UUID, kept: Decimal, split: Decimal, actor: Actor, now: datetime,
    ) -> dict | None:
        copied = rfq_split.split_rfq_line(
            uow.session, procurement_line_id=line_id, new_procurement_line_id=new_line_id, kept_quantity=kept,
            split_quantity=split, actor_id=actor.id, now=now,
        )
        if copied is None:
            return None
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=copied.rfq_id, event_type="purchasing.rfq_line_split", actor=actor, occurred_at=now,
            details={"number": copied.rfq_number, "quotes": str(copied.quotes), "procurement_line_id": str(line_id),
                     "new_procurement_line_id": str(new_line_id)},
        ))
        return {"rfq_id": str(copied.rfq_id), "rfq_number": copied.rfq_number, "quotes": copied.quotes}
