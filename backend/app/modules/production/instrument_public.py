"""Explicit public passport projection. Private card and attachment metadata never reach a renderer."""

from dataclasses import dataclass
from datetime import date
import re

from .instrument_characterization import Characterization, connector_line, number
from .rf_interfaces import RfDetails
from .traceability import InstrumentKind, MeasurementInstrument
from .vna_files import VnaTrace


@dataclass(frozen=True)
class PublicMeasurement:
    measured_on: date
    traces: tuple[VnaTrace, ...]
    instrument: str
    calibration: str
    conditions: tuple[str, ...]

    def points(self, name: str):
        return next(trace.points for trace in self.traces if trace.name == name)

    @property
    def band(self) -> tuple[float, float]:
        points = self.points("S11")
        return points[0][0] / 1e9, points[-1][0] / 1e9


@dataclass(frozen=True)
class PublicVerification:
    number: str
    verified_on: date | None
    valid_until: date | None


@dataclass(frozen=True)
class PublicPassport:
    inventory_number: str
    name: str
    serial_number: str
    manufacturer: str
    connectors: str
    impedance: str
    measurement: PublicMeasurement | None
    verification: PublicVerification | None


_SERIAL = r"([A-Za-z0-9-]{1,32})"
_TEMPERATURE = r"([+-]?\d{1,3}(?:\.\d{1,3})?(?:/[+-]?\d{1,3}(?:\.\d{1,3})?){0,3})"


def _public_measurement(measurement: Characterization) -> PublicMeasurement:
    instrument, calibration, conditions = "", "", ()
    for comment in measurement.comments:
        # Exact structured records emitted by the VNA tool. Unknown free text is never copied.
        if match := re.fullmatch(r"VNA serial=" + _SERIAL + r"; ACM serial=" + _SERIAL, comment):
            instrument = "ВАЦ №" + match[1]
            calibration = "АКМ №" + match[2]
        elif match := re.fullmatch(r"Temperatures VNA=" + _TEMPERATURE + r"; ACM=" + _TEMPERATURE + r" degC", comment):
            conditions = ("Температура ВАЦ: " + match[1].replace(".", ",").replace("/", " / ") + " °C",
                          "Температура АКМ: " + match[2].replace(".", ",").replace("/", " / ") + " °C")
        elif match := re.match(r"^Planar (S\d{3,8}) " + _SERIAL + r", cal АКМ (ACM\d{3,8}) " + _SERIAL + r" at ", comment):
            instrument = f"Planar {match[1]} №{match[2]}"
            calibration = f"АКМ {match[3]} №{match[4]}"
        elif match := re.fullmatch(r"S2VNA Planar (S\d{3,8}) №" + _SERIAL + r" \d{4}-\d{2}-\d{2}T[0-9:.+Z-]{5,32}", comment):
            instrument = f"Planar {match[1]} №{match[2]}"
        elif match := re.fullmatch(r"Калибровка: АКМ №" + _SERIAL, comment):
            calibration = "АКМ №" + match[1]
        elif match := re.fullmatch(r"Calibration[=:] ?(SOLT|TRL|ECal|АКМ)", comment, re.I):
            calibration = match[1].upper()
    return PublicMeasurement(measurement.measured_on, measurement.traces, instrument, calibration, conditions)


def public_passport(item: MeasurementInstrument, rf: RfDetails, measured: Characterization | None) -> PublicPassport:
    ohms = sorted({port.impedance_ohm for port in rf.interfaces if port.impedance_ohm is not None})
    verification = None
    if item.kind is InstrumentKind.MEASURING and (item.verification_number or item.verified_at or item.verification_due):
        verification = PublicVerification(item.verification_number or "", item.verified_at.date() if item.verified_at else None,
                                          item.verification_due.date() if item.verification_due else None)
    return PublicPassport(item.inventory_number, item.name, item.serial_number, item.manufacturer,
                          connector_line(item.name, item.serial_number, rf),
                          " / ".join(number(value) for value in ohms) + " Ом" if ohms else "Не указано",
                          _public_measurement(measured) if measured else None, verification)


def public_fields(passport: PublicPassport) -> tuple[tuple[str, str], ...]:
    fields = [("Серийный номер", passport.serial_number or "Не указан"),
              ("Изготовитель", passport.manufacturer or "Не указан"),
              ("Разъёмы", passport.connectors), ("Сопротивление", passport.impedance)]
    if passport.verification:
        verification = passport.verification
        fields.extend((("Номер поверки", verification.number or "Не указан"),
                       ("Дата поверки", verification.verified_on.strftime("%d.%m.%Y") if verification.verified_on else "Не указана"),
                       ("Поверка до", verification.valid_until.strftime("%d.%m.%Y") if verification.valid_until else "Не указана")))
    return tuple(fields)


def public_measurement_fields(measured: PublicMeasurement) -> tuple[tuple[str, str], ...]:
    return (("Дата замера", measured.measured_on.strftime("%d.%m.%Y")),
            ("Прибор", measured.instrument or "Не указан"),
            ("Калибровка", measured.calibration or "Не указана"),
            ("Условия", "; ".join(measured.conditions) or "Не указаны"))
