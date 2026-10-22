"""«Счета», «Цепочка документов», «Поиск по документам» (stage option documents.chain).

Paths are spelled out under /api/purchasing: the UPD registry (option documents.upd) owns /api/purchasing/upd and its
card; the two UPD routes here hang the invoices block on that card. Links between invoices and UPDs are computed on
read; the buyer's confirmed / rejected word is the only write. Receiving comes through app/workflows."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, procurement_edit_principal, procurement_view_principal
from app.shared.stored_files import file_response
from app.workflows import purchasing_documents_receiving as receiving_flow

from . import doc_chain, doc_search, invoices
from .deps import purchasing_uow_factory, receiving_uow_factory

router = APIRouter(prefix="/api/purchasing", tags=["purchasing"])

CHAIN_PAGE_SIZE = 150
Uow = Annotated[Callable, Depends(purchasing_uow_factory)]
ReceivingUow = Annotated[Callable | None, Depends(receiving_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
Viewer = Annotated[Principal, Depends(procurement_view_principal)]
Editor = Annotated[Principal, Depends(procurement_edit_principal)]
UpdId = Annotated[str, Path(pattern=r"^[\w-]{1,128}$")]
Buyer = Annotated[str | None, Query(pattern=r"^(ours|other|unknown|\d{10}|\d{12})$")]


@contextmanager
def _errors():
    try:
        yield
    except (doc_chain.DocumentNotFound, invoices.InvoiceNotFound) as error:
        raise HTTPException(status_code=404, detail=str(error)) from None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _receiving(make_receiving: Callable | None, upds: list[doc_chain.ChainUpd], detail: bool = False) -> dict | None:
    """UPD id → «Приёмка: N строк, ГРН …, статус» (with the lines when `detail`); None — no receiving on this stand."""
    if make_receiving is None:
        return None
    unique = {u.id: u for u in upds}
    found = receiving_flow.receiving_by_documents(make_receiving, [
        receiving_flow.DocumentKey(u.id, u.number, date.fromisoformat(u.date) if u.date else None, u.seller_name)
        for u in unique.values()
    ])
    out: dict = {}
    for upd_id in unique:
        lines = found.get(upd_id, [])
        out[upd_id] = {**receiving_flow.summary(lines), **({"items": [line.as_json() for line in lines]} if detail else {})}
    return out


def _chain_upds(chains: list[doc_chain.Chain]) -> list[doc_chain.ChainUpd]:
    return [u for c in chains for u, _ in c.upds] + [u for c in chains for u in c.suggestions]


@router.get("/invoices")
def list_invoices(
    _: Viewer, make_uow: Uow, now: Now, q: Annotated[str, Query(max_length=100)] = "",
    status: Literal["to_pay", "paid", "return", "other"] | None = None, supplier: UUID | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None, date_to: Annotated[date | None, Query(alias="to")] = None,
    closing: Literal["none"] | None = None, page: Annotated[int, Query(ge=1)] = 1,
) -> dict:
    uow = make_uow()
    with uow:
        world, _facts = doc_chain.load_world(uow.session, now.date())
        return invoices.list_invoices(uow.session, world, invoices.InvoiceQuery(
            q=q.strip(), status=status, supplier_id=supplier, date_from=_iso(date_from), date_to=_iso(date_to),
            closing=closing, page=page,
        ))


@router.get("/invoices/{invoice_id}")
def invoice_card(invoice_id: UUID, _: Viewer, make_uow: Uow, make_receiving: ReceivingUow, now: Now) -> dict:
    uow = make_uow()
    with uow, _errors():
        world, _facts = doc_chain.load_world(uow.session, now.date())
        card, chain = invoices.invoice_card(uow.session, world, invoice_id)
        receiving = _receiving(make_receiving, _chain_upds([chain]) if chain else [], detail=True)
        return {
            **card, "chain": chain.as_json(receiving or {}) if chain else None,
            "receiving_available": make_receiving is not None,
        }


@router.get("/invoices/{invoice_id}/files/{file_row_id}")
def invoice_file(invoice_id: UUID, file_row_id: UUID, _: Viewer, make_uow: Uow):
    uow = make_uow()
    with uow, _errors():
        content, name = invoices.invoice_file(uow.session, invoice_id, file_row_id)
    return file_response(content, name)


@router.get("/doc-chain")
def list_chains(
    _: Viewer, make_uow: Uow, make_receiving: ReceivingUow, now: Now, view: Literal["problems", "all"] = "problems",
    q: Annotated[str, Query(max_length=100)] = "", supplier: UUID | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None, date_to: Annotated[date | None, Query(alias="to")] = None,
    closing: Literal["edo", "paper", "none"] | None = None, buyer: Buyer = None, page: Annotated[int, Query(ge=1)] = 1,
) -> dict:
    """Invoice → payments → closing documents → receiving, with what disagrees; problems first, then the newest."""
    uow = make_uow()
    with uow:
        world, _facts = doc_chain.load_world(uow.session, now.date())
        shown, summary = doc_chain.list_chains(world, doc_chain.ChainQuery(
            view=view, q=q.strip(), supplier_id=supplier, supplier_inn=invoices.supplier_inn(uow.session, supplier),
            date_from=_iso(date_from), date_to=_iso(date_to), closing=closing, buyer=buyer,
        ))
    chunk = shown[(page - 1) * CHAIN_PAGE_SIZE:page * CHAIN_PAGE_SIZE]
    receiving = _receiving(make_receiving, _chain_upds(chunk))
    return {
        "summary": summary, "chains": [c.as_json(receiving or {}) for c in chunk], "total": len(shown), "page": page,
        "page_size": CHAIN_PAGE_SIZE, "receiving_available": make_receiving is not None,
    }


@router.get("/upd/{upd_id}/invoices")
def upd_invoices(upd_id: UpdId, _: Viewer, make_uow: Uow, make_receiving: ReceivingUow, now: Now) -> dict:
    """The invoices a UPD closes, the ones it might, and the pairs the buyer rejected."""
    return _upd_invoices(make_uow, make_receiving, now, upd_id)


def _upd_invoices(make_uow: Callable, make_receiving: Callable | None, now: datetime, upd_id: str) -> dict:
    uow = make_uow()
    with uow, _errors():
        world, facts = doc_chain.load_world(uow.session, now.date())
        out = doc_chain.upd_invoices(uow.session, world, facts, upd_id)
        upd = facts.all_upds.get(upd_id)
    receiving = _receiving(make_receiving, [upd] if upd else [], detail=True)
    return {**out, "receiving": receiving.get(upd_id) if receiving is not None else None}


class DecisionIn(BaseModel):
    decision: Literal["confirmed", "rejected"] | None


@router.put("/upd/{upd_id}/invoices/{invoice_id}")
def set_upd_invoice(
    upd_id: UpdId, invoice_id: UUID, payload: DecisionIn, principal: Editor, make_uow: Uow, make_receiving: ReceivingUow,
    now: Now,
) -> dict:
    """The buyer's word on a pair: confirmed, rejected, or null to let the data decide again."""
    uow = make_uow()
    with uow, _errors():
        doc_chain.set_decision(uow, upd_id, invoice_id, payload.decision, principal.actor, now)
        uow.commit()
    return _upd_invoices(make_uow, make_receiving, now, upd_id)


@router.get("/doc-search")
def search_documents(
    _: Viewer, make_uow: Uow, q: Annotated[str, Query(max_length=100)] = "", kind: Literal["all", "upd", "invoice"] = "all",
) -> dict:
    uow = make_uow()
    with uow:
        return doc_search.search_documents(uow.session, q.strip(), kind)
