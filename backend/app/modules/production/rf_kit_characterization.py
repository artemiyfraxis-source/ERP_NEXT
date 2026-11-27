"""Physical SMA-MF06 identities and publication rules; no inferred or ideal curves."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
import struct
from uuid import UUID

from .domain import ProductionValidationError

LOADS_DEFINITION = "SMA-MF06-loads"
CHARACTERIZED_DEFINITION = "SMA-MF06-characterized"
MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
KINDS = ("OPEN", "SHORT", "LOAD", "THRU")
GENDERS = ("male", "female")
REQUIRED_PIECES = frozenset(
    [(kind, gender, None) for kind in KINDS[:3] for gender in GENDERS]
    + [("THRU", gender, gender) for gender in GENDERS]
)


@dataclass(frozen=True)
class KitStandard:
    piece_label: str
    kind: str
    gender: str
    gender2: str | None
    f_min_hz: float
    f_max_hz: float

    def __post_init__(self):
        if not isinstance(self.piece_label, str) or not 1 <= len(self.piece_label.strip()) <= 100:
            raise ProductionValidationError("Укажите маркировку меры (до 100 символов)")
        if self.piece_label != self.piece_label.strip():
            raise ProductionValidationError("Маркировка меры не должна содержать пробелы по краям")
        if self.signature not in REQUIRED_PIECES:
            raise ProductionValidationError("Укажите тип и пол разъёмов меры SMA-MF06; проход имеет два одинаковых разъёма")
        maximum = 3e9 if self.gender == "female" and self.kind in ("OPEN", "SHORT") else 6e9
        if (not _finite(self.f_min_hz) or not _finite(self.f_max_hz)
                or not 0 <= self.f_min_hz < self.f_max_hz <= maximum):
            raise ProductionValidationError("Недопустимая полоса меры: розетки ХХ/КЗ до 3 ГГц, остальные до 6 ГГц")

    @property
    def signature(self):
        return self.kind, self.gender, self.gender2

    @property
    def format(self):
        return "s2p" if self.kind == "THRU" else "s1p"


def check_completeness(standards: list[KitStandard]) -> None:
    if len(standards) != 8 or {s.signature for s in standards} != REQUIRED_PIECES:
        raise ProductionValidationError("Для активации нужны все восемь мер: ХХ, КЗ, нагрузка обоих полов и два прохода")


@dataclass(frozen=True)
class CurveMetadata:
    sha256: str
    grid_hash: str
    points: int
    f_min_hz: float
    f_max_hz: float


def curve_metadata(content: bytes, format: str) -> CurveMetadata:
    """Storage contract: explicit 50-ohm Hz/RI Touchstone, one ordered frequency per row.

    T2 owns processing/canonicalization. Hash the stored bytes without rewriting them;
    grid_hash is SHA-256 of consecutive IEEE-754 binary64 frequencies in network order.
    """
    if format not in ("s1p", "s2p") or not isinstance(content, bytes) or not 1 <= len(content) <= MAX_CONTENT_BYTES:
        raise ProductionValidationError("Нужен файл s1p/s2p размером до 2 МиБ")
    try:
        lines = [line.split("!", 1)[0].strip() for line in content.decode("ascii").splitlines()]
        lines = [line for line in lines if line]
        if not lines or lines[0].upper().split() != ["#", "HZ", "S", "RI", "R", "50"]:
            raise ValueError
        width = 9 if format == "s2p" else 3
        frequencies = []
        digest = hashlib.sha256()
        for line in lines[1:]:
            values = [float(value) for value in line.split()]
            if len(values) != width or not all(math.isfinite(v) for v in values):
                raise ValueError
            frequency = values[0]
            if not 0 <= frequency <= 6e9 or (frequencies and frequency <= frequencies[-1]):
                raise ValueError
            frequencies.append(frequency)
            digest.update(struct.pack("!d", frequency))
        if not 2 <= len(frequencies) <= 200_000:
            raise ValueError
    except (UnicodeError, ValueError, OverflowError) as error:
        raise ProductionValidationError("Нужны конечные комплексные данные # Hz S RI R 50 на возрастающей сетке") from error
    return CurveMetadata(hashlib.sha256(content).hexdigest(), digest.hexdigest(), len(frequencies),
                         frequencies[0], frequencies[-1])


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _timestamp(value):
    try:
        return isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def evidence_snapshot(provenance: dict, validation: dict) -> tuple[dict, dict]:
    """Require traceability, retaining additional T2/T3 evidence without interpreting it here."""
    try:
        encoded = [json.dumps(value, allow_nan=False, ensure_ascii=False) for value in (provenance, validation)]
        if any(len(value.encode()) > MAX_JSON_BYTES for value in encoded):
            raise ValueError
        provenance, validation = (json.loads(value) for value in encoded)
        if not isinstance(provenance, dict) or not isinstance(validation, dict):
            raise ValueError
        for key in ("planar_instrument_id", "session_id", "command_id", "calibration_id"):
            UUID(provenance[key])
        for key in ("planar_serial", "method", "plane", "operator", "processing_version"):
            if not isinstance(provenance[key], str) or not provenance[key].strip():
                raise ValueError
        if not _timestamp(provenance["captured_at"]):
            raise ValueError
        if not _finite(provenance["if_bandwidth_hz"]) or provenance["if_bandwidth_hz"] <= 0:
            raise ValueError
        if not _finite(provenance["power_dbm"]):
            raise ValueError
        temperatures = provenance["temperatures"]
        if not isinstance(temperatures, dict) or not temperatures or not all(_finite(v) for v in temperatures.values()):
            raise ValueError
        if not isinstance(provenance["fixtures"], list):
            raise ValueError
        for fixture in provenance["fixtures"]:
            UUID(fixture["instrument_id"])
            if not _hash(fixture["sha256"]) or fixture["orientation"] not in ("forward", "reverse"):
                raise ValueError
        if type(validation["valid"]) is not bool or not isinstance(validation["checks"], dict) or not validation["checks"]:
            raise ValueError
        if not all(type(value) is bool for value in validation["checks"].values()):
            raise ValueError
        if validation["valid"] and not all(validation["checks"].values()):
            raise ValueError
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError) as error:
        raise ProductionValidationError("Неполные или некорректные сведения об измерении и проверке меры") from error
    return provenance, validation


def check_revision(standard: KitStandard, format: str, metadata: CurveMetadata, validation: dict) -> None:
    if format != standard.format:
        raise ProductionValidationError("Для ХХ/КЗ/нагрузки нужен s1p, для прохода — s2p")
    if not standard.f_min_hz <= metadata.f_min_hz < metadata.f_max_hz <= standard.f_max_hz:
        raise ProductionValidationError("Измеренная полоса выходит за подтверждённую полосу меры")
    if not validation.get("valid"):
        raise ProductionValidationError("Ревизия меры не прошла проверку")


def generation_hash(kit_id: UUID, generation: int, revisions: list[dict]) -> str:
    """Pin identities as well as curves; even republishing identical selections advances generation."""
    payload = {"kit_id": str(kit_id), "generation": generation,
               "revisions": sorted(revisions, key=lambda item: item["standard_id"])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
