"""A manual forward stage move closes the work it skips (owner 12.10.2026: «почему всё остаётся как есть, если логика
ручного как раз в том, чтобы скипнуть всё»).

Relative to the stage a КБ is moved to:

* to «В работе» or higher — the КБ's kit requests (main and «Довыдача») still at «Новая» / «Недоукомплектован» are
  cancelled: their lines give РЕЗЕРВ back to the lots; the КБ's active techcard reserves are released too;
* to «ОТК» or higher (production over) — the КБ's shop tasks at «Запущено» with no work session open are cancelled.

Picked kits («Укомплектован», «Готов к выдаче»), issued kits, tasks someone is working on and finished goods stay —
the preview lists them with the reason. Stock quantities never move: only reserve fields are released. A backward
move or «авто» closes nothing and reopens nothing.

The preview carries a token (the project's journal sequence, the plan and the ids/versions of what would close); the
apply re-reads everything in ONE shared session, refuses a stale token with 409 and writes the journal rows, the
cancellations and the reserve releases in one transaction (precedent: `procurement_stock`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.modules.fulfillment.application import cancel_request_in, release_kit_reserves
from app.modules.fulfillment.domain import (
    FulfillmentKind,
    FulfillmentRequest,
    FulfillmentStatus,
    FulfillmentValidationError,
    KitReserve,
    item_index_key,
    kb_of,
)
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentRepository, SqlAlchemyKitReserveRepository
from app.modules.production.application import cancel_task_in
from app.modules.production.domain import ProductionStatus, ProductionTask, ProductionValidationError
from app.modules.production.infrastructure import (
    ProductionWorkSessionRow,
    SqlAlchemyPlanEventRepository,
    SqlAlchemyPlanProjectRepository,
    SqlAlchemyProductionRepository,
)
from app.modules.production.plan import (
    KIT_STAGES,
    STAGE_AUTO,
    STAGE_BACK,
    STAGE_FORWARD,
    StagePlan,
    kit_label,
    normalize_project_number,
)
from app.modules.production.plan_application import (
    ChangeStageCommand,
    StageChangeConflict,
    change_stage,
    write_stage_change,
)
from app.modules.production.traceability import WorkStatus
from app.modules.shipping.domain import FinishedGood
from app.modules.shipping.infrastructure import SqlAlchemyFinishedGoodsRepository
from app.modules.warehouse.domain import WarehouseValidationError
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseRepository
from app.shared.persistence import SqlAlchemyAuditRepository
from app.shared.project_number import project_from_purpose

WORK_STAGE = "В работе"
# Лестница 12.10: производство кончается на «ОТК» — с него простаивающие задания цеха закрываются (было «КБ на складе»).
QC_STAGE = "ОТК"
CASCADE_CONFLICT = "Этапы проекта или связанные задания изменились — обновите и повторите"
BACK_NOTE = "Назад ничего не открывается"

CLOSE_TASK = "task"
CLOSE_KIT_REQUEST = "kit_request"
CLOSE_RESERVE = "reserve"
KEEP_GOODS = "goods"

_OPEN_REQUESTS = {FulfillmentStatus.NEW, FulfillmentStatus.UNDERFILLED}
_CLOSED_REQUESTS = {FulfillmentStatus.RETURNED, FulfillmentStatus.DONE, FulfillmentStatus.CANCELLED}
_OPEN_SESSIONS = (WorkStatus.ACTIVE.value, WorkStatus.PAUSED.value)


class StageCascadeError(ProductionValidationError):
    """A closing step the domain refused (a lot holds less РЕЗЕРВ than the kit line says): nothing is written."""


@dataclass(frozen=True)
class CascadeFacts:
    """What a forward move may close or must keep, for one project (other projects' items are ignored)."""

    tasks: tuple[ProductionTask, ...] = ()
    # Tasks with a work session in progress (active or paused).
    busy: frozenset[UUID] = frozenset()
    requests: tuple[FulfillmentRequest, ...] = ()
    reserves: tuple[KitReserve, ...] = ()
    goods: tuple[FinishedGood, ...] = ()


@dataclass(frozen=True)
class CloseItem:
    kind: str
    id: UUID
    version: int
    label: str
    qty: Decimal
    unit: str

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": str(self.id), "label": self.label, "qty": _number(self.qty), "unit": self.unit}


@dataclass(frozen=True)
class KeepItem:
    kind: str
    label: str
    reason: str

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "reason": self.reason}


@dataclass(frozen=True)
class KbCascade:
    item_index: str
    to_stage: str
    # «пропущено вручную: …» — into every audit event and the journal row of the КБ.
    reason: str
    close: tuple[CloseItem, ...] = ()
    keep: tuple[KeepItem, ...] = ()

    def totals(self) -> dict[str, int]:
        return _totals(self.close, self.keep)

    def warnings(self) -> list[str]:
        """The КБ's lines of the old `warnings` field: what closes (counted) and what stays (with the reason)."""
        texts = []
        counted = self.totals()
        parts = [
            f"{title} — {counted[key]}" for key, title in (
                ("tasks", "заданий цеха"), ("kit_requests", "заявок на комплектацию"), ("reserves", "резервов техкарты"),
            ) if counted[key]
        ]
        if parts:
            texts.append(f"Закроется ({self.reason}): " + ", ".join(parts) + ".")
        texts.extend(f"{item.label}: {item.reason}" for item in self.keep)
        return texts

    def journal_note(self) -> str:
        """The tail of the КБ's «Этап КБ» row: what the move closed."""
        if not self.close:
            return ""
        return f" · {self.reason} — закрыто: " + "; ".join(item.label for item in self.close)


@dataclass(frozen=True)
class StageCascade:
    kbs: tuple[KbCascade, ...] = ()
    # «Назад ничего не открывается» for a backward move or «авто».
    note: str = ""

    @property
    def close(self) -> tuple[CloseItem, ...]:
        return tuple(item for kb in self.kbs for item in kb.close)

    @property
    def keep(self) -> tuple[KeepItem, ...]:
        return tuple(item for kb in self.kbs for item in kb.keep)

    def of(self, item_index: str) -> KbCascade | None:
        return next((kb for kb in self.kbs if kb.item_index == item_index), None)

    def totals(self) -> dict[str, int]:
        return _totals(self.close, self.keep)


def _totals(close: Iterable[CloseItem], keep: Iterable[KeepItem]) -> dict[str, int]:
    close = tuple(close)
    return {
        "tasks": sum(1 for item in close if item.kind == CLOSE_TASK),
        "kit_requests": sum(1 for item in close if item.kind == CLOSE_KIT_REQUEST),
        "reserves": sum(1 for item in close if item.kind == CLOSE_RESERVE),
        "close": len(close),
        "keep": len(tuple(keep)),
    }


def _number(value: Decimal | int) -> str:
    text = format(Decimal(value).normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _quantity(value: Decimal, unit: str) -> str:
    return f"{_number(value)} {str(unit or '').strip()}".rstrip()


def skip_reason(reason: str, from_stage: str, to_stage: str) -> str:
    """«пропущено вручную: <reason>» — the person's reason, or the move itself when none was given."""
    return f"пропущено вручную: {str(reason or '').strip() or f'{from_stage} → {to_stage}'}"


def _task_label(task: ProductionTask) -> str:
    return f"Задание {task.number}" + (f" · {task.name}" if str(task.name or "").strip() else "")


def _request_label(request: FulfillmentRequest) -> str:
    extra = " (довыдача)" if request.kind is FulfillmentKind.EXTRA else ""
    return f"Заявка на комплектацию {request.number}{extra}"


def _reserve_label(reserve: KitReserve) -> str:
    where = " · ".join(part for part in (reserve.grn, reserve.package) if str(part or "").strip())
    return f"Резерв техкарты {reserve.article}" + (f" · {where}" if where else "")


def plan_cascade(project: str, plan: StagePlan, facts: CascadeFacts, reason: str = "") -> StageCascade:
    """What the planned move closes and keeps, per КБ moved forward. Pure: the preview and the apply share it."""
    if plan.direction in (STAGE_BACK, STAGE_AUTO):
        return StageCascade(note=BACK_NOTE)
    if plan.direction != STAGE_FORWARD:
        return StageCascade()
    canon = normalize_project_number(project)
    kbs = []
    for change in plan.changes:
        if change.to_stage not in KIT_STAGES or change.from_stage not in KIT_STAGES:
            continue
        target = KIT_STAGES.index(change.to_stage)
        if target <= KIT_STAGES.index(change.from_stage):
            continue
        whole = not change.item_index  # a project without KBs takes everything of the project
        kb = item_index_key(change.item_index)

        def owns(item_project: str, item_index: str) -> bool:
            return normalize_project_number(item_project) == canon and (whole or item_index_key(item_index) == kb)

        close: list[CloseItem] = []
        keep: list[KeepItem] = []
        at_work = target >= KIT_STAGES.index(WORK_STAGE)
        at_qc = target >= KIT_STAGES.index(QC_STAGE)
        for request in facts.requests:
            if request.status in _CLOSED_REQUESTS or not owns(project_from_purpose(request.purpose), kb_of(request.purpose)):
                continue
            label = _request_label(request)
            if request.status is FulfillmentStatus.ISSUED or any(line.issued > 0 for line in request.lines):
                keep.append(KeepItem(CLOSE_KIT_REQUEST, label, "КБ уже выдана в цех — заявка остаётся выданной (не мешает переносу)."))
            elif request.status is FulfillmentStatus.READY:
                keep.append(KeepItem(CLOSE_KIT_REQUEST, label, "Комплект собран и лежит в зоне выдачи — остаётся, не мешает переносу."))
            elif request.status is FulfillmentStatus.COMPLETE:
                keep.append(KeepItem(CLOSE_KIT_REQUEST, label, "Комплект собран («Укомплектован») — остаётся, не мешает переносу."))
            elif request.status in _OPEN_REQUESTS and at_work:
                positions = sum(1 for line in request.lines if line.lot is not None and line.reserved > 0)
                close.append(CloseItem(
                    CLOSE_KIT_REQUEST, request.id, request.version,
                    label + (f" · снимается резерв с {positions} поз." if positions else ""),
                    Decimal(len(request.lines)), "поз.",
                ))
            elif request.status in _OPEN_REQUESTS:
                keep.append(KeepItem(CLOSE_KIT_REQUEST, label, f"Этап ниже «{WORK_STAGE}» — заявка остаётся."))
        if at_work:
            for reserve in facts.reserves:
                if reserve.active and owns(reserve.project, reserve.item_index):
                    close.append(CloseItem(
                        CLOSE_RESERVE, reserve.id, reserve.version, _reserve_label(reserve), reserve.quantity,
                        reserve.unit,
                    ))
        for task in facts.tasks:
            if task.status is not ProductionStatus.RUNNING or not owns(task.project, task.item):
                continue
            label = _task_label(task)
            if not at_qc:
                keep.append(KeepItem(CLOSE_TASK, label, f"Этап ниже «{QC_STAGE}» — задание остаётся."))
            elif task.id in facts.busy:
                keep.append(KeepItem(CLOSE_TASK, label, "По заданию идёт работа (смена открыта) — остаётся, не мешает переносу."))
            else:
                close.append(CloseItem(CLOSE_TASK, task.id, task.version, label, Decimal(task.quantity), "шт"))
        for good in facts.goods:
            if not owns(good.project, good.item_index):
                continue
            label = f"Готовая продукция {good.name or good.article}".rstrip()
            waiting = good.accepted - good.sent
            if waiting > 0:
                keep.append(KeepItem(
                    KEEP_GOODS, label,
                    f"Принято на склад ГП, не отгружено: {_quantity(waiting, good.unit)} — этап не отгружает, переносу не мешает.",
                ))
            if good.sent > 0:
                keep.append(KeepItem(
                    KEEP_GOODS, label, f"Отгружено {_quantity(good.sent, good.unit)} — отгрузки остаются, переносу не мешают.",
                ))
        kbs.append(KbCascade(
            change.item_index, change.to_stage, skip_reason(reason, change.from_stage, change.to_stage),
            tuple(close), tuple(keep),
        ))
    return StageCascade(tuple(kbs))


def cascade_token(project: str, sequence: int, plan: StagePlan, cascade: StageCascade) -> str:
    """The version the preview was shown at: journal sequence, the plan, the ids/versions of what would close."""
    payload = {
        "project": normalize_project_number(project), "sequence": sequence, "value": plan.value, "kb": plan.kb,
        "changes": [[change.item_index, change.from_stage, change.to_stage] for change in plan.changes],
        "close": sorted([item.kind, str(item.id), item.version] for item in cascade.close),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def cascade_payload(cascade: StageCascade) -> dict[str, Any]:
    return {
        "close": [item.payload() for item in cascade.close],
        "keep": [item.payload() for item in cascade.keep],
        "totals": cascade.totals(),
        "cascade_note": cascade.note,
    }


def kb_payload(cascade: StageCascade, item_index: str) -> dict[str, Any]:
    kb = cascade.of(item_index)
    if kb is None:
        return {"close": [], "keep": [], "totals": _totals((), ())}
    return {"close": [item.payload() for item in kb.close], "keep": [item.payload() for item in kb.keep],
            "totals": kb.totals()}


# ── One session for the whole move ──────────────────────────────────────────────────────────────────────────────


class SqlAlchemyStageCascadeUnitOfWork:
    """The production journal, shop tasks, kit requests, warehouse lots, techcard reserves and finished goods over ONE
    SQLAlchemy session: the stage rows and everything they close commit or roll back together."""

    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self) -> SqlAlchemyStageCascadeUnitOfWork:
        self.committed = False
        self.session = self.session_factory()
        self.plan_events = SqlAlchemyPlanEventRepository(self.session)
        self.plan_projects = SqlAlchemyPlanProjectRepository(self.session)
        self.production = SqlAlchemyProductionRepository(self.session)
        self.fulfillment = SqlAlchemyFulfillmentRepository(self.session)
        self.warehouse = SqlAlchemyWarehouseRepository(self.session)
        self.kit_reserves = SqlAlchemyKitReserveRepository(self.session)
        self.goods = SqlAlchemyFinishedGoodsRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None:
            return
        if exc_type is not None or not self.committed:
            self.session.rollback()
        self.session.close()

    def busy_tasks(self, task_ids: Iterable[UUID]) -> frozenset[UUID]:
        ids = tuple(task_ids)
        if not ids or self.session is None:
            return frozenset()
        return frozenset(self.session.scalars(select(ProductionWorkSessionRow.task_id).where(
            ProductionWorkSessionRow.task_id.in_(ids), ProductionWorkSessionRow.status.in_(_OPEN_SESSIONS),
        )))

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of Work has not been entered")
        try:
            self.session.commit()
        except StaleDataError as error:  # a version-guarded row (task, request, lot, reserve) changed meanwhile
            raise StageChangeConflict(CASCADE_CONFLICT) from error
        self.committed = True


def cascade_uow_factory(production_uow_factory) -> Callable[[], SqlAlchemyStageCascadeUnitOfWork] | None:
    """The shared-session unit of work over the same database as the production module; None when production is
    not a SQLAlchemy unit of work (then a stage change writes journal rows only, as before)."""
    if production_uow_factory is None:
        return None
    sessions = getattr(production_uow_factory(), "session_factory", None)
    if not callable(sessions):
        return None
    return lambda: SqlAlchemyStageCascadeUnitOfWork(sessions)


def read_cascade_facts(uow, project: str, *, names: Iterable[str] = ()) -> CascadeFacts:
    """The project's running tasks (and which of them are being worked on), kit requests, active techcard reserves
    and finished goods. ``names`` — other spellings of the project its techcard reserves may be stored under."""
    canon = normalize_project_number(project)
    tasks = tuple(task for task in uow.production.for_project(canon) if task.status is ProductionStatus.RUNNING)
    reserves: dict[UUID, KitReserve] = {}
    for name in dict.fromkeys((canon, str(project or "").strip(), *(str(item or "").strip() for item in names))):
        if not name:
            continue
        for reserve in uow.kit_reserves.for_project(name):
            if reserve.active and normalize_project_number(reserve.project) == canon:
                reserves.setdefault(reserve.id, reserve)
    return CascadeFacts(
        tasks=tasks,
        busy=uow.busy_tasks(task.id for task in tasks),
        requests=tuple(uow.fulfillment.for_project(canon)),
        reserves=tuple(reserves.values()),
        goods=tuple(uow.goods.for_project(canon)),
    )


def load_cascade_facts(factory, project: str) -> CascadeFacts | None:
    if factory is None:
        return None
    with factory() as uow:
        return read_cascade_facts(uow, project)


def apply_stage_change(
    command: ChangeStageCommand, uow: SqlAlchemyStageCascadeUnitOfWork, *, preview_token: str = "",
) -> tuple[tuple, StageCascade]:
    """Writes the stage rows and closes what the move skips, in ONE transaction. A stale ``preview_token`` (or a
    journal that moved since ``command.expected_sequence``) refuses everything with `StageChangeConflict`; a change
    with nothing left to do (a repeated apply) writes nothing and is not refused. → (journal rows, what closed)."""
    plan = command.plan
    if not plan.changes and not str(command.reopen_from or "").strip():
        return change_stage(command, uow), StageCascade(note=BACK_NOTE if plan.direction in (STAGE_BACK, STAGE_AUTO) else "")
    project = normalize_project_number(command.project)
    with uow:
        sequence = uow.plan_events.last_sequence(project)
        if command.expected_sequence is not None and sequence != command.expected_sequence:
            raise StageChangeConflict(CASCADE_CONFLICT)
        facts = read_cascade_facts(uow, command.project)
        cascade = plan_cascade(project, plan, facts, command.reason)
        if preview_token and cascade_token(project, sequence, plan, cascade) != preview_token:
            raise StageChangeConflict(CASCADE_CONFLICT)
        events = write_stage_change(replace(
            command,
            notes=tuple((kb.item_index, kb.journal_note()) for kb in cascade.kbs if kb.close),
            closed=tuple((kb.item_index, tuple(item.label for item in kb.close)) for kb in cascade.kbs if kb.close),
        ), uow)
        try:
            _close(cascade, facts, project, command, uow)
        except (FulfillmentValidationError, WarehouseValidationError) as error:
            raise StageCascadeError(f"Не удалось закрыть пропущенное: {error}") from error
        except StaleDataError as error:  # a lot changed between this transaction's read and its write
            raise StageChangeConflict(CASCADE_CONFLICT) from error
        uow.commit()
    return events, cascade


def _close(cascade: StageCascade, facts: CascadeFacts, project: str, command: ChangeStageCommand, uow) -> None:
    """Kit requests first (their lines' РЕЗЕРВ), then techcard reserves, then tasks; ``lots`` carries a lot saved in
    this transaction to the next release on it."""
    requests = {request.id: request for request in facts.requests}
    reserves = {reserve.id: reserve for reserve in facts.reserves}
    tasks = {task.id: task for task in facts.tasks}
    lots: dict = {}
    for kb in cascade.kbs:
        details = {"project": project, "kb": kit_label(kb.item_index), "stage": kb.to_stage, "reason": kb.reason}
        for item in kb.close:
            if item.kind == CLOSE_KIT_REQUEST:
                cancel_request_in(requests[item.id], command.actor, command.now, uow, details, lots)
        release_kit_reserves(
            [reserves[item.id] for item in kb.close if item.kind == CLOSE_RESERVE],
            command.actor, command.now, uow, lots, reason=kb.reason,
        )
        for item in kb.close:
            if item.kind == CLOSE_TASK:
                cancel_task_in(tasks[item.id], command.actor, command.now, uow, details)
