"""Frozen GRN contract. No database or neighbouring module dependencies.

Entry points inject GrnAllocationPort; the workflow binds it to their open transaction.
The operation id and body fingerprint belong to the physical acceptance, not a retry.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import re
from typing import Protocol
from uuid import UUID


class GrnCategory(StrEnum):
    PPM = "PPM"
    PAM = "PAM"
    GOS = "GOS"
    SUB = "SUB"
    CSM = "CSM"
    FRX = "FRX"
    RD = "R&D"


MAX_SERIAL = 999999
# Owner: the ВК hold applies to PPM/CSM/SUB only; for these routes «ВК не требуется» is the starting decision.
NO_QC_CATEGORIES = frozenset({"GOS", "PAM", "FRX", "R&D"})


class GrnConflict(ValueError):
    """An existing operation was retried with different immutable data."""


class GrnCapacityExceeded(ValueError):
    """A prefix/year has exhausted the six-digit namespace."""


@dataclass(frozen=True, kw_only=True)
class GrnContext:
    owner_id: UUID | None = None
    customer_order: str | None = None
    transfer_document: str | None = None
    project: str | None = None
    production_order: str | None = None
    subcontractor: str | None = None
    acceptance_document: str | None = None
    purpose: str | None = None
    equipment: str | None = None

    def __post_init__(self) -> None:
        if self.owner_id is not None and not isinstance(self.owner_id, UUID):
            raise ValueError("Владелец материала должен иметь UUID")
        for name, value in asdict(self).items():
            if name == "owner_id" or value is None:
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError(f"Контекст ГРН: {name} — непустая строка до 512 знаков")

    def validate(self, category: GrnCategory) -> None:
        required = {
            GrnCategory.PPM: (),
            GrnCategory.PAM: (),  # purpose OR equipment below
            GrnCategory.GOS: ("purpose",),
            GrnCategory.SUB: ("subcontractor", "production_order", "acceptance_document"),
            GrnCategory.CSM: ("owner_id", "customer_order", "transfer_document"),
            GrnCategory.FRX: ("production_order", "acceptance_document"),
            GrnCategory.RD: ("project", "purpose"),
        }[category]
        missing = [name for name in required if getattr(self, name) is None]
        if category == GrnCategory.PAM and not (self.purpose or self.equipment):
            missing.append("purpose/equipment")
        if missing:
            raise ValueError(f"{category}: укажите {', '.join(missing)}")
        if category != GrnCategory.CSM and self.owner_id is not None:
            raise ValueError("Владелец давальческого материала допустим только для CSM")

    def payload(self) -> dict:
        return {name: str(value) if isinstance(value, UUID) else value for name, value in asdict(self).items()}


def fingerprint_body(payload: dict) -> str:
    """Hash a normalized JSON command (quantities as decimal strings; UUIDs/dates as strings).

Exclude retry-time/server-time fields. Include article, quantity, package, receipt and passport.
Do not pass arbitrary objects: silent stringification could equate different commands.
"""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, kw_only=True)
class GrnRequest:
    category: GrnCategory
    source_kind: str
    source_op_id: str
    context: GrnContext
    body_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", GrnCategory(self.category))
        for name, limit in (("source_kind", 32), ("source_op_id", 128)):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > limit:
                raise ValueError(f"{name}: непустой стабильный ключ до {limit} знаков, без внешних пробелов")
        if self.source_kind == "legacy":
            raise ValueError("Источник legacy зарезервирован для миграции")
        if not isinstance(self.body_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", self.body_fingerprint):
            raise ValueError("body_fingerprint должен быть SHA-256 нормализованной команды")
        self.context.validate(self.category)

    @property
    def fingerprint(self) -> str:
        return fingerprint_body({"category": self.category.value, "context": self.context.payload(),
                                 "body_fingerprint": self.body_fingerprint})


@dataclass(frozen=True, kw_only=True)
class GrnIdentity:
    id: UUID
    number: str
    category: GrnCategory
    full_year: int
    serial: int
    registered_at: datetime
    source_kind: str
    source_op_id: str
    context: GrnContext


class GrnAllocationPort(Protocol):
    def allocate(self, request: GrnRequest, *, registered_at: datetime) -> GrnIdentity:
        """Allocate/replay inside the caller's transaction; never commit or open another UoW."""
        ...

    def allocate_many(self, requests: tuple[GrnRequest, ...], *, registered_at: datetime) -> tuple[GrnIdentity, ...]:
        """Lock counters in a stable order, returning identities in input order."""
        ...
