"""Shared production/setup resource identities and occupancy rules, independent of storage."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID


class ResourceConflict(ValueError):
    pass


class ResourceKind(str, Enum):
    MACHINE = "machine"
    PERSON = "person"


def instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Нужны дата и время с часовым поясом")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ResourceKey:
    kind: ResourceKind
    key: str

    def __post_init__(self):
        if not isinstance(self.kind, ResourceKind):
            raise ValueError("Неизвестный вид ресурса")
        if not isinstance(self.key, str) or not self.key or self.key != self.key.strip() or len(self.key) > 128:
            raise ValueError("Нужен точный ключ ресурса")
        if self.kind is ResourceKind.PERSON:
            try:
                person = UUID(self.key)
            except (ValueError, AttributeError) as error:
                raise ValueError("Нужен UUID сотрудника") from error
            if not person.int or str(person) != self.key:
                raise ValueError("Нужен канонический UUID сотрудника")


@dataclass(frozen=True)
class ResourceOwner:
    kind: str
    id: UUID

    def __post_init__(self):
        if self.kind not in {"setup", "production"} or not isinstance(self.id, UUID) or not self.id.int:
            raise ValueError("Нужна ссылка на задание наладки или производственное назначение")


@dataclass(frozen=True)
class ResourceWindow:
    start: datetime
    finish: datetime

    def __post_init__(self):
        object.__setattr__(self, "start", instant(self.start))
        object.__setattr__(self, "finish", instant(self.finish))
        if self.finish <= self.start:
            raise ValueError("Конец окна должен быть позже начала")

    def overlaps(self, other: "ResourceWindow") -> bool:
        return self.start < other.finish and other.start < self.finish


@dataclass(frozen=True)
class ResourceClaim:
    resource: ResourceKey
    owner: ResourceOwner
    window: ResourceWindow
    activated_at: datetime | None = None
    released_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.resource, ResourceKey) or not isinstance(self.owner, ResourceOwner) or not isinstance(self.window, ResourceWindow):
            raise ValueError("Нужны ресурс, владелец и окно назначения")
        if self.activated_at is not None:
            object.__setattr__(self, "activated_at", instant(self.activated_at))
        if self.released_at is not None:
            object.__setattr__(self, "released_at", instant(self.released_at))
            if self.activated_at is None or self.released_at < self.activated_at:
                raise ValueError("Освобождение должно быть после занятия ресурса")

    @property
    def active(self) -> bool:
        return self.activated_at is not None and self.released_at is None

    def conflicts(self, other: "ResourceClaim") -> bool:
        if self.resource != other.resource or self.owner == other.owner:
            return False
        if self.released_at is not None or other.released_at is not None:
            return False
        # No guessed release: an active holder can overrun any scheduled finish.
        if self.active or other.active:
            return True
        return self.window.overlaps(other.window)
