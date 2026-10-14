"""Склад принял КБ, а данных отправки проекта нет (владелец 14.10.2026): менеджеру проекта — одно уведомление на
приёмку и проект со списком того, чего не хватает; полные данные — тишина."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.modules.shipping.domain import ProjectAddressee, Shipment, ShipmentLine, UpdType
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.shared.persistence import OutboxMessageRow
from tests.workflows.test_finished_goods_register import _accept, flow_api  # noqa: F401 — fixture

PROJECT = "708К.4"
MANAGER = UUID("10000000-0000-0000-0000-0000000000aa")


def _notices(sessions) -> list[dict]:
    with sessions() as session:
        return [row.payload for row in session.scalars(select(OutboxMessageRow))
                if "Нет данных отправки" in str(row.payload.get("title"))]


def _card(sessions, **fields) -> None:
    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        uow.addressees.add(ProjectAddressee(project=PROJECT, version=1, **fields))
        uow.commit()


def test_accept_with_an_empty_card_tells_every_project_manager_once_per_batch(flow_api):
    client, sessions = flow_api
    response = _accept(client, [(PROJECT, "КБ1", "5"), (PROJECT, "КБ2", "4")])
    _accept(client, [(PROJECT, "КБ1", "5"), (PROJECT, "КБ2", "4")])  # повтор того же пакета — не второе уведомление

    notices = _notices(sessions)
    assert response.status_code == 200
    assert len(notices) == 1
    notice = notices[0]
    assert notice["recipient_audiences"] == "project_manager" and notice["recipient_user_ids"] == ""
    assert notice["body"].startswith(
        "Приняли на склад КБ КБ1, КБ2 проекта 708К.4, а данных отправки нет: получатель, адрес доставки, телефон, "
        "вариант УПД. Заполните в „Приёмке с производства“ → „Данные отправки“."
    )
    assert "?section=intake" in notice["body"] and notice["type"] == "ship"


def test_full_card_sends_nothing(flow_api):
    client, sessions = flow_api
    _card(sessions, receiver="ООО Заказчик", shipping_point="Москва", phone="+7 900", upd_type=UpdType.EDO)

    assert _accept(client, [(PROJECT, "КБ1", "5")]).status_code == 200
    assert _notices(sessions) == []


def test_paper_without_file_is_listed_and_goes_to_the_shipment_manager(flow_api):
    client, sessions = flow_api
    _card(sessions, receiver="ООО Заказчик", shipping_point="Москва", phone="+7 900", upd_type=UpdType.PAPER)
    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        good = uow.goods.by_key(PROJECT, "КБ1")
        uow.shipments.add(Shipment.create(
            number="ОТГ-1", created_at=datetime(2026, 10, 1, 9, 0), customer="", receiver="П", shipping_point="Москва",
            contact="", phone="", terms=None, carrier="", upd_type=UpdType.PAPER, deadline=None, upd_number="",
            upd_date=None, upd_url="", manager="Ульяна", note="", manager_id=MANAGER,
            lines=(ShipmentLine.create(good, Decimal("1")),),
        ))
        uow.commit()

    response = client.post(f"/api/shipping/goods/{_good_id(sessions)}/accept", json={"version": _good_version(sessions), "quantity": "3"})

    notices = _notices(sessions)
    assert response.status_code == 200, response.text
    assert len(notices) == 1
    assert notices[0]["recipient_user_ids"] == str(MANAGER) and notices[0]["recipient_audiences"] == ""
    assert "данных отправки нет: файл бумажной УПД." in notices[0]["body"]


def _good_id(sessions) -> str:
    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        return str(uow.goods.by_key(PROJECT, "КБ1").id)


def _good_version(sessions) -> int:
    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        return uow.goods.by_key(PROJECT, "КБ1").version
