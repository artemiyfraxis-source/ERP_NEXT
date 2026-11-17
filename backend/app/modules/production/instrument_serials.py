"""In-house serial numbers for individually inventoried measuring items and accessories."""

from dataclasses import dataclass, replace
from datetime import datetime
import re

from app.shared.clock import MOSCOW

from .domain import ProductionValidationError
from .rf_interfaces import RfDetails, propose_details
from .traceability import MeasurementInstrument


TYPE_RULES = (
    ("ADP", r"^(?:(?:Прямой\s+|ВЧ-)?[Пп]ереход|ADP1A)"),
    ("CAB", r"^Кабель"),
    ("KIT", r"^(?:Комплект|Набор.*мер)"),
    ("LOAD", r"^Нагрузка"),
    ("ACM", r"^(?:Модуль\s+калибровочн|Автоматический\s+калибровочный\s+модуль)|\bACM"),
)
CONNECTOR_LETTERS = {"SMA": "S", "RP-SMA": "P", "N": "N", "BNC": "B", "TNC": "T", "F": "F", "3.5mm": "K"}
IN_HOUSE_SERIAL = re.compile(r"(?:ADP|CAB|KIT|LOAD|ACM|SI)(?:-[A-Za-z]{1,2})?-[0-9]{2}-[0-9]{3,}")


ARTICLE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")


@dataclass(frozen=True)
class InstrumentSerialContext:
    created_at: datetime | None = None
    rf: RfDetails | None = None
    taken: frozenset[str] = frozenset()  # every serial already in the registry (the next «.N» skips them)


def is_in_house_serial(serial: str) -> bool:
    """The earlier in-house schemes (ФР-NNN, ADP-ss-26-034): replaced by «article.N» (owner 18.11)."""
    serial = serial.strip()
    return serial.startswith("ФР-") or IN_HOUSE_SERIAL.fullmatch(serial) is not None


def article_of(item: MeasurementInstrument) -> str | None:
    """The supplier article from the name (its last article-like word, e.g. «B-311», «S-AA322»), else the model."""
    for value in (item.name, item.doc_designation):
        for word in reversed(value.replace("(", " ").replace(")", " ").split()):
            if ARTICLE.match(word) and any(ch.isdigit() for ch in word) and len(word) >= 3:
                return word
    return None


def in_house_serial_number(item: MeasurementInstrument, *, now: datetime,
                           context: InstrumentSerialContext = InstrumentSerialContext()) -> str:
    """«<article>.<N>»: the article keeps the number meaningful and short; N continues after the taken ones."""
    article = article_of(item)
    if article is None:
        raise ProductionValidationError("Нет артикула в наименовании или модели — задайте номер вручную")
    pattern = re.compile(re.escape(article) + r"\.([0-9]+)$")
    used = [int(match[1]) for serial in context.taken if (match := pattern.match(serial.strip()))]
    number = f"{article}.{max(used, default=0) + 1}"
    if len(number) > 128:
        raise ProductionValidationError("Номер длиннее 128 символов")
    return number


def assign_in_house_serial(item: MeasurementInstrument, *, now: datetime,
                           context: InstrumentSerialContext = InstrumentSerialContext()) -> MeasurementInstrument:
    """Keep a vendor serial; an empty or earlier in-house serial gets «article.N»."""
    if item.serial_number.strip() and not is_in_house_serial(item.serial_number):
        return item
    return replace(item, serial_number=in_house_serial_number(item, now=now, context=context))
