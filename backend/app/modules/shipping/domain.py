"""Pure finished-goods and shipment rules ported from Shipment.gs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import re
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

    def reorder(self, ordered: Decimal) -> FinishedGood:
        if self.shipped > 0:
            raise ShippingValidationError("Позиция уже отгружалась: количество в заказе не меняется")
        return replace(self, ordered=_positive(ordered, "Количество в заказе"), version=self.version + 1)

    @property
    def has_movement(self) -> bool:
        """Accepted or shipped goods outlive the project card (`shipGpDropProject_`)."""
        return self.accepted > 0 or self.shipped > 0

    def put_back(self, quantity: Decimal) -> FinishedGood:
        amount = _positive(quantity, "Количество возврата")
        if amount > self.shipped:
            raise ShippingValidationError("Возврат не может превышать отобранное количество")
        return replace(self, shipped=self.shipped - amount, version=self.version + 1)


ADDRESSEE_CONTACT_FIELDS = ("shipping_point", "contact", "phone")
ADDRESSEE_CARD_FIELDS = ("receiver", "shipping_point", "contact", "phone", "terms", "carrier", "upd_type")
# Реквизиты отгрузочной ведомости (форма 04.1) в карточке проекта: `SHIP_PROJ_COLS_` «ЗАКАЗ №» … «ВРЕМЯ РАБОТЫ».
ADDRESSEE_REQUISITE_FIELDS = ("po", "contract", "spec", "invoice", "pays", "customer_address", "hours")


@dataclass(frozen=True)
class ProjectAddressee:
    """Project card's shipping defaults (`ОТГРУЗКА.ПРОЕКТЫ`): one row per project."""

    project: str
    version: int = 0
    receiver: str = ""
    shipping_point: str = ""
    contact: str = ""
    phone: str = ""
    terms: DeliveryTerms | None = None
    carrier: str = ""
    upd_type: UpdType | None = None
    updated_by: str = ""
    updated_at: datetime | None = None
    po: str = ""
    contract: str = ""
    spec: str = ""
    invoice: str = ""
    pays: str = ""
    customer_address: str = ""
    hours: str = ""

    @classmethod
    def empty(cls, project: str) -> ProjectAddressee:
        return cls(project=_text(project, "Проект"))

    def remember(self, *, shipping_point: str, contact: str, phone: str) -> ProjectAddressee:
        """`shipProjPatch_` without replace: a non-empty value wins, an empty one never erases."""
        patch = {"shipping_point": shipping_point, "contact": contact, "phone": phone}
        changes = {}
        for key in ADDRESSEE_CONTACT_FIELDS:
            value = str(patch[key] or "").strip()
            if value and value != getattr(self, key):
                changes[key] = value
        return replace(self, **changes) if changes else self

    def edit(self, fields: dict) -> ProjectAddressee:
        """Card edit (`shipProjectSet_(…, replace)`, `shipProjShipFields_`): a sent field replaces, empty erases."""
        unknown = set(fields) - set(ADDRESSEE_CARD_FIELDS) - set(ADDRESSEE_REQUISITE_FIELDS)
        if unknown:
            raise ShippingValidationError("Неизвестное поле адресата: " + ", ".join(sorted(unknown)))
        changes = {}
        for key, value in fields.items():
            if key == "terms":
                changes[key] = DeliveryTerms(value) if value else None
            elif key == "upd_type":
                changes[key] = UpdType(value) if value else None
            else:
                changes[key] = str(value or "").strip()
        return replace(self, **changes)

    def differs(self, other: ProjectAddressee) -> bool:
        return any(getattr(self, key) != getattr(other, key) for key in ADDRESSEE_CARD_FIELDS + ADDRESSEE_REQUISITE_FIELDS)


@dataclass(frozen=True)
class EffectiveAddress:
    receiver: str
    shipping_point: str
    contact: str
    phone: str
    terms: DeliveryTerms | None
    carrier: str
    upd_type: UpdType | None
    inherited: frozenset[str]


def effective_address(
    *, receiver: str, shipping_point: str, contact: str, phone: str,
    terms: DeliveryTerms | None, carrier: str, upd_type: UpdType | None,
    project: ProjectAddressee | None, customer_fallback: str = "",
) -> EffectiveAddress:
    """`shipAddrEff_` + `shipUpdEff_`: the shipment's own non-empty field wins, an empty one inherits.

    The receiver also falls back to the customer, like `shipPackProject_`. Unlike `shipAddrEff_` the
    project receiver does not override a typed one: ERP never writes the customer as a placeholder.
    """
    own = {
        "receiver": str(receiver or "").strip(), "shipping_point": str(shipping_point or "").strip(),
        "contact": str(contact or "").strip(), "phone": str(phone or "").strip(),
        "terms": terms, "carrier": str(carrier or "").strip(), "upd_type": upd_type,
    }
    result = {}
    inherited = set()
    for key in ADDRESSEE_CARD_FIELDS:
        theirs = getattr(project, key) if project is not None else None
        if own[key]:
            result[key] = own[key]
        elif theirs:
            result[key] = theirs
            inherited.add(key)
        else:
            result[key] = own[key]
    if not result["receiver"]:
        result["receiver"] = str(customer_fallback or "").strip()
    return EffectiveAddress(**result, inherited=frozenset(inherited))


@dataclass(frozen=True)
class ProjectAssembly:
    """One КБ of the project card: what the finished-goods queue is built from."""

    item_index: str
    name: str
    run: Decimal
    article: str = ""


@dataclass(frozen=True)
class QueuePlan:
    add: tuple[ProjectAssembly, ...]
    update: tuple[tuple[FinishedGood, Decimal], ...]
    skipped: tuple[str, ...]


def _index_key(value: str | None) -> str:
    return str(value or "").strip().upper()


def plan_project_queue(assemblies: tuple[ProjectAssembly, ...] | list[ProjectAssembly], existing: list[FinishedGood] | tuple[FinishedGood, ...]) -> QueuePlan:
    """`shipQueuePlan_`: a row per new КБ, the run follows the card until something ships.

    Idempotent: queuing the same card twice adds and updates nothing. A КБ without an index is
    ignored; a КБ without a positive run cannot become a finished-goods row and is reported.
    """
    by_index = {_index_key(good.item_index): good for good in existing}
    add: list[ProjectAssembly] = []
    update: list[tuple[FinishedGood, Decimal]] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for assembly in assemblies:
        key = _index_key(assembly.item_index)
        if not key or key in seen:
            continue
        seen.add(key)
        run = Decimal(assembly.run or 0)
        current = by_index.get(key)
        if current is not None and current.shipped > 0:
            continue
        if not run.is_finite() or run <= 0:
            skipped.append(assembly.item_index.strip())
            continue
        if current is None:
            add.append(replace(assembly, item_index=assembly.item_index.strip(), run=run))
        elif current.ordered.quantize(Decimal("0.01")) != run.quantize(Decimal("0.01")):
            update.append((current, run))
    return QueuePlan(add=tuple(add), update=tuple(update), skipped=tuple(skipped))


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
    # «ПРИМЕЧАНИЯ ДЛЯ ТК» и места (`ОТГРУЗКА.МЕСТА`) из окна «Сформировать отгрузочные».
    tk_note: str = ""
    boxes: tuple[ShipmentBox, ...] = ()

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
        """`shipShipped_`: груз уехал из любого активного статуса; гейт — `shipGateMiss_('Отгружена')`:
        набрана хоть одна позиция и, кроме ЭДО, приложен файл УПД."""
        if self.status not in {ShipmentStatus.NEW, ShipmentStatus.ASSEMBLED, ShipmentStatus.PACKED}:
            raise ShippingValidationError("Отгрузить можно только активную заявку")
        if not any(line.picked > 0 for line in self.lines):
            raise ShippingValidationError("Нельзя отгрузить: ни одна позиция не набрана")
        if self.upd_type != UpdType.EDO and not self.upd_url:
            raise ShippingValidationError("Для бумажной отгрузки приложите файл УПД")
        return replace(self, status=ShipmentStatus.SHIPPED, shipped_at=self.shipped_at or day, version=self.version + 1)

    def set_requested(self, line_id: UUID, quantity: Decimal) -> Shipment:
        """`shipItemSetQty_`: менеджер передумал — «Заказано» не ниже уже набранного, в любом статусе.

        Как в SUPPLYMAP, статус не проверяется: «Заказано» не двигает склад ГП (его двигает только набор),
        поэтому правка закрытой или отменённой отправки регистр остатка не ломает.
        """
        amount = _round2(Decimal(quantity))
        if not amount.is_finite() or amount <= 0:
            raise ShippingValidationError("Количество должно быть больше нуля")
        updated = []
        found = False
        for line in self.lines:
            if line.id == line_id:
                found = True
                if amount < line.picked:
                    raise ShippingValidationError(f"По позиции уже набрано {_plain(line.picked)} — сначала верни на склад")
                updated.append(replace(line, requested=amount))
            else:
                updated.append(line)
        if not found:
            raise ShippingValidationError("Позиция отгрузки не найдена")
        return replace(self, lines=tuple(updated), version=self.version + 1)

    def lay_out(self, boxes: tuple[ShipmentBox, ...], tk_note: str) -> Shipment:
        """`portalShipDocs`: раскладка по коробам и примечания для ТК перед сборкой PL/DN."""
        if self.status in {ShipmentStatus.CLOSED, ShipmentStatus.CANCELLED}:
            raise ShippingValidationError("Закрытую или отменённую отгрузку нельзя менять")
        return replace(self, boxes=boxes, tk_note=str(tk_note or "").strip(), version=self.version + 1)

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


def _round2(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def _plain(value: Decimal) -> str:
    text = format(Decimal(value).normalize(), "f")
    return text


@dataclass(frozen=True)
class BoxItem:
    item_index: str
    quantity: Decimal


@dataclass(frozen=True)
class ShipmentBox:
    """Одно место отправки (`ОТГРУЗКА.МЕСТА`): вид упаковки, габариты, вес текстом «12,5», пломба, состав."""

    place: int
    kind: str = "Короб"
    dims: str = ""
    net: str = ""
    gross: str = ""
    seal: str = ""
    items: tuple[BoxItem, ...] = ()


@dataclass(frozen=True)
class PackedItem:
    item_index: str
    quantity: Decimal
    article: str
    name: str
    unit: str
    grn: str


def packed_by_index(lines: tuple[ShipmentLine, ...] | list[ShipmentLine]) -> list[PackedItem]:
    """`shipPackedByIndex_`: набранное по КБ, две строки одной КБ складываются, порядок — как в отправке."""
    order: list[str] = []
    by: dict[str, PackedItem] = {}
    for line in lines:
        if not line.picked > 0:
            continue
        current = by.get(line.item_index)
        if current is None:
            order.append(line.item_index)
            by[line.item_index] = PackedItem(line.item_index, _round2(line.picked), line.article, line.name, line.unit or "шт", line.grn or "")
        else:
            by[line.item_index] = replace(current, quantity=_round2(current.quantity + line.picked))
    return [by[key] for key in order]


_WEIGHT = re.compile(r"^\d+(,\d+)?$")


def check_boxes(packed: list[PackedItem], boxes: list[dict]) -> tuple[ShipmentBox, ...]:
    """`shipBoxesCheck_`: каждая КБ разложена ровно на упакованное, пустых коробов нет, вес — число."""
    need = {item.item_index: _round2(item.quantity) for item in packed}
    got = {key: Decimal("0") for key in need}
    if not boxes:
        raise ShippingValidationError("Нет ни одного короба.")
    out: list[ShipmentBox] = []
    for number, raw in enumerate(boxes, start=1):
        items: list[BoxItem] = []
        for entry in raw.get("items") or []:
            index = str(entry.get("item_index") or "").strip()
            try:
                quantity = _round2(Decimal(str(entry.get("quantity") or 0).replace(",", ".")))
            except ArithmeticError:
                quantity = Decimal("0")
            if not quantity > 0:
                continue
            if index not in need:
                raise ShippingValidationError(f"BXN {number}: {index} нет в отправке.")
            got[index] = _round2(got[index] + quantity)
            items.append(BoxItem(index, quantity))
        if not items:
            raise ShippingValidationError(f"BXN {number} пустой — убери короб или положи в него позиции.")
        net = str(raw.get("net") or "").strip().replace(".", ",", 1)
        gross = str(raw.get("gross") or "").strip().replace(".", ",", 1)
        if net and not _WEIGHT.match(net):
            raise ShippingValidationError(f"BXN {number}: вес нетто — число в кг, например 12,5.")
        if gross and not _WEIGHT.match(gross):
            raise ShippingValidationError(f"BXN {number}: вес брутто — число в кг, например 13,6.")
        out.append(ShipmentBox(
            place=number, kind=str(raw.get("kind") or "").strip() or "Короб", dims=str(raw.get("dims") or "").strip(),
            net=net, gross=gross, seal=str(raw.get("seal") or "").strip(), items=tuple(items),
        ))
    missing = [key for key in need if got[key] != need[key]]
    if missing:
        raise ShippingValidationError("Разложено не всё упакованное: " + "; ".join(
            f"{key} — {_plain(got[key])} из {_plain(need[key])}" for key in missing) + ".")
    return tuple(out)


def default_boxes(packed: list[PackedItem]) -> tuple[ShipmentBox, ...]:
    """Раскладки нет — один короб со всем упакованным (`shipBuildDocs_`)."""
    return (ShipmentBox(place=1, items=tuple(BoxItem(item.item_index, item.quantity) for item in packed)),)


def total_gross(boxes: tuple[ShipmentBox, ...]) -> str:
    """`shipTotalGross_`: сумма брутто; хоть у одного короба пусто — пусто."""
    if not boxes:
        return ""
    total = Decimal("0")
    for box in boxes:
        if not box.gross.strip():
            return ""
        total += Decimal(box.gross.replace(",", "."))
    return _plain(_round2(total)).replace(".", ",")
