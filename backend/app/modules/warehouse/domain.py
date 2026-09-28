"""Pure lot accounting rules for warehouse stock by GRN and package."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID, uuid4

from app.shared.warehouse_location import normalize_storage_location, storage_location_as_is


WAREHOUSES = ("ЦАР", "ЖУК")


class WarehouseValidationError(ValueError):
    """A warehouse operation would violate physical stock accounting."""


def normalize_location(value: str) -> str:
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

    def receive(
        self,
        quantity: Decimal,
        source_receiving_id: str,
        *,
        article: str = "",
        series: str = "",
    ) -> StockLot:
        source = str(source_receiving_id or "").strip()
        if not source:
            raise WarehouseValidationError("Источник приёмки обязателен")
        if source in self.source_receiving_ids:
            return self
        amount = _quantity(quantity, "Приход")
        return replace(
            self,
            article=self.article or str(article or "").strip(),
            series=str(series or "").strip() or self.series,
            inbound=self.inbound + amount,
            source_receiving_ids=(*self.source_receiving_ids, source),
            version=self.version + 1,
        )
