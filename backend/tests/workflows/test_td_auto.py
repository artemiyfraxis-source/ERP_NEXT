"""Автоматический выпуск комплекта ТД (владелец 21.10.2026): выдача ЭТК открывает выпуск, новая ревизия — изменение
с извещением «ИИ-…»; без блокирующих замечаний выпуск сразу действует; сбой выпуска не ломает выдачу ЭТК."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.techcards import td_backfill
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.td_issues import TdIssue, parse_input
from app.modules.techcards.td_issues_infrastructure import TdIssueRow
from app.shared.clock import moscow_now
from app.shared.persistence import create_schema
from app.workflows import td_auto
from app.workflows.etk_refresh import run_refresh
from fastapi.testclient import TestClient

from tests.references.test_load_mirror import sessions  # noqa: F401  (fixture)
from tests.techcards.conftest import authenticated_techcards_user  # noqa: F401  (autouse fixture)
from tests.techcards.test_project_cards import NOW, _document
from tests.workflows.test_etk_refresh import T0, T1, _derive, _set_depth, _settle, _store, base  # noqa: F401

STAFF = (  # (ФИО, старая роль, должность)
    ("Иванов И.И.", "staff", "Технолог"),
    ("Петров П.П.", "staff", "Директор производства"),
    ("Сидоров С.С.", "management", ""),  # генеральный директор — по старой роли
    ("Архипов А.А.", "staff", "Главный технолог"),  # «содержит» уступает точному совпадению
)


@pytest.fixture()
def env():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    engine.dispose()


def _staff(sessions, people=STAFF) -> None:
    with sessions() as session:
        for name, role, title in people:
            login = f"u{uuid4().hex[:8]}"
            session.add(IdentityUserRow(
                id=uuid4(), login=login, email=f"{login}@example.test", name=name, department="Офис", role=role,
                site=None, active=True, job_title=title,
            ))
        session.commit()


def _card(client, item: str = "01") -> dict:
    card = client.post("/api/techcards/projects", json={
        "project": "P-100", "item_index": item, "product_name": "Жгут", "source_calculation": "",
    }).json()
    return client.put(f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": _document()}).json()


def _issue_etk(client, card: dict) -> dict:
    issued = client.post(f"/api/techcards/projects/{card['id']}/issue", json={"version": card["version"]})
    assert issued.status_code == 200, issued.text
    return issued.json()


def _issues(client, card: dict) -> list[dict]:
    return client.get(f"/api/techcards/projects/{card['id']}/td-issues").json()


def test_issuing_the_etk_opens_an_active_kit_signed_by_staff_roles(env):
    client, sessions = env
    _staff(sessions)
    card = _issue_etk(client, _card(client))

    issue, = _issues(client, card)
    assert (issue["change_no"], issue["status"], issue["created_by"]) == (0, "active", "система")
    assert issue["approved_at"] and issue["etk_revision"] == 1 and issue["has_changes"] is False
    assert (issue["litera"], issue["method_code"], issue["workshop"], issue["site"]) == ("", "85", "Цех электромонтажа", "ЦАР")
    assert issue["signatories"] == {
        "developer": {"name": "Иванов И.И.", "position": "Технолог"},
        "checker": {"name": "Петров П.П.", "position": "Директор производства"},
        "normcontrol": {"name": "Сидоров С.С.", "position": "Генеральный директор"},
        "approver": {"name": "Сидоров С.С.", "position": "Генеральный директор"},
        "agreed_vp": {"name": "", "position": ""},
    }
    printed = client.get(f"/api/techcards/td-issues/{issue['id']}/print").json()
    assert set(printed["card"]["td_ops"]) == {"10::A::", "20::B::"}


def test_a_new_revision_opens_a_change_with_an_automatic_notice(env):
    client, sessions = env
    _staff(sessions)
    issued = _issue_etk(client, _card(client))
    rebuilt = client.put(f"/api/techcards/projects/{issued['id']}/etk",
                         json={"version": issued["version"], "document": _document(changed=True)})
    assert rebuilt.status_code == 200 and rebuilt.json()["revision"] == 2

    second, first = _issues(client, issued)
    assert (second["change_no"], second["status"], second["etk_revision"]) == (1, "active", 2)
    assert (second["notice_no"], second["reason"]) == ("ИИ-00001-1", "Пересборка")
    assert first["status"] == "annulled" and first["annulled_at"]
    assert second["sequence_no"] == first["sequence_no"] and second["signatories"] == first["signatories"]
    # Без новой ревизии новый выпуск не открывается.
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        assert td_auto.on_revision(uow, uow.techcards.get(UUID(issued["id"])), "повтор", NOW).action == "none"


def test_blocking_findings_leave_a_draft_that_later_revisions_refresh(env):
    client, _sessions = env  # сотрудников нет: подписанты пусты — нормоконтроль блокирует
    issued = _issue_etk(client, _card(client))

    draft, = _issues(client, issued)
    assert (draft["status"], draft["change_no"], draft["signatories"]["developer"]["name"]) == ("draft", 0, "")
    findings = client.get(f"/api/techcards/td-issues/{draft['id']}/normcontrol").json()["findings"]
    assert "signatory_missing" in {item["code"] for item in findings}
    rebuilt = client.put(f"/api/techcards/projects/{issued['id']}/etk",
                         json={"version": issued["version"], "document": _document(changed=True)}).json()

    again, = _issues(client, issued)  # второй выпуск не открылся — проект пересъёмлен
    assert (again["id"], again["status"], again["etk_revision"]) == (draft["id"], "draft", rebuilt["revision"])
    printed = client.get(f"/api/techcards/td-issues/{draft['id']}/print").json()
    assert printed["card"]["document"]["operations"][0]["time"] == 12


def test_fields_are_copied_from_the_latest_issue_of_any_card(env):
    client, sessions = env
    other = _card(client, item="02")
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:  # ручной выпуск другой карты: ЖУК, литера О1, вид ТП 88
        value = parse_input({
            "litera": "О1", "method_code": "88", "workshop": "Цех сборки", "site": "ЖУК",
            "signatories": {"developer": {"name": "Д", "position": "Инженер"}, "checker": {"name": "П", "position": "П"},
                            "normcontrol": {"name": "Н", "position": "Н"}, "approver": {"name": "У", "position": "У"}},
        })
        uow.td_issues.add(TdIssue.open(
            card_id=UUID(other["id"]), value=value, snapshot={"revision": 0, "document": {"operations": []}},
            existing=[], next_sequence=1, actor="Инженер", now=NOW,
        ))
        uow.commit()
    issued = _issue_etk(client, _card(client))

    issue, = _issues(client, issued)
    assert (issue["litera"], issue["method_code"], issue["workshop"], issue["site"]) == ("О1", "88", "Цех сборки", "ЖУК")
    assert issue["signatories"]["developer"] == {"name": "Д", "position": "Инженер"}
    assert issue["sequence_no"] == 2 and issue["status"] == "active"


def test_a_failing_kit_never_breaks_issuing_the_etk(env, monkeypatch):
    client, sessions = env

    def broken(*args, **kwargs):
        raise RuntimeError("снимок не собран")

    monkeypatch.setattr(td_auto, "snapshot", broken)
    issued = _issue_etk(client, _card(client))

    assert issued["status"] == "Выдана" and issued["revision"] == 1
    assert _issues(client, issued) == []
    with sessions() as session:
        assert session.scalars(select(TdIssueRow)).all() == []


def test_staff_signatories_blank_the_normcontroller_who_is_also_the_developer(env):
    _client, sessions = env
    _staff(sessions, (("Иванов И.И.", "management", "Технолог"),))
    with sessions() as session:
        found = td_auto.staff_signatories(session)
    assert found["developer"]["name"] == "Иванов И.И." and found["approver"]["name"] == "Иванов И.И."
    assert found["normcontrol"] == {"name": "", "position": "Генеральный директор"}


def test_backfill_dry_run_writes_nothing_and_apply_issues_the_kits(env, monkeypatch, capsys):
    client, sessions = env
    _staff(sessions)
    monkeypatch.setattr(td_auto, "on_revision", lambda *args, **kwargs: None)  # ЭТК выданы до автоматики
    first = _issue_etk(client, _card(client, item="01"))
    _issue_etk(client, _card(client, item="02"))
    monkeypatch.undo()
    _card(client, item="03")  # черновик ЭТК — не выдана, выпуска не будет

    assert td_backfill.main([], sessions_of=lambda _url: sessions) == 0
    out = capsys.readouterr().out
    assert "Пробный прогон, ничего не записано: карт без выпуска ТД — 2" in out
    assert "P-100 01: выпуск изм. 0 — active" in out
    assert _issues(client, first) == []

    assert td_backfill.main(["--apply"], sessions_of=lambda _url: sessions) == 0
    assert "Записано: карт без выпуска ТД — 2" in capsys.readouterr().out
    issue, = _issues(client, first)
    assert issue["status"] == "active" and issue["created_by"] == "система"
    assert td_backfill.backfill(sessions, apply=True) == []  # повтор — карт без выпуска нет


def test_an_automatic_etk_revision_opens_a_change_with_the_revision_reason(base):  # noqa: F811
    card = _store(base, _derive(base))
    _settle(base)
    with SqlAlchemyTechcardsUnitOfWork(base) as uow:  # действующий комплект ревизии 1
        value = parse_input({"method_code": "85", "workshop": "Цех электромонтажа", "site": "ЦАР"})
        opened = TdIssue.open(card_id=card.id, value=value, snapshot={"revision": 1, "document": {"operations": []}},
                              existing=[], next_sequence=1, actor="Инженер", now=T0)
        uow.td_issues.add(replace(opened, status="active", approved_at=T0))
        uow.commit()
    _set_depth(base, "0,9")

    assert run_refresh(base, now=T1).count("revised") == 1
    with SqlAlchemyTechcardsUnitOfWork(base) as uow:
        change, first = uow.td_issues.for_card(card.id)
    assert (change.change_no, change.etk_revision, change.created_by) == (1, 2, "система")
    assert change.notice_no == "ИИ-00001-1" and change.reason.startswith("обновлено по справочнику: ")
    assert change.status == "draft" and first.status == "active"  # подписанты пусты — блокирует, прежний действует
    assert change.snapshot["td_ops"] and change.snapshot["revision"] == 2
