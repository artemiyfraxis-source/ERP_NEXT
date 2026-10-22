"""Port for shipping: which of the people who packed a shipment are storekeepers right now.

The PL / DN signature «Штамп/подпись кладовщика» is a storekeeper's (owner 22.10.2026: «ФИО может быть только
кладовщика»). Shipping keeps the pack events in its audit journal; whether a person is a storekeeper is identity's
fact (the access level «Кладовщик», an active user) — this workflow is the one place the two meet.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.identity.audience_directory import STOREKEEPER_LEVEL, active_at_level


def storekeeper_ids(session: Session, user_ids: Iterable[UUID]) -> set[UUID]:
    return active_at_level(session, user_ids, STOREKEEPER_LEVEL)
