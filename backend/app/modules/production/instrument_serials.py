"""In-house serial numbers for individually inventoried measuring items and accessories."""

from dataclasses import replace
import re

from .domain import ProductionValidationError
from .traceability import MeasurementInstrument


IN_HOUSE_SERIAL_PREFIX = "ФР-"


def in_house_serial_number(inventory_number: str) -> str:
    match = re.search(r"([0-9]+)$", inventory_number.strip())
    if match is None:
        raise ProductionValidationError("В инвентарном номере нет числовой части для собственного номера")
    number = IN_HOUSE_SERIAL_PREFIX + match[1]
    if len(number) > 128:
        raise ProductionValidationError("Собственный номер длиннее 128 символов")
    return number


def assign_in_house_serial(item: MeasurementInstrument) -> MeasurementInstrument:
    """Keep every existing serial; an empty serial derives from the inventory's numeric suffix."""
    if item.serial_number.strip():
        return item
    return replace(item, serial_number=in_house_serial_number(item.inventory_number))
