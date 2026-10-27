"""The seller's ИНН of a read УПД, checked before anything is matched by it (owner 27.10.2026: on scans of
ООО «Промышленный Провод» the model read «5256004951» and «5256084951» for «5256084961» — both fail the check digits).

A ИНН that fails its check digits is put right to the one directory supplier's ИНН it is a slip of (one digit or one
adjacent transposition away, else two digits), among the purchasing suppliers named as the seller is; else it is
kept out of matching — the supplier is found by the name — with a remark. A valid ИНН is never changed. The
warehouse's «Отсканировать УПД» asks here (`warehouse.scan_recognition.scan_answer`): the directory is purchasing's."""

from __future__ import annotations

from collections.abc import Callable

from app.modules.purchasing.suppliers import inns_named
from app.shared.inn import InnCheck, checked_inn


def seller_inn(purchasing_factory: Callable | None, inn: str, seller_name: str) -> InnCheck:
    """`InnCheck.inn` — what to match by («» — by the name), `remark` — what to tell the storekeeper («» — nothing)."""
    check = checked_inn(inn)
    if not check.remark or purchasing_factory is None:
        return check
    with purchasing_factory() as uow:
        known = inns_named(uow.session, seller_name)
    return checked_inn(inn, known=known)
