"""VNA calibration and accuracy-control journal (СТО ФРАК-002-2026, 11.4, 14; form В.1).

One append-only record per check written by the VNA program on the VNA's own card:
- session — after every calibration: since 26.11.2026 «только калибровка» (the owner dropped the ACM confidence
  test and LOAD on both ports: no numbers, passed); older rows hold the confidence test (4.1 limits) and both LOADs;
- flex — the cable bend check (10.6): LOAD on each cable end stays ≤ 1.06 and S21/S12 of a thru within ±0.05 dB;
- control_standard — weekly: the control adapter (СИ.009) against its stored reference curve, deviation ≤ U;
- kit_standards — monthly: the 6650F27 standards against their description, √(U² + δ²) (МП п. 10.6.61).
The verdict is recomputed here and by CHECK constraints in the database: a record whose numbers disagree with its
verdict cannot be stored.
"""

from __future__ import annotations

import cmath
import math
from calendar import monthrange
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.shared.clock import as_utc
from .domain import ProductionValidationError
from .instrument_norms import loss_uncertainty_for, uncertainty_for
from .vna_dsp import TRACES, TwoPort, db, summary, vswr

KINDS = ("session", "flex", "control_standard", "kit_standards")
KIND_LABELS = {
    "session": "Калибровка АКМ",
    "flex": "Проверка тракта на изгиб",
    "control_standard": "Контрольная мера (еженедельно)",
    "kit_standards": "Меры 6650F27 (ежемесячно)",
}
LOAD_LIMIT = 1.06            # 11.4: max VSWR of the LOAD on each port over the whole band
CONFIDENCE_TX_LIMIT_DB = 0.1  # 4.1: transmission near 0 dB
FLEX_S21_LIMIT_DB = 0.05      # 14: S21 of the thru while bending
PERIODIC = ("control_standard", "kit_standards")
SESSION_VALUES = ("confidence_passed", "confidence_tx_dev_db", "confidence_rx_dev_db", "load_p1_vswr", "load_p2_vswr")
CALIBRATION_ONLY_NOTE = "доверительный тест и LOAD отменены решением владельца 26.11.2026"
DUE_STATUS_LABELS = {
    "ok": "В срок",
    "overdue": "Контроль просрочен",
    "never": "Контроль просрочен — ещё не выполнялся",
    "failed": "Последняя проверка не пройдена — замеры остановить",
}


def add_month(moment: datetime) -> datetime:
    """The same day next month; the 31st becomes the last day of a shorter month."""
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return moment.replace(year=year, month=month, day=min(moment.day, monthrange(year, month)[1]))


def due_after(kind: str, moment: datetime) -> datetime:
    return moment + timedelta(days=7) if kind == "control_standard" else add_month(moment)


def expected_verdict(kind: str, values: dict) -> bool:
    """The verdict the numbers give; the program's own verdict must agree with it."""
    def need(*names):
        missing = [name for name in names if values.get(name) is None]
        if missing:
            raise ProductionValidationError(f"Для проверки «{KIND_LABELS[kind]}» не хватает значений: {', '.join(missing)}")
    if kind == "session":
        if values.get("calibration_only"):
            # 0221: confidence and LOAD dropped by the owner — the accepted calibration itself is the record.
            given = [name for name in SESSION_VALUES if values.get(name) is not None]
            if given:
                raise ProductionValidationError(f"Запись «только калибровка» без значений проверки: {', '.join(given)}")
            return True
        need("confidence_passed", "confidence_tx_dev_db")
        if values["confidence_passed"] and values["confidence_tx_dev_db"] > CONFIDENCE_TX_LIMIT_DB:
            raise ProductionValidationError("Доверительный тест отмечен годным, но отклонение передачи больше 0,1 дБ")
        if values["confidence_passed"]:
            need("load_p1_vswr", "load_p2_vswr")
        loads = (values.get("load_p1_vswr"), values.get("load_p2_vswr"))
        return bool(values["confidence_passed"]) and all(load is not None and load <= LOAD_LIMIT for load in loads)
    if kind == "flex":
        need("s21_dev_db", "vswr_dev", "load_p1_vswr", "load_p2_vswr")
        return (values["s21_dev_db"] <= FLEX_S21_LIMIT_DB and values["load_p1_vswr"] <= LOAD_LIMIT
                and values["load_p2_vswr"] <= LOAD_LIMIT)
    if kind == "control_standard":
        need("standard_inventory", "vswr_dev", "vswr_limit", "s21_dev_db", "s21_limit_db")
        return values["vswr_dev"] <= values["vswr_limit"] and values["s21_dev_db"] <= values["s21_limit_db"]
    if kind == "kit_standards":
        need("standard_inventory", "kit_ratio")
        return values["kit_ratio"] <= 1
    raise ProductionValidationError("Неизвестный вид проверки ВАЦ")


def due_items(latest: dict[str, tuple[datetime, bool, datetime | None]], now: datetime) -> list[dict]:
    """Weekly and monthly control state from the latest record of each kind and the latest passed one."""
    items = []
    for kind in PERIODIC:
        last_at, last_passed, last_pass_at = latest.get(kind, (None, None, None))
        if last_at is None:
            status, due_at = "never", None
        elif not last_passed:
            status, due_at = "failed", None
        else:
            due_at = due_after(kind, as_utc(last_pass_at))
            status = "overdue" if as_utc(now) > due_at else "ok"
        items.append({"kind": kind, "label": KIND_LABELS[kind], "last_at": as_utc(last_at) if last_at else None, "last_passed": last_passed,
                      "due_at": due_at, "status": status, "status_label": DUE_STATUS_LABELS[status]})
    return items


# ---- Evaluators of the bench checks (port of tools/vna-measure/checks.ps1) ------------------------------------------
# The limits are the instrument's accuracy (СТО ФРАК-002-2026, 4.1 = РЭ S50180 ч. 1, табл. 23). The VSWR and
# transmission U come from the VNA entry of backend/data/rf-norms.json — the same table the passports use.

VNA_MODEL = "S50180"
REFLECTION_FLOOR_DB = -15.0                          # 4.1: reflection is judged from −15 to 0 dB
REFLECTION_U_DB = ((10e9, 0.4), (math.inf, 0.5))     # 4.1: ±0,4 дБ до 10 ГГц, ±0,5 дБ выше
JOURNAL_CEILING = 1000.0                             # a lost signal (∞/NaN) is journaled as 1000

VswrU = Callable[[float, float], float]   # (frequency Hz, VSWR) -> ±U of that VSWR
TxU = Callable[[float], float]            # frequency Hz -> ±U of transmission near 0 dB, dB


def reflection_u_db(hz: float) -> float:
    return next(value for top, value in REFLECTION_U_DB if hz <= top)


def table_vswr_u(model: str = VNA_MODEL) -> VswrU:
    """U of a VSWR value at a frequency from the stored VNA table (РЭ S50180 ч. 1, табл. 23): `Uncertainty.for_value`,
    the same rule as the passport and ОТК verdicts."""
    table = uncertainty_for(model)
    if table is None:
        raise ProductionValidationError(f"Нет погрешности ВАЦ {model} в rf-norms.json")
    return lambda hz, value: table.for_value(hz / 1e9, value)


def table_tx_u(model: str = VNA_MODEL) -> TxU:
    table = loss_uncertainty_for(model)
    if table is None:
        raise ProductionValidationError(f"Нет погрешности передачи ВАЦ {model} в rf-norms.json")
    return lambda hz: table.at(hz / 1e9)


def journal_number(value: float | None) -> float | None:
    """The number as the journal stores it: 6 decimals; ∞, NaN and anything above 1000 become 1000."""
    if value is None:
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number) or number > JOURNAL_CEILING:
        return JOURNAL_CEILING
    return round(number, 6) + 0.0   # never −0.0


def _finite_or(value: float, fallback: float) -> float:
    return fallback if math.isnan(value) or math.isinf(value) else value


def same_grid(first: TwoPort, second: TwoPort, what: str) -> None:
    """The same calibration profile: equal point counts (at least 2) and frequencies within max(1 Hz, 1e-9·f)."""
    if len(first.frequencies) != len(second.frequencies) or len(first.frequencies) < 2:
        raise ProductionValidationError(f"{what}: другая сетка частот — нужен тот же профиль калибровки.")
    for a, b in zip(first.frequencies, second.frequencies):
        if abs(a - b) > max(1.0, a * 1e-9):
            raise ProductionValidationError(f"{what}: частоты не совпадают с профилем замера.")


@dataclass(frozen=True)
class Confidence:
    passed: bool
    tx_dev_db: float
    rx_dev_db: float | None   # None when no stored reflection reached −15 dB


def confidence(data: TwoPort, memory: TwoPort) -> Confidence:
    """11.4: the ACM in its attenuator state, «Данные/Память» in dB. Transmission within ±0,1 дБ; reflection from −15 dB
    up within ±0,4 дБ to 10 GHz and ±0,5 дБ above."""
    same_grid(data, memory, "Доверительный тест")
    tx, rx, passed = 0.0, None, True
    for index, hz in enumerate(data.frequencies):
        for name in ("S21", "S12"):
            tx = max(tx, _finite_or(abs(db(data.trace(name)[index]) - db(memory.trace(name)[index])), math.inf))
        for name in ("S11", "S22"):
            reference = db(memory.trace(name)[index])
            if reference < REFLECTION_FLOOR_DB:
                continue
            deviation = _finite_or(abs(db(data.trace(name)[index]) - reference), math.inf)
            rx = deviation if rx is None else max(rx, deviation)
            if deviation > reflection_u_db(hz):
                passed = False
    if tx > CONFIDENCE_TX_LIMIT_DB:
        passed = False
    return Confidence(passed, tx, rx)


def confidence_from_receipt(receipt: dict) -> Confidence:
    """The verdict of a validated `acm_confidence` receipt (protocol v2); all four traces are required by 11.4."""
    def sweep(traces: dict) -> TwoPort:
        missing = [name for name in TRACES if name not in traces]
        if missing:
            raise ProductionValidationError(f"Доверительный тест: нет трасс {', '.join(missing)}")
        return TwoPort.from_traces(receipt["frequencies_hz"],
                                   {name: [complex(re, im) for re, im in traces[name]] for name in TRACES})
    return confidence(sweep(receipt["data"]), sweep(receipt["memory"]))


def load_vswr(data: TwoPort, port: int) -> float:
    """11.4 / 10.6: the maximum VSWR of the LOAD on that port over the whole band."""
    if port not in (1, 2):
        raise ProductionValidationError("Нагрузка проверяется на порту 1 или 2.")
    result = summary(data)
    return result.vswr1 if port == 1 else result.vswr2


@dataclass(frozen=True)
class Flex:
    s21_dev_db: float
    vswr_dev: float
    passed: bool       # S21/S12 only; the LOAD on each cable end is judged by `load_vswr` (record kind «flex»)


def flex(before: TwoPort, after: TwoPort) -> Flex:
    """10.6 / 14: a thru between the cable ends, straight and then bent as in work — S21 and S12 stay within ±0,05 дБ."""
    same_grid(before, after, "Проверка на изгиб")
    s21 = vswr_dev = 0.0
    for index in range(len(before.frequencies)):
        for name in ("S21", "S12"):
            s21 = max(s21, _finite_or(abs(db(after.trace(name)[index]) - db(before.trace(name)[index])), math.inf))
        for name in ("S11", "S22"):
            vswr_dev = max(vswr_dev, _finite_or(abs(vswr(after.trace(name)[index]) - vswr(before.trace(name)[index])), math.inf))
    return Flex(s21, vswr_dev, s21 <= FLEX_S21_LIMIT_DB)


@dataclass(frozen=True)
class ControlStandard:
    vswr_dev: float
    vswr_limit: float
    s21_dev_db: float
    s21_limit_db: float
    passed: bool


def control_standard(measured: TwoPort, reference: TwoPort, vswr_u: VswrU | None = None,
                     tx_u: TxU | None = None) -> ControlStandard:
    """14, weekly: the control adapter against its stored reference curve. VSWR of both sides within U of the reference
    VSWR, S21 and S12 within the transmission U; the worst point (deviation / U) of each is reported."""
    vswr_u, tx_u = vswr_u or table_vswr_u(), tx_u or table_tx_u()
    same_grid(measured, reference, "Контрольная мера")
    worst_vswr = worst_s21 = (-1.0, 0.0, 0.0)
    for index, hz in enumerate(measured.frequencies):
        for name in ("S11", "S22"):
            target = vswr(reference.trace(name)[index])
            deviation = _finite_or(abs(vswr(measured.trace(name)[index]) - target), JOURNAL_CEILING)
            limit = vswr_u(hz, target)
            if deviation / limit > worst_vswr[0]:
                worst_vswr = (deviation / limit, deviation, limit)
        limit = tx_u(hz)
        for name in ("S21", "S12"):
            deviation = _finite_or(abs(db(measured.trace(name)[index]) - db(reference.trace(name)[index])), JOURNAL_CEILING)
            if deviation / limit > worst_s21[0]:
                worst_s21 = (deviation / limit, deviation, limit)
    _, vswr_dev, vswr_limit = worst_vswr
    _, s21_dev, s21_limit = worst_s21
    return ControlStandard(vswr_dev, vswr_limit, s21_dev, s21_limit, vswr_dev <= vswr_limit and s21_dev <= s21_limit)


# ---- 6650F27 kit standards (14, monthly; МП п. 10.6, метод 4) --------------------------------------------------------

KIT_TYPES = ("OPEN", "SHORT", "LOAD", "THRU")


@dataclass(frozen=True)
class KitStandard:
    """A coaxial standard as S2VNA defines it: C0…C3 in 1e-15/-27/-36/-45 F, L0…L3 in 1e-12/-24/-33/-42 H, offset delay
    in s, offset loss in Ω/s, offset Z0 in Ω."""
    type: str
    delay: float = 0.0
    loss: float = 0.0
    z0: float = 50.0
    c: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    l: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # noqa: E741 — S2VNA's own name
    label: str = ""

    def __post_init__(self):
        if self.type not in KIT_TYPES:
            raise ProductionValidationError(f"Неизвестный тип меры {self.type}.")
        if not self.z0 > 0:
            raise ProductionValidationError("Волновое сопротивление меры должно быть положительным.")

    @classmethod
    def from_s2vna(cls, properties: dict) -> KitStandard:
        """From the kit definition the agent reads back (TYPE, DELay, LOSS, Z0, C0…C3, L0…L3, LABel)."""
        def number(name: str) -> float:
            return float(properties.get(name) or 0.0)
        return cls(str(properties["TYPE"]).upper(), number("DELay"), number("LOSS"), float(properties.get("Z0", 50.0)),
                   tuple(number(f"C{i}") for i in range(4)), tuple(number(f"L{i}") for i in range(4)),
                   str(properties.get("LABel", "")))


def kit_response(standard: KitStandard, hz: float, system_z0: float = 50.0) -> complex:
    """Γ of a one-port standard or S21 of a thru from its offset model (lossy offset line + terminating impedance)."""
    omega, root = 2 * math.pi * hz, math.sqrt(hz / 1e9)
    z0, delay, loss = standard.z0, standard.delay, standard.loss
    alpha = loss * delay / (2 * z0) * root
    gamma_l = complex(alpha, omega * delay + alpha)
    if standard.type == "THRU":
        return cmath.exp(-gamma_l)
    zc = complex(z0 + loss / (2 * omega) * root, -loss / (2 * omega) * root)
    if standard.type == "OPEN":
        c0, c1, c2, c3 = standard.c
        capacitance = c0 * 1e-15 + c1 * 1e-27 * hz + c2 * 1e-36 * hz * hz + c3 * 1e-45 * hz * hz * hz
        if capacitance == 0:
            gamma_t = complex(1)
        else:
            zt = 1 / complex(0, omega * capacitance)
            gamma_t = (zt - zc) / (zt + zc)
    elif standard.type == "SHORT":
        l0, l1, l2, l3 = standard.l
        inductance = l0 * 1e-12 + l1 * 1e-24 * hz + l2 * 1e-33 * hz * hz + l3 * 1e-42 * hz * hz * hz
        zt = complex(0, omega * inductance)
        gamma_t = (zt - zc) / (zt + zc)
    else:
        zt = complex(system_z0)
        gamma_t = (zt - zc) / (zt + zc)
    zr = complex(system_z0)
    gamma1 = (zc - zr) / (zc + zr)
    e = cmath.exp(-2 * gamma_l)
    numerator = gamma1 * (1 - e - gamma1 * gamma_t) + e * gamma_t
    denominator = 1 - gamma1 * (e * gamma1 + gamma_t * (1 - e))
    return numerator / denominator


@dataclass(frozen=True)
class KitTolerance:
    """δ — the uncertainty of the standard's description (МП п. 10.6.61). LOAD: described Γ = 0, |Γ| ≤ 0,05 (РЭ ч. 1,
    табл. 13). OPEN, SHORT and thrus have no stated δ in the documents: 0, the stricter criterion — U alone."""
    reflection_db: float = 0.0
    load_gamma: float = 0.05
    thru_db: float = 0.0

    def __post_init__(self):
        values = (self.reflection_db, self.load_gamma, self.thru_db)
        if not all(math.isfinite(v) and v >= 0 for v in values):
            raise ProductionValidationError("Погрешность описания меры не может быть отрицательной.")


@dataclass(frozen=True)
class KitCheck:
    ratio: float       # the worst deviation / √(U² + δ²); ≤ 1 passes
    deviation: float
    limit: float


def kit_standard(standard: KitStandard, port: int, sweeps: Sequence[TwoPort], tolerance: KitTolerance = KitTolerance(),
                 vswr_u: VswrU | None = None, tx_u: TxU | None = None) -> KitCheck:
    """One standard against its description. Each of the three connections (rotated by 120°) must hold on its own, so
    the worst point of the worst connection decides. `port` is 1 or 2 for a one-port standard and ignored for a thru."""
    vswr_u, tx_u = vswr_u or table_vswr_u(), tx_u or table_tx_u()
    if standard.type != "THRU" and port not in (1, 2):
        raise ProductionValidationError("Мера отражения проверяется на порту 1 или 2.")
    worst = KitCheck(0.0, 0.0, 1.0)
    for data in sweeps:
        for index, hz in enumerate(data.frequencies):
            model = kit_response(standard, hz)
            if standard.type == "THRU":
                limit = math.hypot(tx_u(hz), tolerance.thru_db)
                pairs = [(abs(db(data.trace(name)[index]) - db(model)), limit) for name in ("S21", "S12")]
            else:
                measured = data.trace("S11" if port == 1 else "S22")[index]
                if standard.type == "LOAD":
                    u = vswr_u(hz, 1.0) / 2   # near Γ = 0 a VSWR error δS is a |Γ| error of δS/2
                    pairs = [(abs(abs(measured) - abs(model)), math.hypot(u, tolerance.load_gamma))]
                else:
                    pairs = [(abs(db(measured) - db(model)), math.hypot(reflection_u_db(hz), tolerance.reflection_db))]
            for deviation, limit in pairs:
                deviation = _finite_or(deviation, JOURNAL_CEILING)
                if deviation / limit > worst.ratio:
                    worst = KitCheck(deviation / limit, deviation, limit)
    return worst
