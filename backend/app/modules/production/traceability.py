"""Pure production-output, box, and consumption rules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import re
from typing import Iterable, Sequence
from uuid import UUID, uuid4

from .domain import ProductionValidationError


BOX_NUMBER = re.compile(r"^(\d{6})-?(\d)$")
# SUPPLYMAP issues 100000 + seq; ERP stays below it so both systems never hand out the same number.
# The same split holds for defect boxes «Б-»: ERP Б-000001…099999, SUPPLYMAP from Б-100001.
ERP_BOX_SEQUENCE_LIMIT = 99_999


def box_check_digit(sequence: int) -> int:
    """Luhn check digit over the six-digit body, as SUPPLYMAP `prodLuhn_`."""
    if sequence < 0 or sequence > 999_999:
        raise ProductionValidationError("Номер коробки вышел за пределы шестизначной серии")
    total = 0
    for offset, char in enumerate(reversed(f"{sequence:06d}")):
        digit = int(char)
        if offset % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10


def format_box_number(sequence: int) -> str:
    return f"{sequence:06d}-{box_check_digit(sequence)}"


def issue_box_number(sequence: int) -> str:
    if sequence < 1 or sequence > ERP_BOX_SEQUENCE_LIMIT:
        raise ProductionValidationError("Серия номеров коробок ERP 000001–099999 исчерпана")
    return format_box_number(sequence)


def issue_defect_number(sequence: int) -> str:
    if sequence < 1 or sequence > ERP_BOX_SEQUENCE_LIMIT:
        raise ProductionValidationError("Серия номеров брака ERP Б-000001–099999 исчерпана")
    return format_box_number(sequence)


def validate_box_number(number: str) -> str:
    """Accept a hand-typed number (spaces, long dash, no hyphen) and return it as `dddddd-d`."""
    clean = re.sub(r"\s", "", str(number or "")).replace("–", "-").replace("—", "-")
    match = BOX_NUMBER.fullmatch(clean)
    if match is None:
        raise ProductionValidationError(
            f"«{str(number or '').strip()}» — не номер коробки: 6 цифр, дефис, контрольная цифра",
        )
    body, check = match.groups()
    if int(check) != box_check_digit(int(body)):
        raise ProductionValidationError("Номер коробки не прошёл проверку контрольной цифры")
    return f"{body}-{check}"


# Код коробки для подписи маркером (владелец, 13.10.2026): проект·КБ·дата·код сотрудника·номер,
# например «180К.11·КБ1·13.10·Б07·3». Номер — порядковый у этого сотрудника по этой КБ проекта за день.
# Шестизначный номер с контрольной цифрой остаётся ключом учёта; код — то, что пишут на коробке и по чему ищут.
BOX_CODE_SEPARATOR = "·"
BOX_CODE_PROJECT_LIMIT = 20
BOX_CODE_KB_LIMIT = 12
_KB = re.compile(r"(?:КБ|KB)[\s.\-]*(\d+)", re.IGNORECASE)
_CYRILLIC_INITIAL = re.compile(r"[А-ЯЁ]")
# Латиница, похожая на кириллицу: «180K.11» и «180К.11» на коробке пишутся одинаково.
_LOOKALIKE = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")


def box_code_kb(project: str, item: str) -> str:
    """КБ в коде: «КБ1» из «КБ1», «КБ-1», «591K.3.КБ1»; иное изделие — как есть, без пробелов и разделителя."""
    clean = re.sub(r"\s+", "", str(item or "")).replace(BOX_CODE_SEPARATOR, ".")
    match = _KB.search(clean)
    if match is not None:
        return f"КБ{int(match.group(1))}"
    prefix = re.sub(r"\s+", "", str(project or ""))
    if prefix and clean.casefold().startswith(f"{prefix}.".casefold()):
        clean = clean[len(prefix) + 1:]
    return clean[:BOX_CODE_KB_LIMIT] or "КБ"


def box_code_prefix(project: str, item: str, day: date, operator_code: str) -> str:
    """Код без порядкового номера: проект·КБ·дд.мм·код сотрудника."""
    clean_project = re.sub(r"\s+", "", str(project or "")).replace(BOX_CODE_SEPARATOR, ".")
    if not clean_project:
        raise ProductionValidationError("У задания нет проекта — код коробки не составить")
    worker = str(operator_code or "").strip()
    if not worker:
        raise ProductionValidationError("Нет кода сотрудника — код коробки не составить")
    return BOX_CODE_SEPARATOR.join((
        clean_project[:BOX_CODE_PROJECT_LIMIT], box_code_kb(project, item), day.strftime("%d.%m"), worker,
    ))


def box_code(prefix: str, sequence: int) -> str:
    if sequence < 1:
        raise ProductionValidationError("Порядковый номер коробки начинается с 1")
    return f"{prefix}{BOX_CODE_SEPARATOR}{sequence}"


def box_code_sequence(code: str, prefix: str) -> int | None:
    """Порядковый номер коробки из её кода, если код начинается этим префиксом; иначе None."""
    head = f"{prefix}{BOX_CODE_SEPARATOR}"
    tail = str(code or "")
    if not tail.startswith(head) or not tail[len(head):].isdigit():
        return None
    return int(tail[len(head):])


def next_box_code(prefix: str, existing: Iterable[str]) -> str:
    """Следующий код с этим префиксом: номер на единицу больше наибольшего уже выданного."""
    taken = [number for number in (box_code_sequence(code, prefix) for code in existing) if number is not None]
    return box_code(prefix, max(taken, default=0) + 1)


def box_code_key(text: str) -> str:
    """Ключ поиска по коду: без регистра, пробелов и разделителей, латиница-двойник — кириллицей.

    «180к.11 кб1 13.10 б07 3», «180К11·КБ1·1310·Б07·3» и сам код дают один ключ."""
    clean = str(text or "").upper().translate(_LOOKALIKE).replace("Ё", "Е")
    clean = re.sub(r"[\s.·•,;:/_\-–—]+", "", clean)
    return re.sub(r"КВ(?=\d)", "КБ", clean)  # «KB1» латиницей


def operator_short_code(name: str, personnel_number: str | None, taken: Iterable[str]) -> str:
    """Короткий код сотрудника для коробки: первая буква фамилии + табельный номер (если он из цифр и свободен),
    иначе + наименьший свободный двузначный номер среди сотрудников: «Б07». Не буква — «С» (сотрудник)."""
    surname = str(name or "").strip().upper().replace("Ё", "Е")
    first = surname[:1]
    initial = first if _CYRILLIC_INITIAL.fullmatch(first) else "С"
    # «О» и «З» от руки читаются как 0 и 3 — к ним вторая буква фамилии: «Орлов» → «ОР07».
    if initial in "ОЗ" and _CYRILLIC_INITIAL.fullmatch(surname[1:2] or "-"):
        initial += surname[1]
    busy = {str(code).upper() for code in taken}
    personnel = re.sub(r"\s+", "", str(personnel_number or ""))
    if personnel.isdigit() and len(personnel) <= 4:
        candidate = f"{initial}{int(personnel):02d}"
        if candidate not in busy:
            return candidate
    number = 1
    while f"{initial}{number:02d}" in busy:
        number += 1
    return f"{initial}{number:02d}"


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


def _positions(value: int | Iterable[int]) -> tuple[int, ...]:
    return (value,) if isinstance(value, int) else tuple(sorted(set(value)))


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
    code: str | None = None  # код для подписи маркером (0119); у коробок до 0119 — None

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

    @classmethod
    def submitted(
        cls,
        *,
        task_id: UUID,
        operation_id: UUID,
        item: str,
        operation_position: int,
        operator_id: UUID,
        number: str,
        good: Decimal,
        actor: str,
        now: datetime,
        code: str | None = None,
    ) -> ProductionBox:
        """prodWorkSubmit_: one submission is one box, closed by the operator at once with the good pieces."""
        return cls(
            id=uuid4(), number=validate_box_number(number), code=code, version=1, task_id=task_id,
            operation_id=operation_id, item=str(item).strip(),
            operation_position=operation_position, operator_id=operator_id,
            quantity=_quantity(good, "Количество годных", allow_zero=True),
            consumed=Decimal("0"), reserved=Decimal("0"),
            status=BoxStatus.OPERATOR_CLOSED, created_at=now,
            closed_at=now, closed_by=str(actor).strip(),
        )

    def input_error(self, *, item: str, previous_position: int | Iterable[int], task_id: UUID | None = None) -> str:
        """prodInputError_: why this box cannot be an input of the operation ('' when it can).

        previous_position — номер этапа-входа или номера всех входов операции (сетевой график, ARCH-TRACK-2)."""
        label = self.number or "без номера"
        positions = _positions(previous_position)
        if self.status is not BoxStatus.CONFIRMED:
            return f"Коробка {label} — «{self.status.value}»: брать можно только подтверждённую мастером."
        if task_id is not None and self.task_id != task_id:
            return f"Коробка {label} — из другого производственного задания."
        if self.item.lower() != str(item).strip().lower():
            return f"Коробка {label} — от другого изделия ({self.item})."
        if self.operation_position not in positions:
            need = (
                f"нужен этап {positions[0]}" if len(positions) == 1
                else f"нужны этапы {', '.join(str(position) for position in positions)}"
            )
            return f"Коробка {label} — с этапа {self.operation_position}, а на вход {need}."
        if self.available <= 0:
            return f"Коробка {label} пустая."
        return ""

    def add_output(self, quantity: Decimal) -> ProductionBox:
        if self.status is not BoxStatus.OPEN:
            raise ProductionValidationError("Докладывать продукцию можно только в открытую коробку")
        return replace(
            self,
            quantity=self.quantity + _quantity(quantity, "Количество годных"),
            version=self.version + 1,
        )

    def reserve(self, quantity: Decimal, *, item: str, previous_position: int | Iterable[int]) -> ProductionBox:
        amount = _quantity(quantity, "Количество из коробки")
        if self.status is not BoxStatus.CONFIRMED:
            raise ProductionValidationError("В работу можно брать только подтверждённую коробку")
        if self.item != item:
            raise ProductionValidationError("В коробке другое изделие")
        if self.operation_position not in _positions(previous_position):
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

    def correct(
        self, *, replaced_good: Decimal, good: Decimal, operator_id: UUID, actor: str, now: datetime,
    ) -> ProductionBox:
        """Replace one output's good quantity in a returned box and hand it back to the master (`prodWorkFix_`)."""
        label = self.number or "без номера"
        if self.status is not BoxStatus.RETURNED:
            raise ProductionValidationError(f"Коробка {label} не возвращена мастером — исправлять нечего")
        if self.operator_id != operator_id:
            raise ProductionValidationError(f"Коробку {label} сдавал другой оператор")
        quantity = (
            self.quantity
            - _quantity(replaced_good, "Исправляемое количество годных", allow_zero=True)
            + _quantity(good, "Количество годных", allow_zero=True)
        )
        if quantity < 0:
            raise ProductionValidationError("Исправление уводит количество коробки ниже нуля")
        return replace(
            self, quantity=quantity, status=BoxStatus.OPERATOR_CLOSED,
            closed_at=now, closed_by=str(actor).strip(), return_reason="",
            version=self.version + 1,
        )

    def give_back(self, quantity: Decimal) -> ProductionBox:
        """Return pieces a corrected output no longer takes; an emptied box becomes usable again."""
        amount = _quantity(quantity, "Возврат во входную коробку")
        if self.status not in (BoxStatus.CONFIRMED, BoxStatus.CONSUMED):
            raise ProductionValidationError("Вернуть можно только в подтверждённую или израсходованную коробку")
        if amount > self.consumed:
            raise ProductionValidationError("Возврат больше расхода коробки")
        return replace(
            self, consumed=self.consumed - amount, status=BoxStatus.CONFIRMED,
            version=self.version + 1,
        )


def _count(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


def pick_inputs(sources: Sequence[ProductionBox], quantity: Decimal) -> tuple[WorkInput, ...]:
    """Take `quantity` from confirmed boxes in the given order, as SUPPLYMAP `prodPick_`."""
    usable = [box for box in sources if box.status is BoxStatus.CONFIRMED and box.available > 0]
    left = Decimal(quantity)
    taken: list[WorkInput] = []
    for box in usable:
        if left <= 0:
            break
        amount = min(box.available, left)
        taken.append(WorkInput(box.id, amount))
        left -= amount
    if left > 0:
        have = sum((box.available for box in usable), Decimal("0"))
        raise ProductionValidationError(
            f"Добрать {_count(quantity)} шт не из чего: во входных коробках {_count(have)} шт",
        )
    return tuple(taken)


def give_back_inputs(taken: Sequence[WorkInput], quantity: Decimal) -> tuple[WorkInput, ...]:
    """Return `quantity` to the boxes it was taken from, latest first, as SUPPLYMAP `prodGiveBack_`."""
    left = Decimal(quantity)
    returned: list[WorkInput] = []
    for item in reversed(taken):
        if left <= 0:
            break
        amount = min(item.quantity, left)
        if amount > 0:
            returned.append(WorkInput(item.box_id, amount))
            left -= amount
    if left > 0:
        raise ProductionValidationError(
            f"Вернуть {_count(quantity)} шт некуда: из входных коробок взято меньше",
        )
    return tuple(returned)


@dataclass(frozen=True)
class InputStage:
    """Вход операции при исполнении (сетевой график, ARCH-TRACK-2): этап задания, чьи коробки операция берёт,
    и сколько его штук идёт на одну её штуку (per_unit ребра).

    Коробка относится к входу по номеру этапа: в задании номер этапа однозначен (это и есть операция-вход), а
    коробка другого задания того же изделия идёт по номеру этапа, как в SUPPLYMAP `prodInputError_`."""
    operation_id: UUID
    position: int
    name: str = ""
    per_unit: Decimal = Decimal(1)

    @property
    def label(self) -> str:
        return f"{self.position} «{self.name}»" if self.name else str(self.position)


def stages_text(stages: Sequence[InputStage], *, named: bool = False) -> str:
    """«этапа 2» / «этапов 1, 2» — для сообщений оператору."""
    parts = [stage.label if named else str(stage.position) for stage in stages]
    return f"этапа {parts[0]}" if len(parts) == 1 else f"этапов {', '.join(parts)}"


def missing_stages(stages: Sequence[InputStage], boxes: Sequence[ProductionBox]) -> tuple[InputStage, ...]:
    """Входы операции, из которых не взято ни одной коробки: сборка начинается только со всеми входами."""
    have = {box.operation_position for box in boxes}
    return tuple(stage for stage in stages if stage.position not in have)


def take_inputs(stages: Sequence[InputStage], sources: Sequence[ProductionBox], quantity: Decimal) -> tuple[WorkInput, ...]:
    """prodPick_ по каждому входу операции: на `quantity` штук выхода с этапа-входа берётся quantity × per_unit
    его штук из его коробок по порядку ввода. Не хватает одного входа — сдача отклоняется целиком."""
    taken: list[WorkInput] = []
    for stage in stages:
        own = [box for box in sources if box.operation_position == stage.position]
        need = Decimal(quantity) * stage.per_unit
        have = sum((box.available for box in own), Decimal("0"))
        if have < need:
            if len(stages) == 1 and stage.per_unit == 1:
                raise ProductionValidationError(
                    f"Во входных коробках {_count(have)} шт, а сдаёшь {_count(quantity)} (годные + брак).",
                )
            raise ProductionValidationError(
                f"С этапа {stage.label} во входных коробках {_count(have)} шт, а на {_count(quantity)} шт "
                f"нужно {_count(need)} ({_count(stage.per_unit)} на одну).",
            )
        taken.extend(pick_inputs(own, need))
    return tuple(taken)


def correction_moves(
    stages: Sequence[InputStage],
    taken: Sequence[WorkInput],
    boxes: dict[UUID, ProductionBox],
    delta: Decimal,
) -> tuple[WorkInput, ...]:
    """prodWorkFix_ по каждому входу: разница в штуках выхода × per_unit добирается из тех же коробок этого
    входа (delta > 0) или возвращается в них, последние — первыми (delta < 0). `taken` — нетто по коробкам,
    `boxes` — те из них, что есть в учёте. Один вход — все взятые коробки его, как до сетевого графика."""
    moves: list[WorkInput] = []
    for stage in stages:
        own = [item for item in taken if len(stages) == 1 or (
            item.box_id in boxes and boxes[item.box_id].operation_position == stage.position
        )]
        amount = abs(Decimal(delta)) * stage.per_unit
        if delta > 0:
            moves.extend(pick_inputs([boxes[item.box_id] for item in own if item.box_id in boxes], amount))
        elif delta < 0:
            moves.extend(give_back_inputs(own, amount))
    return tuple(moves)


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

    def running_seconds(self, now: datetime) -> int:
        """Фактическое время работы: от начала до now за вычетом пауз (идущая пауза — до now тоже не считается)."""
        session = self.resume(now) if self.status is WorkStatus.PAUSED else self
        started_at = session.started_at
        if started_at.tzinfo is None and now.tzinfo is not None:
            started_at = started_at.replace(tzinfo=now.tzinfo)
        elif started_at.tzinfo is not None and now.tzinfo is None:
            started_at = started_at.replace(tzinfo=None)
        return max(0, int((now - started_at).total_seconds()) - session.pause_seconds)

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
    session_id: UUID | None
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
    replaced_by_output_id: UUID | None = None

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
        if self.replaced_by_output_id is not None:
            raise ProductionValidationError(
                f"Запись брака {self.number} заменена исправлением — решение по новой записи",
            )
        if self.status is not DefectStatus.PENDING:
            raise ProductionValidationError("Решение по браку уже принято")
        return replace(self, status=DefectStatus.DECIDED, version=self.version + 1)

    def mark_replaced(self, output_id: UUID) -> DefectRecord:
        """Quantity, status and decision stay as recorded; only the replacement is noted."""
        if self.replaced_by_output_id is not None:
            raise ProductionValidationError(f"Запись брака {self.number} уже заменена")
        return replace(self, replaced_by_output_id=output_id, version=self.version + 1)


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
