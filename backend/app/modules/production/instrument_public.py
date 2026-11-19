"""Explicit public passport projection. Private card and attachment metadata never reach a renderer."""

from dataclasses import dataclass
from datetime import date
import re
from urllib.parse import urlsplit

from .instrument_control import CONTROL_INTERVAL_MONTHS, RECHECK_MATINGS, ControlDue, control_due, recheck_text
from .instrument_characterization import Application, Characterization, application, connector_line, impedance_warning, number
from .instrument_norms import (Assessment, ItemSpec, LossAssessment, LossUncertainty, Uncertainty, ghz_text, item_spec,
                               REPEAT_TEXT, loss_uncertainty_for, norm_for, norms_data, resolve_vna, uncertainty_for)
from .instrument_serials import article_of
from .rf_interfaces import RfDetails, ROLE_LABELS, is_adapter_name, parse_interfaces
from .traceability import InstrumentKind, MeasurementInstrument
from .vna_files import VnaTrace, _decode

# Owner 18.11: the general director approves the conclusion by hand; no name is stored.
APPROVER_ROLE = "Генеральный директор"


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
    usage: Application | None = None         # frequency range within the item's own norm
    uncertainty: Uncertainty | None = None    # of the VNA that measured, from the reference data
    loss_check: LossAssessment | None = None  # insertion loss against the loss norm of the same source
    loss_uncertainty: LossUncertainty | None = None  # transmission uncertainty of the VNA that measured
    vna: str = ""                             # «ВАЦ Планар S50180 (СИ.002)» — the VNA that measured, as resolved
    control: ControlDue | None = None         # 12 months or 100 matings since the measurement, whichever first


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
    if not kind and (is_adapter_name(item.name) or len(parse_interfaces(item.name)) == 2):
        kind = ROLE_LABELS["adapter"]
    # The card's document designation is private. Reuse only a short model already in the public name.
    model = re.search(r"\b([A-ZА-Я]{1,6}-\d{1,8}[A-Za-zА-Яа-я0-9-]*)\s*$", item.name)
    article = article_of(item)  # «ADP1A-35M-35F»: an article word already in the public name
    shown = model[1] if model else article if article and article in item.name.split() else ""
    return " ".join(filter(None, (kind, connectors))) + (", " + shown if shown else "")


def _series(rf: RfDetails) -> tuple[str, ...]:
    return tuple(port.series for port in rf.interfaces)  # the same series the label's frequency range uses


def _usage(item: MeasurementInstrument, rf: RfDetails, measured: Characterization, projected: PublicMeasurement,
           registry=(), repeats=()) -> tuple[Application, str, str]:
    """The norm-based range and the VNA (model, label) resolved from the header, the file name or the registry."""
    model, vna = resolve_vna((projected.instrument, *measured.comments, measured.file_name), registry)
    usage = application(projected, _series(rf), norm_for(item.name, item.doc_designation or "", rf),
                        uncertainty=uncertainty_for(model), loss_uncertainty=loss_uncertainty_for(model), repeats=repeats)
    title = next((entry["title"] for entry in norms_data()["vna"] if entry["model"] == model), "")
    label = " ".join(filter(None, ("ВАЦ " + title if title else projected.instrument,
                                   f"({vna.inventory_number})" if vna is not None else "")))
    return usage, model, label


def history_entry(item: MeasurementInstrument, rf: RfDetails, measured: Characterization, registry=()) -> PublicHistory:
    """One earlier measurement judged by today's norm."""
    usage, _, _ = _usage(item, rf, measured, _public_measurement(measured), registry)
    parts = [f"КСВН: {usage.check.short}"]
    if usage.loss_check and usage.loss_check.status != "none":
        parts.append(f"потери: {usage.loss_check.short}")
    if usage.fit_line:
        parts.append(usage.fit_line[0].lower() + usage.fit_line[1:])
    return PublicHistory(measured.measured_on, "; ".join(parts))


def public_passport(item: MeasurementInstrument, rf: RfDetails, measured: Characterization | None,
                    *, history: tuple[PublicHistory, ...] = (), today: date | None = None,
                    registry=(), repeats=(), matings_since: int | None = None) -> PublicPassport:
    """`registry` - instruments to resolve the VNA by its serial or model when the file names only «ВАЦ №…»;
    `repeats` - the other connections of the same control (same day), see `application`; `matings_since` - matings
    recorded since the measurement day (None - not read)."""
    ohms = sorted({port.impedance_ohm for port in rf.interfaces if port.impedance_ohm is not None})
    verification = None
    if item.kind is InstrumentKind.MEASURING and (item.verification_number or item.verified_at or item.verification_due):
        verification = PublicVerification(item.verification_number or "", item.verified_at.date() if item.verified_at else None,
                                          item.verification_due.date() if item.verification_due else None)
    control = control_due(measured.measured_on, matings_since, today or date.today()) if measured else None
    projected = _public_measurement(measured) if measured else None
    usage, model, vna = _usage(item, rf, measured, projected, registry, repeats) if projected else (None, "", "")
    return PublicPassport(item.inventory_number, _name(item, rf), item.serial_number, item.manufacturer.strip() or _manufacturer(rf),
                          connector_line(item.name, item.serial_number, rf),
                          " / ".join(number(value) for value in ohms) + " Ом" if ohms else "50 Ом" if measured else "",
                          projected, verification, impedance_warning(rf),
                          item.status.value, control.due_on if control else None, bool(control and control.overdue),
                          tuple(history), item_spec(item.name, item.doc_designation or "", rf),
                          usage.check if usage else None, usage, uncertainty_for(model) if usage else None,
                          usage.loss_check if usage else None, loss_uncertainty_for(model) if usage else None, vna,
                          control)


def conclusion(passport: PublicPassport) -> PassportConclusion | None:
    """Short by owner decision 18.11: norm (the bands that apply), result, range within the norm, method, control, signer.
    The decision rule and the uncertainty stay in the code: a margin under the uncertainty reads «на границе допуска»."""
    measured, check, usage = passport.measurement, passport.norm_check, passport.usage
    if measured is None or check is None or usage is None:
        return None
    start, stop = measured.band
    lines = [("Норма", check.norm.brief(start, stop) if check.norm else "не назначена")]
    loss = passport.loss_check
    result = "; ".join(filter(None, (check.brief, loss.brief if loss else "")))
    if result:
        lines.append(("Результат", result))
    if usage.fit_line:
        lines.append(("", usage.fit_line))
    lines.append(("Методика", norms_data()["method"]["code"]))
    verdict = combined_verdict(check, loss)
    return PassportConclusion("Заключение", verdict, "", tuple(lines),
                              (f"Утвердил: {APPROVER_ROLE} ______________ (подпись)   «____» __________ 20___ г.",))


def combined_verdict(check, loss) -> str:
    """One rule for VSWR and loss: a failure of either fails the item; else a border of either asks for the repeat."""
    loss = loss if loss is not None and loss.status in ("pass", "border", "fail") else None
    if check.status == "fail":
        return check.verdict + ("; вносимые потери тоже вне нормы" if loss and loss.status == "fail" else "")
    if loss and loss.status == "fail":
        above = f" выше {ghz_text(loss.last_pass_ghz)} ГГц" if loss.last_pass_ghz is not None else ""
        return f"Не соответствует норме по вносимым потерям{above}"
    if check.status == "border" or (loss and loss.status == "border"):
        which = [name for name, a in (("КСВН", check), ("потери", loss)) if a and a.status == "border"]
        passes = [a.last_pass_ghz for a in (check, loss) if a and a.status == "border"]
        above = f" выше {ghz_text(min(passes))} ГГц" if None not in passes else ""
        return f"На границе допуска ({', '.join(which)}){above} — {REPEAT_TEXT}"
    return check.verdict


def control_line(passport: PublicPassport) -> str:
    if passport.measurement is None:
        return ""
    text = f"Замер {passport.measurement.measured_on:%d.%m.%Y}"
    if passport.control:
        text += " · " + passport.control.line
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
    fields = [(label, value) for label, value in (("Заводской №", passport.serial_number),
              ("Изготовитель", passport.manufacturer), ("Разъёмы", passport.connectors),
              ("Сопротивление", passport.impedance), ("Статус", passport.status)) if value]
    spec = passport.spec
    if spec and spec.temperature_line:
        fields.append(("Рабочие температуры", spec.temperature_line))
    if spec and spec.mating_resource:
        fields.append(("Ресурс сочленений", str(spec.mating_resource)))
    if passport.verification:
        verification = passport.verification
        fields.extend((label, value) for label, value in (("Номер поверки", verification.number),
                       ("Дата поверки", verification.verified_on.strftime("%d.%m.%Y") if verification.verified_on else ""),
                       ("Поверка до", verification.valid_until.strftime("%d.%m.%Y") if verification.valid_until else "")) if value)
    return tuple(fields)


def measurement_line(passport: PublicPassport, operator: str = "") -> str:
    """«Замер 16.11.2026, Васильев А., ВАЦ Планар S50180 (СИ.002), калибровка АКМ» — one line, no file header text."""
    measured = passport.measurement
    if measured is None:
        return ""
    calibration = measured.calibration.split()[0] if measured.calibration else ""
    return ", ".join(filter(None, (f"Замер {measured.measured_on:%d.%m.%Y}", operator.strip(), passport.vna,
                                   f"калибровка {calibration}" if calibration else "")))
