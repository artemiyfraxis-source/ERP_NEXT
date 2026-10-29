"""«Отгрузить без УПД» по PIN (владелец 29.10.2026): «пинкод у Артемия, Зингера, Касаточкина — при попытке они вводят
и дают разрешение на такую отгрузку».

Кто может разрешать — факт Identity (раздел «Отгрузка без УПД — разрешение» на правку, действующий сотрудник), сама
отметка на отправке — Shipping. Модули друг друга не знают, встречаются здесь. Разрешают не PIN входа, а разрешительным
ПИН (владелец 29.10: «ПИН того же, что для входа — нет»; `identity.approval_pin.confirm_with_approval_pin`, ключ учётной
записи `approval:<логин>`, в журнале — цель «noupd») под пределами входа по PIN: неверный ПИН — неудачная попытка, серия —
блокировка на 10 минут. Разрешение — на одну отправку; откат «Отгружено» его снимает.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.modules.identity.api import _client_key as client_key  # the login's counters: a PIN permit is a login check
from app.modules.identity.application import (
    ApproverRefused, AuthenticationError, AuthenticationRateLimited, PinRefused, section_editors,
)
from app.modules.identity.approval_pin import confirm_with_approval_pin
from app.modules.shipping.application import ApproveNoUpdCommand, ShipmentCommand, approve_no_upd, check_no_upd
from app.modules.shipping.domain import Shipment
from app.shared.identity import Actor

NO_UPD_SECTION = "ship_no_upd"
NO_UPD_PURPOSE = "noupd"


class NoUpdRefused(PermissionError):
    """Разрешение не принято: `status` — HTTP-ответ (403 — не тот человек или неверный PIN, 429 — блокировка)."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Approver:
    id: UUID
    name: str


def no_upd_approvers(identity_uow) -> list[Approver]:
    """Кто сейчас может разрешить отгрузку без УПД: действующие сотрудники с разделом на правку, по имени."""
    return [Approver(id=user.id, name=user.name or user.email) for user in section_editors(NO_UPD_SECTION, identity_uow)]


@dataclass(frozen=True)
class ApproveNoUpd:
    shipment_id: UUID
    expected_version: int
    approver_id: UUID
    pin: str
    actor: Actor
    now: datetime
    client_key: str


def approve_shipment_without_upd(
    command: ApproveNoUpd, identity_uow: Callable[[], object], shipping_uow: Callable[[], object],
) -> Shipment:
    """Сначала отправка (версия формы, статус, ещё не разрешено) — чтобы не тратить попытку PIN впустую, затем PIN и
    право разрешающего, затем отметка на отправке с событием журнала."""
    check_no_upd(ShipmentCommand(
        shipment_id=command.shipment_id, expected_version=command.expected_version, actor=command.actor, now=command.now,
    ), shipping_uow())
    try:
        approver = confirm_with_approval_pin(
            command.approver_id, command.pin, NO_UPD_PURPOSE, command.now, identity_uow(),
            client_key=command.client_key, section=NO_UPD_SECTION,
        )
    except AuthenticationRateLimited as error:
        raise NoUpdRefused(str(error), 429) from error
    except (ApproverRefused, PinRefused, AuthenticationError) as error:
        raise NoUpdRefused(str(error), 403) from error
    return approve_no_upd(ApproveNoUpdCommand(
        shipment_id=command.shipment_id, expected_version=command.expected_version, actor=command.actor,
        now=command.now, approver_id=approver.id, approver_name=approver.name or approver.email,
    ), shipping_uow())
