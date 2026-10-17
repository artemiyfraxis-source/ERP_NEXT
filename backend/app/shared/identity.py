"""Authenticated employee identity shared by bounded contexts."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Actor:
    id: UUID
    name: str
    department: str
