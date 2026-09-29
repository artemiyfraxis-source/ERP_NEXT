"""Pure rules for reserving and issuing a complete production kit."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4


class FulfillmentValidationError(ValueError):
    """A fulfillment transition would break stock traceability."""


class FulfillmentStatus(str, Enum):
    NEW = "Новая"
    UNDERFILLED = "Недоукомплектован"
    COMPLETE = "Укомплектован"
    READY = "Готов к выдаче"
    ISSUED = "КБ Выдана"
    RETURNED = "КБ Возвращена"
    DONE = "Готово"
    CANCELLED = "Отменена"


def project_of(purpose: str) -> str:
    """SUPPLYMAP issueProjectOf_: the project is the first part of НАЗНАЧЕНИЕ «728К.2 · КБ1 · …»; a manual request
    without « · » has no project → ''."""
    parts = str(purpose or "").split(" · ")
    return parts[0].strip() if len(parts) > 1 else ""


def _positive(value: Decimal, label: str) -> Decimal:
    normalized = Decimal(value)
    if not normalized.is_finite() or normalized <= 0:
        raise FulfillmentValidationError(f"{label} должно быть положительным числом")
    return normalized


def _nonnegative(value: Decimal, label: str) -> Decimal:
    normalized = Decimal(value)
    if not normalized.is_finite() or normalized < 0:
        raise FulfillmentValidationError(f"{label} не может быть отрицательным")
    return normalized


@dataclass(frozen=True)
class LotSnapshot:
    lot_id: UUID
    warehouse: str
    grn: str
    package: str
    storage_location: str

    def __post_init__(self) -> None:
        if not str(self.warehouse).strip() or not str(self.grn).strip():
            raise FulfillmentValidationError("Для резерва обязательны склад и ГРН")


@dataclass(frozen=True)
class FulfillmentLine:
    id: UUID
    requirement_id: UUID
    article: str
    name: str
    unit: str
    requirement: Decimal
    reserved: Decimal = Decimal("0")
    issued: Decimal = Decimal("0")
    lot: LotSnapshot | None = None
    returned_good: Decimal = Decimal("0")
    defective_returned: Decimal = Decimal("0")
    defective_missing: Decimal = Decimal("0")

    @classmethod
    def create(
        cls,
        *,
        article: str,
        name: str,
        unit: str,
        requirement: Decimal,
    ) -> FulfillmentLine:
        line_id = uuid4()
        if not str(name).strip() or not str(unit).strip():
            raise FulfillmentValidationError("Наименование и единица позиции обязательны")
        return cls(
            id=line_id,
            requirement_id=line_id,
            article=str(article or "").strip(),
            name=str(name).strip(),
            unit=str(unit).strip(),
            requirement=_positive(requirement, "Потребность"),
        )

    @property
    def covered(self) -> bool:
        return bool(self.lot and self.reserved == self.requirement and self.issued == 0)

    def bind(
        self,
        lot: LotSnapshot,
        quantity: Decimal,
    ) -> tuple[FulfillmentLine, FulfillmentLine | None]:
        if self.issued > 0:
            raise FulfillmentValidationError("Выданную позицию нельзя перепривязать")
        if self.lot is not None or self.reserved > 0:
            raise FulfillmentValidationError("Сначала снимите прежнюю привязку партии")
        amount = _positive(quantity, "Резерв")
        if amount > self.requirement:
            raise FulfillmentValidationError("Резерв не может превышать потребность")
        bound = replace(self, requirement=amount, reserved=amount, lot=lot)
        remainder = None
        if amount < self.requirement:
            remainder = replace(
                self,
                id=uuid4(),
                requirement=self.requirement - amount,
                reserved=Decimal("0"),
                lot=None,
            )
        return bound, remainder

    def clear_binding(self) -> FulfillmentLine:
        if self.issued > 0:
            raise FulfillmentValidationError("Выданную позицию нельзя перепривязать")
        return replace(self, reserved=Decimal("0"), lot=None)

    def mark_issued(self) -> FulfillmentLine:
        if not self.covered:
            raise FulfillmentValidationError("Нельзя выдать неполностью зарезервированную позицию")
        return replace(self, issued=self.reserved, reserved=Decimal("0"))

    def settle_return(
        self,
        returned_good: Decimal,
        defective_returned: Decimal,
        defective_missing: Decimal,
    ) -> FulfillmentLine:
        if self.issued <= 0:
            raise FulfillmentValidationError("Невыданную позицию нельзя принимать обратно")
        good = _nonnegative(returned_good, "Возвращённое количество")
        defect_back = _nonnegative(defective_returned, "Возвращённый брак")
        defect_missing = _nonnegative(defective_missing, "Невозвращённый брак")
        if good + defect_back + defect_missing > self.issued:
            raise FulfillmentValidationError(
                "Возврат и брак не могут превышать выданное количество"
            )
        return replace(
            self,
            returned_good=good,
            defective_returned=defect_back,
            defective_missing=defect_missing,
        )

    @property
    def consumed(self) -> Decimal:
        return self.issued - self.returned_good - self.defective_returned - self.defective_missing


@dataclass(frozen=True)
class FulfillmentRequest:
    id: UUID
    number: str
    source_idempotency_key: str
    source_fingerprint: str
    version: int
    created_at: datetime
    requester: str
    purpose: str
    deadline: date | None
    status: FulfillmentStatus
    lines: tuple[FulfillmentLine, ...]
    handout_location: str | None = None
    return_location: str | None = None
    # ЗАЯВИТЕЛЬ as a user: «готова к получению» goes to this id; None = the name matched nobody.
    requester_id: UUID | None = None
    # FF-4 (SUPPLYMAP ISSUE_SHEET В РАБОТУ / НАБРАНА / ВЫДАНА / ВОЗВРАЩЕНА / ЗАКРЫТА): the first time the request
    # reached the stage; set once by `stamped`, never moved back (issueStatusSweep_ writes only an empty cell).
    at_work: datetime | None = None
    at_picked: datetime | None = None
    at_issued: datetime | None = None
    at_returned: datetime | None = None
    at_closed: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        number: str,
        source_idempotency_key: str,
        source_fingerprint: str,
        requester: str,
        purpose: str,
        deadline: date | None,
        lines: tuple[FulfillmentLine, ...],
        created_at: datetime,
        requester_id: UUID | None = None,
    ) -> FulfillmentRequest:
        # FF-1: Issue.gs:121, 919 — a request without a deadline is refused.
        if deadline is None or not str(deadline).strip():
            raise FulfillmentValidationError("Укажи дедлайн.")
        if not lines:
            raise FulfillmentValidationError("Для сборки без состава заявка не создаётся")
        if len({line.id for line in lines}) != len(lines):
            raise FulfillmentValidationError("Идентификаторы позиций заявки должны быть уникальны")
        if len({line.requirement_id for line in lines}) != len(lines):
            raise FulfillmentValidationError("Идентификаторы потребностей заявки должны быть уникальны")
        if any(
            line.id != line.requirement_id
            or line.lot is not None
            or line.reserved != 0
            or line.issued != 0
            for line in lines
        ):
            raise FulfillmentValidationError("Новая заявка принимает только незарезервированные позиции")
        if (
            not str(number).strip()
            or not str(source_idempotency_key).strip()
            or not str(source_fingerprint).strip()
            or not str(requester).strip()
            or not str(purpose).strip()
        ):
            raise FulfillmentValidationError("Номер, заявитель и назначение обязательны")
        return cls(
            id=uuid4(),
            number=str(number).strip(),
            source_idempotency_key=str(source_idempotency_key).strip(),
            source_fingerprint=str(source_fingerprint).strip(),
            version=1,
            created_at=created_at,
            requester=str(requester).strip(),
            purpose=str(purpose).strip(),
            deadline=deadline,
            status=FulfillmentStatus.NEW,
            lines=lines,
            requester_id=requester_id,
        )

    @property
    def fully_reserved(self) -> bool:
        return bool(self.lines) and all(line.covered for line in self.lines)

    def bind_line(
        self,
        line_id: UUID,
        lot: LotSnapshot,
        quantity: Decimal,
    ) -> FulfillmentRequest:
        self._require_editable()
        updated: list[FulfillmentLine] = []
        found = False
        for line in self.lines:
            if line.id != line_id:
                updated.append(line)
                continue
            found = True
            bound, remainder = line.bind(lot, quantity)
            updated.append(bound)
            if remainder is not None:
                updated.append(remainder)
        if not found:
            raise FulfillmentValidationError("Позиция заявки не найдена")
        # FF-3 (Issue.gs issueHeadSync_): the first bound lot moves «Новая» → «Недоукомплектован» and nothing else;
        # «Укомплектован» is a separate manual step (`mark_complete`) with the whole-kit gate.
        status = FulfillmentStatus.UNDERFILLED if self.status is FulfillmentStatus.NEW else self.status
        return replace(self, lines=tuple(updated), status=status, version=self.version + 1)

    def open_lines(self) -> tuple[FulfillmentLine, ...]:
        """Lines a lot does not close yet (issueStatusSweep_: КОЛ-ВО < ПОТРЕБНОСТЬ)."""
        return tuple(line for line in self.lines if not line.covered)

    def mark_complete(self) -> FulfillmentRequest:
        """«Укомплектован» by hand (ISS_MODES_.pick.next: from «Новая» and «Недоукомплектован»). The gate is
        issueStatusSweep_'s: while a line is not closed by a lot the status would roll back — refuse instead."""
        if self.status not in {FulfillmentStatus.NEW, FulfillmentStatus.UNDERFILLED}:
            raise FulfillmentValidationError(f"Из статуса «{self.status.value}» в «Укомплектован» не переводят")
        waiting = self.open_lines()
        if waiting:
            names = ", ".join(line.name for line in waiting[:3])
            raise FulfillmentValidationError(
                f"Комплект не собран: не закрыто позиций — {len(waiting)} ({names})"
            )
        return replace(self, status=FulfillmentStatus.COMPLETE, version=self.version + 1)

    def close_issued(self) -> FulfillmentRequest:
        """«Готово» straight from «КБ Выдана» (ISS_MODES_.give.next): the kit is used up, nothing comes back.
        Issue.gs sweep only stamps ЗАКРЫТА; every issued quantity stays consumed."""
        if self.status is not FulfillmentStatus.ISSUED:
            raise FulfillmentValidationError("Закрыть без возврата можно только выданный комплект")
        return replace(self, status=FulfillmentStatus.DONE, version=self.version + 1)

    def stamped(self, now: datetime) -> FulfillmentRequest:
        """FF-4: stage dates by the first hit (issueStatusSweep_ 431-493, issueHeadSync_): В РАБОТУ at
        «Недоукомплектован»/«Укомплектован» (and at «Готов к выдаче» if still empty), НАБРАНА at «Готов к выдаче»,
        ВЫДАНА, ВОЗВРАЩЕНА, ЗАКРЫТА at «Готово»/«Отменена». A filled date is never overwritten."""
        status = self.status
        changes: dict[str, datetime] = {}
        if status in {FulfillmentStatus.UNDERFILLED, FulfillmentStatus.COMPLETE, FulfillmentStatus.READY}:
            if self.at_work is None:
                changes["at_work"] = now
        if status is FulfillmentStatus.READY and self.at_picked is None:
            changes["at_picked"] = now
        if status is FulfillmentStatus.ISSUED and self.at_issued is None:
            changes["at_issued"] = now
        if status is FulfillmentStatus.RETURNED and self.at_returned is None:
            changes["at_returned"] = now
        if status in {FulfillmentStatus.DONE, FulfillmentStatus.CANCELLED} and self.at_closed is None:
            changes["at_closed"] = now
        return replace(self, **changes) if changes else self

    def unbind_line(self, line_id: UUID) -> FulfillmentRequest:
        self._require_editable()
        released = False
        updated: list[FulfillmentLine] = []
        for line in self.lines:
            if line.id == line_id:
                updated.append(line.clear_binding())
                released = True
            else:
                updated.append(line)
        if not released:
            raise FulfillmentValidationError("Позиция заявки не найдена")
        merged: list[FulfillmentLine] = []
        for line in updated:
            if line.lot is not None or line.reserved > 0 or line.issued > 0:
                merged.append(line)
                continue
            existing_index = next(
                (
                    index
                    for index, candidate in enumerate(merged)
                    if candidate.requirement_id == line.requirement_id
                    and candidate.lot is None
                    and candidate.issued == 0
                ),
                None,
            )
            if existing_index is None:
                merged.append(line)
            else:
                existing = merged[existing_index]
                merged[existing_index] = replace(
                    existing,
                    requirement=existing.requirement + line.requirement,
                )
        return replace(
            self,
            lines=tuple(merged),
            status=FulfillmentStatus.UNDERFILLED,
            handout_location=None,
            version=self.version + 1,
        )

    def mark_ready(self, handout_location: str) -> FulfillmentRequest:
        if self.status is not FulfillmentStatus.COMPLETE or not self.fully_reserved:
            raise FulfillmentValidationError("Комплект ещё не укомплектован целиком")
        location = str(handout_location or "").strip()
        if not location:
            raise FulfillmentValidationError("Укажите место выдачи комплекта")
        return replace(
            self,
            status=FulfillmentStatus.READY,
            handout_location=location,
            version=self.version + 1,
        )

    def mark_issued(self) -> FulfillmentRequest:
        if self.status is not FulfillmentStatus.READY or not self.fully_reserved:
            raise FulfillmentValidationError("К выдаче допускается только полный готовый комплект")
        return replace(
            self,
            status=FulfillmentStatus.ISSUED,
            lines=tuple(line.mark_issued() for line in self.lines),
            handout_location=None,
            version=self.version + 1,
        )

    def mark_returned(self, return_location: str) -> FulfillmentRequest:
        if self.status is not FulfillmentStatus.ISSUED:
            raise FulfillmentValidationError("Вернуть можно только выданный комплект")
        location = str(return_location or "").strip()
        if not location:
            raise FulfillmentValidationError("Укажите место возврата комплекта")
        return replace(
            self,
            status=FulfillmentStatus.RETURNED,
            return_location=location,
            version=self.version + 1,
        )

    def complete_return(
        self,
        dispositions: dict[UUID, tuple[Decimal, Decimal, Decimal]],
    ) -> FulfillmentRequest:
        if self.status is not FulfillmentStatus.RETURNED:
            raise FulfillmentValidationError("Возврат ещё не принят на склад")
        expected = {line.id for line in self.lines}
        if set(dispositions) != expected:
            raise FulfillmentValidationError("Укажите результат возврата по каждой позиции")
        return replace(
            self,
            status=FulfillmentStatus.DONE,
            lines=tuple(line.settle_return(*dispositions[line.id]) for line in self.lines),
            version=self.version + 1,
        )

    def cancel(self) -> FulfillmentRequest:
        if any(line.issued > 0 for line in self.lines):
            raise FulfillmentValidationError("Выданную заявку нельзя отменить — оформите возврат")
        if self.status in {FulfillmentStatus.CANCELLED, FulfillmentStatus.DONE}:
            return self
        return replace(
            self,
            status=FulfillmentStatus.CANCELLED,
            lines=tuple(line.clear_binding() for line in self.lines),
            handout_location=None,
            version=self.version + 1,
        )

    def _require_editable(self) -> None:
        if self.status in {
            FulfillmentStatus.READY,
            FulfillmentStatus.ISSUED,
            FulfillmentStatus.RETURNED,
            FulfillmentStatus.DONE,
            FulfillmentStatus.CANCELLED,
        }:
            raise FulfillmentValidationError("Заявка закрыта для изменения партий")
