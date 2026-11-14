"""RF connector facts and conservative, one-time name proposals (no persistence)."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .domain import ProductionValidationError

SERIES = ("SMA", "3.5mm", "N", "TNC", "BNC", "F", "RP-SMA", "тип III")
ROLES = ("unknown", "vna", "adapter", "test_cable", "auto_calibration", "solt_kit", "load", "load_kit")
ROLE_LABELS = dict(zip(ROLES, ("Не указано", "ВАЦ", "Переходник", "Тестовый кабель", "АКМ", "Набор SOLT", "Нагрузка", "Нагрузки SMA-MF06")))


@dataclass(frozen=True)
class RfInterface:
    series: str
    body: str
    center: str
    impedance_ohm: float | None = None
    f_max_ghz: float | None = None
    f_confirmed: bool = False
    f_source: str = ""

    def __post_init__(self):
        if self.series not in SERIES or self.body not in ("вилка", "розетка") or self.center not in ("штырь", "гнездо"):
            raise ProductionValidationError("Укажите серию, корпус и центральный контакт ВЧ-интерфейса")
        for value, maximum, label in ((self.impedance_ohm, 1000, "сопротивление"), (self.f_max_ghz, 1000, "частоту")):
            if value is not None and (isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= maximum):
                raise ProductionValidationError(f"Укажите допустимое {label} ВЧ-интерфейса")
        if len(self.f_source) > 2000:
            raise ProductionValidationError("Источник ВЧ-интерфейса: не более 2000 символов")
        if self.f_confirmed and (self.impedance_ohm is None or self.f_max_ghz is None or not self.f_source.strip()):
            raise ProductionValidationError("Для подтверждения нужны сопротивление, частота и источник")

    @property
    def label(self) -> str:
        return f"{self.series} {self.body}, {self.center}"


@dataclass(frozen=True)
class RfDetails:
    interfaces: tuple[RfInterface, ...] = ()
    role: str = "unknown"
    confirmed: bool = False
    source: str = ""
    f_min_ghz: float | None = None
    max_points: int | None = None
    # This definition explicitly means the complete M+F SOL set and all three named thrus.
    kit_definition: str = ""
    version: int = 0

    def __post_init__(self):
        if len(self.interfaces) > 2 or self.role not in ROLES:
            raise ProductionValidationError("Допустимы не более двух ВЧ-интерфейсов и известное назначение")
        if len(self.source) > 2000 or self.version < 0:
            raise ProductionValidationError("Некорректный источник или версия ВЧ-данных")
        if self.f_min_ghz is not None and (not math.isfinite(self.f_min_ghz) or not 0 <= self.f_min_ghz <= 1000):
            raise ProductionValidationError("Нижняя частота должна быть от 0 до 1000 ГГц")
        if self.max_points is not None and (type(self.max_points) is not int or not 2 <= self.max_points <= 10_000_001):
            raise ProductionValidationError("Некорректное максимальное число точек ВАЦ")
        if self.max_points is not None and self.role != "vna":
            raise ProductionValidationError("Число точек указывается только для ВАЦ")
        definitions = {"solt_kit": "6650F27-full", "load_kit": "SMA-MF06-loads"}
        if self.kit_definition and self.kit_definition != definitions.get(self.role):
            raise ProductionValidationError("Неизвестный состав набора калибровки")
        if any(p.f_max_ghz is not None and self.f_min_ghz is not None and p.f_max_ghz <= self.f_min_ghz for p in self.interfaces):
            raise ProductionValidationError("Верхняя частота должна быть выше нижней")
        if self.confirmed:
            counts = (1,) if self.role == "load" else (1, 2) if self.role == "load_kit" else (2,)
            if len(self.interfaces) not in counts or self.role == "unknown" or not self.source.strip():
                raise ProductionValidationError("Для подтверждения укажите назначение, интерфейсы и источник")
            if any(p.impedance_ohm is None for p in self.interfaces):
                raise ProductionValidationError("Для подтверждения интерфейсов укажите сопротивление")
            if self.role in definitions and self.kit_definition != definitions[self.role]:
                raise ProductionValidationError("Подтвердите состав набора калибровки или нагрузок")


def mates(left: RfInterface, right: RfInterface) -> bool:
    compatible = left.series == right.series or {left.series, right.series} == {"SMA", "3.5mm"}
    return (compatible and left.body != right.body and left.center != right.center
            and left.impedance_ohm is not None and left.impedance_ohm == right.impedance_ohm)


def mating_rating(left: RfInterface, right: RfInterface) -> float | None:
    if not mates(left, right) or left.f_max_ghz is None or right.f_max_ghz is None:
        return None
    return min(left.f_max_ghz, right.f_max_ghz)


_CONNECTOR = re.compile(
    r"(?<![\w])(?P<series>RP[- ]?SMA|SMA|3[.,]5\s*(?:mm|мм)|TNC|BNC|N|F|тип\s+III)"
    r"[\s-]*(?P<gender>female|male|вилка|розетка)(?![\w])", re.I,
)
_SERIES_TOKEN = re.compile(r"(?<![\w])(?:RP[- ]?SMA|SMA|3[.,]5\s*(?:mm|мм)|TNC|BNC|N|F|тип\s+III)(?![\w])", re.I)


def parse_interfaces(name: str) -> tuple[RfInterface, ...]:
    """Parse explicit connector/gender pairs only; article codes and partial names are not evidence.

    male/female describes the body; RP-SMA reverses the centre contact. Impedance
    and frequency stay unknown unless explicitly stated in the name.
    """
    found = list(_CONNECTOR.finditer(name))
    if not 1 <= len(found) <= 2 or len(_SERIES_TOKEN.findall(name)) != len(found):
        return ()
    impedance = re.findall(r"(?<![\d.])(\d+(?:[.,]\d+)?)\s*(?:Ω|Ом|ohm)\b", name, re.I)
    freq = re.findall(r"(?:до|≤)\s*(\d+(?:[.,]\d+)?)\s*(?:ГГц|GHz)\b", name, re.I)
    ohms = float(impedance[0].replace(",", ".")) if len(set(impedance)) == 1 else None
    ghz = float(freq[0].replace(",", ".")) if len(set(freq)) == 1 else None
    result = []
    for match in found:
        series = match["series"].upper().replace(" ", "")
        if series.startswith("3"):
            series = "3.5mm"
        elif series.startswith("ТИП"):
            series = "тип III"
        elif series == "RPSMA":
            series = "RP-SMA"
        male = match["gender"].lower() in ("male", "вилка")
        result.append(RfInterface(series, "вилка" if male else "розетка",
                                  "штырь" if male != (series == "RP-SMA") else "гнездо",
                                  ohms, ghz, f_source=f"Наименование: {name}"))
    return tuple(result)


def propose_details(inventory_number: str, name: str) -> RfDetails:
    """Known equipment is matched by inventory AND model, never inventory alone."""
    source = "Перечень оборудования владельца, 12–13.11.2026; требуется сверка с паспортом"

    def pair(series1, male1, series2, male2, maximum, role, minimum=0, kit=""):
        def port(series, male):
            return RfInterface(series, "вилка" if male else "розетка", "штырь" if male else "гнездо",
                               50, maximum, f_source=source)
        return RfDetails((port(series1, male1), port(series2, male2)), role, source=source,
                         f_min_ghz=minimum, kit_definition=kit)

    upper = name.upper()
    if inventory_number == "СИ.002" and "S50180" in upper:
        return pair("N", False, "N", False, 18, "vna", 0.0001)
    if inventory_number == "СИ.104" and "ACM2520-111" in upper:
        # Owner's 14.11 answer confirms geometry and impedance, not the rated frequency band.
        evidence = "Владелец, 14.11.2026: оба порта АКМ ACM2520-111 — 3.5 mm розетка, гнездо, 50 Ом"
        connector = RfInterface("3.5mm", "розетка", "гнездо", 50, f_source=evidence)
        return RfDetails((connector, connector), "auto_calibration", confirmed=True, source=evidence)
    if inventory_number == "СИ.003" and "6650F27" in upper:
        return pair("3.5mm", True, "3.5mm", False, 26.5, "solt_kit", kit="6650F27-full")
    if inventory_number == "СИ.004" and "SMA-MF06" in upper:
        return RfDetails(role="load_kit", kit_definition="SMA-MF06-loads",
                         source="Владелец, 14.11.2026: разрешена нагрузка SMA-MF06; пол разъёма и полосу подтвердить")
    if inventory_number == "СИ.005" and re.search(r"(?<![\w])N(?![\w])", upper):
        return RfDetails(role="solt_kit", source="Набор N под Arinst: состав стандартов и рабочую полосу подтвердить")
    if inventory_number in ("СИ.006", "СИ.007") and "ADP1A-35M-35F" in upper:
        return pair("3.5mm", True, "3.5mm", False, 20, "adapter")
    if inventory_number in ("СИ.008", "СИ.009") and "ADP1A-35F-35F" in upper:
        return pair("3.5mm", False, "3.5mm", False, 20, "adapter")
    if inventory_number in ("СИ.098", "СИ.099") and "C50NMIIIM.2" in upper:
        return pair("N", True, "тип III", True, 18, "test_cable")
    for numbers, model, series in ((('СИ.100', 'СИ.101'), 'C50SMNM.2', 'SMA'),
                                    (('СИ.102', 'СИ.103'), 'C50NMNM.2', 'N')):
        if inventory_number in numbers and model in upper:
            # The brief identifies the ends, but gives no explicit maximum for these models.
            return pair(series, True, "N", True, None, "test_cable", None)
    interfaces = parse_interfaces(name)
    if any(re.search(rf"(?<![\w]){article}(?![\w])", upper) for article in ("B-311", "F-311", "F-322")):
        from dataclasses import replace
        interfaces = tuple(replace(p, impedance_ohm=75, f_source=f"{p.f_source}; перечень владельца: 75 Ом") for p in interfaces)
    # A kit name without its contents never becomes a two-port adapter or a valid SOLT kit.
    role = "adapter" if len(interfaces) == 2 and "переходник" in name.lower() else "unknown"
    return RfDetails(interfaces, role, source=f"Наименование: {name}")
