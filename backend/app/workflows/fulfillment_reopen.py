"""Вернуть заявки на комплектацию, отменённые ручным переносом этапа:
`python -m app.workflows.fulfillment_reopen --request REQ.26.0000062 [--request …] [--apply]`.

Prod 663К.1 (16.11.2026): «Новый → В работе» on КБ7/10/11/13 cancelled their kit requests (the 12.10 rule of
`stage_cascade`, dropped 17.11) and gave the storekeeper's picked lots back to the shelf. This one-off workflow brings
such a request back:

* only a request at «Отменена» whose LAST `fulfillment.cancelled` event came from the stage cascade (details carry
  `stage` and a `reason` «пропущено вручную…»); a main request is refused while its project + КБ has another live
  one (one live kit per КБ — the partial UNIQUE of migration 0089 enforces it in the database too);
* each line gets back the lot it held right before that cancel: the request's audit trail is replayed in time order —
  `lot_bound` binds its `line_id`, `lot_unbound` clears it, an earlier `cancelled` clears all, an earlier `reopened`
  sets its `bound` list. «есть, но не заведено» and «нет на складе» marks are not touched by a cancel, so they stay as
  stored. Lots reserved while a request was CREATED leave no per-line event — those lines wait (reported);
* a line is bound again only when its lot exists and has at least the line's quantity free for this kit now (ДОСТУПНО
  plus the project's own «Со склада» / techcard reserve on the lot — the normal bind path `_reserve_for_kit`, which
  also takes those reserves over first). Otherwise the line waits and is reported; nothing is reserved partly;
* the request goes to «Недоукомплектован» («Новая» if it was never in work), ЗАКРЫТА cleared, version + 1, one
  audit event `fulfillment.reopened`.

Without `--apply` the very same steps run and are rolled back: the printout is what `--apply` would write. Every
request of the run goes in ONE transaction: a refused request, or a request / lot changed meanwhile (version check),
writes nothing at all.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.fulfillment.application import (
    _reserve_for_kit as reserve_for_kit,
    free_for_project,
    kit_key,
    project_reserve_by_lot,
    same_unit,
)
from app.modules.production.plan import KIT_STAGES
from app.modules.fulfillment.domain import (
    FulfillmentKind,
    FulfillmentRequest,
    FulfillmentStatus,
    FulfillmentValidationError,
    LotSnapshot,
    kb_of,
    project_of,
)
from app.modules.fulfillment.infrastructure import FulfillmentRequestRow
from app.modules.warehouse.domain import StockLot, WarehouseValidationError
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow

BOUND = "fulfillment.lot_bound"
UNBOUND = "fulfillment.lot_unbound"
CANCELLED = "fulfillment.cancelled"
REOPENED = "fulfillment.reopened"
CREATED = "fulfillment.created"
CASCADE_REASON = "пропущено вручную"
# Same instant on one request: a reopen comes before anything on it, a removal before a new pick, a cancel last.
_RANK = {REOPENED: 0, UNBOUND: 1, BOUND: 2, CANCELLED: 3}

ACTOR_NAME = "возврат заявок на комплектацию"


class ReopenRefused(RuntimeError):
    """The run writes nothing: a request may not be reopened, or the data changed while writing."""


@dataclass(frozen=True)
class TrailEvent:
    event_type: str
    occurred_at: datetime
    details: dict


@dataclass(frozen=True)
class Wanted:
    """A line's binding right before the cancel, as the trail tells it."""

    line_id: UUID
    lot_id: UUID
    quantity: Decimal
    grn: str = ""
    article: str = ""
    name: str = ""


@dataclass(frozen=True)
class LinePlan:
    line_id: UUID
    article: str
    name: str
    unit: str
    quantity: Decimal
    grn: str
    storage_location: str = ""
    available: Decimal | None = None
    reason: str = ""

    def payload(self) -> dict[str, Any]:
        data = {"line_id": str(self.line_id), "article": self.article, "name": self.name, "unit": self.unit,
                "quantity": _number(self.quantity), "lot_grn": self.grn}
        if self.available is not None:
            data["available"] = _number(self.available)
        if self.reason:
            data["reason"] = self.reason
        return data


@dataclass(frozen=True)
class RequestPlan:
    number: str
    purpose: str = ""
    status_before: str = ""
    status_after: str = ""
    cancelled_at: datetime | None = None
    cancel_reason: str = ""
    bound: tuple[LinePlan, ...] = ()
    skipped: tuple[LinePlan, ...] = ()
    notes: tuple[str, ...] = ()
    refused: str = ""


@dataclass
class RunResult:
    plans: list[RequestPlan] = field(default_factory=list)
    applied: bool = False
    error: str = ""

    @property
    def refused(self) -> bool:
        return bool(self.error) or any(plan.refused for plan in self.plans)


def _number(value: Decimal | int) -> str:
    text = format(Decimal(value).normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def from_cascade(details: dict) -> bool:
    """The cancel came from a manual stage move (`stage_cascade._close`) to a stage BELOW «Готово» — exactly what the
    17.11 rule no longer cancels. A kit the current rule still cancels (a move to «Готово» or higher) is not revived."""
    stage = str(details.get("stage") or "").strip()
    if stage not in KIT_STAGES or KIT_STAGES.index(stage) >= KIT_STAGES.index("Готово"):
        return False
    return str(details.get("reason") or "").startswith(CASCADE_REASON)


def ordered(trail: list[TrailEvent]) -> list[TrailEvent]:
    return sorted(trail, key=lambda event: (event.occurred_at, _RANK.get(event.event_type, 1)))


def last_cancel(trail: list[TrailEvent]) -> TrailEvent | None:
    cancels = [event for event in ordered(trail) if event.event_type == CANCELLED]
    return cancels[-1] if cancels else None


def bindings_before_cancel(trail: list[TrailEvent]) -> dict[UUID, Wanted]:
    """Replay the trail up to its last cancel: line id → the lot and quantity the line held right then."""
    events = ordered(trail)
    stop = max((index for index, event in enumerate(events) if event.event_type == CANCELLED), default=len(events))
    held: dict[UUID, Wanted] = {}
    for event in events[:stop]:
        details = event.details or {}
        if event.event_type == CANCELLED:
            held.clear()
        elif event.event_type == REOPENED:
            held.clear()
            for item in details.get("bound") or ():
                wanted = _wanted(item)
                if wanted is not None:
                    held[wanted.line_id] = wanted
        elif event.event_type == BOUND:
            wanted = _wanted(details)
            if wanted is not None:
                held[wanted.line_id] = wanted
        elif event.event_type == UNBOUND:
            line_id = _uuid(details.get("line_id"))
            if line_id is not None:
                held.pop(line_id, None)
    return held


def _wanted(details: dict) -> Wanted | None:
    line_id, lot_id, quantity = _uuid(details.get("line_id")), _uuid(details.get("lot_id")), _decimal(details.get("quantity"))
    if line_id is None or lot_id is None or quantity is None or quantity <= 0:
        return None
    return Wanted(
        line_id, lot_id, quantity, grn=str(details.get("lot_grn") or ""),
        article=str(details.get("kd_article") or details.get("article") or ""),
        name=str(details.get("position_name") or details.get("name") or ""),
    )


def read_trail(session, request_id: UUID) -> list[TrailEvent]:
    rows = session.scalars(select(AuditEventRow).where(
        AuditEventRow.aggregate_id == request_id,
        AuditEventRow.event_type.in_((BOUND, UNBOUND, CANCELLED, REOPENED, CREATED)),
    ).order_by(AuditEventRow.occurred_at, AuditEventRow.id))
    return [TrailEvent(row.event_type, row.occurred_at, dict(row.details or {})) for row in rows]


def _find(uow, number: str) -> FulfillmentRequest | None:
    request_id = uow.session.scalar(select(FulfillmentRequestRow.id).where(FulfillmentRequestRow.number == number))
    return uow.fulfillment.get(request_id) if request_id is not None else None


def _refuse(request: FulfillmentRequest | None, number: str, reason: str) -> RequestPlan:
    if request is None:
        return RequestPlan(number=number, refused=reason)
    return RequestPlan(number=request.number, purpose=request.purpose, status_before=request.status.value, refused=reason)


def reopen_in(
    number: str, uow, actor: Actor, now: datetime, lots: dict[UUID, StockLot], reopened: set[tuple[str, str]],
) -> RequestPlan:
    """One request inside the caller's transaction (the caller commits or rolls back). A refusal writes nothing for
    this request and is returned in `refused`. ``lots`` — lots already reserved in this run; ``reopened`` — kit keys
    brought back earlier in this run."""
    request = _find(uow, number)
    if request is None:
        return _refuse(None, number, "заявка не найдена")
    if request.status is not FulfillmentStatus.CANCELLED:
        return _refuse(request, number, f"заявка не отменена («{request.status.value}») — возвращать нечего")
    trail = read_trail(uow.session, request.id)
    cancel = last_cancel(trail)
    if cancel is None:
        return _refuse(request, number, "в журнале нет записи об отмене — вернуть нельзя")
    if not from_cascade(cancel.details):
        return _refuse(request, number, "последняя отмена сделана не переносом этапа ниже «Готово» (вручную или "
                                        "другим путём) — вернуть этой командой нельзя")
    if not kb_of(request.purpose):
        # `project_reserve_by_lot` without a КБ counts every КБ's reserve while `_reserve_for_kit` takes none.
        return _refuse(request, number, "в назначении заявки нет КБ — вернуть этой командой нельзя")
    key = kit_key(request.purpose) if request.kind is FulfillmentKind.MAIN else None
    if key is not None:
        live = uow.fulfillment.live_kit_request(*key)
        if live is not None and live.id != request.id:
            return _refuse(request, number, f"у {key[1]} проекта {key[0]} уже есть живая заявка {live.number} "
                                            f"(«{live.status.value}») — две заявки на одну КБ нельзя")
        if key in reopened:
            return _refuse(request, number, f"{key[1]} проекта {key[0]} уже возвращается в этом запуске")

    wanted = bindings_before_cancel(trail)
    lines = {line.id: line for line in request.lines}
    project, kb = project_of(request.purpose), kb_of(request.purpose)
    bindings: dict[UUID, tuple[LotSnapshot, Decimal]] = {}
    bound: list[LinePlan] = []
    skipped: list[LinePlan] = []
    for item in [wanted[line.id] for line in request.lines if line.id in wanted] + [
        item for line_id, item in wanted.items() if line_id not in lines
    ]:
        line = lines.get(item.line_id)
        plan = LinePlan(
            item.line_id, line.article if line else item.article, line.name if line else item.name,
            line.unit if line else "", item.quantity, item.grn,
        )
        if line is None:
            skipped.append(replace(plan, reason="строки уже нет в заявке"))
            continue
        if line.requirement != item.quantity:
            skipped.append(replace(plan, reason=f"потребность строки теперь {_number(line.requirement)}"))
            continue
        if line.off_book or line.lot is not None or line.reserved != 0:
            skipped.append(replace(plan, reason="у строки уже есть отметка или партия"))
            continue
        lot = lots.get(item.lot_id) or uow.warehouse.get(item.lot_id)
        if lot is None:
            skipped.append(replace(plan, reason="партии больше нет на складе"))
            continue
        if not same_unit(lot.unit, line.unit):
            skipped.append(replace(plan, grn=lot.grn, reason=f"у партии теперь единица «{lot.unit}», у строки «{line.unit}»"))
            continue
        free = free_for_project(lot, project_reserve_by_lot(uow, [lot], project, kb))
        plan = replace(plan, grn=lot.grn, storage_location=lot.storage_location, available=free)
        if free < item.quantity:
            skipped.append(replace(plan, reason="свободного остатка меньше, чем было набрано"))
            continue
        given, reserved_lot = reserve_for_kit(request, lot, item.quantity, actor, now, uow)
        if given != item.quantity:  # free_for_project and _reserve_for_kit count the same: never expected
            raise ReopenRefused(f"{request.number}: партия {lot.grn} дала {_number(given)} вместо {_number(item.quantity)}")
        lots[lot.id] = reserved_lot
        uow.warehouse.add(reserved_lot)
        bindings[line.id] = (LotSnapshot(
            lot_id=lot.id, warehouse=lot.warehouse, grn=lot.grn, package=lot.package,
            storage_location=lot.storage_location,
        ), item.quantity)
        bound.append(plan)

    notes = []
    created = next((event for event in ordered(trail) if event.event_type == CREATED), None)
    if created is not None and int(_decimal(created.details.get("reserved")) or 0) > 0:
        notes.append(
            f"при создании заявки партии выбраны для {_number(_decimal(created.details.get('reserved')) or 0)} поз. — "
            "по строкам это в журнал не пишется: такие позиции ждут, наберите их заново"
        )
    updated = request.reopen(bindings)
    uow.fulfillment.add(updated)
    plan = RequestPlan(
        number=request.number, purpose=request.purpose, status_before=request.status.value,
        status_after=updated.status.value, cancelled_at=cancel.occurred_at,
        cancel_reason=str(cancel.details.get("reason") or ""), bound=tuple(bound), skipped=tuple(skipped),
        notes=tuple(notes),
    )
    uow.audit.add(AuditEvent(
        id=uuid.uuid4(), aggregate_id=updated.id, event_type=REOPENED, actor=actor, occurred_at=now,
        details={
            "by": "workflow", "workflow": "fulfillment_reopen", "previous_cancel_at": cancel.occurred_at.isoformat(),
            "previous_cancel_reason": plan.cancel_reason, "status": updated.status.value,
            "bound": [
                {**line.payload(), "lot_id": str(bindings[line.line_id][0].lot_id)} for line in bound
            ],
            "skipped": [line.payload() for line in skipped],
        },
    ))
    if key is not None:
        reopened.add(key)
    return plan


def run(numbers: list[str], uow_factory, actor: Actor, now: datetime, *, apply: bool) -> RunResult:
    """Every request in ONE transaction; without ``apply`` (or with any refusal) it is rolled back."""
    result = RunResult()
    numbers = list(dict.fromkeys(str(number or "").strip() for number in numbers if str(number or "").strip()))
    if not numbers:
        result.error = "не указано ни одной заявки (--request REQ.26.0000062)"
        return result
    try:
        with uow_factory() as uow:
            lots: dict[UUID, StockLot] = {}
            reopened: set[tuple[str, str]] = set()
            for number in numbers:
                result.plans.append(reopen_in(number, uow, actor, now, lots, reopened))
            if apply and not result.refused:
                uow.commit()
                result.applied = True
    except (StaleDataError, IntegrityError) as error:
        result.error = f"заявка или партия изменилась во время записи — ничего не записано, повторите ({error.__class__.__name__})"
        result.applied = False
    except (ReopenRefused, FulfillmentValidationError, WarehouseValidationError) as error:
        result.error = f"ничего не записано: {error}"
        result.applied = False
    return result


def format_result(result: RunResult, *, apply: bool) -> str:
    out: list[str] = []
    for plan in result.plans:
        head = f"{plan.number}" + (f" ({plan.purpose})" if plan.purpose else "")
        if plan.refused:
            out.append(f"{head}: ОТКАЗ — {plan.refused}")
            continue
        when = plan.cancelled_at.strftime("%d.%m.%Y %H:%M") if plan.cancelled_at else "—"
        out.append(f"{head}: {plan.status_before} → {plan.status_after}; отменена {when} ({plan.cancel_reason})")
        for line in plan.bound:
            out.append(
                f"  привязать: {line.article or '—'} · {line.name} · {_number(line.quantity)} {line.unit} · "
                f"ГРН {line.grn} · место {line.storage_location or '—'} · доступно сейчас {_number(line.available or 0)}"
            )
        for line in plan.skipped:
            available = f" · доступно сейчас {_number(line.available)}" if line.available is not None else ""
            out.append(
                f"  НЕ привязать: {line.article or '—'} · {line.name} · нужно {_number(line.quantity)} {line.unit} · "
                f"ГРН {line.grn or '—'}{available} — {line.reason}"
            )
        if not plan.bound and not plan.skipped:
            out.append("  партий до отмены не было — все позиции ждут")
        out.extend(f"  примечание: {note}" for note in plan.notes)
    if result.error:
        out.append(f"ОШИБКА: {result.error}")
    if result.applied:
        out.append("applied: записано.")
    elif result.refused:
        out.append("Ничего не записано: исправьте отказы (или уберите эти заявки из команды) и повторите.")
    elif not apply:
        out.append("Проверка без записи. Записать — та же команда с --apply.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Вернуть заявки на комплектацию, отменённые ручным переносом этапа: статус и набранные партии "
                    "(если свободный остаток есть). Без --apply только показывает.",
    )
    parser.add_argument("--request", action="append", default=[], required=True, help="№ заявки (можно несколько раз)")
    parser.add_argument("--apply", action="store_true", help="записать (без флага — проверка без записи)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from types import SimpleNamespace

    from app.database import configure_database
    from app.shared.clock import moscow_now

    holder = SimpleNamespace(state=SimpleNamespace())
    engine = configure_database(holder, url)
    actor = Actor(id=uuid.uuid4(), name=ACTOR_NAME, department="")
    try:
        result = run(args.request, holder.state.fulfillment_uow_factory, actor, moscow_now(), apply=args.apply)
    finally:
        engine.dispose()
    print(format_result(result, apply=args.apply))
    return 1 if result.refused else 0


if __name__ == "__main__":
    sys.exit(main())
