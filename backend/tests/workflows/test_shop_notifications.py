"""Цеховые уведомления арбитра: коробка сдана → монтажникам следующих операций (подтверждения мастером нет с 13.10),
возвращена ОТК → сдавшему. По одному письму на человека и событие, повтор события ничего не добавляет."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import (
    ProductionBoxRow,
    ProductionOperationInputRow,
    ProductionOperationOperatorRow,
    ProductionOperationRow,
    ProductionTaskRow,
)
from app.shared.notifications import NOTIFICATION_TOPIC
from app.shared.persistence import AuditEventRow, OutboxMessageRow, create_schema
from app.workflows.arbiter import TOPIC, Trigger, dispatch_next_trigger, feed_triggers, notify_trigger

NOW = datetime(2026, 10, 5, 10, 0)


@pytest.fixture()
def shop():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    ids = SimpleNamespace(**{name: uuid4() for name in (
        "task", "press", "assemble", "fitter", "next_a", "next_b", "master", "master_zhuk", "idle_master", "box",
    )})
    with sessions() as session:
        def user(key, name, *, site, master=False, active=True):
            session.add(IdentityUserRow(
                id=getattr(ids, key), login=name, email=f"{key}@fraxis.local", name=name, department="Монтаж",
                role="master" if master else "staff", site=site, active=active, site_master=master,
            ))

        user("fitter", "Рыбкина", site="ЦАР")
        user("next_a", "Епифанов", site="ЦАР")
        user("next_b", "Попов", site=None)
        user("master", "Мастер ЦАР", site="ЦАР", master=True)
        user("master_zhuk", "Степан", site="ЖУК", master=True)
        user("idle_master", "Бывший мастер", site="ЦАР", master=True, active=False)
        session.add(ProductionTaskRow(
            id=ids.task, number="ПЗ-0412", source_idempotency_key="k", source_fingerprint="f", version=1,
            created_at=NOW, project="708К.4", item="КБ1", name="Жгут", quantity=50, status="Запущено",
            launched_by="Технолог",
        ))
        for operation_id, position, stage in ((ids.press, 1, "Опрессовка"), (ids.assemble, 2, "Сборка")):
            session.add(ProductionOperationRow(
                id=operation_id, task_id=ids.task, position=position, code=str(position), stage=stage, description="",
                seconds_per_unit=Decimal(1), setup_seconds=Decimal(0), norm_seconds=1,
            ))
        session.flush()
        session.add(ProductionOperationInputRow(id=uuid4(), operation_id=ids.assemble, input_operation_id=ids.press))
        for position, key in enumerate(("next_a", "next_b")):
            session.add(ProductionOperationOperatorRow(
                operation_id=ids.assemble, operator_id=getattr(ids, key), position=position,
            ))
        session.add(ProductionOperationOperatorRow(operation_id=ids.press, operator_id=ids.fitter, position=0))
        session.commit()
    yield SimpleNamespace(sessions=sessions, ids=ids)
    engine.dispose()


def add_box(shop, status, *, reason="", number="000123-4"):
    with shop.sessions() as session:
        session.add(ProductionBoxRow(
            id=shop.ids.box, number=number, version=1, task_id=shop.ids.task, operation_id=shop.ids.press, item="КБ1",
            operation_position=1, operator_id=shop.ids.fitter, quantity=Decimal("50"), consumed=Decimal(0),
            reserved=Decimal(0), status=status, created_at=NOW, return_reason=reason,
        ))
        session.commit()


def event(shop, event_type, details=None, aggregate=None):
    event_id = uuid4()
    with shop.sessions() as session:
        session.add(AuditEventRow(
            id=event_id, aggregate_id=aggregate or shop.ids.box, event_type=event_type, actor_id=uuid4(),
            actor_name="кто-то", actor_department="Монтаж", occurred_at=NOW, details=details or {},
        ))
        session.commit()
    return event_id


def run(shop):
    feed_triggers(NOW, shop.sessions)
    while dispatch_next_trigger(NOW, shop.sessions) is not None:
        pass


def notices(shop):
    with shop.sessions() as session:
        rows = session.scalars(select(OutboxMessageRow).where(OutboxMessageRow.topic == NOTIFICATION_TOPIC))
        return sorted((row.payload["recipient_user_ids"], row.payload["body"], row.payload["reference"]) for row in rows)


def test_handed_box_tells_fitters_of_the_next_operations_without_the_master(shop):
    add_box(shop, "Закрыта оператором")
    event(shop, "production.output_recorded", {"box_id": str(shop.ids.box)}, aggregate=uuid4())
    run(shop)
    body = "Можно начинать: ПЗ-0412 Сборка — пришла коробка 000123-4 (50 шт)"
    # Мастерам «ждёт подтверждения» больше не пишется: коробку подтверждает только ОТК.
    assert notices(shop) == sorted([(str(shop.ids.next_a), body, "000123-4"), (str(shop.ids.next_b), body, "000123-4")])


def test_final_operation_box_tells_nobody_it_waits_for_qc(shop):
    add_box(shop, "Закрыта оператором")
    with shop.sessions() as session:  # коробка последней операции: её на вход не берёт никто
        session.get(ProductionBoxRow, shop.ids.box).operation_id = shop.ids.assemble
        session.commit()
    event(shop, "production.output_recorded", {"box_id": str(shop.ids.box)}, aggregate=uuid4())
    run(shop)
    assert notices(shop) == []


def test_confirmed_box_tells_fitters_of_the_next_operations(shop):
    add_box(shop, "Подтверждена")
    event(shop, "production.box_confirmed", {"number": "000123-4"})
    run(shop)
    body = "Можно начинать: ПЗ-0412 Сборка — пришла коробка 000123-4 (50 шт)"
    assert notices(shop) == sorted([(str(shop.ids.next_a), body, "000123-4"), (str(shop.ids.next_b), body, "000123-4")])


def test_returned_box_goes_back_to_its_fitter_with_the_reason(shop):
    add_box(shop, "Возвращена", reason="Не хватает 2 шт")
    event(shop, "production.box_returned", {"reason": "Не хватает 2 шт"})
    run(shop)
    assert notices(shop) == [(str(shop.ids.fitter), "Коробка 000123-4 возвращена: Не хватает 2 шт", "000123-4")]


def test_replay_adds_nothing(shop):
    add_box(shop, "Подтверждена")
    event_id = event(shop, "production.box_confirmed", {"number": "000123-4"})
    run(shop)
    before = notices(shop)
    assert len(before) == 2
    with shop.sessions() as session:  # то же событие обработано заново
        row = session.get(AuditEventRow, event_id)
        trigger = Trigger(row.id, row.event_type, row.aggregate_id, row.occurred_at, dict(row.details))
        assert notify_trigger(session, trigger) == 0
        session.commit()
    with shop.sessions() as session:  # и очередь арбитра почищена — событие встало в неё заново
        session.execute(delete(OutboxMessageRow).where(OutboxMessageRow.topic == TOPIC))
        session.commit()
    run(shop)
    assert notices(shop) == before


def test_stale_event_is_silent(shop):
    add_box(shop, "Израсходована")  # коробку подтвердили и уже взяли в работу — «можно начинать» устарело
    event(shop, "production.box_confirmed", {"number": "000123-4"})
    run(shop)
    assert notices(shop) == []


def test_person_on_two_next_operations_gets_one_notice(shop):
    add_box(shop, "Подтверждена")
    event(shop, "production.box_confirmed", {"number": "000123-4"})
    other = uuid4()
    with shop.sessions() as session:
        session.add(ProductionOperationRow(
            id=other, task_id=shop.ids.task, position=3, code="3", stage="Проверка", description="",
            seconds_per_unit=Decimal(1), setup_seconds=Decimal(0), norm_seconds=1,
        ))
        session.flush()
        session.add(ProductionOperationInputRow(id=uuid4(), operation_id=other, input_operation_id=shop.ids.press))
        session.add(ProductionOperationOperatorRow(operation_id=other, operator_id=shop.ids.next_a, position=0))
        session.commit()
    run(shop)
    assert [item[0] for item in notices(shop)].count(str(shop.ids.next_a)) == 1


def test_start_notice_names_the_operation_not_its_code(shop, monkeypatch):
    add_box(shop, "Подтверждена")
    with shop.sessions() as session:
        row = session.get(ProductionOperationRow, shop.ids.assemble)
        row.code, row.stage = "INS_terminal_1.00", "INS_terminal_1.00"
        session.commit()
    monkeypatch.setattr("app.workflows.arbiter._route_names", lambda _session: {"INS_terminal_1.00": "Монтаж терминалов"})
    event(shop, "production.box_confirmed", {"number": "000123-4"})
    run(shop)
    bodies = [body for _, body, _ in notices(shop)]
    assert bodies and all("INS_terminal" not in body and "ПЗ-0412 Монтаж терминалов —" in body for body in bodies)


def test_start_notice_strips_code_prefix_without_route(shop):
    add_box(shop, "Подтверждена")
    with shop.sessions() as session:
        session.get(ProductionOperationRow, shop.ids.assemble).stage = "INS_terminal_1.00 | Монтаж терминалов"
        session.commit()
    event(shop, "production.box_confirmed", {"number": "000123-4"})
    run(shop)
    bodies = [body for _, body, _ in notices(shop)]
    assert bodies and all("INS_terminal" not in body and "ПЗ-0412 Монтаж терминалов —" in body for body in bodies)
