"""«Подбор»: the whole set of choices of a request at once (port of Roller_Buyer routes/decisions.ts batch + shared/rfq-basket.ts).

One reason covers every pick that needs it (the gate, «не самое дешёвое», the analog sign-off); each decision keeps only
the reasons its own line required. Every pick is checked first — the line, the offer, the buyer, the stage, the line's
version, the rules — and nothing is written while one fails. Then the picks are applied one by one, as «Выбрать» does
(the procurement line's facts, then the decision): procurement and purchasing are two transactions, so a failure midway
is reported with exactly which lines got their decision and which did not — never a silent partial state."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.shared.identity import Actor

from . import settings as purchasing_settings
from .infrastructure import DecisionRow, RfqLineRow
from .rfq import (
    Conflict,
    DecisionInput,
    ProcurementLines,
    Refused,
    RfqError,
    UnitOfWork,
    _open,
    _own,
    _rfq,
    _session,
    _today,
    check_pick,
    decide,
)


@dataclass(frozen=True)
class Pick:
    rfq_line_id: UUID
    quote_line_id: UUID
    expected_version: int | None = None


class BatchRefused(Refused):
    """Some picks break a rule: nothing was written."""

    def __init__(self, problems: Sequence[dict]):
        names = "; ".join(f"{problem['name']}: {problem['message']}" for problem in problems)
        super().__init__(f"Подбор не сохранён — {names}")
        self.payload = {"code": "batch_refused", "message": str(self), "lines": list(problems)}


class BatchPartial(Conflict):
    """Applying stopped midway: the decided lines keep their decision, the rest have none."""

    def __init__(self, decided: Sequence[str], failed: dict, not_done: Sequence[str]):
        parts = [f"Выбор записан: {', '.join(decided)}." if decided else "Ни одного выбора не записано."]
        parts.append(f"Не записано: {failed['name']} — {failed['message']}.")
        if not_done:
            parts.append(f"Не дошли: {', '.join(not_done)}.")
        super().__init__(" ".join(parts))
        self.payload = {"code": "batch_partial", "message": str(self), "decided": list(decided), "failed": failed,
                        "not_done": list(not_done)}


def decide_batch(
    make_uow: Callable[[], UnitOfWork], lines: ProcurementLines, actor: Actor, now: datetime, rfq_id: UUID,
    picks: Sequence[Pick], reasons: DecisionInput,
) -> dict:
    """`reasons` — one DecisionInput whose reason fields (codes, notes, analog sign-off) apply to every pick.
    → {decided, kept}; a pick on a line already decided with the same offer is kept as is."""
    if not picks:
        raise Refused("Ничего не выбрано")
    if len({pick.rfq_line_id for pick in picks}) != len(picks):
        raise Refused("Строка запроса указана дважды")
    today = _today(now)
    with make_uow() as uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        values = purchasing_settings.load(session)
        decided = {row.rfq_line_id: row for row in session.scalars(
            select(DecisionRow).where(DecisionRow.rfq_line_id.in_([pick.rfq_line_id for pick in picks])))}
        names = {row.id: row.name for row in session.scalars(
            select(RfqLineRow).where(RfqLineRow.id.in_([pick.rfq_line_id for pick in picks])))}
        procurement_ids = list(session.scalars(
            select(RfqLineRow.procurement_line_id).where(RfqLineRow.id.in_([pick.rfq_line_id for pick in picks]))))
        views = lines.get_many(procurement_ids)
        problems: list[dict] = []
        todo: list[Pick] = []
        kept = 0
        for pick in picks:
            name = names.get(pick.rfq_line_id, str(pick.rfq_line_id))
            before = decided.get(pick.rfq_line_id)
            if before is not None:
                if before.quote_line_id == pick.quote_line_id:
                    kept += 1
                else:
                    problems.append({"rfq_line_id": str(pick.rfq_line_id), "name": name, "message":
                                     "уже выбран другой поставщик — отмените решение по строке или верните выбор в подборе"})
                continue
            data = replace(reasons, rfq_line_id=pick.rfq_line_id, quote_line_id=pick.quote_line_id,
                           expected_version=pick.expected_version)
            try:
                check_pick(session, lines, actor, rfq, data, values, today, views)
            except RfqError as error:
                problems.append({"rfq_line_id": str(pick.rfq_line_id), "name": name, "message": str(error),
                                 "fields": [violation.field for violation in error.violations]})
                continue
            todo.append(pick)
    if problems:
        raise BatchRefused(problems)

    done: list[str] = []
    for index, pick in enumerate(todo):
        data = replace(reasons, rfq_line_id=pick.rfq_line_id, quote_line_id=pick.quote_line_id,
                       expected_version=pick.expected_version)
        try:
            decide(make_uow(), lines, actor, now, rfq_id, data)
        except Exception as error:  # any failure midway is reported with what was written, never swallowed
            message = str(error) if isinstance(error, RfqError) else "сбой записи — обновите запрос и повторите"
            raise BatchPartial(done, {"name": names.get(pick.rfq_line_id, ""), "message": message},
                               [names.get(rest.rfq_line_id, "") for rest in todo[index + 1:]]) from error
        done.append(names.get(pick.rfq_line_id, ""))
    return {"decided": len(done), "kept": kept}
