"""Заявления на отпуск и отгул («Личная карточка», фаза 2): маршрут Сотрудник → Руководитель → Кадры, подпись PIN или
паролем на каждом шаге, проверка версии, норма и пересечения, уведомления, календарь цеха."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.domain import AccessMode, IdentityUser, hash_password, hash_pin
from app.modules.identity.infrastructure import (
    IdentityLeaveRequestRow,
    IdentityLeaveSignatureRow,
    IdentityLoginThrottleRow,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
    seed_level_matrix,
)
from app.modules.identity.leave import (
    VACATION_NORM_DAYS,
    LeaveConflict,
    LeaveError,
    LeaveForbidden,
    LeaveKind,
    LeaveStatus,
    SignatureRefused,
    check_period,
    content_sha256,
    create_leave,
    decide_leave,
    leave_overview,
    sign_leave,
    update_leave,
    waiting_count,
    withdraw_leave,
    year_balance,
)
from app.modules.production.application import (
    Absence,
    ReplaceProductionCalendarCommand,
    replace_production_calendar,
)
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.shared.clock import MOSCOW, moscow_now
from app.shared.identity import Actor
from app.shared.persistence import OutboxMessageRow, create_schema
from app.workflows.leave_calendar import absence_id, sync_leave_calendar

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=MOSCOW)
TODAY = NOW.date()
PASSWORD = "leave-password-1"
PIN = "482913"
CLIENT = "client:test"

EMPLOYEE = UUID("70000000-0000-0000-0000-000000000001")
MANAGER = UUID("70000000-0000-0000-0000-000000000002")
DIRECTOR = UUID("70000000-0000-0000-0000-000000000003")
HR = UUID("70000000-0000-0000-0000-000000000004")
COLLEAGUE = UUID("70000000-0000-0000-0000-000000000005")
ORPHAN = UUID("70000000-0000-0000-0000-000000000006")  # без руководителя
ADMIN = UUID("70000000-0000-0000-0000-000000000007")


def _user(user_id, login, name, *, level="operator", manager=None, active=True):
    return IdentityUser(
        id=user_id, login=login, email=f"{login}@example.test", name=name, department="Цех", role="staff", site="ЦАР",
        active=active, access_level=level, manager_id=manager,
    )


@pytest.fixture()
def erp():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    password, pin = hash_password(PASSWORD), hash_pin(PIN)
    people = (
        (_user(DIRECTOR, "director", "Директор Дмитрий", level="tech_lead"), {}),
        (_user(MANAGER, "manager", "Мастер Пётр", level="foreman", manager=DIRECTOR), {}),
        (_user(EMPLOYEE, "fitter", "Монтажник Анна", manager=MANAGER), {}),
        (_user(HR, "hr", "Кадровик Ольга"), {"hr": AccessMode.EDIT}),
        (_user(COLLEAGUE, "colleague", "Монтажник Борис", manager=MANAGER), {}),
        (_user(ORPHAN, "orphan", "Сотрудник без руководителя"), {}),
        (_user(ADMIN, "admin", "Администратор", level="admin"), {}),
    )
    with sessions() as session:
        for user, personal in people:
            provision_user(session, user, password, pin_hash=pin, level_access=personal)
        seed_level_matrix(session)
        session.commit()
    yield SimpleNamespace(
        sessions=sessions,
        identity=lambda: SqlAlchemyIdentityUnitOfWork(sessions),
        production=lambda: SqlAlchemyProductionUnitOfWork(sessions),
    )
    engine.dispose()


def actor(user_id: UUID) -> Actor:
    return Actor(id=user_id, name=str(user_id), department="Цех")


def draft(erp, who=EMPLOYEE, kind="vacation", start=14, days=14, comment="Отпуск по графику"):
    from_date = TODAY + timedelta(days=start)
    return create_leave(actor(who), kind, from_date, from_date + timedelta(days=days - 1), comment, NOW, erp.identity())


def signed(erp, who=EMPLOYEE, **kwargs):
    request = draft(erp, who, **kwargs)
    return sign_leave(request.id, request.version, actor(who), PIN, NOW, erp.identity(), client_key=CLIENT)


def decide(erp, request, who, approve=True, comment="", secret=PASSWORD):
    return decide_leave(
        request.id, request.version, actor(who), approve, comment, secret, NOW, erp.identity(), client_key=CLIENT,
    )


def registered(erp, **kwargs):
    approved = decide(erp, signed(erp, **kwargs), MANAGER)
    return decide(erp, approved, HR)


def notifications(erp) -> list[dict]:
    with erp.sessions() as session:
        rows = session.scalars(select(OutboxMessageRow).where(OutboxMessageRow.topic == "notification.requested"))
        return [dict(row.payload) for row in rows]


def calendar(erp) -> list[Absence]:
    with erp.production() as uow:
        return list(uow.calendar.absences())


# ── Маршрут ──────────────────────────────────────────────────────────────


def test_full_route_signs_every_step_and_lands_in_the_shop_calendar(erp):
    request = draft(erp)
    assert (request.status, request.content_version, request.version) == (LeaveStatus.DRAFT, 1, 1)

    request = sign_leave(request.id, 1, actor(EMPLOYEE), PIN, NOW, erp.identity(), client_key=CLIENT)
    assert request.status is LeaveStatus.SIGNED and request.version == 2
    request = decide(erp, request, MANAGER)
    assert request.status is LeaveStatus.APPROVED
    assert calendar(erp) == []  # согласовано руководителем — ещё не отпуск
    request = decide(erp, request, HR)
    assert request.status is LeaveStatus.REGISTERED and request.version == 4

    assert sync_leave_calendar(erp.identity, erp.production, NOW) == 1
    [absence] = calendar(erp)
    assert (absence.operator_id, absence.from_date, absence.to_date, absence.kind, absence.leave_request_id) == (
        EMPLOYEE, request.from_date, request.to_date, "Отпуск", request.id,
    )
    assert absence.id == absence_id(request.id)
    assert sync_leave_calendar(erp.identity, erp.production, NOW) == 0  # уже в календаре — ничего не пишет

    with erp.identity() as uow:
        stored = uow.leave.get(request.id)
    steps = [(item.step, item.decision, item.signer_id, item.method, item.content_version) for item in stored.signatures]
    assert steps == [
        ("employee", "signed", EMPLOYEE, "pin", 1),
        ("manager", "approved", MANAGER, "password", 1),
        ("hr", "registered", HR, "password", 1),
    ]
    assert {item.content_sha256 for item in stored.signatures} == {stored.content_sha256}
    assert stored.content_sha256 == content_sha256(
        EMPLOYEE, LeaveKind.VACATION, stored.from_date, stored.to_date, stored.comment, 1,
    )


def test_the_secret_is_never_stored(erp):
    request = registered(erp)
    with erp.sessions() as session:
        values = [
            str(value) for row in session.execute(select(IdentityLeaveSignatureRow)).all() for value in row[0].__dict__.values()
        ] + [str(value) for value in session.scalar(select(IdentityLeaveRequestRow)).__dict__.values()]
    assert not any(PIN in value or PASSWORD in value for value in values)
    assert request.status is LeaveStatus.REGISTERED


def test_a_wrong_secret_is_refused_and_counted_like_a_failed_login(erp):
    request = draft(erp)
    with pytest.raises(SignatureRefused, match="Неверный PIN или пароль"):
        sign_leave(request.id, 1, actor(EMPLOYEE), "000000", NOW, erp.identity(), client_key=CLIENT)
    with erp.identity() as uow:
        assert uow.leave.get(request.id).status is LeaveStatus.DRAFT
        assert uow.identity.failure_count("login:fitter", NOW, timedelta(minutes=10)) == 1
    with erp.sessions() as session:
        assert session.scalar(select(func.count()).select_from(IdentityLeaveSignatureRow)) == 0
        assert session.get(IdentityLoginThrottleRow, "login:fitter").failures == 1


def test_nobody_approves_their_own_request_and_only_the_manager_line_decides(erp):
    request = signed(erp)
    with pytest.raises(LeaveForbidden):
        decide(erp, request, EMPLOYEE)
    with pytest.raises(LeaveForbidden, match="руководитель"):
        decide(erp, request, COLLEAGUE)
    with pytest.raises(LeaveForbidden):
        decide(erp, request, HR)  # у сотрудника есть руководитель — кадры не подменяют его
    approved = decide(erp, request, DIRECTOR)  # руководитель руководителя — замещающий
    assert approved.status is LeaveStatus.APPROVED
    with pytest.raises(LeaveForbidden, match="Кадры"):
        decide(erp, approved, MANAGER)  # регистрирует только «Кадры»


def test_without_a_manager_the_manager_step_goes_to_hr(erp):
    request = signed(erp, who=ORPHAN)
    with pytest.raises(LeaveForbidden):
        decide(erp, request, MANAGER)
    approved = decide(erp, request, HR)
    assert approved.status is LeaveStatus.APPROVED
    assert decide(erp, approved, ADMIN).status is LeaveStatus.REGISTERED  # администратор — «Кадры» по уровню


def test_an_inactive_manager_is_replaced_by_the_managers_manager(erp):
    with erp.sessions() as session:
        from app.modules.identity.infrastructure import IdentityUserRow

        session.get(IdentityUserRow, MANAGER).active = False
        session.commit()
    request = signed(erp)
    with erp.identity() as uow:
        overview = leave_overview(DIRECTOR, uow)
    assert [item.id for item in overview.inbox] == [request.id]
    assert decide(erp, request, DIRECTOR).status is LeaveStatus.APPROVED


def test_a_stale_version_is_a_conflict(erp):
    request = signed(erp)
    decide(erp, request, MANAGER)
    with pytest.raises(LeaveConflict):
        decide(erp, request, DIRECTOR)  # версия 2 уже ушла: руководитель успел первым


def test_an_edit_after_signing_raises_the_content_version_and_needs_a_new_signature(erp):
    request = signed(erp)
    edited = update_leave(
        request.id, request.version, actor(EMPLOYEE), "vacation", request.from_date, request.to_date + timedelta(days=1),
        "Отпуск по графику", NOW, erp.identity(),
    )
    assert (edited.status, edited.content_version) == (LeaveStatus.DRAFT, 2)
    assert edited.content_sha256 != request.content_sha256
    with pytest.raises(LeaveConflict):
        decide(erp, edited, MANAGER)  # черновик решения не ждёт
    resigned = sign_leave(edited.id, edited.version, actor(EMPLOYEE), PIN, NOW, erp.identity(), client_key=CLIENT)
    assert [(item.step, item.content_version) for item in resigned.signatures] == [("employee", 1), ("employee", 2)]
    unchanged = update_leave(
        resigned.id, resigned.version, actor(EMPLOYEE), "vacation", resigned.from_date, resigned.to_date,
        resigned.comment, NOW, erp.identity(),
    )
    assert unchanged.version == resigned.version  # та же правка — ничего не меняется
    approved = decide(erp, resigned, MANAGER)
    with pytest.raises(LeaveConflict, match="правка невозможна"):
        update_leave(approved.id, approved.version, actor(EMPLOYEE), "vacation", approved.from_date,
                     approved.to_date, "", NOW, erp.identity())
    with pytest.raises(LeaveForbidden):
        update_leave(approved.id, approved.version, actor(MANAGER), "vacation", approved.from_date,
                     approved.to_date, "", NOW, erp.identity())


def test_a_rejection_needs_a_reason_and_tells_the_employee(erp):
    request = signed(erp)
    with pytest.raises(LeaveError, match="причину"):
        decide(erp, request, MANAGER, approve=False)
    rejected = decide(erp, request, MANAGER, approve=False, comment="Сдача проекта 708К")
    assert rejected.status is LeaveStatus.REJECTED
    [signature] = [item for item in rejected.signatures if item.step == "manager"]
    assert (signature.decision, signature.comment) == ("rejected", "Сдача проекта 708К")
    last = notifications(erp)[-1]
    assert last["recipient_user_ids"] == str(EMPLOYEE) and "Сдача проекта 708К" in last["body"]
    assert last["type"] == "leave"


def test_each_step_notifies_the_next_one(erp):
    request = signed(erp)
    first = notifications(erp)[-1]
    assert first["recipient_user_ids"].split(",") == [str(MANAGER), str(DIRECTOR)]
    assert first["title"] == "Заявление на согласование" and "Монтажник Анна" in first["body"]
    decide(erp, request, MANAGER)
    to_hr, to_employee = notifications(erp)[-2:]
    assert to_hr["recipient_user_ids"] == str(HR)  # явный «Кадры»; администраторы — только если никого нет
    assert to_employee["recipient_user_ids"] == str(EMPLOYEE)


def test_waiting_counts_follow_the_step(erp):
    request = signed(erp)
    assert waiting_count(MANAGER, erp.identity()) == 1 and waiting_count(DIRECTOR, erp.identity()) == 1
    assert waiting_count(HR, erp.identity()) == 0 and waiting_count(EMPLOYEE, erp.identity()) == 0
    decide(erp, request, MANAGER)
    assert waiting_count(MANAGER, erp.identity()) == 0
    assert waiting_count(HR, erp.identity()) == 1 and waiting_count(ADMIN, erp.identity()) == 1


# ── Период, норма, пересечения ───────────────────────────────────────────


def test_period_rules(erp):
    with pytest.raises(LeaveError, match="задним числом"):
        draft(erp, start=-1, days=2)
    with pytest.raises(LeaveError, match="отпуск или отгул"):
        draft(erp, kind="sick")
    from_date = TODAY + timedelta(days=3)
    with pytest.raises(LeaveError, match="раньше начала"):
        create_leave(actor(EMPLOYEE), "day_off", from_date, from_date - timedelta(days=1), "", NOW, erp.identity())
    draft(erp, start=20, days=5)
    with pytest.raises(LeaveError, match="пересекается"):
        draft(erp, kind="day_off", start=24, days=1)
    assert draft(erp, kind="day_off", start=25, days=1).kind is LeaveKind.DAY_OFF  # вплотную — можно


def test_the_years_vacation_norm_holds_and_withdrawn_requests_free_it(erp):
    first = draft(erp, start=20, days=20)
    with pytest.raises(LeaveError, match=f"норма {VACATION_NORM_DAYS} дн., уже в заявлениях 20 дн., в этом — 10 дн"):
        draft(erp, start=50, days=10)
    assert draft(erp, kind="day_off", start=50, days=10).kind is LeaveKind.DAY_OFF  # отгул норму не ест
    withdraw_leave(first.id, first.version, actor(EMPLOYEE), "", "", NOW, erp.identity(), client_key=CLIENT)
    assert draft(erp, start=70, days=10).days == 10


def test_a_vacation_across_new_year_counts_in_each_year():
    def request(start: date, finish: date, status=LeaveStatus.REGISTERED):
        return SimpleNamespace(
            kind=LeaveKind.VACATION, status=status, from_date=start, to_date=finish,
            overlaps=lambda a, b, s=start, f=finish: s <= b and a <= f,
        )

    others = [request(date(2026, 12, 1), date(2026, 12, 25))]  # 25 дней в 2026
    check_period(LeaveKind.VACATION, date(2026, 12, 29), date(2027, 1, 10), TODAY, others)  # 3 + 10
    with pytest.raises(LeaveError, match="Отпуск 2026 года"):
        check_period(LeaveKind.VACATION, date(2026, 12, 27), date(2027, 1, 10), TODAY, others)  # 5 в 2026
    balance = year_balance([
        replace_status(registered_request(date(2026, 12, 1), date(2026, 12, 25)), LeaveStatus.REGISTERED),
        replace_status(registered_request(date(2026, 12, 29), date(2027, 1, 10)), LeaveStatus.SIGNED),
    ], 2026)
    assert balance == {"year": 2026, "norm": 28, "registered": 25, "pending": 3, "left": 0}


def registered_request(start: date, finish: date):
    from app.modules.identity.leave import LeaveRequest

    return LeaveRequest(
        id=uuid4(), employee_id=EMPLOYEE, kind=LeaveKind.VACATION, from_date=start, to_date=finish, comment="",
        status=LeaveStatus.REGISTERED, content_version=1, content_sha256="0" * 64, version=1, created_at=NOW,
        updated_at=NOW,
    )


def replace_status(request, status):
    return replace(request, status=status)


# ── Отзыв ────────────────────────────────────────────────────────────────


def test_the_author_withdraws_until_the_leave_starts_and_hr_cancels_any_time(erp):
    request = registered(erp)
    sync_leave_calendar(erp.identity, erp.production, NOW)
    assert len(calendar(erp)) == 1
    with pytest.raises(LeaveForbidden):
        withdraw_leave(request.id, request.version, actor(COLLEAGUE), PASSWORD, "", NOW, erp.identity(), client_key=CLIENT)
    later = NOW + timedelta(days=15)  # отпуск уже идёт
    with pytest.raises(LeaveForbidden, match="уже начался"):
        withdraw_leave(request.id, request.version, actor(EMPLOYEE), PIN, "", later, erp.identity(), client_key=CLIENT)
    cancelled = withdraw_leave(
        request.id, request.version, actor(HR), PASSWORD, "Отозван из отпуска", later, erp.identity(), client_key=CLIENT,
    )
    assert cancelled.status is LeaveStatus.WITHDRAWN
    assert cancelled.signatures[-1].step == "withdraw" and cancelled.signatures[-1].signer_id == HR
    assert notifications(erp)[-1]["recipient_user_ids"] == str(EMPLOYEE)
    assert sync_leave_calendar(erp.identity, erp.production, NOW) == 1
    assert calendar(erp) == []


def test_withdrawing_a_draft_needs_no_signature_but_a_signed_one_does(erp):
    request = draft(erp)
    gone = withdraw_leave(request.id, request.version, actor(EMPLOYEE), "", "", NOW, erp.identity(), client_key=CLIENT)
    assert gone.status is LeaveStatus.WITHDRAWN and gone.signatures == ()
    request = signed(erp, start=40)
    with pytest.raises(SignatureRefused):
        withdraw_leave(request.id, request.version, actor(EMPLOYEE), "", "", NOW, erp.identity(), client_key=CLIENT)
    withdrawn = withdraw_leave(request.id, request.version, actor(EMPLOYEE), PIN, "", NOW, erp.identity(), client_key=CLIENT)
    assert withdrawn.status is LeaveStatus.WITHDRAWN
    with pytest.raises(LeaveConflict, match="закрыто"):
        withdraw_leave(withdrawn.id, withdrawn.version, actor(EMPLOYEE), PIN, "", NOW, erp.identity(), client_key=CLIENT)


# ── Календарь цеха ───────────────────────────────────────────────────────


def test_the_masters_calendar_save_keeps_leave_absences_and_does_not_copy_them(erp):
    request = registered(erp)
    sync_leave_calendar(erp.identity, erp.production, NOW)
    [leave] = calendar(erp)
    sick = Absence(uuid4(), COLLEAGUE, TODAY, TODAY + timedelta(days=2), "Больничный")
    # The screen sends everything it listed back, the leave row included (an older screen without the mark too).
    replace_production_calendar(ReplaceProductionCalendarCommand(
        holidays=frozenset(), absences=(sick, replace(leave, id=uuid4(), leave_request_id=None)),
        actor=actor(MANAGER), now=NOW,
    ), erp.production())
    rows = calendar(erp)
    assert sorted((item.operator_id, item.kind, item.leave_request_id) for item in rows) == sorted([
        (EMPLOYEE, "Отпуск", request.id), (COLLEAGUE, "Больничный", None),
    ])
    replace_production_calendar(ReplaceProductionCalendarCommand(
        holidays=frozenset(), absences=(), actor=actor(MANAGER), now=NOW,
    ), erp.production())
    assert [item.leave_request_id for item in calendar(erp)] == [request.id]  # мастер стёр свои — отпуск на месте


def test_an_employee_with_leave_requests_has_history(erp):
    draft(erp)
    with erp.identity() as uow:
        assert uow.identity.user_history(EMPLOYEE) >= 1
        assert uow.identity.user_history(COLLEAGUE) == 0


# ── HTTP ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def api(erp):
    app.state.identity_uow_factory = erp.identity
    app.state.production_uow_factory = erp.production
    app.state.cookie_secure = False
    app.state.identity_clock = lambda: NOW
    app.dependency_overrides[moscow_now] = lambda: NOW

    def login(name: str) -> TestClient:
        client = TestClient(app)
        response = client.post("/api/auth/login", json={"login": name, "password": PASSWORD})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = client.cookies.get("erp_csrf")
        return client

    yield login
    app.dependency_overrides.pop(moscow_now, None)
    for name in ("identity_uow_factory", "production_uow_factory", "cookie_secure", "identity_clock"):
        if hasattr(app.state, name):
            delattr(app.state, name)


def test_http_route_from_card_to_calendar(erp, api):
    me, boss, hr = api("fitter"), api("manager"), api("hr")
    start = (TODAY + timedelta(days=10)).isoformat()
    finish = (TODAY + timedelta(days=12)).isoformat()

    created = me.post("/api/leave-requests", json={"kind": "day_off", "from_date": start, "to_date": finish, "comment": ""})
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["status_label"], body["kind_label"], body["days"]) == ("Черновик", "Отгул", 3)
    assert body["actions"] == {"edit": True, "sign": True, "decide": False, "withdraw": True}

    wrong = me.post(f"/api/leave-requests/{body['id']}/sign", json={"version": 1, "secret": "111111"})
    assert wrong.status_code == 422 and "PIN" in wrong.json()["detail"]
    sent = me.post(f"/api/leave-requests/{body['id']}/sign", json={"version": 1, "secret": PIN})
    assert sent.status_code == 200, sent.text
    assert sent.json()["waiting_for"] == "Мастер Пётр, Директор Дмитрий"

    assert boss.get("/api/leave-requests/waiting").json() == {"count": 1}
    inbox = boss.get("/api/leave-requests").json()
    assert [item["id"] for item in inbox["inbox"]] == [body["id"]] and inbox["journal"] is None
    assert inbox["inbox"][0]["actions"]["decide"] is True

    assert me.post(f"/api/leave-requests/{body['id']}/decision",
                   json={"version": 2, "approve": True, "secret": PIN}).status_code == 403
    stale = boss.post(f"/api/leave-requests/{body['id']}/decision", json={"version": 1, "approve": True, "secret": PIN})
    assert stale.status_code == 409
    approved = boss.post(f"/api/leave-requests/{body['id']}/decision", json={"version": 2, "approve": True, "secret": PIN})
    assert approved.status_code == 200 and approved.json()["waiting_for"] == "Кадры"

    journal = hr.get("/api/leave-requests").json()
    assert journal["hr"] is True and [item["id"] for item in journal["journal"]] == [body["id"]]
    registered_ = hr.post(f"/api/leave-requests/{body['id']}/decision",
                          json={"version": 3, "approve": True, "secret": PASSWORD})
    assert registered_.status_code == 200 and registered_.json()["status_label"] == "Зарегистрировано кадрами"
    assert [item["step_label"] for item in registered_.json()["signatures"]] == ["Сотрудник", "Руководитель", "Кадры"]

    # Синхронизация сразу после решения: отгул уже в календаре цеха, его видит «Загрузка по людям».
    shop = boss.get("/api/production/calendar")
    assert shop.status_code == 200, shop.text
    assert [item["leave_request_id"] for item in shop.json()["absences"]] == [body["id"]]
    assert [item.leave_request_id for item in calendar(erp)] == [UUID(body["id"])]

    own = me.get("/api/leave-requests").json()
    assert own["balance"] == {"year": 2026, "norm": 28, "registered": 0, "pending": 0, "left": 28}
    assert own["own"][0]["actions"]["withdraw"] is True
    withdrawn = me.post(f"/api/leave-requests/{body['id']}/withdraw", json={"version": 4, "secret": PIN})
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "withdrawn"
    assert calendar(erp) == []


def test_http_needs_a_login_and_refuses_unknown_requests(erp, api):
    assert TestClient(app).get("/api/leave-requests").status_code == 401
    me = api("fitter")
    missing = me.post(f"/api/leave-requests/{uuid4()}/sign", json={"version": 1, "secret": PIN})
    assert missing.status_code == 404
    bad = me.post("/api/leave-requests", json={"kind": "vacation", "from_date": "2026-10-01", "to_date": "2026-10-02"})
    assert bad.status_code == 422 and "задним числом" in bad.json()["detail"]
