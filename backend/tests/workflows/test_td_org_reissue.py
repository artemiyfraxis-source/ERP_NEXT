"""Перевыпуск комплектов ТД под кодом ЮТНВ (владелец 22.10.2026): выпущенный документ не правится — открывается изменение
с тем же порядковым номером и снимком, новыми подписантами; прежний аннулируется; пробный прогон не пишет ничего."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.techcards.td_issues_infrastructure import TdIssueRow
from app.workflows import td_org_reissue

from tests.techcards.conftest import authenticated_techcards_user  # noqa: F401  (autouse fixture)
from tests.techcards.test_project_cards import NOW
from tests.workflows.test_td_auto import _card, _issue_etk, _issues, _staff, env  # noqa: F401  (env — фикстура)

OLD_PEOPLE = {
    "developer": {"name": "Администратор ERP", "position": ""},
    "checker": {"name": "Касаточкин Василий", "position": "Директор производства"},
    "normcontrol": {"name": "Баковский Всеволод", "position": ""},
    "approver": {"name": "Баковский Всеволод", "position": "Генеральный директор"},
    "agreed_vp": {"name": "", "position": ""},
}


def _legacy(sessions, card: dict, **people) -> None:
    """Действующий выпуск карты — как выпущенный до 22.10: код ФРАК, прежние подписанты."""
    with sessions() as session:
        session.execute(update(TdIssueRow).where(TdIssueRow.project_card_id == UUID(card["id"])).values(
            org_code="ФРАК", signatories={**OLD_PEOPLE, **people},
        ))
        session.commit()


def _rows(sessions) -> list[tuple]:
    with sessions() as session:
        return sorted(
            (str(row.project_card_id), row.change_no, row.sequence_no, row.org_code, row.status, row.version)
            for row in session.scalars(select(TdIssueRow))
        )


def _setup(client, sessions) -> dict[str, dict]:
    _staff(sessions)
    with sessions() as session:
        session.add(IdentityUserRow(
            id=uuid4(), login="zingera", email="zingera@example.test", name="Зингер Артем", department="Офис",
            role="staff", site=None, active=True, job_title="Инженер по качеству",
        ))
        session.commit()
    cards = {item: _issue_etk(client, _card(client, item=item)) for item in ("01", "02", "03", "04")}
    for item in ("01", "02"):
        _legacy(sessions, cards[item])
    _legacy(sessions, cards["03"], checker={"name": "Зингер Артем", "position": ""})  # Пров. = Н.контр. по умолчанию
    # 04 — уже ЮТНВ; у 02 — изменение в работе
    active, = _issues(client, cards["02"])
    draft = client.post(f"/api/techcards/projects/{cards['02']['id']}/td-issues", json={
        "method_code": "85", "signatories": OLD_PEOPLE, "notice_no": "ИИ-9", "reason": "правка", "workshop": "Цех", "site": "ЦАР",
    })
    assert draft.status_code == 201, draft.text
    assert active["status"] == "active"
    return cards


def test_dry_run_writes_nothing_and_yes_reissues_under_the_new_code(env, capsys):
    client, sessions = env
    cards = _setup(client, sessions)
    before_rows = _rows(sessions)
    before, = _issues(client, cards["01"])
    sessions_of = lambda _url: sessions  # noqa: E731

    assert td_org_reissue.main(["--dry-run"], sessions_of=sessions_of) == 0
    out = capsys.readouterr().out
    assert "Пробный прогон, ничего не записано: действующих выпусков ФРАК — 3; перевыпустить — 1, пропущено — 2" in out
    assert "P-100 01: ФРАК.40185.00001 изм. 0 → ЮТНВ.40185.00001 изм. 1 — будет перевыпущена" in out
    assert "Разраб. Администратор ERP → Васильев Артемий; Пров. Касаточкин Василий; " \
           "Н.контр. Баковский Всеволод → Зингер Артем; Утв. Баковский Всеволод → Костромин Василий" in out
    assert "P-100 02: ФРАК.40185.00002 изм. 0 — пропущена: у карты выпуск в работе (изм. 1, «проект»)" in out
    assert "P-100 03: ФРАК.40185.00003 изм. 0 — пропущена: нормоконтроль блокирует: normcontrol_same_person" in out
    assert _rows(sessions) == before_rows

    assert td_org_reissue.main(["--yes"], sessions_of=sessions_of) == 0
    assert "Записано: действующих выпусков ФРАК — 3; перевыпущено — 1, пропущено — 2" in capsys.readouterr().out
    new, old = _issues(client, cards["01"])
    assert (old["id"], old["status"], old["org_code"]) == (before["id"], "annulled", "ФРАК")
    assert old["annulled_at"] and old["annulled_at"] == new["approved_at"]  # в одной транзакции
    assert old["signatories"] == OLD_PEOPLE and old["designations"]["vtd"] == "ФРАК.40185.00001"  # прежний не правился
    assert (new["status"], new["org_code"], new["change_no"], new["sequence_no"]) == ("active", "ЮТНВ", 1, old["sequence_no"])
    assert new["designations"]["vtd"] == "ЮТНВ.40185.00001" and new["approved_at"]
    assert (new["etk_revision"], new["litera"], new["method_code"], new["workshop"], new["site"]) == (
        old["etk_revision"], old["litera"], old["method_code"], old["workshop"], old["site"])
    assert (new["notice_no"], new["reason"], new["created_by"]) == ("ИИ-00001-1", td_org_reissue.REASON, "система")
    assert new["signatories"] == {
        "developer": {"name": "Васильев Артемий", "position": ""},
        "checker": OLD_PEOPLE["checker"],
        "normcontrol": {"name": "Зингер Артем", "position": "Инженер по качеству"},
        "approver": {"name": "Костромин Василий", "position": ""},
        "agreed_vp": OLD_PEOPLE["agreed_vp"],
    }
    printed = {item: client.get(f"/api/techcards/td-issues/{issue['id']}/print").json()
               for item, issue in (("old", old), ("new", new))}
    assert printed["new"]["card"] == printed["old"]["card"]  # тот же снимок
    assert [row["change_no"] for row in printed["new"]["changes"]] == [0, 1]  # лист регистрации изменений
    for item in ("02", "03", "04"):  # пропущенные и уже ЮТНВ не тронуты
        assert [(issue["org_code"], issue["status"]) for issue in _issues(client, cards[item]) if issue["status"] == "active"] == [
            ("ЮТНВ" if item == "04" else "ФРАК", "active")]

    after_rows = _rows(sessions)
    assert td_org_reissue.main(["--yes"], sessions_of=sessions_of) == 0  # повтор: перевыпущенная не трогается
    assert "перевыпущено — 0, пропущено — 2" in capsys.readouterr().out
    assert _rows(sessions) == after_rows


def test_nothing_to_reissue(env, capsys):
    client, sessions = env
    _staff(sessions)
    _issue_etk(client, _card(client))

    assert td_org_reissue.reissue(sessions, apply=True, now=NOW) == []
    assert td_org_reissue.main([], sessions_of=lambda _url: sessions) == 0
    assert "действующих выпусков ФРАК — 0" in capsys.readouterr().out
