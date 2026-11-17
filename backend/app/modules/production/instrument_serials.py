"""In-house serial numbers for individually inventoried measuring items and accessories."""

from dataclasses import dataclass, replace
from datetime import datetime
import re

from app.shared.clock import MOSCOW

from .domain import ProductionValidationError
from .rf_interfaces import RfDetails, propose_details
from .traceability import MeasurementInstrument


TYPE_RULES = (
    ("ADP", r"^(?:Переход|ADP1A)"),
    ("CAB", r"^Кабель"),
    ("KIT", r"^(?:Комплект|Набор.*мер)"),
    ("LOAD", r"^Нагрузка"),
    ("ACM", r"^(?:Модуль\s+калибровочн|Автоматический\s+калибровочный\s+модуль)|\bACM"),
)
CONNECTOR_LETTERS = {"SMA": "S", "RP-SMA": "P", "N": "N", "BNC": "B", "TNC": "T", "F": "F", "3.5mm": "K"}
IN_HOUSE_SERIAL = re.compile(r"(?:ADP|CAB|KIT|LOAD|ACM|SI)(?:-[A-Za-z]{1,2})?-[0-9]{2}-[0-9]{3,}")


@dataclass(frozen=True)
class InstrumentSerialContext:
    created_at: datetime | None = None
    rf: RfDetails | None = None


def is_in_house_serial(serial: str) -> bool:
    serial = serial.strip()
    return serial.startswith("ФР-") or IN_HOUSE_SERIAL.fullmatch(serial) is not None


def in_house_serial_number(item: MeasurementInstrument, *, now: datetime,
                           context: InstrumentSerialContext = InstrumentSerialContext()) -> str:
    match = re.search(r"([0-9]+)$", item.inventory_number.strip())
    if match is None:
        raise ProductionValidationError("В инвентарном номере нет числовой части для собственного номера")
    type_code = next((code for value in (item.name, item.doc_designation) for code, pattern in TYPE_RULES
                      if re.search(pattern, value.strip(), re.I)), "SI")
    details = context.rf if context.rf is not None else propose_details(item.inventory_number, item.name)
    ends = []
    for port in details.interfaces:
        series = "RP-SMA" if port.series == "SMA" and ((port.body == "вилка") != (port.center == "штырь")) else port.series
        letter = CONNECTOR_LETTERS.get(series, "")
        ends.append(letter if port.center == "штырь" else letter.lower())
    connectors = "".join(ends)
    created = context.created_at or now
    year = (created.astimezone(MOSCOW) if created.tzinfo is not None else created).year % 100
    suffix = match[1].lstrip("0").zfill(3)
    number = f"{type_code}{'-' + connectors if connectors else ''}-{year:02d}-{suffix}"
    if len(number) > 128:
        raise ProductionValidationError("Собственный номер длиннее 128 символов")
    return number


def assign_in_house_serial(item: MeasurementInstrument, *, now: datetime,
                           context: InstrumentSerialContext = InstrumentSerialContext()) -> MeasurementInstrument:
    """Keep every existing serial; an empty serial derives from the inventory's numeric suffix."""
    if item.serial_number.strip():
        return item
    return replace(item, serial_number=in_house_serial_number(item, now=now, context=context))
