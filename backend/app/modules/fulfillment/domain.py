"""Pure rules for reserving and issuing a complete production kit."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import re
from uuid import UUID, uuid4


class FulfillmentValidationError(ValueError):
    """A fulfillment transition would break stock traceability."""


class DuplicateKitRequest(FulfillmentValidationError):
    """A КБ of a project already has a live kit request (any status but «Отменена»), or two of them would get the same
    lots: a second one would hold the same goods twice (prod 180К.11, 10.10.2026). The API answers 409."""


class FulfillmentStatus(str, Enum):
    NEW = "Новая"
    UNDERFILLED = "Недоукомплектован"
    COMPLETE = "Укомплектован"
    READY = "Готов к выдаче"
    ISSUED = "КБ Выдана"
    RETURNED = "КБ Возвращена"
    DONE = "Готово"
    CANCELLED = "Отменена"


class FulfillmentKind(str, Enum):
    """The main kit request of a КБ (one live per project + КБ) or a «Довыдача» on top of an issued kit: the kit is
    issued exactly to the need, a shortage is handed out later as an extra request (owner, 10.10.2026)."""

    MAIN = "main"
    EXTRA = "extra"


# Statuses of a main kit after which «Довыдать» is offered: the kit went to the shop.
EXTRA_AFTER = frozenset({FulfillmentStatus.ISSUED, FulfillmentStatus.RETURNED, FulfillmentStatus.DONE})


# «Отправить на выдачу» offers this place when the kit's site has none used before (owner, G1-T3 05.10.2026): the rack
# layout has no named handout zone, so it is a text place, editable on the screen.
DEFAULT_HANDOUT_PLACE = "Зона выдачи"


def project_of(purpose: str) -> str:
    """SUPPLYMAP issueProjectOf_: the project is the first part of НАЗНАЧЕНИЕ «728К.2 · КБ1 · …»; a manual request
    without « · » has no project → ''."""
    parts = str(purpose or "").split(" · ")
    return parts[0].strip() if len(parts) > 1 else ""


_KB = re.compile(r"КБ\s*-?\s*(\d+)", re.IGNORECASE)


def kb_number(purpose: str) -> int | None:
    """The КБ of a kit from НАЗНАЧЕНИЕ «728К.2 · КБ1 · …» (also «… / КБ-1»): 1; no КБ in the text → None."""
    match = _KB.search(str(purpose or ""))
    return int(match.group(1)) if match else None


def kb_of(purpose: str) -> str:
    """«КБ1» — the item index a kit's reserves are keyed by; '' when НАЗНАЧЕНИЕ names no КБ."""
    number = kb_number(purpose)
    return f"КБ{number}" if number is not None else ""


def item_index_key(value: str | None) -> str:
    """«кб 1», «КБ-1», «КБ1» → «КБ1»: how two item indexes compare."""
    number = kb_number(str(value or ""))
    return f"КБ{number}" if number is not None else re.sub(r"[\s\-]+", "", str(value or "")).upper()


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
    # «есть, но не заведено» (owner 05.10.2026): the goods are on the shelf but on no lot — the position passes
    # without a lot, no reserve, no stock movement and no СПИСАНИЕ row at issue.
    off_book: bool = False
    # «нет на складе» (owner 12.10.2026): the storekeeper confirmed the part is physically absent — the line is a
    # shortage, it keeps waiting for a delivery and the project's «Снабжение» stops counting book stock for it. Who and
    # when; never together with «есть, но не заведено», a lot, a reserve or an issue (CHECK in migration 0104).
    absent_at: datetime | None = None
    absent_by: str = ""

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
        if self.issued != 0:
            return False
        if self.off_book:
            return self.lot is None and self.reserved == 0
        return bool(self.lot and self.reserved == self.requirement)

    @property
    def waiting(self) -> bool:
        """No lot, no «есть, но не заведено», nothing issued: the position still waits for stock (a line marked «нет
        на складе» waits too — an arrival or a bulk ГРН binds it like any waiting line)."""
        return self.lot is None and not self.off_book and self.reserved == 0 and self.issued == 0

    @property
    def absent(self) -> bool:
        """«нет на складе»: the storekeeper confirmed the part is not on the shelf."""
        return self.absent_at is not None

    def bind(
        self,
        lot: LotSnapshot,
        quantity: Decimal,
        unit: str | None = None,
    ) -> tuple[FulfillmentLine, FulfillmentLine | None]:
        if self.issued > 0:
            raise FulfillmentValidationError("Выданную позицию нельзя перепривязать")
        if self.off_book:
            raise FulfillmentValidationError(
                "Позиция отмечена «есть, но не заведено» — сначала сними отметку"
            )
        if self.lot is not None or self.reserved > 0:
            raise FulfillmentValidationError("Сначала снимите прежнюю привязку партии")
        amount = _positive(quantity, "Резерв")
        if amount > self.requirement:
            raise FulfillmentValidationError("Резерв не может превышать потребность")
        # issueAttachLot_: the bound row takes the lot's ЕД.ИЗМ; the remainder row keeps the position's own unit.
        # A lot found for a line marked «нет на складе» (a delivery came): the bound part loses the mark, the rest
        # keeps it.
        lot_unit = str(unit or "").strip()
        bound = replace(
            self, requirement=amount, reserved=amount, lot=lot, unit=lot_unit or self.unit, absent_at=None, absent_by="",
        )
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

    def mark_off_book(self) -> FulfillmentLine:
        if self.issued > 0:
            raise FulfillmentValidationError("Выданную позицию нельзя менять")
        if self.off_book:
            raise FulfillmentValidationError("Позиция уже отмечена «есть, но не заведено»")
        if self.lot is not None or self.reserved > 0:
            raise FulfillmentValidationError("У позиции выбрана партия — сначала сними её")
        return replace(self, off_book=True, absent_at=None, absent_by="")  # the part is there after all

    def clear_off_book(self) -> FulfillmentLine:
        if self.issued > 0:
            raise FulfillmentValidationError("Выданную позицию нельзя менять")
        if not self.off_book:
            raise FulfillmentValidationError("Позиция не отмечена «есть, но не заведено»")
        return replace(self, off_book=False)

    def mark_absent(self, by: str, at: datetime) -> FulfillmentLine:
        """«нет на складе» on a line without a lot; it replaces «есть, но не заведено» (one of the two, never both)."""
        if self.issued > 0:
            raise FulfillmentValidationError("Выданную позицию нельзя менять")
        if self.absent:
            raise FulfillmentValidationError("Позиция уже отмечена «нет на складе»")
        if self.lot is not None or self.reserved > 0:
            raise FulfillmentValidationError("У позиции выбрана партия — сначала сними её")
        who = str(by or "").strip()
        if not who:
            raise FulfillmentValidationError("Не указано, кто отметил «нет на складе»")
        return replace(self, off_book=False, absent_at=at, absent_by=who)

    def clear_absent(self) -> FulfillmentLine:
        if self.issued > 0:
            raise FulfillmentValidationError("Выданную позицию нельзя менять")
        if not self.absent:
            raise FulfillmentValidationError("Позиция не отмечена «нет на складе»")
        return replace(self, absent_at=None, absent_by="")

    def mark_issued(self) -> FulfillmentLine:
        if not self.covered:
            raise FulfillmentValidationError("Нельзя выдать неполностью зарезервированную позицию")
        if self.off_book:  # handed over from the shelf as it is: the whole need, nothing reserved
            return replace(self, issued=self.requirement, reserved=Decimal("0"))
        return replace(self, issued=self.reserved, reserved=Decimal("0"))

    def settle_return(
        self,
        returned_good: Decimal,
        defective_returned: Decimal,
        defective_missing: Decimal,
    ) -> FulfillmentLine:
        if self.issued <= 0:
            raise FulfillmentValidationError("Невыданную позицию нельзя принимать обратно")
        if self.off_book and Decimal(returned_good) > 0:
            raise FulfillmentValidationError(
                f"«{self.name}» выдана как «есть, не заведено» — без партии годное на склад не вернуть; "
                "заведи приход отдельно"
            )
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
    # НЕДОБОР written when the request is created (issueCreateTasks_ `shortfall`: shortNote of the form plus what the
    # picked lots did not give). None — nothing was written: the header shows issueShortText_ of the lines. The first
    # lot bound or removed rewrites it from the lines (issueHeadSync_), so both operations reset it to None.
    shortfall_note: str | None = None
    # ЗАЯВИТЕЛЬ as a user: «готова к получению» goes to this id; None = the name matched nobody.
    requester_id: UUID | None = None
    # FF-4 (SUPPLYMAP ISSUE_SHEET В РАБОТУ / НАБРАНА / ВЫДАНА / ВОЗВРАЩЕНА / ЗАКРЫТА): the first time the request
    # reached the stage; set once by `stamped`, never moved back (issueStatusSweep_ writes only an empty cell).
    at_work: datetime | None = None
    at_picked: datetime | None = None
    at_issued: datetime | None = None
    at_returned: datetime | None = None
    at_closed: datetime | None = None
    kind: FulfillmentKind = FulfillmentKind.MAIN

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
        kind: FulfillmentKind = FulfillmentKind.MAIN,
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
            kind=FulfillmentKind(kind),
        )

    @property
    def fully_reserved(self) -> bool:
        return bool(self.lines) and all(line.covered for line in self.lines)

    def bind_line(
        self,
        line_id: UUID,
        lot: LotSnapshot,
        quantity: Decimal,
        unit: str | None = None,
        *,
        touch: bool = True,
    ) -> FulfillmentRequest:
        """touch=False — the reserve made while the request is created (issueCreateTasks_): it stays «Новая»."""
        self._require_editable()
        updated: list[FulfillmentLine] = []
        found = False
        for line in self.lines:
            if line.id != line_id:
                updated.append(line)
                continue
            found = True
            bound, remainder = line.bind(lot, quantity, unit)
            updated.append(bound)
            if remainder is not None:
                updated.append(remainder)
        if not found:
            raise FulfillmentValidationError("Позиция заявки не найдена")
        lines = tuple(updated)
        return replace(
            self, lines=lines, status=self._picked_status(lines, touch), version=self.version + 1,
            shortfall_note=None if touch else self.shortfall_note,
        )

    def _picked_status(self, lines: tuple[FulfillmentLine, ...], touch: bool) -> FulfillmentStatus:
        """FF-3 after the picker binds a lot or marks «есть, но не заведено» (owner, G1-T3 05.10.2026: fewer clicks
        to hand a kit out): the pick that closes the last open line makes the kit «Укомплектован» by itself; while a
        line still waits, the first pick moves «Новая» → «Недоукомплектован» (issueHeadSync_). touch=False — the
        reserve made while the request is created — leaves «Новая» as it is (issueCreateTasks_), even when every line
        got its lot there: that kit is completed by hand (`mark_complete`)."""
        if not touch:
            return self.status
        if self.status in {FulfillmentStatus.NEW, FulfillmentStatus.UNDERFILLED} and lines and all(
            line.covered for line in lines
        ):
            return FulfillmentStatus.COMPLETE
        return FulfillmentStatus.UNDERFILLED if self.status is FulfillmentStatus.NEW else self.status

    @property
    def shortfall(self) -> str:
        """НЕДОБОР of the header: the text written at creation, or issueShortText_ of the lines — what a lot does not
        close yet is what we wait for."""
        if self.shortfall_note is not None:
            return self.shortfall_note

        def number(value: Decimal) -> str:
            return format(value.quantize(Decimal("0.01")).normalize(), "f")

        parts = []
        for line in self.lines:
            if line.off_book:
                continue
            picked = line.issued if line.issued > 0 else line.reserved
            if picked >= line.requirement:
                continue
            where = f" ({line.lot.grn})" if line.lot else " (нет на складе)" if line.absent else " (ГРН не выбран)"
            parts.append(f"{line.name}{where}: −{number(line.requirement - picked)}")
        return "; ".join(parts)

    def open_lines(self) -> tuple[FulfillmentLine, ...]:
        """Lines a lot does not close yet (issueStatusSweep_: КОЛ-ВО < ПОТРЕБНОСТЬ)."""
        return tuple(line for line in self.lines if not line.covered)

    def mark_complete(self) -> FulfillmentRequest:
        """«Укомплектован» by hand (ISS_MODES_.pick.next: from «Новая» and «Недоукомплектован»). Picking the last open
        line completes the kit by itself (`_picked_status`); the button stays for a kit that is whole without a pick:
        every line got its lot when the request was created (it stays «Новая»), or a request left
        «Недоукомплектован» with every line closed before the automatic step existed. The gate is issueStatusSweep_'s:
        while a line is not closed by a lot the status would roll back — refuse instead. A kit the last pick already
        completed is left as it is (a screen or a script that still presses the button changes nothing)."""
        if self.status is FulfillmentStatus.COMPLETE:
            return self
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
        """The lot removed: the line waits again, so the kit rolls back to «Недоукомплектован» — also from the
        «Укомплектован» the last pick set by itself — and the handout place is forgotten."""
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
        return replace(
            self,
            lines=_merged_free(updated),
            status=FulfillmentStatus.UNDERFILLED,
            handout_location=None,
            version=self.version + 1,
            shortfall_note=None,
        )

    def mark_off_book(self, line_id: UUID, *, touch: bool = True) -> FulfillmentRequest:
        """«есть, но не заведено»: the waiting position counts as covered without a lot. Like a bound lot it moves the
        status (`_picked_status`: the last open line → «Укомплектован») and rewrites НЕДОБОР from the lines
        (issueHeadSync_)."""
        self._require_editable()
        if not any(line.id == line_id for line in self.lines):
            raise FulfillmentValidationError("Позиция заявки не найдена")
        lines = tuple(line.mark_off_book() if line.id == line_id else line for line in self.lines)
        return replace(
            self, lines=lines, status=self._picked_status(lines, touch), version=self.version + 1, shortfall_note=None,
        )

    def clear_off_book(self, line_id: UUID) -> FulfillmentRequest:
        """The mark removed: the position waits again, exactly as after a lot is removed (FF-6 fold included)."""
        self._require_editable()
        if not any(line.id == line_id for line in self.lines):
            raise FulfillmentValidationError("Позиция заявки не найдена")
        lines = [line.clear_off_book() if line.id == line_id else line for line in self.lines]
        return replace(
            self,
            lines=_merged_free(lines),
            status=FulfillmentStatus.UNDERFILLED,
            handout_location=None,
            version=self.version + 1,
            shortfall_note=None,
        )

    def mark_absent(self, line_id: UUID, by: str, at: datetime) -> FulfillmentRequest:
        """«нет на складе»: the line stays open — a shortage, never «есть». The kit is in work and short: «Новая» and
        «Укомплектован» (the line was «есть, но не заведено») → «Недоукомплектован»; the handout place is forgotten
        like after a lot is removed; НЕДОБОР is rewritten from the lines."""
        self._require_editable()
        if not any(line.id == line_id for line in self.lines):
            raise FulfillmentValidationError("Позиция заявки не найдена")
        lines = tuple(line.mark_absent(by, at) if line.id == line_id else line for line in self.lines)
        return replace(
            self, lines=lines, status=FulfillmentStatus.UNDERFILLED, handout_location=None,
            version=self.version + 1, shortfall_note=None,
        )

    def clear_absent(self, line_id: UUID) -> FulfillmentRequest:
        """The mark removed: the line waits again without it (folds with free rows of the same item, FF-6); the kit
        stays «Недоукомплектован» — the line is still open."""
        self._require_editable()
        if not any(line.id == line_id for line in self.lines):
            raise FulfillmentValidationError("Позиция заявки не найдена")
        lines = [line.clear_absent() if line.id == line_id else line for line in self.lines]
        return replace(
            self, lines=_merged_free(lines), status=FulfillmentStatus.UNDERFILLED, version=self.version + 1,
            shortfall_note=None,
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

    def return_to_kitting(self) -> FulfillmentRequest:
        """«Вернуть в комплектацию» (owner, 10.10.2026): a kit sent to the handout point comes back to the picker —
        «Укомплектован» (or «Недоукомплектован» if a line lost its cover). The reserves stay on their lots; the
        handout place and «Отправлена на выдачу» are cleared, the next sending stamps them anew. Only from
        «Готов к выдаче»: cancelling a kit lying at the handout point goes through this step first."""
        if self.status is not FulfillmentStatus.READY:
            raise FulfillmentValidationError(
                f"Вернуть в комплектацию можно только комплект «Готов к выдаче», а не «{self.status.value}»"
            )
        status = FulfillmentStatus.UNDERFILLED if self.open_lines() else FulfillmentStatus.COMPLETE
        return replace(self, status=status, handout_location=None, at_picked=None, version=self.version + 1)

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
        if self.status is FulfillmentStatus.READY:
            # Prod 719К.2 (05.10.2026): a kit at the handout point was cancelled and its КБ vanished from the order.
            raise FulfillmentValidationError(
                "Комплект лежит в зоне выдачи — сначала «Вернуть в комплектацию», потом отменять"
            )
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


def _merged_free(lines) -> tuple[FulfillmentLine, ...]:
    """FF-6, Issue.gs issueMergeFreePlan_: after a lot is removed, every FREE row (no lot, no reserve, no issue, not
    «есть, но не заведено») of one article + name + unit folds into the first such row — also rows that came from
    different positions of the composition; the need is summed. Any other row keeps its own quantity. A row marked
    «нет на складе» folds only with another marked row: a need nobody confirmed absent never takes the mark."""

    def free_key(line: FulfillmentLine) -> tuple[str, ...]:
        return (
            *(str(value or "").strip().casefold() for value in (line.article, line.name, line.unit)),
            "absent" if line.absent else "",
        )

    merged: list[FulfillmentLine] = []
    for line in lines:
        if not line.waiting:
            merged.append(line)
            continue
        existing_index = next(
            (
                index
                for index, candidate in enumerate(merged)
                if candidate.waiting and free_key(candidate) == free_key(line)
            ),
            None,
        )
        if existing_index is None:
            merged.append(line)
        else:
            existing = merged[existing_index]
            merged[existing_index] = replace(existing, requirement=existing.requirement + line.requirement)
    return tuple(merged)


@dataclass(frozen=True)
class KitReserve:
    """A techcard reserve: one КБ of a project holds part of one warehouse lot before its kit request exists.

    It sits in the lot's РЕЗЕРВ like a procurement «Со склада» cover. The kit request of the same project and КБ takes
    it over first after the project's covers (`consume`): the reserve moves to the kit line without leaving the lot,
    `consumed`/`consumed_by` keep the trace."""

    id: UUID
    project: str
    item_index: str
    article: str
    lot_id: UUID
    warehouse: str
    grn: str
    package: str
    unit: str
    quantity: Decimal
    created_at: datetime
    created_by: str
    consumed: Decimal = Decimal("0")
    consumed_by: str = ""
    released_at: datetime | None = None
    version: int = 1

    @property
    def active(self) -> bool:
        return self.quantity > 0

    @property
    def label(self) -> str:
        """«728К.2 · КБ1» — who holds the reserve, as the lot shows it."""
        return f"{self.project} · {self.item_index}" if self.item_index else self.project

    def release(self, now: datetime) -> KitReserve:
        if not self.active:
            raise FulfillmentValidationError("Резерв техкарты уже снят")
        return replace(self, quantity=Decimal("0"), released_at=now, version=self.version + 1)

    def consume(self, quantity: Decimal, request_number: str) -> KitReserve:
        amount = _positive(quantity, "Количество из резерва техкарты")
        if amount > self.quantity:
            raise FulfillmentValidationError("Комплектация берёт больше, чем держит резерв техкарты")
        numbers = [item for item in self.consumed_by.split(", ") if item]
        if request_number and request_number not in numbers:
            numbers.append(request_number)
        return replace(
            self,
            quantity=self.quantity - amount,
            consumed=self.consumed + amount,
            consumed_by=", ".join(numbers),
            version=self.version + 1,
        )
