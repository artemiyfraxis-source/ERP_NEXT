"""Комплект ТД: API выпусков, их статусы и ограничения таблицы td_issues на SQLite."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.td_issues_infrastructure import TdIssueRow
from app.shared.clock import moscow_now
from app.shared.persistence import create_schema
from app.workflows import td_auto
from fastapi.testclient import TestClient

from .test_project_cards import NOW, _document

PEOPLE = {
    "developer": {"name": "Иванов И.И.", "position": "Технолог"},
    "checker": {"name": "Петров П.П.", "position": "Ведущий технолог"},
    "normcontrol": {"name": "Сидорова С.С.", "position": "Нормоконтролёр"},
    "approver": {"name": "Васильев А.А.", "position": "Главный инженер"},
    "agreed_vp": {"name": "", "position": ""},
}
PLACE = {"workshop": "Цех электромонтажа", "site": "ЦАР"}


@pytest.fixture()
def td(monkeypatch):
    # Ручной путь выпусков: автоматический выпуск при выдаче ЭТК проверяется в tests/workflows/test_td_auto.py.
    monkeypatch.setattr(td_auto, "on_revision", lambda *args, **kwargs: None)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.dependency_overrides[moscow_now] = lambda: NOW
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    yield TestClient(app), engine
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    del app.state.references_uow_factory
    engine.dispose()


def _card(client, *, item: str = "01", issued: bool = True) -> dict:
    card = client.post("/api/techcards/projects", json={
        "project": "P-100", "item_index": item, "product_name": "Жгут", "source_calculation": "Расчёт 7",
    }).json()
    card = client.put(
        f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": _document()},
    ).json()
    if issued:
        card = client.post(f"/api/techcards/projects/{card['id']}/issue", json={"version": card["version"]}).json()
    return card


def _open(client, card: dict, **override):
    body = {"litera": "О1", "method_code": "85", "signatories": PEOPLE, "notice_no": "", "reason": "",
            **PLACE, **override}
    return client.post(f"/api/techcards/projects/{card['id']}/td-issues", json=body)


def _move(client, issue: dict, to: str):
    return client.post(f"/api/techcards/td-issues/{issue['id']}/status", json={"version": issue["version"], "to": to})


def _activate(client, issue: dict) -> dict:
    signing = _move(client, issue, "signing")
    assert signing.status_code == 200, signing.text
    active = _move(client, signing.json(), "active")
    assert active.status_code == 200, active.text
    return active.json()


def test_open_issue_freezes_the_card_and_names_the_documents(td):
    client, _ = td
    card = _card(client)

    created = _open(client, card)
    issue = created.json()

    assert created.status_code == 201, created.text
    assert issue["status"] == "draft" and issue["status_label"] == "проект"
    assert (issue["change_no"], issue["sequence_no"], issue["version"]) == (0, 1, 1)
    assert issue["designations"]["ok"] == "ЮТНВ.60185.00001"
    assert issue["designations"]["ok_tk"] == "ЮТНВ.60102.00001"
    assert issue["etk_revision"] == card["revision"] == issue["current_etk_revision"]
    assert issue["has_changes"] is False
    assert issue["created_by"] == "Инженер" and issue["approved_at"] is None
    assert set(issue) >= {"org_code", "method_code", "litera", "signatories", "notice_no", "reason", "annulled_at"}
    assert (issue["workshop"], issue["site"]) == ("Цех электромонтажа", "ЦАР")
    printed = client.get(f"/api/techcards/td-issues/{issue['id']}/print").json()
    td_ops = printed["card"].pop("td_ops")
    assert printed["card"] == client.get(f"/api/techcards/projects/{card['id']}").json()
    assert td_ops == {  # коды «10» / «20» — не из семейств ЕТКС и не из БД.ОП: разряд пуст
        "10::A::": {"profession": "Монтажник радиоэлектронной аппаратуры и приборов", "grade": "", "source": "по умолчанию"},
        "20::B::": {"profession": "Монтажник радиоэлектронной аппаратуры и приборов", "grade": "", "source": "по умолчанию"},
    }
    assert printed["sto"] == {"designation": "СТО ФРАК-001-2026", "revision": "1"}
    assert set(printed["sto_sections"]) == {"10::A::", "20::B::"}
    assert printed["changes"] == []
    listed = client.get(f"/api/techcards/projects/{card['id']}/td-issues").json()
    assert [item["id"] for item in listed] == [issue["id"]]


def test_card_without_etk_cannot_be_issued(td):
    client, _ = td
    card = client.post("/api/techcards/projects", json={
        "project": "P-100", "item_index": "02", "product_name": "Жгут", "source_calculation": "",
    }).json()

    assert _open(client, card).status_code == 409
    assert client.post(f"/api/techcards/projects/{uuid4()}/td-issues", json={"method_code": "85"}).status_code == 404


def test_full_lifecycle_and_wrong_transitions(td):
    client, _ = td
    issue = _open(client, _card(client)).json()

    assert _move(client, issue, "active").status_code == 409  # проект сразу в «действует» нельзя
    signing = _move(client, issue, "signing").json()
    assert signing["status_label"] == "на подписи"
    edit = client.put(f"/api/techcards/td-issues/{issue['id']}", json={
        "version": signing["version"], "litera": "А", "method_code": "88", "signatories": PEOPLE, **PLACE,
    })
    assert edit.status_code == 409  # правка только в проекте
    resnap = client.post(f"/api/techcards/td-issues/{issue['id']}/resnapshot", json={"version": signing["version"]})
    assert resnap.status_code == 409
    back = _move(client, signing, "draft").json()
    assert back["status"] == "draft"
    edited = client.put(f"/api/techcards/td-issues/{issue['id']}", json={
        "version": back["version"], "litera": "А", "method_code": "88", "signatories": PEOPLE, **PLACE,
    })
    assert edited.status_code == 200 and edited.json()["designations"]["mk"] == "ЮТНВ.10188.00001"
    stale = client.put(f"/api/techcards/td-issues/{issue['id']}", json={
        "version": back["version"], "litera": "А", "method_code": "88", "signatories": PEOPLE, **PLACE,
    })
    assert stale.status_code == 409  # устаревшая версия
    active = _activate(client, edited.json())
    assert active["status_label"] == "действует" and active["approved_at"]
    assert _move(client, active, "signing").status_code == 409
    annulled = _move(client, active, "annulled").json()
    assert annulled["status_label"] == "аннулирован" and annulled["annulled_at"]
    assert _move(client, annulled, "draft").status_code == 409


def test_draft_can_be_annulled_and_bad_input_is_refused(td):
    client, _ = td
    card = _card(client)

    assert _open(client, card, litera="Z").status_code == 422
    assert _open(client, card, method_code="99").status_code == 422
    same = _open(client, card, signatories={**PEOPLE, "normcontrol": {"name": "иванов и.и.", "position": ""}})
    assert same.status_code == 422 and "Н.контр." in same.json()["detail"]
    issue = _open(client, card).json()
    assert _open(client, card).status_code == 409  # у карты уже есть выпуск в работе
    assert _move(client, issue, "annulled").json()["status"] == "annulled"


def test_signing_is_blocked_by_normcontrol(td):
    client, _ = td
    # Пров. по умолчанию не подставляется: пустой — нормоконтроль блокирует.
    issue = _open(client, _card(client), signatories={**PEOPLE, "checker": {"name": "", "position": ""}}).json()

    blocked = _move(client, issue, "signing")
    findings = client.get(f"/api/techcards/td-issues/{issue['id']}/normcontrol").json()["findings"]

    assert blocked.status_code == 422
    assert [item["code"] for item in blocked.json()["detail"]["findings"]] == ["signatory_missing"]
    assert {item["code"] for item in findings} >= {"signatory_missing", "agreed_vp_missing", "no_control_step", "grade_missing"}
    assert client.get(f"/api/techcards/td-issues/{issue['id']}").json()["status"] == "draft"


def _users(engine, *people) -> None:
    """Сотрудники (логин, ФИО, должность, действует)."""
    with Session(engine) as session:
        for login, name, title, active in people:
            session.add(IdentityUserRow(
                id=uuid4(), login=login, email=f"{login}@example.test", name=name, department="Офис", role="staff",
                site=None, active=active, job_title=title,
            ))
        session.commit()


def test_blank_developer_normcontrol_and_approver_get_the_default_signers(td):
    client, engine = td
    _users(
        engine, ("vasilev", "Васильев Артемий", "Технолог", True), ("zinger", "Зингер Антон", "Монтажник", True),
        ("zingera", "Зингер Артем", "Инженер по качеству", True), ("kostromin-old", "Костромин Василий", "", False),
    )
    blank = {"name": "", "position": ""}
    created = _open(client, _card(client), signatories={
        **PEOPLE, "developer": blank, "normcontrol": blank, "approver": {"name": "", "position": "Генеральный директор"},
    })

    assert created.status_code == 201, created.text
    issue = created.json()
    assert issue["org_code"] == "ЮТНВ" and issue["designations"]["vtd"] == "ЮТНВ.40185.00001"
    assert issue["signatories"] == {
        "developer": {"name": "Васильев Артемий", "position": "Технолог"},  # по логину vasilev
        "checker": PEOPLE["checker"],
        "normcontrol": {"name": "Зингер Артем", "position": "Инженер по качеству"},  # zingera, не однофамилец zinger
        # kostromin нет среди действующих: ФИО из настройки, вписанная должность остаётся
        "approver": {"name": "Костромин Василий", "position": "Генеральный директор"},
        "agreed_vp": PEOPLE["agreed_vp"],
    }
    explicit = _open(client, _card(client, item="02")).json()
    assert explicit["signatories"] == PEOPLE  # вписанное вручную не меняется


def test_change_needs_notice_and_activation_annuls_the_previous_issue(td):
    client, _ = td
    card = _card(client)
    first = _activate(client, _open(client, card).json())

    assert _open(client, card).status_code == 422  # изменение 1 без извещения
    assert _open(client, card, notice_no="ИИ-7").status_code == 422  # без причины
    second = _open(client, card, notice_no="ИИ-7", reason="Новая ревизия ЭТК").json()
    assert (second["change_no"], second["sequence_no"]) == (1, first["sequence_no"])
    clear = client.put(f"/api/techcards/td-issues/{second['id']}", json={
        "version": second["version"], "method_code": "85", "signatories": PEOPLE,
    })
    assert clear.status_code == 422  # снять извещение с изменения нельзя
    second = _activate(client, second)

    issues = {item["change_no"]: item for item in client.get(f"/api/techcards/projects/{card['id']}/td-issues").json()}
    assert list(issues) == [1, 0]  # новые сверху
    assert issues[0]["status"] == "annulled" and issues[0]["annulled_at"]
    assert issues[1]["status"] == "active"
    changes = client.get(f"/api/techcards/td-issues/{second['id']}/print").json()["changes"]
    assert [(row["change_no"], row["notice_no"], row["designation"], row["developer"]) for row in changes] == [
        (0, "", "ЮТНВ.40185.00001", "Иванов И.И."), (1, "ИИ-7", "ЮТНВ.40185.00001", "Иванов И.И."),
    ]


def test_place_is_kept_and_its_absence_blocks_signing(td):
    client, _ = td
    issue = _open(client, _card(client), workshop="", site="").json()

    assert _open(client, _card(client, item="02"), site="Москва").status_code == 422
    blocked = _move(client, issue, "signing")
    assert blocked.status_code == 422
    assert [item["code"] for item in blocked.json()["detail"]["findings"]] == ["place_missing"]
    edited = client.put(f"/api/techcards/td-issues/{issue['id']}", json={
        "version": issue["version"], "method_code": "85", "signatories": PEOPLE, "workshop": "Цех электромонтажа", "site": "ЖУК",
    })
    assert edited.status_code == 200 and (edited.json()["workshop"], edited.json()["site"]) == ("Цех электромонтажа", "ЖУК")
    assert _move(client, edited.json(), "signing").status_code == 200


def _op_row(engine, fields: dict) -> None:
    with SqlAlchemyReferencesUnitOfWork(sessionmaker(bind=engine, expire_on_commit=False, class_=Session)) as uow:
        uow.tech_operations.save(TechOperationRecord.create(
            tab=TechOperationTab.OPERATION, display_text=fields["opNumber"], search_text="", source_sheet="БД.ОП",
            sort_key=fields["opNumber"], fields=fields,
        ))
        uow.commit()


def test_issue_freezes_profession_and_grade_from_the_operations_base(td):
    client, engine = td
    _op_row(engine, {"opNumber": "10", "opName": "Обработка", "opProfession": "Слесарь-сборщик", "opGrade": "5"})
    card = _card(client)
    issue = _open(client, card).json()

    td_ops = client.get(f"/api/techcards/td-issues/{issue['id']}/print").json()["card"]["td_ops"]
    assert td_ops["10::A::"] == {"profession": "Слесарь-сборщик", "grade": "5", "source": "БД.ОП"}
    assert td_ops["20::B::"]["source"] == "по умолчанию"
    # Правка БД.ОП после выпуска снимок не меняет — до «Обновить снимок ЭТК».
    _op_row(engine, {"opNumber": "20", "opName": "Сборка", "opGrade": "4"})
    again = client.get(f"/api/techcards/td-issues/{issue['id']}/print").json()["card"]["td_ops"]
    assert again["20::B::"]["grade"] == ""
    client.post(f"/api/techcards/td-issues/{issue['id']}/resnapshot", json={"version": issue["version"]})
    fresh = client.get(f"/api/techcards/td-issues/{issue['id']}/print").json()["card"]["td_ops"]
    assert fresh["20::B::"] == {"profession": "Монтажник радиоэлектронной аппаратуры и приборов", "grade": "4", "source": "БД.ОП"}


def test_second_card_takes_the_next_sequence_number(td):
    client, _ = td
    first = _open(client, _card(client, item="01")).json()
    second = _open(client, _card(client, item="02")).json()

    assert (first["sequence_no"], second["sequence_no"]) == (1, 2)
    assert second["designations"]["vtd"] == "ЮТНВ.40185.00002"


def test_etk_revision_bump_shows_changes_while_print_keeps_the_snapshot(td):
    client, _ = td
    card = _card(client)
    issue = _open(client, card).json()

    rebuilt = client.put(
        f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": _document(changed=True)},
    ).json()
    fresh = client.get(f"/api/techcards/td-issues/{issue['id']}").json()
    printed = client.get(f"/api/techcards/td-issues/{issue['id']}/print").json()

    assert rebuilt["revision"] > card["revision"]
    assert fresh["has_changes"] is True and fresh["current_etk_revision"] == rebuilt["revision"]
    assert printed["issue"]["has_changes"] is True
    assert printed["card"]["revision"] == card["revision"]
    assert printed["card"]["document"]["operations"][0]["time"] == 10
    resnap = client.post(f"/api/techcards/td-issues/{issue['id']}/resnapshot", json={"version": issue["version"]})
    assert resnap.status_code == 200 and resnap.json()["has_changes"] is False
    assert client.get(f"/api/techcards/td-issues/{issue['id']}/print").json()["card"]["document"]["operations"][0]["time"] == 12


def test_sto_endpoint_returns_the_standard(td):
    client, _ = td
    body = client.get("/api/techcards/sto").json()

    assert body["designation"] == "СТО ФРАК-001-2026" and [section["code"] for section in body["sections"]][:2] == ["4.1", "4.2"]


# ── ограничения таблицы ──────────────────────────────────────────────────────


def _row(card_id, **override) -> dict:
    row = {
        "id": uuid4(), "version": 1, "project_card_id": card_id, "change_no": 0, "sequence_no": 1, "org_code": "ФРАК",
        "method_code": "85", "litera": "", "status": "draft", "etk_revision": 1, "snapshot": {}, "signatories": {},
        "notice_no": "", "reason": "", "created_at": datetime(2026, 10, 20, tzinfo=timezone.utc), "created_by": "И",
    }
    row.update(override)
    return row


def _insert(engine, *rows) -> None:
    with engine.begin() as connection:
        connection.execute(insert(TdIssueRow.__table__), list(rows))


@pytest.fixture()
def card_ids(td):
    client, engine = td
    return engine, _card(client, item="01")["id"], _card(client, item="02")["id"]


def test_second_active_issue_of_a_card_is_impossible(card_ids):
    engine, first, second = card_ids
    _insert(engine, _row(UUID(first), status="active"), _row(UUID(second), status="active", sequence_no=2))

    with pytest.raises(IntegrityError):
        _insert(engine, _row(UUID(first), status="active", change_no=1, notice_no="И-1", reason="р"))
    _insert(engine, _row(UUID(first), status="annulled", change_no=1, notice_no="И-1", reason="р"))


@pytest.mark.parametrize("override", [
    {"status": "approved"},
    {"litera": "Z"},
    {"method_code": "99"},
    {"change_no": -1},
    {"change_no": 1},
    {"change_no": 1, "notice_no": "И-1"},
    {"change_no": 1, "reason": "причина"},
    {"site": "МСК"},
])
def test_checks_fire_on_sqlite(card_ids, override):
    engine, first, _ = card_ids

    with pytest.raises(IntegrityError):
        _insert(engine, _row(UUID(first), **override))


def test_change_number_and_sequence_are_unique(card_ids):
    engine, first, second = card_ids
    _insert(engine, _row(UUID(first)))

    with pytest.raises(IntegrityError):  # тот же номер изменения у карты
        _insert(engine, _row(UUID(first), sequence_no=5))
    with pytest.raises(IntegrityError):  # первый выпуск другой карты под тем же порядковым номером
        _insert(engine, _row(UUID(second)))
    _insert(engine, _row(UUID(second), sequence_no=2))

