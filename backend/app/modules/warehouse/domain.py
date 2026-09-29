"""Pure lot accounting rules for warehouse stock by GRN and package."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.shared.warehouse_location import (
    existing_storage_location,
    normalize_storage_location,
    storage_location_as_is,
)


WAREHOUSES = ("ЦАР", "ЖУК")


class WarehouseValidationError(ValueError):
    """A warehouse operation would violate physical stock accounting."""


def normalize_location(value: str) -> str:
    """A place the stock goes to: a rack code that exists in the rack layout (SK-9)."""
    try:
        return existing_storage_location(value)
    except ValueError as error:
        raise WarehouseValidationError(str(error)) from error


def normalize_location_code(value: str) -> str:
    """A place stock is taken from: only the code format. A place that left the layout still empties."""
    try:
        return normalize_storage_location(value)
    except ValueError as error:
        raise WarehouseValidationError(str(error)) from error


def normalize_location_as_is(value: str) -> str:
    try:
        return storage_location_as_is(value)
    except ValueError as error:
        raise WarehouseValidationError(str(error)) from error


def _quantity(value: Decimal, label: str) -> Decimal:
    normalized = Decimal(value)
    if normalized <= 0:
        raise WarehouseValidationError(f"{label} должно быть больше нуля")
    return normalized


# Text passport fields of a lot (SUPPLYMAP Config.gs WAREHOUSE_STOCK_COLS 172-174) → their longest value.
LOT_DETAIL_LIMITS = {
    "manufacturer": 255,  # ПРОИЗВОДИТЕЛЬ
    "description": 4096,  # ОПИСАНИЕ
    "storage_conditions": 1024,  # УСЛОВИЯ ХРАНЕНИЯ
    "datasheet": 4096,  # DATASHEET: a link or a name, as typed
    "certificate": 4096,  # СЕРТИФИКАТ: a link or a number, as typed
}


def _detail(field_name: str, value: str) -> str:
    text = str(value or "").strip()
    if len(text) > LOT_DETAIL_LIMITS[field_name]:
        raise WarehouseValidationError(f"Слишком длинное поле {field_name}")
    return text


def _unit_mass(value: Decimal | None) -> Decimal | None:
    """МАССА 1 ШТ, Г as the lot keeps it. SUPPLYMAP writes the mass into the stock row only when it is
    truthy (Warehouse.gs:508, 527 `if (it2.mass)`), so a zero or missing mass means «no mass»."""
    if value is None:
        return None
    mass = Decimal(value)
    if mass < 0:
        raise WarehouseValidationError("Масса 1 шт не может быть отрицательной")
    return mass if mass > 0 else None


# Fields of the manual row edit (SUPPLYMAP STOCK05_EDIT_TEXT_ / STOCK05_EDIT_NUM_, Stock05.gs:982-993) → the
# stock sheet column, used in the «было → стало» journal. DATASHEET and СЕРТИФИКАТ are edited by hand in the
# SUPPLYMAP sheet; ERP has no sheet, so they are in the edit form too.
LOT_ROW_FIELDS = {
    "article": "АРТИКУЛ",
    "series": "СЕРИЯ",
    "name": "НАИМЕНОВАНИЕ",
    "item_type": "ТИП",
    "unit": "ЕД.ИЗМ",
    "purpose": "НАЗНАЧЕНИЕ",
    "storage_location": "МЕСТО ХРАНЕНИЯ",
    "unit_mass_g": "МАССА 1 ШТ, Г",
    "expiry_date": "СРОК ГОДНОСТИ",
    "manufacturer": "ПРОИЗВОДИТЕЛЬ",
    "description": "ОПИСАНИЕ",
    "storage_conditions": "УСЛОВИЯ ХРАНЕНИЯ",
    "datasheet": "DATASHEET",
    "certificate": "СЕРТИФИКАТ",
    "inbound": "ПРИХОД",
    "written_off": "СПИСАНО",
    "reserved": "РЕЗЕРВ",
}
LOT_ROW_QUANTITIES = ("inbound", "written_off", "reserved")


def _shown(value) -> str:
    """A cell value as the journal shows it: a number without trailing zeros, a date as ISO, None as ""."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def lot_row_changes(before: StockLot, after: StockLot) -> list[tuple[str, str, str, str]]:
    """(field, column, было, стало) for every edited field that changed, БАЛАНС included when it moved —
    SUPPLYMAP writes it as one more changed cell (Stock05.gs:1068)."""
    changes = [
        (field_name, column, _shown(getattr(before, field_name)), _shown(getattr(after, field_name)))
        for field_name, column in LOT_ROW_FIELDS.items()
        if getattr(before, field_name) != getattr(after, field_name)
    ]
    if before.balance != after.balance:
        changes.append(("balance", "БАЛАНС", _shown(before.balance), _shown(after.balance)))
    return changes


@dataclass(frozen=True)
class StockLot:
    id: UUID
    version: int
    warehouse: str
    grn: str
    package: str
    article: str
    series: str
    name: str
    item_type: str
    purpose: str
    unit: str
    storage_location: str
    inbound: Decimal
    written_off: Decimal
    reserved: Decimal
    source_receiving_ids: tuple[str, ...]
    # Lot passport (SK-6): МАССА 1 ШТ, Г, СРОК ГОДНОСТИ, ПРОИЗВОДИТЕЛЬ, ОПИСАНИЕ, УСЛОВИЯ ХРАНЕНИЯ,
    # DATASHEET, СЕРТИФИКАТ. None / "" = not filled.
    unit_mass_g: Decimal | None = None
    expiry_date: date | None = None
    manufacturer: str = ""
    description: str = ""
    storage_conditions: str = ""
    datasheet: str = ""
    certificate: str = ""

    @classmethod
    def create(
        cls,
        *,
        warehouse: str,
        grn: str,
        package: str,
        name: str,
        item_type: str,
        purpose: str,
        unit: str,
        quantity: Decimal,
        storage_location: str,
        source_receiving_ids: tuple[str, ...],
        article: str = "",
        series: str = "",
        location_as_is: bool = False,
        unit_mass_g: Decimal | None = None,
        expiry_date: date | None = None,
        manufacturer: str = "",
        description: str = "",
        storage_conditions: str = "",
        datasheet: str = "",
        certificate: str = "",
    ) -> StockLot:
        """location_as_is — old data: an empty place or an old address is kept as it was."""
        if str(warehouse).strip() not in WAREHOUSES:
            raise WarehouseValidationError("Неизвестный склад")
        if not str(grn).strip():
            raise WarehouseValidationError("ГРН обязателен")
        if not source_receiving_ids:
            raise WarehouseValidationError("Источник приёмки обязателен")
        normalized_name = str(name or "").strip()
        normalized_article = str(article or "").strip() or normalized_name
        normalized_purpose = str(purpose or "").strip()
        normalized_unit = str(unit or "").strip()
        if not normalized_article:
            raise WarehouseValidationError("Артикул обязателен")
        if not normalized_purpose:
            raise WarehouseValidationError("Назначение обязательно")
        if not normalized_unit:
            raise WarehouseValidationError("Единица измерения обязательна")
        return cls(
            id=uuid4(),
            version=1,
            warehouse=str(warehouse).strip(),
            grn=str(grn).strip(),
            package=str(package or "").strip(),
            article=normalized_article,
            series=str(series or "").strip(),
            name=normalized_name,
            item_type=str(item_type).strip(),
            purpose=normalized_purpose,
            unit=normalized_unit,
            storage_location=(
                normalize_location_as_is(storage_location) if location_as_is else normalize_location(storage_location)
            ),
            inbound=_quantity(quantity, "Приход"),
            written_off=Decimal("0"),
            reserved=Decimal("0"),
            source_receiving_ids=tuple(dict.fromkeys(source_receiving_ids)),
            unit_mass_g=_unit_mass(unit_mass_g),
            expiry_date=expiry_date,
            manufacturer=_detail("manufacturer", manufacturer),
            description=_detail("description", description),
            storage_conditions=_detail("storage_conditions", storage_conditions),
            datasheet=_detail("datasheet", datasheet),
            certificate=_detail("certificate", certificate),
        )

    @property
    def key(self) -> tuple[str, str, str]:
        return self.warehouse, self.grn, self.package

    @property
    def balance(self) -> Decimal:
        return self.inbound - self.written_off

    @property
    def available(self) -> Decimal:
        return self.balance - self.reserved

    def reserve(self, quantity: Decimal) -> StockLot:
        amount = _quantity(quantity, "Резерв")
        if amount > self.available:
            raise WarehouseValidationError("Недостаточно доступного остатка для резерва")
        return replace(self, reserved=self.reserved + amount, version=self.version + 1)

    def release_reserve(self, quantity: Decimal) -> StockLot:
        amount = _quantity(quantity, "Снятие резерва")
        if amount > self.reserved:
            raise WarehouseValidationError("Нельзя снять больше существующего резерва")
        return replace(self, reserved=self.reserved - amount, version=self.version + 1)

    def write_off(self, quantity: Decimal) -> StockLot:
        amount = _quantity(quantity, "Списание")
        if amount > self.balance:
            raise WarehouseValidationError("Недостаточно остатка для списания")
        if self.balance - amount < self.reserved:
            raise WarehouseValidationError("Списание затрагивает зарезервированный остаток")
        return replace(self, written_off=self.written_off + amount, version=self.version + 1)

    def issue_reserved(self, quantity: Decimal) -> StockLot:
        amount = _quantity(quantity, "Выдача резерва")
        if amount > self.reserved:
            raise WarehouseValidationError("Нельзя выдать больше зарезервированного количества")
        if amount > self.balance:
            raise WarehouseValidationError("Недостаточно физического остатка для выдачи")
        return replace(
            self,
            written_off=self.written_off + amount,
            reserved=self.reserved - amount,
            version=self.version + 1,
        )

    def return_writeoff(self, quantity: Decimal) -> StockLot:
        amount = _quantity(quantity, "Возврат списания")
        if amount > self.written_off:
            raise WarehouseValidationError("Нельзя вернуть больше, чем было списано")
        return replace(self, written_off=self.written_off - amount, version=self.version + 1)

    def move(self, storage_location: str) -> StockLot:
        # Keeping the current place is not a move, even when it is an old address.
        if self.storage_location and str(storage_location or "").strip() == self.storage_location:
            return self
        normalized = normalize_location(storage_location)
        if normalized == self.storage_location:
            return self
        return replace(self, storage_location=normalized, version=self.version + 1)

    def transfer(self, warehouse: str, storage_location: str = "") -> StockLot:
        """Whole lot to the other warehouse (SUPPLYMAP Stock05.gs stock05Move_): same aggregate, same
        ПРИХОД/СПИСАНО, so the balance and the write-off history travel with it. No place given → the lot
        keeps its current one, as `place || МЕСТО ХРАНЕНИЯ` in SUPPLYMAP. The caller checks the target key."""
        target = str(warehouse or "").strip()
        if target not in WAREHOUSES:
            raise WarehouseValidationError(f"Неизвестный склад «{target}».")
        if target == self.warehouse:
            return self.move(storage_location) if str(storage_location or "").strip() else self
        if self.balance <= 0:
            raise WarehouseValidationError(f"Нечего перемещать: остаток по {self.grn} пуст.")
        if self.reserved > 0:
            raise WarehouseValidationError(
                f"Партия {self.grn} в резерве ({format(self.reserved.normalize(), 'f')}): "
                "сначала сними резерв, потом перемещай на другой склад."
            )
        place = self.move(storage_location).storage_location if str(storage_location or "").strip() else self.storage_location
        return replace(self, warehouse=target, storage_location=place, version=self.version + 1)

    def correct_location(self, storage_location: str) -> StockLot:
        """Manual correction «as it really is»: any text, an old address or no place at all."""
        corrected = normalize_location_as_is(storage_location)
        if corrected == self.storage_location:
            return self
        return replace(self, storage_location=corrected, version=self.version + 1)

    def edit_identity(self, *, article: str, name: str, series: str) -> StockLot:
        normalized_article = str(article or "").strip()
        if not normalized_article:
            raise WarehouseValidationError("Артикул обязателен")
        values = {
            "article": normalized_article,
            "name": str(name or "").strip(),
            "series": str(series or "").strip(),
        }
        if all(getattr(self, field) == value for field, value in values.items()):
            return self
        return replace(self, **values, version=self.version + 1)

    def edit_row(self, changes: Mapping[str, object], *, kitting_reserve: Decimal = Decimal("0")) -> StockLot:
        """Manual edit of the whole stock row (SUPPLYMAP Stock05.gs stock05EditRowLocked_ 1027-1079).

        `changes` holds only the fields to set (a missing key is left alone); a text field set to "" is
        cleared. ГРН, ПАКЕТ and СКЛАД are the key of the row and are not edited here. БАЛАНС is not a field:
        it follows ПРИХОД − СПИСАНО. `kitting_reserve` is what kitting lines hold on this lot — РЕЗЕРВ
        cannot go under it, or unbinding/issuing those lines would fail."""
        key_fields = set(changes) & {"grn", "package", "warehouse"}
        if key_fields:
            raise WarehouseValidationError("ГРН, пакет и склад — ключ партии, правкой строки не меняются.")
        unknown = set(changes) - set(LOT_ROW_FIELDS)
        if unknown:
            raise WarehouseValidationError(f"Поле не правится: {', '.join(sorted(unknown))}")
        values: dict[str, object] = {}
        for field_name, raw in changes.items():
            if field_name in LOT_ROW_QUANTITIES:
                amount = Decimal(raw)
                if amount < 0:
                    raise WarehouseValidationError(f"{LOT_ROW_FIELDS[field_name]} не может быть отрицательным")
                values[field_name] = amount
            elif field_name == "storage_location":
                values[field_name] = normalize_location_as_is(str(raw or ""))
            elif field_name == "unit_mass_g":
                values[field_name] = _unit_mass(raw)
            elif field_name == "expiry_date":
                if raw is not None and not isinstance(raw, date):
                    raise WarehouseValidationError("Срок годности — дата")
                values[field_name] = raw
            elif field_name in LOT_DETAIL_LIMITS:
                values[field_name] = _detail(field_name, raw)
            else:
                values[field_name] = str(raw or "").strip()
        for field_name, message in (
            ("article", "Артикул обязателен"),
            ("purpose", "Назначение обязательно"),
            ("unit", "Единица измерения обязательна"),
        ):
            if field_name in values and not values[field_name]:
                raise WarehouseValidationError(message)
        updated = replace(self, **values)
        if updated == self:
            return self
        unit = f" {self.unit}" if self.unit else ""
        if updated.balance < 0:
            raise WarehouseValidationError(
                f"СПИСАНО ({_shown(updated.written_off)}) больше ПРИХОДА ({_shown(updated.inbound)}): "
                "остаток ушёл бы в минус."
            )
        if updated.reserved > updated.balance:
            raise WarehouseValidationError(
                f"РЕЗЕРВ ({_shown(updated.reserved)}) больше остатка ({_shown(updated.balance)}{unit})."
            )
        if updated.reserved != self.reserved and updated.reserved < kitting_reserve:
            raise WarehouseValidationError(
                f"Комплектация держит на партии {self.grn} резерв {_shown(kitting_reserve)}{unit}: РЕЗЕРВ меньше "
                "не ставится. Сначала сними резерв в комплектации."
            )
        return replace(updated, version=self.version + 1)

    def receive(
        self,
        quantity: Decimal,
        source_receiving_id: str,
        *,
        article: str = "",
        series: str = "",
        purpose: str = "",
        storage_location: str = "",
        expiry_date: date | None = None,
        unit_mass_g: Decimal | None = None,
    ) -> StockLot:
        """Repeated receipt of the same ГРН+ПАКЕТ (SUPPLYMAP Warehouse.gs applyWarehouseAdds_ 492-515): the
        incoming НАЗНАЧЕНИЕ and МЕСТО ХРАНЕНИЯ replace the lot's; an empty one keeps what the lot has.
        The place goes through the same strict check as the first receipt. СРОК ГОДНОСТИ is always the
        incoming one, even empty (line 498); МАССА 1 ШТ changes only with a real mass (line 508)."""
        source = str(source_receiving_id or "").strip()
        if not source:
            raise WarehouseValidationError("Источник приёмки обязателен")
        if source in self.source_receiving_ids:
            return self
        amount = _quantity(quantity, "Приход")
        place = str(storage_location or "").strip()
        return replace(
            self,
            article=self.article or str(article or "").strip(),
            series=str(series or "").strip() or self.series,
            purpose=str(purpose or "").strip() or self.purpose,
            storage_location=normalize_location(place) if place else self.storage_location,
            expiry_date=expiry_date,
            unit_mass_g=_unit_mass(unit_mass_g) or self.unit_mass_g,
            inbound=self.inbound + amount,
            source_receiving_ids=(*self.source_receiving_ids, source),
            version=self.version + 1,
        )


# The СПИСАНИЕ journal (SUPPLYMAP Config.gs:182 WAREHOUSE_WRITEOFF_COLS): ДАТА, СКЛАД, ГРН, ПАКЕТ, НАИМЕНОВАНИЕ,
# КОЛ-ВО, ЕД.ИЗМ, ОСНОВАНИЕ, НАЗНАЧЕНИЕ, КТО СПИСАЛ, ПРОВЕДЕНО, ОСТАТОК ПОСЛЕ. One row per movement of one lot; a
# return is a row whose ОСНОВАНИЕ starts with «Возврат» (writeoffIsReturn_), КОЛ-ВО stays positive.
WRITEOFF_KINDS = (
    "writeoff",  # manual write-off of a lot (Stock05.gs stock05Writeoff_)
    "return",  # manual return of a write-off to its lot (Stock05.gs stock05Return_)
    "issue",  # kitting request handed out, «КБ Выдана» (Issue.gs:475-502)
    "legacy",  # a SUPPLYMAP СПИСАНИЕ row brought in by the stock import (WH-IMPORT)
    # an accepted lot goes back to its supplier (SupplierReturn.gs, RC-6). SUPPLYMAP journals it with
    # ОСНОВАНИЕ «Возврат поставщику: …», which its tick then takes for a return TO stock
    # (writeoffIsReturn_) — ERP keeps its own kind, so the basis text never flips the sign.
    "supplier_return",
)
ISSUE_WRITEOFF_AUTHOR = "выдача по заявке"  # КТО СПИСАЛ of an issue row (Issue.gs:500)


def issue_writeoff_reason(request_number: str) -> str:
    """ОСНОВАНИЕ of an issue row: «Выдача » + the request number (Issue.gs:499)."""
    return f"Выдача {str(request_number).strip()}"


@dataclass(frozen=True)
class WriteOffEntry:
    """One append-only row of the warehouse write-off journal (СПИСАНИЕ).

    `occurred_at` is ДАТА (ERP keeps the time as well), `remaining_after` is ОСТАТОК ПОСЛЕ — the lot balance
    (ПРИХОД − СПИСАНО) once the movement is posted, None when it is not known. `source_key` is unique: the same
    movement cannot be journalled twice."""

    id: UUID
    occurred_at: datetime
    kind: str
    lot_id: UUID | None
    warehouse: str
    grn: str
    package: str
    name: str
    quantity: Decimal
    unit: str
    reason: str
    purpose: str
    author: str
    posted: bool
    remaining_after: Decimal | None
    actor_id: UUID
    actor_name: str
    source_key: str | None = None

    @classmethod
    def for_lot(
        cls,
        lot: StockLot,
        *,
        kind: str,
        quantity: Decimal,
        reason: str,
        purpose: str,
        author: str,
        actor_id: UUID,
        actor_name: str,
        occurred_at: datetime,
        remaining_after: Decimal | None,
        posted: bool = True,
        source_key: str | None = None,
        name: str | None = None,
    ) -> WriteOffEntry:
        """`name` — НАИМЕНОВАНИЕ when the movement names the item itself (an issue position); else the lot's."""
        if kind not in WRITEOFF_KINDS:
            raise WarehouseValidationError(f"Неизвестный вид строки списания: {kind}")
        amount = Decimal(quantity)
        if amount <= 0:
            raise WarehouseValidationError("Количество в строке списания должно быть больше нуля")
        return cls(
            id=uuid4(),
            occurred_at=occurred_at,
            kind=kind,
            lot_id=lot.id,
            warehouse=lot.warehouse,
            grn=lot.grn,
            package=lot.package,
            name=(lot.name if name is None else str(name).strip()),
            quantity=amount,
            unit=lot.unit,
            reason=str(reason or "").strip(),
            purpose=str(purpose or "").strip(),
            author=str(author or "").strip(),
            posted=posted,
            remaining_after=remaining_after,
            actor_id=actor_id,
            actor_name=str(actor_name or "").strip(),
            source_key=source_key,
        )
