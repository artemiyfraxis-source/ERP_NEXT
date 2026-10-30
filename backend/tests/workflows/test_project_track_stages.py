"""«Трек проекта»: путь этапов из журнала плана — пропущено вручную, выставлено, возврат, «авто», блокеры, статусы."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.production.plan import PLAN_KINDS, KitStageNow, PlanEvent, plan_stage_change
from app.modules.production.plan_application import ChangeStageCommand, change_stage
from app.shared.persistence import create_schema
from app.workflows.project_track import project_track
from tests.factories import Actor

PROJECT = "708К.4"
MASTER = Actor(name="Мастер", department="Производство")
T0 = datetime(2026, 10, 10, 12, 0)


@pytest.fixture()
def production():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    yield lambda: SqlAlchemyProductionUnitOfWork(sessions)
    engine.dispose()


def _kit(index, status, *, manual=False, auto=None):
    return KitStageNow(index, status, manual, auto or status, False)


def _move(factory, kits, value, kb="", *, minutes=0, reason="", reopen_from=""):
    plan = plan_stage_change(kits, value, kb)
    change_stage(ChangeStageCommand(
        PROJECT, plan, MASTER, T0 + timedelta(minutes=minutes), reason=reason, reopen_from=reopen_from,
    ), factory())


def _event(factory, kind, minutes, **fields):
    with factory() as uow:
        uow.plan_events.add(PlanEvent(
            project=PROJECT, occurred_at=T0 + timedelta(minutes=minutes), who="Мастер", kind=PLAN_KINDS[kind], **fields,
        ))
        uow.commit()


def _track(factory):
    return [
        (item.kind, item.ref, item.status, item.item_index)
        for item in project_track(PROJECT, production=factory)
    ]


def test_a_jump_gives_a_row_per_skipped_stage_then_the_target(production):
    _move(production, (_kit("КБ1", "Новый"),), "Принято складом")

    assert _track(production) == [
        ("Этап КБ", "В работе", "пропущен вручную · Мастер", "КБ1"),
        ("Этап КБ", "Готово", "пропущен вручную · Мастер", "КБ1"),
        ("Этап КБ", "ОТК", "пропущен вручную · Мастер", "КБ1"),
        ("Этап КБ", "ОТК пройден", "пропущен вручную · Мастер", "КБ1"),
        ("Этап КБ", "Принято складом", "выставлен вручную · Мастер", "КБ1"),
    ]


def test_a_step_to_the_next_stage_has_no_skipped_rows(production):
    _move(production, (_kit("КБ1", "Новый"),), "В работе")

    assert _track(production) == [("Этап КБ", "В работе", "выставлен вручную · Мастер", "КБ1")]


def test_a_backward_move_shows_from_to_and_the_reason(production):
    _move(production, (_kit("КБ1", "Принято складом"),), "В работе", "КБ1", reason="брак на ОТК")

    assert _track(production) == [
        ("Этап КБ", "В работе", "возврат вручную Принято складом → В работе · причина: брак на ОТК · Мастер", "КБ1"),
    ]


def test_a_forward_pick_with_a_reason_keeps_it_on_the_target(production):
    _move(production, (_kit("КБ1", "Новый"),), "В работе", reason="работа начата в обход системы")

    assert _track(production) == [
        ("Этап КБ", "В работе", "выставлен вручную · Мастер · причина: работа начата в обход системы", "КБ1"),
    ]


def test_auto_shows_the_stage_is_back_on_facts(production):
    _move(production, (_kit("КБ1", "В работе", manual=True, auto="Новый"),), "авто")

    assert _track(production) == [("Этап КБ", "авто", "этап снова по фактам · Мастер", "КБ1")]


def test_blocker_set_and_removed(production):
    _event(production, "block", 0, blocker="СТОП", text="ждём заказчика")
    _event(production, "unblock", 5, blocker="СТОП")

    assert _track(production) == [
        ("Блокер", "СТОП", "поставлен · Мастер · ждём заказчика", ""),
        ("Блокер", "СТОП", "снят · Мастер", ""),
    ]


def test_terminal_status_and_reopen(production):
    _event(production, "status", 0, value="Отменён", text="Отменён")
    _move(production, (_kit("КБ1", "Новый"),), "В работе", minutes=10, reopen_from="Отменён", reason="заказчик вернулся")

    assert _track(production) == [
        ("Статус проекта", "Отменён", "выставлен · Мастер", ""),
        ("Статус проекта", "Отменён", "возвращён в работу · Мастер · причина: заказчик вернулся", ""),
        ("Этап КБ", "В работе", "выставлен вручную · Мастер · причина: заказчик вернулся", "КБ1"),
    ]


def test_a_legacy_stage_row_without_from_value_is_the_target_only(production):
    _event(production, "stage", 0, item_index="КБ1", value="В работе")

    assert _track(production) == [("Этап КБ", "В работе", "выставлен вручную · Мастер", "КБ1")]


def test_journal_rows_of_another_project_are_not_shown(production):
    with production() as uow:
        uow.plan_events.add(PlanEvent(
            project="708К.41", occurred_at=T0, who="Мастер", kind=PLAN_KINDS["block"], blocker="СТОП",
        ))
        uow.commit()

    assert _track(production) == []
