"""Pure finished-goods and shipment rules ported from Shipment.gs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4


class ShippingValidationError(ValueError):
    """A finished-goods or shipment operation breaks traceability."""


def _text(value: str | None, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ShippingValidationError(f"{label} обязательно")
    return normalized


def _positive(value: Decimal, label: str) -> Decimal:
    normalized = Decimal(value)
    if not normalized.is_finite() or normalized <= 0:
        raise ShippingValidationError(f"{label} должно быть положительным числом")
    return normalized


class ShipmentStatus(str, Enum):
    NEW = "Новая"
    ASSEMBLED = "Собрана"
    PACKED = "Упакована"
    SHIPPED = "Отгружена"
    CLOSED = "Закрыта"
    CANCELLED = "Отменена"


class DeliveryTerms(str, Enum):
    PICKUP = "САМОВЫВОЗ"
    CARRIER = "ДОСТАВКА ТК"


class UpdType(str, Enum):
    EDO = "ЭДО"
    PAPER = "БУМАГА"


@dataclass(frozen=True)
class FinishedGood:
    id: UUID
    version: int
    project: str
    item_index: str
    customer: str
    article: str
    name: str
    ordered: Decimal
    unit: str
    accepted: Decimal = Decimal("0")
    shipped: Decimal = Decimal("0")
    received_at: date | None = None
    grn: str | None = None
    note: str = ""

    @classmethod
    def create(
        cls, *, project: str, item_index: str, customer: str, article: str,
        name: str, ordered: Decimal, unit: str, note: str = "",
    ) -> FinishedGood:
        return cls(
            id=uuid4(), version=1, project=_text(project, "Проект"),
            item_index=_text(item_index, "КБ"), customer=str(customer or "").strip(),
            article=str(article or "").strip(), name=_text(name, "Наименование"),
            ordered=_positive(ordered, "Количество в заказе"), unit=_text(unit, "Единица"),
            note=str(note or "").strip(),
        )

    @property
    def remaining(self) -> Decimal:
        return max(self.accepted - self.shipped, Decimal("0"))

    def accept(self, quantity: Decimal, received_at: date, grn: str) -> FinishedGood:
        return replace(
            self,
            accepted=self.accepted + _positive(quantity, "Количество"),
            received_at=received_at,
            grn=self.grn or _text(grn, "ГРН"),
            version=self.version + 1,
        )

    def take(self, quantity: Decimal) -> FinishedGood:
        amount = _positive(quantity, "Количество отбора")
        if amount > self.remaining:
            raise ShippingValidationError("Нельзя отобрать больше остатка готовой продукции")
        return replace(self, shipped=self.shipped + amount, version=self.version + 1)

    def put_back(self, quantity: Decimal) -> FinishedGood:
        amount = _positive(quantity, "Количество возврата")
        if amount > self.shipped:
            raise ShippingValidationError("Возврат не может превышать отобранное количество")
        return replace(self, shipped=self.shipped - amount, version=self.version + 1)


@dataclass(frozen=True)
class ShipmentLine:
    id: UUID
    finished_good_id: UUID
    project: str
    item_index: str
    article: str
    name: str
    requested: Decimal
    picked: Decimal
    unit: str
    grn: str | None = None

    @classmethod
    def create(cls, good: FinishedGood, quantity: Decimal) -> ShipmentLine:
        return cls(
            id=uuid4(), finished_good_id=good.id, project=good.project,
            item_index=good.item_index, article=good.article, name=good.name,
            requested=_positive(quantity, "Заказанное количество"),
            picked=Decimal("0"), unit=good.unit,
        )

    @property
    def shortage(self) -> Decimal:
        return max(self.requested - self.picked, Decimal("0"))


@dataclass(frozen=True)
class Shipment:
    id: UUID
    number: str
    version: int
    created_at: datetime
    status: ShipmentStatus
    customer: str
    receiver: str
    shipping_point: str
    contact: str
    phone: str
    terms: DeliveryTerms
    carrier: str
    deadline: date | None
    upd_type: UpdType
    upd_number: str
    upd_date: date | None
    upd_url: str
    pl_number: str
    dn_number: str
    track_number: str
    packed_at: date | None
    shipped_at: date | None
    manager: str
    note: str
    lines: tuple[ShipmentLine, ...]
    # МЕНЕДЖЕР as a user: the track number goes to this id; None = the name matched nobody (→ all pm).
    manager_id: UUID | None = None

    @classmethod
    def create(
        cls, *, number: str, created_at: datetime, customer: str, receiver: str,
        shipping_point: str, contact: str, phone: str, terms: DeliveryTerms,
        carrier: str, deadline: date | None, upd_type: UpdType, upd_number: str,
        upd_date: date | None, upd_url: str, manager: str, note: str,
        lines: tuple[ShipmentLine, ...], manager_id: UUID | None = None,
    ) -> Shipment:
        if not lines:
            raise ShippingValidationError("Не выбрано ни одной позиции с количеством")
        if len({line.finished_good_id for line in lines}) != len(lines):
            raise ShippingValidationError("Позиция готовой продукции указана дважды")
        return cls(
            id=uuid4(), number=_text(number, "Номер отгрузки"), version=1,
            created_at=created_at, status=ShipmentStatus.NEW,
            customer=str(customer or "").strip(), receiver=_text(receiver, "Получатель"),
            shipping_point=_text(shipping_point, "Точка отгрузки"),
            contact=str(contact or "").strip(), phone=str(phone or "").strip(),
            terms=terms, carrier=str(carrier or "").strip(), deadline=deadline,
            upd_type=upd_type, upd_number=str(upd_number or "").strip(), upd_date=upd_date,
            upd_url=str(upd_url or "").strip(), pl_number="", dn_number="",
            track_number="", packed_at=None, shipped_at=None,
            manager=_text(manager, "Менеджер"), note=str(note or "").strip(), lines=lines,
            manager_id=manager_id,
        )

    def pick(self, available: dict[UUID, Decimal], grns: dict[UUID, str]) -> tuple[Shipment, dict[UUID, Decimal]]:
        if self.status in {ShipmentStatus.PACKED, ShipmentStatus.SHIPPED, ShipmentStatus.CLOSED, ShipmentStatus.CANCELLED}:
            raise ShippingValidationError("В текущем статусе набор недоступен")
        taken: dict[UUID, Decimal] = {}
        updated: list[ShipmentLine] = []
        for line in self.lines:
            amount = min(line.shortage, max(available.get(line.finished_good_id, Decimal("0")), Decimal("0")))
            available[line.finished_good_id] = available.get(line.finished_good_id, Decimal("0")) - amount
            if amount > 0:
                taken[line.finished_good_id] = taken.get(line.finished_good_id, Decimal("0")) + amount
            updated.append(replace(line, picked=line.picked + amount, grn=grns.get(line.finished_good_id) or line.grn))
        if not taken:
            return self, {}
        return replace(self, lines=tuple(updated), status=ShipmentStatus.ASSEMBLED, version=self.version + 1), taken

    def unpick(self, line_id: UUID) -> tuple[Shipment, UUID, Decimal]:
        if self.status in {ShipmentStatus.PACKED, ShipmentStatus.SHIPPED, ShipmentStatus.CLOSED, ShipmentStatus.CANCELLED}:
            raise ShippingValidationError("В текущем статусе возврат отбора недоступен")
        updated: list[ShipmentLine] = []
        released: tuple[UUID, Decimal] | None = None
        for line in self.lines:
            if line.id == line_id:
                if line.picked <= 0:
                    raise ShippingValidationError("По позиции ничего не набрано")
                released = (line.finished_good_id, line.picked)
                updated.append(replace(line, picked=Decimal("0"), grn=None))
            else:
                updated.append(line)
        if released is None:
            raise ShippingValidationError("Позиция отгрузки не найдена")
        status = ShipmentStatus.ASSEMBLED if any(line.picked > 0 for line in updated) else ShipmentStatus.NEW
        return replace(self, lines=tuple(updated), status=status, version=self.version + 1), released[0], released[1]

    def pack(self, pl_number: str, dn_number: str, day: date) -> Shipment:
        if not any(line.picked > 0 for line in self.lines):
            raise ShippingValidationError("Нельзя упаковать: ни одна позиция не набрана")
        if self.status != ShipmentStatus.ASSEMBLED:
            raise ShippingValidationError("В текущем статусе упаковка недоступна")
        return replace(
            self, status=ShipmentStatus.PACKED, pl_number=_text(pl_number, "Номер PL"),
            dn_number=_text(dn_number, "Номер DN"), packed_at=day, version=self.version + 1,
        )

    def update_documents(
        self, *, upd_type: UpdType, upd_number: str, upd_date: date | None,
        upd_url: str, track_number: str,
    ) -> Shipment:
        if self.status in {ShipmentStatus.CLOSED, ShipmentStatus.CANCELLED}:
            raise ShippingValidationError("Закрытую или отменённую отгрузку нельзя менять")
        return replace(
            self, upd_type=upd_type, upd_number=str(upd_number or "").strip(),
            upd_date=upd_date, upd_url=str(upd_url or "").strip(),
            track_number=str(track_number or "").strip(), version=self.version + 1,
        )

    def mark_shipped(self, day: date) -> Shipment:
        if self.status != ShipmentStatus.PACKED:
            raise ShippingValidationError("Сначала наберите и упакуйте отгрузку")
        if not any(line.picked > 0 for line in self.lines):
            raise ShippingValidationError("Нельзя отгрузить: ни одна позиция не набрана")
        if self.upd_type != UpdType.EDO and not self.upd_url:
            raise ShippingValidationError("Для бумажной отгрузки приложите файл УПД")
        return replace(self, status=ShipmentStatus.SHIPPED, shipped_at=day, version=self.version + 1)

    def close(self) -> Shipment:
        if self.status != ShipmentStatus.SHIPPED:
            raise ShippingValidationError("Закрыть можно только отгруженную заявку")
        missing = []
        if not self.upd_number:
            missing.append("№ УПД")
        if not self.upd_url:
            missing.append("файл УПД")
        if self.terms == DeliveryTerms.CARRIER and not self.track_number:
            missing.append("трек-номер")
        if not self.shipped_at:
            missing.append("дата отгрузки")
        if missing:
            raise ShippingValidationError("Нельзя закрыть: не хватает — " + ", ".join(missing))
        return replace(self, status=ShipmentStatus.CLOSED, version=self.version + 1)

    def cancel(self) -> tuple[Shipment, dict[UUID, Decimal]]:
        if self.status in {ShipmentStatus.SHIPPED, ShipmentStatus.CLOSED, ShipmentStatus.CANCELLED}:
            raise ShippingValidationError("Отгруженную, закрытую или отменённую заявку нельзя отменить")
        released: dict[UUID, Decimal] = {}
        lines = []
        for line in self.lines:
            if line.picked > 0:
                released[line.finished_good_id] = released.get(line.finished_good_id, Decimal("0")) + line.picked
            lines.append(replace(line, picked=Decimal("0"), grn=None))
        return replace(self, status=ShipmentStatus.CANCELLED, lines=tuple(lines), version=self.version + 1), released
