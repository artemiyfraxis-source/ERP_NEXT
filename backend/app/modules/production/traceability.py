"""Pure production-output, box, and consumption rules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
import re
from uuid import UUID, uuid4

from .domain import ProductionValidationError


BOX_NUMBER = re.compile(r"^(\d{6})-(\d)$")


def box_check_digit(sequence: int) -> int:
    if sequence < 0 or sequence > 999_999:
        raise ProductionValidationError("Номер коробки вышел за пределы шестизначной серии")
    digits = [int(value) for value in f"{sequence:06d}"]
    weighted = sum(digit * (3 if offset % 2 == 0 else 1) for offset, digit in enumerate(reversed(digits)))
    return (10 - weighted % 10) % 10


def format_box_number(sequence: int) -> str:
    return f"{sequence:06d}-{box_check_digit(sequence)}"


def validate_box_number(number: str) -> str:
    clean = str(number or "").strip()
    match = BOX_NUMBER.fullmatch(clean)
    if match is None or int(match.group(2)) != box_check_digit(int(match.group(1))):
        raise ProductionValidationError("Номер коробки не прошёл проверку контрольной цифры")
    return clean


class BoxStatus(str, Enum):
    OPEN = "Открыта"
    OPERATOR_CLOSED = "Закрыта оператором"
    CONFIRMED = "Подтверждена"
    RETURNED = "Возвращена"
    CONSUMED = "Израсходована"


class WorkStatus(str, Enum):
    ACTIVE = "В работе"
    PAUSED = "Пауза"
    COMPLETED = "Завершена"
    CANCELLED = "Отменена"


class DefectStatus(str, Enum):
    PENDING = "Ожидает решения"
    DECIDED = "Решение принято"


class DefectDisposition(str, Enum):
    REWORK = "Доработка"
    SCRAP = "Списание"
    USE_AS_IS = "Использовать как есть"


class SerialStatus(str, Enum):
    IN_PROGRESS = "В работе"
    QC_ACCEPTED = "Принято ОТК"
    FINISHED_GOODS = "Сдано на ГП"
    DEFECT = "Брак"


class InstrumentStatus(str, Enum):
    ACTIVE = "Действует"
    INACTIVE = "Не используется"


def _quantity(value: Decimal, label: str, *, allow_zero: bool = False) -> Decimal:
    quantity = Decimal(value)
    if not quantity.is_finite() or quantity < 0 or (not allow_zero and quantity == 0):
        raise ProductionValidationError(f"{label} должно быть положительным числом")
    return quantity


@dataclass(frozen=True)
class ProductionBox:
    id: UUID
    number: str | None
    version: int
    task_id: UUID
    operation_id: UUID
    item: str
    operation_position: int
    operator_id: UUID
    quantity: Decimal
    consumed: Decimal
    reserved: Decimal
    status: BoxStatus
    created_at: datetime
    rejected: Decimal = Decimal("0")
    closed_at: datetime | None = None
    closed_by: str | None = None
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    return_reason: str = ""

    @property
    def available(self) -> Decimal:
        return self.quantity - self.consumed - self.reserved - self.rejected

    @classmethod
    def create(
        cls,
        *,
        task_id: UUID,
        operation_id: UUID,
        item: str,
        operation_position: int,
        operator_id: UUID,
        created_at: datetime,
    ) -> ProductionBox:
        return cls(
            id=uuid4(), number=None, version=1, task_id=task_id,
            operation_id=operation_id, item=str(item).strip(),
            operation_position=operation_position, operator_id=operator_id,
            quantity=Decimal("0"), consumed=Decimal("0"), reserved=Decimal("0"),
            status=BoxStatus.OPEN, created_at=created_at,
        )

    def add_output(self, quantity: Decimal) -> ProductionBox:
        if self.status is not BoxStatus.OPEN:
            raise ProductionValidationError("Докладывать продукцию можно только в открытую коробку")
        return replace(
            self,
            quantity=self.quantity + _quantity(quantity, "Количество годных"),
            version=self.version + 1,
        )

    def reserve(self, quantity: Decimal, *, item: str, previous_position: int) -> ProductionBox:
        amount = _quantity(quantity, "Количество из коробки")
        if self.status is not BoxStatus.CONFIRMED:
            raise ProductionValidationError("В работу можно брать только подтверждённую коробку")
        if self.item != item:
            raise ProductionValidationError("В коробке другое изделие")
        if self.operation_position != previous_position:
            raise ProductionValidationError("Коробка относится не к предыдущему этапу маршрута")
        if amount > self.available:
            raise ProductionValidationError("В коробке недостаточно доступного остатка")
        return replace(self, reserved=self.reserved + amount, version=self.version + 1)

    def release(self, quantity: Decimal) -> ProductionBox:
        amount = _quantity(quantity, "Снимаемый резерв")
        if amount > self.reserved:
            raise ProductionValidationError("Снимаемый резерв больше резерва коробки")
        return replace(self, reserved=self.reserved - amount, version=self.version + 1)

    def consume(self, quantity: Decimal) -> ProductionBox:
        amount = _quantity(quantity, "Расход")
        if amount > self.reserved:
            raise ProductionValidationError("Расход не был зарезервирован при начале работы")
        consumed = self.consumed + amount
        reserved = self.reserved - amount
        status = BoxStatus.CONSUMED if self.quantity - consumed - reserved == 0 else self.status
        return replace(
            self,
            consumed=consumed,
            reserved=reserved,
            status=status,
            version=self.version + 1,
        )

    def handoff(self) -> ProductionBox:
        if self.status is not BoxStatus.CONFIRMED:
            raise ProductionValidationError("На склад ГП можно сдать только подтверждённую коробку")
        if self.reserved != 0 or self.available <= 0:
            raise ProductionValidationError("Коробка зарезервирована или уже израсходована")
        consumed = self.consumed + self.available
        return replace(
            self,
            consumed=consumed,
            status=BoxStatus.CONSUMED if consumed + self.rejected == self.quantity else self.status,
            version=self.version + 1,
        )

    def record_qc_rejection(self, quantity: Decimal) -> ProductionBox:
        amount = _quantity(quantity, "Брак ОТК", allow_zero=True)
        if self.status is not BoxStatus.CONFIRMED:
            raise ProductionValidationError("ОТК принимает только подтверждённую коробку")
        if amount > self.available:
            raise ProductionValidationError("Брак ОТК превышает доступное количество коробки")
        rejected = self.rejected + amount
        status = BoxStatus.CONSUMED if self.consumed + rejected == self.quantity else self.status
        return replace(self, rejected=rejected, status=status, version=self.version + 1)

    def close(self, number: str, actor: str, now: datetime) -> ProductionBox:
        if self.status is not BoxStatus.OPEN:
            raise ProductionValidationError("Закрыть можно только открытую коробку")
        if self.quantity <= 0:
            raise ProductionValidationError("Пустую коробку закрыть нельзя")
        assigned = validate_box_number(number)
        if self.number is not None and self.number != assigned:
            raise ProductionValidationError("Номер возвращённой коробки менять нельзя")
        return replace(
            self, number=assigned, status=BoxStatus.OPERATOR_CLOSED,
            closed_at=now, closed_by=str(actor).strip(), version=self.version + 1,
            return_reason="",
        )

    def confirm(self, actor: str, now: datetime) -> ProductionBox:
        if self.status is not BoxStatus.OPERATOR_CLOSED:
            raise ProductionValidationError("Подтвердить можно только закрытую оператором коробку")
        return replace(
            self, status=BoxStatus.CONFIRMED, confirmed_at=now,
            confirmed_by=str(actor).strip(), version=self.version + 1,
        )

    def return_to_operator(self, reason: str) -> ProductionBox:
        clean = str(reason or "").strip()
        if self.status is not BoxStatus.OPERATOR_CLOSED:
            raise ProductionValidationError("Вернуть можно только закрытую оператором коробку")
        if not clean:
            raise ProductionValidationError("Причина возврата обязательна")
        return replace(self, status=BoxStatus.RETURNED, return_reason=clean, version=self.version + 1)

    def reopen(self, operator_id: UUID) -> ProductionBox:
        if self.operator_id != operator_id:
            raise ProductionValidationError("Вернуть коробку в работу может только её оператор")
        if self.status is not BoxStatus.RETURNED:
            raise ProductionValidationError("В работу возвращается только отклонённая коробка")
        return replace(self, status=BoxStatus.OPEN, version=self.version + 1)


@dataclass(frozen=True)
class WorkSession:
    id: UUID
    task_id: UUID
    operation_id: UUID
    operator_id: UUID
    started_at: datetime
    status: WorkStatus
    pause_started_at: datetime | None = None
    pause_seconds: int = 0

    @classmethod
    def start(cls, task_id: UUID, operation_id: UUID, operator_id: UUID, now: datetime) -> WorkSession:
        return cls(uuid4(), task_id, operation_id, operator_id, now, WorkStatus.ACTIVE)

    def pause(self, now: datetime) -> WorkSession:
        if self.status is not WorkStatus.ACTIVE:
            raise ProductionValidationError("Поставить на паузу можно только активную работу")
        return replace(self, status=WorkStatus.PAUSED, pause_started_at=now)

    def resume(self, now: datetime) -> WorkSession:
        if self.status is not WorkStatus.PAUSED or self.pause_started_at is None:
            raise ProductionValidationError("Продолжить можно только работу на паузе")
        pause_started_at = self.pause_started_at
        if pause_started_at.tzinfo is None and now.tzinfo is not None:
            pause_started_at = pause_started_at.replace(tzinfo=now.tzinfo)
        elif pause_started_at.tzinfo is not None and now.tzinfo is None:
            pause_started_at = pause_started_at.replace(tzinfo=None)
        elapsed = max(0, int((now - pause_started_at).total_seconds()))
        return replace(
            self, status=WorkStatus.ACTIVE, pause_started_at=None,
            pause_seconds=self.pause_seconds + elapsed,
        )

    def complete(self, now: datetime) -> WorkSession:
        session = self.resume(now) if self.status is WorkStatus.PAUSED else self
        if session.status is not WorkStatus.ACTIVE:
            raise ProductionValidationError("Завершить можно только текущую работу")
        return replace(session, status=WorkStatus.COMPLETED)

    def cancel(self) -> WorkSession:
        if self.status not in (WorkStatus.ACTIVE, WorkStatus.PAUSED):
            raise ProductionValidationError("Отменить можно только текущую работу")
        return replace(self, status=WorkStatus.CANCELLED, pause_started_at=None)


@dataclass(frozen=True)
class WorkInput:
    box_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class MaterialInput:
    fulfillment_line_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class MaterialSource:
    fulfillment_line_id: UUID
    article: str
    name: str
    unit: str
    grn: str
    package: str
    issued: Decimal
    consumed: Decimal
    reserved: Decimal

    @property
    def available(self) -> Decimal:
        return self.issued - self.consumed - self.reserved


@dataclass(frozen=True)
class ProductionOutput:
    id: UUID
    session_id: UUID
    task_id: UUID
    operation_id: UUID
    operator_id: UUID
    started_at: datetime
    finished_at: datetime
    pause_seconds: int
    good_quantity: Decimal
    defect_quantity: Decimal
    output_box_id: UUID
    correction_of_id: UUID | None = None


@dataclass(frozen=True)
class ConsumptionEdge:
    id: UUID
    source_box_id: UUID
    target_box_id: UUID
    quantity: Decimal
    output_id: UUID


@dataclass(frozen=True)
class MaterialConsumption:
    id: UUID
    fulfillment_line_id: UUID
    target_box_id: UUID
    quantity: Decimal
    output_id: UUID
    grn: str
    package: str


@dataclass(frozen=True)
class FinishedGoodsReceipt:
    id: UUID
    source_box_id: UUID
    finished_good_id: UUID
    grn: str
    quantity: Decimal
    received_at: datetime


@dataclass(frozen=True)
class DefectRecord:
    id: UUID
    number: str
    version: int
    created_at: datetime
    output_id: UUID
    source_box_id: UUID
    task_id: UUID
    operation_id: UUID
    operator_id: UUID
    quantity: Decimal
    character: str
    description: str
    status: DefectStatus = DefectStatus.PENDING

    @classmethod
    def create(
        cls, *, number: str, output_id: UUID, source_box_id: UUID,
        task_id: UUID, operation_id: UUID, operator_id: UUID,
        quantity: Decimal, character: str, description: str, created_at: datetime,
    ) -> DefectRecord:
        clean_character = str(character or "").strip()
        clean_description = str(description or "").strip()
        if not clean_character or not clean_description:
            raise ProductionValidationError("Для брака обязательны характер и описание")
        return cls(
            id=uuid4(), number=f"Б-{validate_box_number(number)}", version=1,
            created_at=created_at, output_id=output_id, source_box_id=source_box_id,
            task_id=task_id, operation_id=operation_id, operator_id=operator_id,
            quantity=_quantity(quantity, "Количество брака"),
            character=clean_character, description=clean_description,
        )

    def decide(self) -> DefectRecord:
        if self.status is not DefectStatus.PENDING:
            raise ProductionValidationError("Решение по браку уже принято")
        return replace(self, status=DefectStatus.DECIDED, version=self.version + 1)


@dataclass(frozen=True)
class DefectDecision:
    id: UUID
    defect_id: UUID
    disposition: DefectDisposition
    decided_by: str
    decided_at: datetime
    act_reference: str


@dataclass(frozen=True)
class MeasurementInstrument:
    id: UUID
    version: int
    inventory_number: str
    name: str
    instrument_type: str
    verification_number: str
    verified_at: datetime
    verification_due: datetime
    status: InstrumentStatus

    def validate_for(self, moment: datetime) -> None:
        if self.status is not InstrumentStatus.ACTIVE:
            raise ProductionValidationError(f"СИ {self.inventory_number} не используется")
        due = self.verification_due
        if due.tzinfo is None and moment.tzinfo is not None:
            due = due.replace(tzinfo=moment.tzinfo)
        elif due.tzinfo is not None and moment.tzinfo is None:
            due = due.replace(tzinfo=None)
        if due < moment:
            raise ProductionValidationError(f"Поверка СИ {self.inventory_number} истекла")


@dataclass(frozen=True)
class QcInspection:
    id: UUID
    box_id: UUID
    inspector_id: UUID
    inspected_at: datetime
    accepted: int
    rejected: int
    defect_number: str | None
    defect_character: str
    defect_description: str
    instrument_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class QcDecision:
    id: UUID
    inspection_id: UUID
    disposition: DefectDisposition
    decided_by: str
    decided_at: datetime
    act_reference: str


@dataclass(frozen=True)
class SerialUnit:
    id: UUID
    serial_number: str
    task_id: UUID
    position: int
    status: SerialStatus
    current_box_id: UUID | None = None
    finished_goods_grn: str | None = None
