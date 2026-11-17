"""Shared measured facts for an instrument label and its on-demand passport."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_CEILING
import math
import re

from .instrument_serials import CONNECTOR_LETTERS, IN_HOUSE_SERIAL
from .rf_interfaces import RfDetails, parse_interfaces
from .traceability import InstrumentFile
from .vna_characterization import is_characterization_file
from .vna_files import VSWR_CEILING, VnaFileError, VnaTrace, _decode, parse_vna_traces

WORKING_VSWR_LIMIT = 1.2


@dataclass(frozen=True)
class WorkingBand:
    start_ghz: float
    stop_ghz: float
    max_loss_in_band: float
    limit: float = WORKING_VSWR_LIMIT

    @property
    def band_line(self) -> str:
        return f"Рабочая полоса {number(self.start_ghz)}–{number(self.stop_ghz)} ГГц"

    @property
    def limits_line(self) -> str:
        loss = upper_two(self.max_loss_in_band) if math.isfinite(self.max_loss_in_band) else "∞"
        return f"КСВН ≤ {number(self.limit)} · потери ≤ {loss} дБ"


def working_band(summary, limit: float = WORKING_VSWR_LIMIT) -> WorkingBand | None:
    """Measured continuous prefix passing both ports; never bridge a failing sample.

    The boundary is the last passing sample, without interpolating unmeasured evidence.
    Characterization and the public projection expose the same aligned trace interface.
    """
    first, second = summary.points("S11"), summary.points("S22")
    stop = None
    for (frequency, left), (other_frequency, right) in zip(first, second):
        if frequency != other_frequency or not (1 <= left <= limit and 1 <= right <= limit):
            break
        stop = frequency
    if stop is None:
        return None
    loss = max((loss_db(v) for f, v in summary.points("S21") if first[0][0] <= f <= stop), default=math.inf)
    return WorkingBand(first[0][0] / 1e9, stop / 1e9, loss, limit)


def number(value: float) -> str:
    return format(value, ".8g").replace(".", ",")


def upper_two(value: float) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)).replace(".", ",")


def connector_line(name: str, serial: str, rf: RfDetails) -> str:
    ports = rf.interfaces or parse_interfaces(name)
    if ports:
        ends = []
        for port in ports:
            series = "RP-SMA" if port.series == "SMA" and ((port.body == "вилка") != (port.center == "штырь")) else port.series
            # Russian connector gender follows the central contact, including reverse-polarity SMA.
            ends.append(f"{series} " + ("вилка" if port.center == "штырь" else "розетка"))
        return " – ".join(ends)
    if IN_HOUSE_SERIAL.fullmatch(serial):
        parts = serial.split("-")
        if len(parts) == 4:
            series = {letter: key for key, letter in CONNECTOR_LETTERS.items()}
            if all(letter.upper() in series for letter in parts[1]):
                # Serial case and Russian connector gender both encode the central contact.
                return " – ".join(f"{series[letter.upper()]} " +
                                  ("вилка" if letter.isupper() else "розетка")
                                  for letter in parts[1])
    short = re.sub(r"(?i)\b(?:переходник|прямой)\b|\bВЧ[-–—]", "", name)
    return " ".join(short.split()).strip(" ,–—-") or name.strip()


def impedance_warning(rf: RfDetails) -> str:
    values = sorted({p.impedance_ohm for p in rf.interfaces if p.impedance_ohm is not None and p.impedance_ohm != 50})
    return f"{' / '.join(number(value) for value in values)} Ом — не для 50 Ом тракта" if values else ""


@dataclass(frozen=True)
class Characterization:
    file_name: str
    measured_on: date
    operator: str
    traces: tuple[VnaTrace, ...]
    comments: tuple[str, ...]

    def points(self, name: str):
        return next(trace.points for trace in self.traces if trace.name == name)

    @property
    def band(self) -> tuple[float, float]:
        points = self.points("S11")
        return points[0][0] / 1e9, points[-1][0] / 1e9

    @property
    def parameters_line(self) -> str:
        maximum = max(value for name in ("S11", "S22") for _, value in self.points(name))
        start, stop = self.band
        bound = f"≥ {number(VSWR_CEILING)}" if maximum >= VSWR_CEILING else f"≤ {upper_two(maximum)}"
        return f"КСВН {bound} · {number(start)}–{number(stop)} ГГц"

    @property
    def loss_line(self) -> str:
        loss = max(loss_db(value) for _, value in self.points("S21"))
        return "Потери: ∞ дБ" if not math.isfinite(loss) else f"Потери ≤ {upper_two(loss)} дБ"

    @property
    def measurement_date(self) -> str:
        return f"Замер {self.measured_on:%d.%m.%y}"


def loss_db(magnitude: float) -> float:
    return max(0.0, -20 * math.log10(magnitude)) if magnitude > 0 else math.inf


def _date(text: str) -> date | None:
    for match in re.finditer(r"(?<!\d)(\d{4})[-.](\d{2})[-.](\d{2})(?!\d)|(?<!\d)(\d{2})[.](\d{2})[.](\d{2}|\d{4})(?!\d)", text):
        parts = match.groups()
        try:
            if parts[0]:
                return date(int(parts[0]), int(parts[1]), int(parts[2]))
            year = int(parts[5])
            return date(year + 2000 if year < 100 else year, int(parts[4]), int(parts[3]))
        except ValueError:
            continue
    return None


def characterization(file: InstrumentFile) -> Characterization | None:
    """Only a named, two-port 50-ohm measurement is eligible; unrelated attachments are never evidence."""
    if not is_characterization_file(file.file_name):
        return None
    try:
        text = _decode(file.content or b"")
        for line in text.splitlines():
            options = line.split("!", 1)[0].strip()
            if options.startswith("#"):
                match = re.search(r"\bR\s+(\S+)", options, re.I)
                if match and float(match[1]) != 50:
                    return None
        traces = parse_vna_traces(file.content or b"", file.file_name)
    except (VnaFileError, ValueError, OverflowError):
        return None
    if {trace.name for trace in traces} != {"S11", "S21", "S12", "S22"}:
        return None
    comments = tuple(line.strip()[1:].strip() for line in text.splitlines() if line.strip().startswith("!"))
    if any("SYNTHETIC OFFLINE DATA - NOT A MEASUREMENT" in comment.upper() for comment in comments):
        return None
    captured = next((_date(match[1]) for comment in comments
                     if (match := re.search(r"(?:^|[;\s])captured=(\d{4}-\d{2}-\d{2}T[^;\s]{1,40})(?:$|[;\s])", comment, re.I))
                     and _date(match[1]) is not None), None)
    measured = captured or _date("\n".join(comments)) or _date(file.file_name) or file.uploaded_at.date()
    # Uploaded files are bounded, but arbitrary comment payloads must not produce thousands of PDF pages.
    clipped = len(comments) > 100 or any(len(line) > 2000 for line in comments)
    comments = tuple(line[:2000] for line in comments[:100])
    if clipped:
        comments += ("Заголовок сокращён; полный текст — в исходном файле замера.",)
    return Characterization(file.file_name, measured, file.uploaded_by_name, traces, comments)
