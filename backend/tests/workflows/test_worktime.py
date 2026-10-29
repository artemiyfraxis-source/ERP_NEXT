"""«Рабочее время» в личной карточке (фаза 3, T2): месяц по дням и баланс года из выработки, смены, календаря и отгулов."""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.domain import IdentityUser, hash_password
from app.modules.identity.infrastructure import SqlAlchemyIdentityUnitOfWork, provision_user, seed_level_matrix
from app.modules.identity.leave import LeaveKind, LeaveRequest, LeaveStatus
from app.modules.production.application import Absence, LaunchProductionCommand, launch_production
from app.modules.production.domain import DEFAULT_SHIFT, ProductionOperation
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.production.traceability import ProductionOutput, WorkSession, WorkStatus
from app.shared.clock import MOSCOW, moscow_now
from app.shared.persistence import create_schema
from app.workflows.worktime import DEBT, SOURCE, WorkSpan, parse_month, worked_by_day, worktime
from tests.factories import Actor

NOW = datetime(2026, 10, 14, 12, 0)  # по Москве; октябрь 2026: 1-е — четверг, 3–4 — выходные
PASSWORD = "worktime-password-1"

ADMIN = UUID("70000000-0000-0000-0000-000000000001")
MASTER = UUID("70000000-0000-0000-0000-000000000002")
FITTER = UUID("70000000-0000-0000-0000-000000000003")
FITTER_ZHUK = UUID("70000000-0000-0000-0000-000000000004")
OFFICE = UUID("70000000-0000-0000-0000-000000000005")
UNKNOWN = UUID("70000000-0000-0000-0000-0000000000ff")
OCTOBER = date(2026, 10, 1)


def _user(user_id, login, name, role, site=None):
    return IdentityUser(
        id=user_id, login=login, email=f"{login}@example.test", name=name, department="Цех", role=role, site=site,
        active=True,
    )


@pytest.fixture()
def erp():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    password = hash_password(PASSWORD)
    with sessions() as session:
        for user in (
            _user(ADMIN, "admin", "Технолог Иван", "admin"),
            _user(MASTER, "master", "Мастер Пётр", "master", "ЦАР"),
            _user(FITTER, "fitter", "Монтажник Анна", "staff", "ЦАР"),
            _user(FITTER_ZHUK, "fitter3", "Монтажник Жук", "staff", "ЖУК"),
            _user(OFFICE, "office", "Кадровик", "staff"),
        ):
            provision_user(session, user, password)
        seed_level_matrix(session)
        session.commit()
    yield SimpleNamespace(
        identity=lambda: SqlAlchemyIdentityUnitOfWork(sessions),
        production=lambda: SqlAlchemyProductionUnitOfWork(sessions),
    )
    engine.dispose()


def msk(month: int, day: int, hour: int, minute: int = 0, year: int = 2026) -> datetime:
    """Московское время → UTC, как хранит выработка."""
    return datetime(year, month, day, hour, minute, tzinfo=MOSCOW).astimezone(timezone.utc)


def launch(erp, operators=(FITTER,)):
    operation = ProductionOperation.create(
        position=1, code="OP-01", stage="Разделка", description="Подготовить кабель",
        seconds_per_unit=Decimal("60"), setup_seconds=Decimal("0"), quantity=100,
    )
    return launch_production(LaunchProductionCommand(
        idempotency_key=f"worktime:{uuid4()}", project="708К.4", item="КБ1", name="Жгут", quantity=100,
        fulfillment_request_id=None, deadline=None, actor=Actor(name="Мастер", department="Цех"), now=NOW,
        operations=(replace(operation, operator_ids=tuple(operators)),),
    ), erp.production())


def worked(erp, task, started, finished, pause=0, *, operator=FITTER):
    """Выработка по закрытой сессии; возвращает её."""
    session_id = uuid4()
    output = ProductionOutput(
        id=uuid4(), session_id=session_id, task_id=task.id, operation_id=task.operations[0].id, operator_id=operator,
        started_at=started, finished_at=finished, pause_seconds=pause, good_quantity=Decimal("1"),
        defect_quantity=Decimal("0"), output_box_id=uuid4(),
    )
    with erp.production() as uow:
        uow.work.save(WorkSession(
            id=session_id, task_id=task.id, operation_id=task.operations[0].id, operator_id=operator,
            started_at=started, status=WorkStatus.COMPLETED, pause_seconds=pause,
        ))
        uow.outputs.add(output)
        uow.commit()
    return output


def corrected(erp, task, original, *, by=MASTER):
    """Исправление выработки, как `correct_box`: без сессии, время нулевое, исполнитель — кто исправил."""
    with erp.production() as uow:
        uow.outputs.add(replace(
            original, id=uuid4(), session_id=None, operator_id=by, started_at=msk(10, 13, 10), finished_at=msk(10, 13, 10),
            pause_seconds=0, good_quantity=Decimal("2"), correction_of_id=original.id,
        ))
        uow.commit()


def calendar(erp, *absences, holidays=frozenset()):
    with erp.production() as uow:
        uow.calendar.replace(frozenset(holidays), tuple(
            Absence(uuid4(), FITTER, start, finish, kind) for start, finish, kind in absences
        ), DEFAULT_SHIFT)
        uow.commit()


def leave(erp, start, finish, kind=LeaveKind.DAY_OFF, status=LeaveStatus.REGISTERED, employee=FITTER):
    with erp.identity() as uow:
        uow.leave.add(LeaveRequest(
            id=uuid4(), employee_id=employee, kind=kind, from_date=start, to_date=finish, comment="", status=status,
            content_version=1, content_sha256="0" * 64, version=1, created_at=NOW, updated_at=NOW,
        ))
        uow.commit()


def read(erp, user_id=FITTER, first=OCTOBER, **kwargs):
    return worktime(user_id, first, NOW, identity=erp.identity, production=erp.production, **kwargs)


def day(body, iso):
    return next(item for item in body["days"] if item["date"] == iso)


# ── День: норма, отработано, переработка ─────────────────────────────────


def test_a_normal_day_is_the_shift_without_overtime(erp):
    task = launch(erp)
    worked(erp, task, msk(10, 5, 9), msk(10, 5, 17), pause=3600)  # пн: 8 ч − 1 ч паузы

    body = read(erp)

    assert body["available"] is True and body["source"] == SOURCE == "по данным выработки"
    assert body["month"] == "2026-10" and body["label"] == "октябрь 2026" and body["shift_hours"] == 7.0
    assert day(body, "2026-10-05") == {
        "date": "2026-10-05", "weekday": "пн", "workday": True, "absence": None, "day_off": False, "future": False,
        "norm_hours": 7.0, "worked_hours": 7.0, "overtime_hours": 0.0,
    }
    # День смены без работы: норма есть, отработано 0, переработки нет (недоработка в долг не идёт).
    assert day(body, "2026-10-06")["norm_hours"] == 7.0 and day(body, "2026-10-06")["worked_hours"] == 0.0
    assert len(body["days"]) == 31


def test_overtime_is_the_work_above_the_shift(erp):
    task = launch(erp)
    worked(erp, task, msk(10, 6, 9), msk(10, 6, 19), pause=3600)  # вт: 9 ч

    assert day(read(erp), "2026-10-06")["overtime_hours"] == 2.0


def test_a_worked_weekend_is_overtime_in_full(erp):
    task = launch(erp)
    worked(erp, task, msk(10, 3, 10), msk(10, 3, 14))  # сб: 4 ч

    item = day(read(erp), "2026-10-03")

    assert item["weekday"] == "сб" and item["workday"] is False
    assert (item["norm_hours"], item["worked_hours"], item["overtime_hours"]) == (0.0, 4.0, 4.0)


def test_a_day_with_an_absence_has_no_norm(erp):
    task = launch(erp)
    calendar(erp, (date(2026, 10, 7), date(2026, 10, 7), "Больничный"))
    worked(erp, task, msk(10, 7, 10), msk(10, 7, 12))

    item = day(read(erp), "2026-10-07")

    assert item["absence"] == "Больничный" and item["workday"] is True
    assert (item["norm_hours"], item["worked_hours"], item["overtime_hours"]) == (0.0, 2.0, 2.0)


def test_a_holiday_has_no_norm(erp):
    task = launch(erp)
    calendar(erp, holidays={date(2026, 10, 8)})
    worked(erp, task, msk(10, 5, 9), msk(10, 5, 16))

    item = day(read(erp), "2026-10-08")

    assert item["workday"] is False and item["norm_hours"] == 0.0


def test_a_corrected_output_is_counted_once_for_its_worker(erp):
    task = launch(erp)
    original = worked(erp, task, msk(10, 8, 9), msk(10, 8, 16))  # 7 ч
    corrected(erp, task, original)

    body = read(erp)

    assert day(body, "2026-10-08")["worked_hours"] == 7.0
    assert body["totals"]["worked_hours"] == 7.0
    # Исправивший мастер времени не получает: у исправления нет сессии.
    assert read(erp, MASTER)["available"] is False


def test_overlapping_sessions_are_merged(erp):
    task = launch(erp)
    worked(erp, task, msk(10, 9, 9), msk(10, 9, 13))
    worked(erp, task, msk(10, 9, 11), msk(10, 9, 15))  # 9–15 по часам, а не 4 + 4

    item = day(read(erp), "2026-10-09")

    assert item["worked_hours"] == 6.0 and item["overtime_hours"] == 0.0


def test_merged_sessions_never_exceed_their_net_time():
    # 9–12 с часом паузы и 10–11 внутри: по часам 3 ч, чистого 2 + 1 — 3 ч; с двумя паузами по часу — не больше 4.
    spans = [
        WorkSpan(msk(10, 9, 9), msk(10, 9, 12), 2 * 3600),
        WorkSpan(msk(10, 9, 10), msk(10, 9, 11), 3600),
    ]
    assert worked_by_day(spans, OCTOBER, date(2026, 11, 1)) == {date(2026, 10, 9): 3 * 3600}
    lone = [WorkSpan(msk(10, 9, 9), msk(10, 9, 12), 2 * 3600)]
    assert worked_by_day(lone, OCTOBER, date(2026, 11, 1)) == {date(2026, 10, 9): 2 * 3600}


def test_a_session_through_midnight_is_split_by_moscow_days(erp):
    task = launch(erp)
    worked(erp, task, msk(10, 9, 23), msk(10, 10, 1))  # пт 23:00 → сб 01:00

    body = read(erp)

    assert day(body, "2026-10-09")["worked_hours"] == 1.0
    assert day(body, "2026-10-10")["worked_hours"] == 1.0


def test_days_after_today_show_the_norm_but_no_work(erp):
    task = launch(erp)
    worked(erp, task, msk(10, 5, 9), msk(10, 5, 16))

    item = day(read(erp), "2026-10-15")

    assert item["future"] is True and item["norm_hours"] == 7.0
    assert item["worked_hours"] is None and item["overtime_hours"] is None


# ── Баланс года ──────────────────────────────────────────────────────────


def test_day_offs_take_the_balance_into_debt(erp):
    task = launch(erp)
    worked(erp, task, msk(3, 14, 10), msk(3, 14, 14))  # сб в марте: 4 ч
    worked(erp, task, msk(10, 6, 9), msk(10, 6, 19), pause=3600)  # 2 ч сверх смены
    # Отгул пт–пн: суббота и воскресенье не стоят ничего, пт и пн — по смене. Календарь его ещё не получил.
    leave(erp, date(2026, 10, 16), date(2026, 10, 19))

    body = read(erp)

    assert body["balance"] == {
        "year": 2026, "from": "2026-01-01", "to": "2026-10-31", "overtime_hours": 6.0, "day_off_days": 2,
        "day_off_hours": 14.0, "balance_hours": -8.0, "state": "debt", "label": DEBT,
    }
    assert body["balance"]["label"] == "долг с отработкой"
    friday = day(body, "2026-10-16")
    assert friday["absence"] == "Отгул" and friday["day_off"] is True and friday["norm_hours"] == 0.0
    # Месячные итоги — только октябрь: мартовская суббота в них не входит.
    assert body["totals"]["overtime_hours"] == 2.0


def test_balance_with_overtime_left_is_positive(erp):
    task = launch(erp)
    worked(erp, task, msk(10, 3, 10), msk(10, 3, 19))  # 9 ч в субботу
    leave(erp, date(2026, 10, 5), date(2026, 10, 5))

    balance = read(erp)["balance"]

    assert balance["balance_hours"] == 2.0 and balance["state"] == "overtime" and balance["label"] == "переработка"


def test_only_registered_day_offs_cost_and_vacations_do_not(erp):
    task = launch(erp)
    worked(erp, task, msk(10, 3, 10), msk(10, 3, 12))
    leave(erp, date(2026, 10, 5), date(2026, 10, 5), status=LeaveStatus.APPROVED)
    leave(erp, date(2026, 10, 20), date(2026, 10, 23), kind=LeaveKind.VACATION)

    body = read(erp)

    assert body["balance"]["day_off_days"] == 0 and body["balance"]["balance_hours"] == 2.0
    assert day(body, "2026-10-05")["norm_hours"] == 7.0  # заявление ещё не зарегистрировано
    assert day(body, "2026-10-20")["absence"] == "Отпуск" and day(body, "2026-10-20")["norm_hours"] == 0.0


def test_the_balance_is_not_carried_into_the_next_year(erp):
    task = launch(erp)
    worked(erp, task, msk(12, 26, 10, year=2026), msk(12, 26, 20, year=2026))  # сб 26.12.2026: 10 ч
    worked(erp, task, msk(1, 10, 10, year=2027), msk(1, 10, 11, year=2027))  # вс 10.01.2027: 1 ч

    december = worktime(FITTER, date(2026, 12, 1), NOW, identity=erp.identity, production=erp.production)
    january = worktime(FITTER, date(2027, 1, 1), NOW, identity=erp.identity, production=erp.production)

    assert december["balance"]["balance_hours"] == 10.0
    assert january["balance"]["balance_hours"] == 1.0 and january["balance"]["from"] == "2027-01-01"


# ── «Нет данных» ─────────────────────────────────────────────────────────


def test_zhuk_time_is_counted_like_tsar(erp):
    # Владелец 29.10: «ЖУК — планировать как ЦАР»; до того у ЖУК было «нет данных: без нормирования».
    task = launch(erp, (FITTER_ZHUK,))
    worked(erp, task, msk(10, 5, 9), msk(10, 5, 16), operator=FITTER_ZHUK)

    body = read(erp, FITTER_ZHUK)

    assert body["available"] is True and body["shift_hours"] == 7.0
    assert day(body, "2026-10-05")["worked_hours"] == 7.0 and body["totals"]["worked_hours"] == 7.0


def test_a_person_without_work_sessions_has_no_data(erp):
    body = read(erp, OFFICE)

    assert body["available"] is False and body["no_data_reason"].startswith("Нет данных")
    assert body["source"] == SOURCE


def test_sessions_of_an_earlier_year_only_give_no_data(erp):
    task = launch(erp)
    worked(erp, task, msk(12, 1, 9, year=2025), msk(12, 1, 16, year=2025))

    assert read(erp)["available"] is False


def test_no_production_module_means_no_data(erp):
    body = worktime(FITTER, OCTOBER, NOW, identity=erp.identity)

    assert body["available"] is False


def test_unknown_person_is_none(erp):
    assert read(erp, UNKNOWN) is None


def test_parse_month():
    assert parse_month("2026-02", NOW) == date(2026, 2, 1)
    assert parse_month(None, NOW) == OCTOBER
    for wrong in ("2026-13", "2026-1", "октябрь", "1999-01"):
        with pytest.raises(ValueError):
            parse_month(wrong, NOW)


# ── HTTP ─────────────────────────────────────────────────────────────────

STATE = {"identity_uow_factory": "identity", "production_uow_factory": "production"}


@pytest.fixture()
def api(erp):
    for name, factory in STATE.items():
        setattr(app.state, name, getattr(erp, factory))
    app.state.cookie_secure = False
    app.state.identity_clock = lambda: NOW
    app.dependency_overrides[moscow_now] = lambda: NOW

    def login(name: str) -> TestClient:
        client = TestClient(app)
        response = client.post("/api/auth/login", json={"login": name, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    yield login
    app.dependency_overrides.pop(moscow_now, None)
    for name in (*STATE, "cookie_secure", "identity_clock"):
        if hasattr(app.state, name):
            delattr(app.state, name)


def test_own_worktime_is_open_to_its_owner(erp, api):
    task = launch(erp)
    worked(erp, task, msk(9, 7, 9), msk(9, 7, 18))

    response = api("fitter").get("/api/employee-card/me/worktime", params={"month": "2026-09"})

    assert response.status_code == 200, response.text
    assert response.json() == read(erp, first=date(2026, 9, 1))
    assert response.json()["month"] == "2026-09"
    assert api("fitter").get("/api/employee-card/me/worktime").json()["month"] == "2026-10"


def test_anothers_worktime_follows_the_card_rule(erp, api):
    assert TestClient(app).get("/api/employee-card/me/worktime").status_code == 401
    assert api("fitter").get(f"/api/employee-card/{MASTER}/worktime").status_code == 403
    admin = api("admin")
    assert admin.get(f"/api/employee-card/{FITTER}/worktime").status_code == 200
    assert admin.get(f"/api/employee-card/{UNKNOWN}/worktime").status_code == 404


def test_a_wrong_month_is_422(erp, api):
    response = api("fitter").get("/api/employee-card/me/worktime", params={"month": "2026-13"})

    assert response.status_code == 422 and "ГГГГ-ММ" in response.json()["detail"]
