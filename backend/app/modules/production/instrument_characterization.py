"""Shared measured facts for an instrument label and its on-demand passport."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_CEILING
import math
import re

from .instrument_serials import CONNECTOR_LETTERS, IN_HOUSE_SERIAL
from .rf_interfaces import RfDetails, parse_interfaces
from .instrument_norms import loss_db, norms_data
from .traceability import InstrumentFile
from .vna_characterization import is_characterization_file
from .vna_files import VSWR_CEILING, VnaFileError, VnaTrace, _decode, parse_vna_traces

# Standard frequency steps and round limits (owner 17.11): the label and the planner speak in these, not in raw maxima.
# Classic frequency steps (owner 17.11/18.11): VSWR is quoted «до 1 / 3 / 6 / 12 / 18 ГГц»; 26,5 and 40 GHz are kept for a
# future VNA upgrade. A band below a step (series limit or measured top) ends at that maximum instead («или максимум»).
STEPS_GHZ = (1.0, 3.0, 6.0, 12.0, 18.0, 26.5, 40.0)
STEP_TOLERANCE_GHZ = 0.05
# The frequency range ends at the widest band whose worst VSWR stays within the shop requirement (not a type norm).
APPLICATION_VSWR_LIMIT = float(norms_data()["shop_requirement"]["vswr_max"])
APPLICATION_BASIS = norms_data()["shop_requirement"]["label"]
VSWR_LADDER = (1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.40, 1.50, 2.0)
LOSS_LADDER = (0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0, 2.0)
# Above the band its MIL sheets cover a step is never claimed. N is not capped: /54B and ADP1A rate it to 18 GHz.
SERIES_LIMIT_GHZ = {name: float(entry["to_ghz"]) for name, entry in norms_data()["series_top_ghz"].items()}


def step_stops(highest: float) -> list[float]:
    """Band edges «до 1 / 3 / 6 / 12 / 18 ГГц» up to `highest`, ending at `highest` itself when it is off-step."""
    stops = [stop for stop in STEPS_GHZ if stop <= highest + STEP_TOLERANCE_GHZ]
    if not stops or highest - stops[-1] > STEP_TOLERANCE_GHZ:
        stops.append(round(highest, 1))  # «или максимум»: the last band ends where the measurement/series does
    return stops


def round_up_ladder(value: float, ladder: tuple[float, ...]) -> float | None:
    """The smallest round value not below `value` (tiny float noise tolerated); None above the ladder."""
    return next((step for step in ladder if value <= step + 1e-9), None)


@dataclass(frozen=True)
class StepLimits:
    stop_ghz: float
    vswr: float          # worst VSWR of both ports from the lowest measured frequency to this step
    loss_db: float       # worst insertion loss over the same span

    @property
    def vswr_round(self) -> float | None:
        return round_up_ladder(self.vswr, VSWR_LADDER)

    @property
    def loss_round(self) -> float | None:
        return round_up_ladder(self.loss_db, LOSS_LADDER)


def _vswr_text(value: float | None) -> str:
    return f"{value:.2f}".replace(".", ",") if value is not None else ">2"


def _loss_text(value: float | None) -> str:
    return f"{value:.2f}".replace(".", ",") if value is not None else ">2"


@dataclass(frozen=True)
class Application:
    steps: tuple[StepLimits, ...]   # every band the measurement and the connector series allow
    limit_ghz: float | None         # end of the usable range (widest band within APPLICATION_VSWR_LIMIT); None - unusable

    @property
    def bands(self) -> tuple[StepLimits, ...]:
        """The bands inside the usable frequency range — what the label and the passport quote."""
        if self.limit_ghz is None:
            return ()
        return tuple(step for step in self.steps if step.stop_ghz <= self.limit_ghz + 1e-9)

    @property
    def at_limit(self) -> StepLimits | None:
        return self.bands[-1] if self.bands else None

    @property
    def requirement_line(self) -> str:
        """«КСВН ≤ 1,30 (требование цеха) — до 12 ГГц»: the shop requirement is never presented as a type norm."""
        head = f"КСВН ≤ {_vswr_text(APPLICATION_VSWR_LIMIT)} ({APPLICATION_BASIS})"
        return f"{head} — до {number(self.limit_ghz)} ГГц" if self.limit_ghz else f"{head} — не выполняется"

    @property
    def range_line(self) -> str:
        return f"Частотный диапазон 0–{number(self.limit_ghz)} ГГц" if self.limit_ghz else "Не годен для 50 Ом тракта"

    def table_lines(self, long: bool = False) -> tuple[str, str, str]:
        """Three monospace rows: the band edges, VSWR and loss upper bounds (round values) per band.
        `long` — full row titles for the wide label («Частота до, ГГц», «Вносимые потери, дБ ≤»)."""
        bands = self.bands
        if not bands:
            return "", "", ""
        cells = [(number(step.stop_ghz), _vswr_text(step.vswr_round), _loss_text(step.loss_round)) for step in bands]
        width = max(len(text) for cell in cells for text in cell) + 2  # ≥ 2 spaces: the renderer keeps them
        head, vswr, loss = (("Частота до, ГГц", "КСВН ≤", "Потери, дБ ≤") if long
                            else ("до ГГц", "КСВН ≤", "дБ ≤"))
        label = max(len(head), len(vswr), len(loss)) + 2
        rows = [name.ljust(label) + "".join(cell[index].ljust(width) for cell in cells)
                for index, name in enumerate((head, vswr, loss))]
        return tuple(row.rstrip() for row in rows)

    # Kept for templates saved before 18.11 that still show these two fields.
    @property
    def limit_line(self) -> str:
        return self.range_line

    @property
    def values_line(self) -> str:
        step = self.at_limit
        if step is None:
            return ""
        return f"КСВН ≤ {_vswr_text(step.vswr_round)} · потери ≤ {_loss_text(step.loss_round)} дБ"


def series_cap_ghz(series: tuple[str, ...]) -> float | None:
    caps = [SERIES_LIMIT_GHZ[name] for name in series if name in SERIES_LIMIT_GHZ]
    return min(caps) if caps else None


def application(summary, series: tuple[str, ...] = ()) -> Application:
    """Worst-case limits per classic band (cumulative from the lowest frequency) and the usable frequency range."""
    first, second, transfer = summary.points("S11"), summary.points("S22"), summary.points("S21")
    highest = min(first[-1][0] / 1e9, series_cap_ghz(series) or math.inf)
    steps = []
    for stop in step_stops(highest):
        edge = stop * 1e9 + 0.5  # the step edge itself, no tolerance: a point at 6.04 GHz is not «до 6»
        vswr = max((v for pts in (first, second) for f, v in pts if f <= edge), default=math.inf)
        loss = max((loss_db(v) for f, v in transfer if f <= edge), default=math.inf)
        steps.append(StepLimits(stop, vswr, loss))
    limit = None
    for step in steps:
        if step.vswr <= APPLICATION_VSWR_LIMIT:
            limit = step.stop_ghz
        else:
            break
    return Application(tuple(steps), limit)


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
