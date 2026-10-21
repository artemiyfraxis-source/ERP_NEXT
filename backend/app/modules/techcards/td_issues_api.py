"""Выпуски комплекта ТД по ЕСТД: API выпусков, нормоконтроля, печати и СТО. Контракт — frontend/src/tdTypes.ts
(TdIssue, TdIssueInput, TdFinding, TdPrintModel, Sto); правила — td_issues.py."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, techcards_edit_principal, techcards_view_principal

from .api import EtkCards, etk_cards, operation_grade_reference, techcards_uow_factory
from .application import UnitOfWork
from .td_issues import (
    TdIssue, TdIssueError, TdIssueNotFound, change_rows, create_issue, edit_issue, move_issue, normcontrol,
    parse_input, print_card, resnapshot_issue, sto_sections, with_td_ops,
)
from .td_sto import sto, sto_reference

router = APIRouter(prefix="/api", tags=["Комплект ТД"])


class SignatoryPayload(BaseModel):
    name: str = Field(default="", max_length=255)
    position: str = Field(default="", max_length=255)


class SignatoriesPayload(BaseModel):
    developer: SignatoryPayload = SignatoryPayload()
    checker: SignatoryPayload = SignatoryPayload()
    normcontrol: SignatoryPayload = SignatoryPayload()
    approver: SignatoryPayload = SignatoryPayload()
    agreed_vp: SignatoryPayload = SignatoryPayload()


class TdIssuePayload(BaseModel):
    litera: str = Field(default="", max_length=8)
    method_code: str = Field(min_length=2, max_length=8)
    signatories: SignatoriesPayload = SignatoriesPayload()
    notice_no: str = Field(default="", max_length=64)
    reason: str = Field(default="", max_length=2000)
    workshop: str = Field(default="", max_length=255)
    site: str = Field(default="", max_length=8)


class TdIssueEditPayload(TdIssuePayload):
    version: int = Field(ge=1)


class TdVersionPayload(BaseModel):
    version: int = Field(ge=1)


class TdStatusPayload(TdVersionPayload):
    to: Literal["draft", "signing", "active", "annulled"]


def _run(operation):
    try:
        return operation()
    except TdIssueError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Выпуск ТД изменён конкурентно: обновите и повторите") from error


def _current_revision(uow_factory: Callable[[], UnitOfWork], card_id: UUID) -> int:
    with uow_factory() as uow:
        card = uow.techcards.get(card_id)
    return card.revision if card is not None else 0


def _snapshot_of(
    uow_factory: Callable[[], UnitOfWork], etk: EtkCards, grades: dict[str, dict[str, str]],
) -> Callable[[UUID], dict[str, Any]]:
    """Карта ЭТК ровно как её отдаёт GET /api/techcards/projects/{id}, приведённая к JSON, и `td_ops` — профессия и
    разряд операций из БД.ОП (иначе по умолчанию)."""

    def snapshot(card_id: UUID) -> dict[str, Any]:
        with uow_factory() as uow:
            card = uow.techcards.get(card_id)
        if card is None:
            raise TdIssueNotFound("Карточка проекта не найдена")
        return with_td_ops(jsonable_encoder(etk.one(card)), grades)

    return snapshot


def _view(issue: TdIssue, uow_factory: Callable[[], UnitOfWork]) -> dict[str, Any]:
    return issue.view(_current_revision(uow_factory, issue.project_card_id))


def _issue(uow_factory: Callable[[], UnitOfWork], issue_id: UUID) -> TdIssue:
    with uow_factory() as uow:
        issue = uow.td_issues.get(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Выпуск ТД не найден")
    return issue


@router.get("/techcards/projects/{card_id}/td-issues", dependencies=[Depends(techcards_view_principal)])
def td_issues(
    card_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> list[dict[str, Any]]:
    with uow_factory() as uow:
        card = uow.techcards.get(card_id)
        if card is None:
            raise HTTPException(status_code=404, detail="Карточка проекта не найдена")
        issues = uow.td_issues.for_card(card_id)
    return [issue.view(card.revision) for issue in issues]


@router.post("/techcards/projects/{card_id}/td-issues", status_code=201)
def open_td_issue(
    card_id: UUID,
    payload: TdIssuePayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
    grades: Annotated[dict[str, dict[str, str]], Depends(operation_grade_reference)],
) -> dict[str, Any]:
    value = _run(lambda: parse_input(payload.model_dump()))
    snapshot = _run(lambda: _snapshot_of(uow_factory, etk, grades)(card_id))
    issue = _run(lambda: create_issue(uow_factory(), card_id, value, snapshot, principal.actor.name, now))
    return _view(issue, uow_factory)


@router.get("/techcards/td-issues/{issue_id}", dependencies=[Depends(techcards_view_principal)])
def td_issue(
    issue_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict[str, Any]:
    return _view(_issue(uow_factory, issue_id), uow_factory)


@router.put("/techcards/td-issues/{issue_id}", dependencies=[Depends(techcards_edit_principal)])
def edit_td_issue(
    issue_id: UUID,
    payload: TdIssueEditPayload,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict[str, Any]:
    value = _run(lambda: parse_input(payload.model_dump()))
    issue = _run(lambda: edit_issue(uow_factory(), issue_id, payload.version, value))
    return _view(issue, uow_factory)


@router.post("/techcards/td-issues/{issue_id}/resnapshot", dependencies=[Depends(techcards_edit_principal)])
def resnapshot_td_issue(
    issue_id: UUID,
    payload: TdVersionPayload,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
    grades: Annotated[dict[str, dict[str, str]], Depends(operation_grade_reference)],
) -> dict[str, Any]:
    issue = _run(lambda: resnapshot_issue(
        uow_factory(), issue_id, payload.version, _snapshot_of(uow_factory, etk, grades),
    ))
    return _view(issue, uow_factory)


@router.post("/techcards/td-issues/{issue_id}/status", dependencies=[Depends(techcards_edit_principal)])
def move_td_issue(
    issue_id: UUID,
    payload: TdStatusPayload,
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict[str, Any]:
    issue = _run(lambda: move_issue(uow_factory(), issue_id, payload.version, payload.to, now))
    return _view(issue, uow_factory)


@router.get("/techcards/td-issues/{issue_id}/normcontrol", dependencies=[Depends(techcards_view_principal)])
def td_issue_normcontrol(
    issue_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict[str, Any]:
    return {"findings": normcontrol(_issue(uow_factory, issue_id))}


@router.get("/techcards/td-issues/{issue_id}/print", dependencies=[Depends(techcards_view_principal)])
def td_issue_print(
    issue_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict[str, Any]:
    """TdPrintModel: всё для печати из замороженного снимка выпуска."""
    issue = _issue(uow_factory, issue_id)
    with uow_factory() as uow:
        card = uow.techcards.get(issue.project_card_id)
        issues = uow.td_issues.for_card(issue.project_card_id)
    return {
        "issue": issue.view(card.revision if card is not None else 0),
        "card": print_card(issue.snapshot),
        "sto_sections": sto_sections(issue.snapshot),
        "changes": change_rows(issues),
        "sto": sto_reference(),
    }


@router.get("/techcards/sto", dependencies=[Depends(techcards_view_principal)])
def td_sto() -> dict[str, Any]:
    """СТО ФРАК-001-2026 как есть (backend/data/sto-frak-001-2026.json)."""
    return sto()
