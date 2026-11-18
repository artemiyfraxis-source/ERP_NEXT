"""Explicit public passport projection. Private card and attachment metadata never reach a renderer."""

from dataclasses import dataclass
from datetime import date
from calendar import monthrange
import re
from urllib.parse import urlsplit

from .instrument_characterization import Application, Characterization, application, connector_line, impedance_warning, number
from .instrument_norms import (Assessment, ItemSpec, Uncertainty, assess, item_spec, norm_for, norms_data, uncertainty_for,
                               vna_model)
from .rf_interfaces import RfDetails, ROLE_LABELS, parse_interfaces
from .traceability import InstrumentKind, MeasurementInstrument
from .vna_files import VnaTrace, _decode

CONTROL_INTERVAL_MONTHS = int(norms_data()["recheck"]["months"])
RECHECK_MATINGS = int(norms_data()["recheck"]["matings"])


@dataclass(frozen=True)
class PublicMeasurement:
    measured_on: date
    traces: tuple[VnaTrace, ...]
    instrument: str
    calibration: str
    conditions: tuple[str, ...]
    chain: str = ""

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
    impedance_warning: str
    status: str
    next_control: date | None
    control_overdue: bool
    history: tuple["PublicHistory", ...]
    spec: ItemSpec | None = None              # temperature range and mating resource with their sources
    norm_check: Assessment | None = None      # decision against the maker spec / MIL sheet; None - not measured
    usage: Application | None = None         # frequency range within the shop requirement (1,30)
    uncertainty: Uncertainty | None = None    # of the VNA that measured, from the reference data


@dataclass(frozen=True)
class PublicHistory:
    measured_on: date
    summary: str


@dataclass(frozen=True)
class PassportConclusion:
    title: str
    verdict: str
    warning: str
    lines: tuple[tuple[str, str], ...]
    closing: tuple[str, ...]


_SERIAL = r"([A-Za-z0-9-]{1,32})"
_TEMPERATURE = r"([+-]?\d{1,3}(?:\.\d{1,3})?(?:/[+-]?\d{1,3}(?:\.\d{1,3})?){0,3})"


def _public_measurement(measurement: Characterization) -> PublicMeasurement:
    instrument, calibration, conditions, chain = "", "", (), ""
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
        elif match := re.fullmatch(r"Температура ВАЦ: " + _TEMPERATURE + r" C; АКМ: " + _TEMPERATURE + r" C", comment):
            conditions = ("Температура ВАЦ: " + match[1].replace(".", ",") + " °C",
                          "Температура АКМ: " + match[2].replace(".", ",") + " °C")
        elif match := re.fullmatch(r"Chain: (напрямую|(?:слева СИ\.\d{1,6})(?:, справа СИ\.\d{1,6})?|справа СИ\.\d{1,6})(?:;.*)?", comment):
            chain = match[1]
    return PublicMeasurement(measurement.measured_on, measurement.traces, instrument, calibration, conditions, chain)


def _manufacturer(rf: RfDetails) -> str:
    # Publish a known maker/source label, never the private source text or URL path.
    sources = (rf.source, *(p.f_source for p in rf.interfaces))
    for source in sources:
        if re.fullmatch(r"(?:Planar|Планар)", source.strip(), re.I):
            return "Планар"
        for url in re.findall(r"https?://[^\s;]+", source):
            try:
                host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
            except ValueError:
                continue
            if host in ("planar.chel.ru", "planar.ru"):
                return "Планар"
            if host in ("gsm-repiteri.ru", "gsm-repiteri.com"):
                return "gsm-repiteri"
    return ""


def _name(item: MeasurementInstrument, rf: RfDetails) -> str:
    if rf.role not in ("unknown", "adapter", "test_cable"):
        return item.name
    connectors = connector_line(item.name, item.serial_number, rf)
    ports = rf.interfaces or parse_interfaces(item.name)
    if not ports and connectors == connector_line(item.name, "", RfDetails()):
        return item.name
    kind = ROLE_LABELS.get(rf.role, "") if rf.role != "unknown" else ""
    if not kind and ("переходник" in item.name.lower() or len(parse_interfaces(item.name)) == 2):
        kind = "Переходник"
    # The card's document designation is private. Reuse only a short model already in the public name.
    model = re.search(r"\b([A-ZА-Я]{1,6}-\d{1,8}[A-Za-zА-Яа-я0-9-]*)\s*$", item.name)
    return " ".join(filter(None, (kind, connectors))) + (", " + model[1] if model else "")


def _series(rf: RfDetails) -> tuple[str, ...]:
    return tuple(port.series for port in rf.interfaces)  # the same series the label's frequency range uses


def history_entry(item: MeasurementInstrument, rf: RfDetails, measured: Characterization, registry=()) -> PublicHistory:
    """One earlier measurement judged by today's norm and shop requirement."""
    projected = _public_measurement(measured)
    check = assess(projected, norm_for(item.name, item.doc_designation or "", rf),
                   uncertainty_for(vna_model(projected.instrument, registry)))
    usage = application(projected, _series(rf))
    return PublicHistory(measured.measured_on, f"{check.short}; применение: {usage.requirement_line}")


def public_passport(item: MeasurementInstrument, rf: RfDetails, measured: Characterization | None,
                    *, history: tuple[PublicHistory, ...] = (), today: date | None = None,
                    registry=()) -> PublicPassport:
    """`registry` - instruments to resolve the VNA model by its serial when the file names only «ВАЦ №…»."""
    ohms = sorted({port.impedance_ohm for port in rf.interfaces if port.impedance_ohm is not None})
    verification = None
    if item.kind is InstrumentKind.MEASURING and (item.verification_number or item.verified_at or item.verification_due):
        verification = PublicVerification(item.verification_number or "", item.verified_at.date() if item.verified_at else None,
                                          item.verification_due.date() if item.verification_due else None)
    next_control = None
    if measured:
        month_index = measured.measured_on.year * 12 + measured.measured_on.month - 1 + CONTROL_INTERVAL_MONTHS
        year, month = divmod(month_index, 12)
        if year <= date.max.year:
            next_control = date(year, month + 1, min(measured.measured_on.day, monthrange(year, month + 1)[1]))
    projected = _public_measurement(measured) if measured else None
    check = usage = uncertainty = None
    if projected:
        uncertainty = uncertainty_for(vna_model(projected.instrument, registry))
        check = assess(projected, norm_for(item.name, item.doc_designation or "", rf), uncertainty)
        usage = application(projected, _series(rf))
    return PublicPassport(item.inventory_number, _name(item, rf), item.serial_number, item.manufacturer.strip() or _manufacturer(rf),
                          connector_line(item.name, item.serial_number, rf),
                          " / ".join(number(value) for value in ohms) + " Ом" if ohms else "50 Ом" if measured else "",
                          projected, verification, impedance_warning(rf),
                          item.status.value, next_control, bool(next_control and next_control < (today or date.today())),
                          tuple(history), item_spec(item.name, item.doc_designation or "", rf), check, usage, uncertainty)


def recheck_text() -> str:
    return f"через {CONTROL_INTERVAL_MONTHS} мес. или {RECHECK_MATINGS} сочленений — что раньше"


def conclusion(passport: PublicPassport) -> PassportConclusion | None:
    """ISO/IEC 17025 §7.8: norm and its source, result, uncertainty, decision rule, method, signer, scope note."""
    measured, check, usage = passport.measurement, passport.norm_check, passport.usage
    if measured is None or check is None or usage is None:
        return None
    method = norms_data()["method"]
    lines = [("Норма", check.norm.line if check.norm else
              "не установлена — нет данных изготовителя и листа MIL-PRF-55339 для этой пары разъёмов")]
    if check.result_line:
        lines.append(("Результат", check.result_line))
    lines += [("Применение", usage.requirement_line),
              ("Погрешность измерения КСВН", passport.uncertainty.line if passport.uncertainty
               else "не определена — модель ВАЦ не указана в файле замера"),
              ("Правило принятия решения", norms_data()["decision_rule"]),
              ("Метод", f"{method['code']} «{method['title']}», {method['edition']}")]
    warning = ""
    if usage.limit_ghz is None:
        warning = "КСВН больше требования цеха уже на первой ступени — не использовать в тракте 50 Ом"
    elif usage.limit_ghz < measured.band[1] - 0.05:
        warning = f"Выше {number(usage.limit_ghz)} ГГц КСВН больше требования цеха — не использовать в тракте 50 Ом"
    return PassportConclusion("Заключение (контроль характеристик, не поверка)", check.verdict, warning, tuple(lines),
                              ("Результаты относятся только к данному изделию.",
                               "Утвердил: ______________________ (подпись, ФИО, дата)"))


def control_line(passport: PublicPassport) -> str:
    if passport.measurement is None:
        return ""
    text = f"Замер: {passport.measurement.measured_on:%d.%m.%Y}"
    if passport.next_control:
        text += f" · Следующий контроль: не позднее {passport.next_control:%d.%m.%Y} ({recheck_text()})"
    return text


def public_measurement_download(content: bytes, measured: PublicMeasurement) -> bytes:
    """Replace comments with whitelisted metadata; retain data spelling, encoding and line endings."""
    text = _decode(content)
    bom, encoding = b"", "utf-8"
    for prefix, candidate in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"), (b"\xef\xbb\xbf", "utf-8")):
        if content.startswith(prefix):
            bom, encoding = prefix, candidate
            break
    else:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            encoding = "cp1251"
    newline = "\r\n" if "\r\n" in text else "\n"
    header = ["! " + label + ": " + value for label, value in (
        ("Прибор", measured.instrument), ("Калибровка", measured.calibration),
        ("Условия", "; ".join(measured.conditions)), ("Цепь", measured.chain)) if value]
    body = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("!"):
            continue
        if "!" in line:
            ending = line[len(line.rstrip("\r\n")):]
            line = line.split("!", 1)[0] + ending
        body.append(line)
    return bom + ((newline.join(header) + newline if header else "") + "".join(body)).encode(encoding)


def public_fields(passport: PublicPassport) -> tuple[tuple[str, str], ...]:
    fields = [(label, value) for label, value in (("Серийный номер", passport.serial_number),
              ("Изготовитель", passport.manufacturer), ("Разъёмы", passport.connectors),
              ("Сопротивление", passport.impedance), ("Статус", passport.status)) if value]
    spec = passport.spec
    if spec and spec.temperature_line:
        fields.append(("Рабочие температуры", f"{spec.temperature_line} — {spec.temp_source}"))
    if spec and spec.mating_resource:
        fields.append(("Ресурс сочленений", f"{spec.mating_resource} — {spec.mating_source}"))
    if passport.verification:
        verification = passport.verification
        fields.extend((label, value) for label, value in (("Номер поверки", verification.number),
                       ("Дата поверки", verification.verified_on.strftime("%d.%m.%Y") if verification.verified_on else ""),
                       ("Поверка до", verification.valid_until.strftime("%d.%m.%Y") if verification.valid_until else "")) if value)
    return tuple(fields)


def public_measurement_fields(measured: PublicMeasurement) -> tuple[tuple[str, str], ...]:
    return tuple((label, value) for label, value in (("Дата замера", measured.measured_on.strftime("%d.%m.%Y")),
            ("Опорное сопротивление измерения", "50 Ом"),
            ("Прибор", measured.instrument), ("Калибровка", measured.calibration),
            ("Условия", "; ".join(measured.conditions)), ("Цепь", measured.chain)) if value)
