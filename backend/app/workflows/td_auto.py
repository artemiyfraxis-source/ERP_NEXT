"""Автоматический выпуск комплекта ТД (владелец 21.10.2026: «выпуск в фоне автоматом»).

Вызывается в той же единице работы, где ЭТК выдают или пишут её новую ревизию (techcards.application, workflows
etk_refresh), до её commit:
- у карты нет ни одного выпуска — открывается выпуск, изменение 0;
- у карты есть выпуск в работе (проект / на подписи) — он пересъёмывается (на подписи — сперва в проект): второй не
  открывается; автоматический выпуск после пересъёмки снова проходит утверждение, ручной остаётся проектом;
- у карты есть действующий выпуск старше новой ревизии — открывается изменение: номер — следующий, извещение
  «ИИ-<порядковый:05d>-<изменение>», причина — причина ревизии (иначе «Новая ревизия ЭТК N»);
- иначе (все выпуски аннулированы, или действующий уже этой ревизии) — ничего.
Поля (литера, вид ТП, подписанты, цех, участок) — из последнего неаннулированного выпуска любой карты; если выпусков
нет — по умолчанию: подписанты из сотрудников по должности (Разраб. — технолог, Пров. — директор производства,
Н. контр. и Утв. — генеральный директор), литера пустая, вид ТП 85, «Цех электромонтажа», участок ЦАР. Автор — «система».

Затем нормоконтроль: без блокирующих замечаний выпуск сразу проходит «на подписи» → «действует» (прежний действующий
аннулируется в той же транзакции); с блокирующими остаётся проектом — замечания видны в блоке «Комплект ТД».
Сбой автоматического выпуска не ломает выдачу ЭТК и запись ревизии: всё делается в точке сохранения (SAVEPOINT),
ошибка откатывает только её и пишется в лог.

Снимок — карта как её отдаёт GET /api/techcards/projects/{id} (детали по правилам склада, ГРН выданных партий) и
`td_ops` из БД.ОП; читается в той же сессии.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import logging
import re
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentRepository
from app.modules.identity.domain import ROLE_LABELS
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.references.domain import TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyTechOperationRepository
from app.modules.techcards.etk_read import card_views
from app.modules.techcards.project_cards import ProjectTechcard
from app.modules.techcards.td_grades import reference_grades
from app.modules.techcards.td_issues import (
    DEFAULT_WORKSHOP, ROLES, SYSTEM_ACTOR, TdIssue, TdIssueBlocked, TdIssueInput, normcontrol, with_td_ops,
)
from app.workflows.etk_lots import IssuedKitLots
from app.workflows.warehouse_article import infer_article_identity

logger = logging.getLogger(__name__)

DEFAULT_SITE = "ЦАР"
DEFAULT_METHOD = "85"
# Графа основной надписи → должность сотрудника, который её подписывает по умолчанию.
SIGNER_ROLES = {
    "developer": "технолог", "checker": "директор производства",
    "normcontrol": "генеральный директор", "approver": "генеральный директор",
}


@dataclass(frozen=True)
class AutoResult:
    """Что сделал автоматический выпуск: action — created / resnapshot / none; status — статус выпуска после."""

    action: str
    status: str = ""
    change_no: int | None = None
    blocking: tuple[str, ...] = ()

    def text(self) -> str:
        if self.action == "none":
            return "без выпуска"
        head = f"{'выпуск' if self.action == 'created' else 'пересъёмка'} изм. {self.change_no} — {self.status}"
        return head + (f" (блокирует: {', '.join(self.blocking)})" if self.blocking else "")


def _fold(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold().replace("ё", "е")


def staff_signatories(session: Session) -> dict[str, dict[str, str]]:
    """Подписанты по должностям: сначала «Должность» сотрудника (точно, потом содержит), затем старая роль; среди
    равных — по ФИО. Должность в графе — название роли с заглавной. Не нашёлся — ФИО пустое (нормоконтроль блокирует)."""
    users = list(session.scalars(select(IdentityUserRow).where(IdentityUserRow.active.is_(True)).order_by(IdentityUserRow.name)))

    def person(title: str) -> str:
        for matches in (
            lambda user: _fold(user.job_title) == title,
            lambda user: title in _fold(user.job_title),
            lambda user: _fold(ROLE_LABELS.get(user.role, "")) == title,
        ):
            found = next((user for user in users if matches(user)), None)
            if found is not None:
                return str(found.name or "").strip()
        return ""

    signatories = {role: {"name": "", "position": ""} for role in ROLES}
    for role, title in SIGNER_ROLES.items():
        signatories[role] = {"name": person(title), "position": title[:1].upper() + title[1:]}
    for role in ("developer", "checker"):
        if signatories["normcontrol"]["name"] and _fold(signatories["normcontrol"]["name"]) == _fold(signatories[role]["name"]):
            signatories["normcontrol"] = {**signatories["normcontrol"], "name": ""}  # Н.контр. не подписывает за себя
    return signatories


def issue_template(uow: Any, session: Session) -> TdIssueInput:
    """Поля нового автоматического выпуска: из последнего неаннулированного выпуска, иначе по умолчанию."""
    latest: TdIssue | None = uow.td_issues.latest()
    if latest is not None:
        return TdIssueInput(
            litera=latest.litera, method_code=latest.method_code, signatories=latest.signatories, notice_no="",
            reason="", workshop=latest.workshop or DEFAULT_WORKSHOP, site=latest.site or DEFAULT_SITE,
        )
    return TdIssueInput(
        litera="", method_code=DEFAULT_METHOD, signatories=staff_signatories(session), notice_no="", reason="",
        workshop=DEFAULT_WORKSHOP, site=DEFAULT_SITE,
    )


class _SessionFulfillment:
    """Порт выданных партий (IssuedKitLots) поверх уже открытой сессии: не закрывает и не фиксирует её."""

    def __init__(self, session: Session):
        self.fulfillment = SqlAlchemyFulfillmentRepository(session)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def snapshot(uow: Any, session: Session, card: ProjectTechcard) -> dict[str, Any]:
    """Карта ЭТК как её отдаёт GET карты, с `td_ops` из БД.ОП."""
    records = SqlAlchemyTechOperationRepository(session)
    parts = [record for tab in (TechOperationTab.TERMINAL, TechOperationTab.COAX)
             for record in records.list(tab=tab, query="", limit=10_000)]

    def identify(article: str) -> str:
        info = infer_article_identity(article, parts)
        return info.name if info else ""

    view = card_views(uow, [card], identify, IssuedKitLots(lambda: _SessionFulfillment(session)))[0]
    grades = reference_grades(record.fields for record in records.list(tab=TechOperationTab.OPERATION, query="", limit=100_000))
    return with_td_ops(jsonable_encoder(view), grades)


def _approve(uow: Any, issue: TdIssue, now: datetime) -> tuple[TdIssue, tuple[str, ...]]:
    """Нормоконтроль и, если блокирующих нет, «на подписи» → «действует» с аннулированием прежнего действующего."""
    try:
        signing = issue.moved("signing", now)
    except TdIssueBlocked:
        return issue, tuple(dict.fromkeys(item["code"] for item in normcontrol(issue) if item["severity"] == "blocking"))
    uow.td_issues.save(signing, issue.version)
    previous = uow.td_issues.active_for_card(issue.project_card_id)
    if previous is not None and previous.id != issue.id:
        uow.td_issues.save(previous.moved("annulled", now), previous.version)
        uow.td_issues.flush()  # прежний снят до утверждения нового: частичный UNIQUE «один действующий»
    active = signing.moved("active", now)
    uow.td_issues.save(active, signing.version)
    return active, ()


def auto_issue(uow: Any, session: Session, card: ProjectTechcard, reason: str, now: datetime) -> AutoResult:
    """Сам автоматический выпуск в открытой единице работы (без commit); ошибки не глотает — их ловит on_revision."""
    issues: list[TdIssue] = uow.td_issues.for_card(card.id)
    open_issue = next((item for item in issues if item.status in {"draft", "signing"}), None)
    if open_issue is not None:
        current = open_issue
        if current.status == "signing":
            current = current.moved("draft", now)
            uow.td_issues.save(current, open_issue.version)
        updated = current.resnapshot(snapshot(uow, session, card))
        uow.td_issues.save(updated, current.version)
        blocking: tuple[str, ...] = ()
        if updated.created_by == SYSTEM_ACTOR:
            updated, blocking = _approve(uow, updated, now)
        return AutoResult("resnapshot", updated.status, updated.change_no, blocking)
    template = issue_template(uow, session)
    if issues:
        active = uow.td_issues.active_for_card(card.id)
        if active is None or active.etk_revision >= card.revision:
            return AutoResult("none")
        change_no = max(item.change_no for item in issues) + 1
        template = replace(
            template, notice_no=f"ИИ-{active.sequence_no:05d}-{change_no}",
            reason=str(reason or "").strip() or f"Новая ревизия ЭТК {card.revision}",
        )
    issue = TdIssue.open(
        card_id=card.id, value=template, snapshot=snapshot(uow, session, card), existing=issues,
        next_sequence=uow.td_issues.next_sequence(), actor=SYSTEM_ACTOR, now=now,
    )
    uow.td_issues.add(issue)
    uow.td_issues.flush()
    issue, blocking = _approve(uow, issue, now)
    return AutoResult("created", issue.status, issue.change_no, blocking)


def on_revision(uow: Any, card: ProjectTechcard, reason: str, now: datetime) -> AutoResult | None:
    """Автоматический выпуск после выдачи ЭТК или её новой ревизии; никогда не бросает. None — не выпускалось (нет
    сессии и выпусков у единицы работы, карта не выдана или сбой — он в логе)."""
    session = getattr(uow, "session", None)
    if session is None or getattr(uow, "td_issues", None) is None or card.document is None or card.revision <= 0:
        return None
    try:
        with session.begin_nested():
            return auto_issue(uow, session, card, reason, now)
    except Exception:  # noqa: BLE001 — выпуск ТД не должен ломать выдачу ЭТК и запись ревизии
        logger.exception("Автоматический выпуск комплекта ТД: карта %s, ревизия %s", card.id, card.revision)
        return None
