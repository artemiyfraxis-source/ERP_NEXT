"""«Обновить из ЭТК»: неначатая группа резки перекрепляется к новой ревизии ЭТК целиком или не меняется совсем."""

import json
from uuid import UUID, uuid4
from types import SimpleNamespace

import pytest

from app.main import app
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_cutting_groups import NOW, batch, document, groups, issue


def reissue(card, doc):
    with app.state.techcards_uow_factory() as uow:
        current = uow.techcards.get(card.id)
        uow.techcards.save(current.rebuild(doc, NOW))
        uow.commit()


def pinned():
    with app.state.production_uow_factory() as uow:
        return sorted((task.item, task.techcard_revision) for group in uow.cutting.list()
                      for task in (uow.production.get(m.task_id) for m in uow.cutting.members(group.id)))


def refresh(client, group):
    return client.post(f"/api/production/cutting-groups/{group['id']}/refresh-etk", json={"version": group["version"]})


def setup(client):
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    return cards, groups(client)[0]


def test_refresh_repins_all_members_keeps_group_and_is_idempotent(production_api):
    client, _ = production_api
    cards, group = setup(client)
    assert group["etk_stale"] is False
    reissue(cards[0], document(length=1200))
    reissue(cards[1], document(length=1300))
    assert groups(client)[0]["etk_stale"] is True
    response = refresh(client, group)
    assert response.status_code == 200, response.text
    fresh = response.json()["group"]
    assert (fresh["id"], fresh["number"]) == (group["id"], group["number"])
    assert fresh["version"] > group["version"]
    assert [row["position"] for row in fresh["rows"]] == [row["position"] for row in group["rows"]]
    assert sorted(row["cut_length_mm"] for row in fresh["rows"]) == ["1200", "1300"]
    assert pinned() == [("КБ1", 2), ("КБ2", 2)]
    assert len(groups(client)) == 1 and groups(client)[0]["etk_stale"] is False
    again = refresh(client, fresh)
    assert again.status_code == 200, again.text
    assert again.json()["group"]["version"] == fresh["version"]
    assert pinned() == [("КБ1", 2), ("КБ2", 2)]


def test_diverged_recipe_is_refused_without_partial_writes(production_api):
    client, _ = production_api
    cards, group = setup(client)
    reissue(cards[0], document(length=1200))
    reissue(cards[1], document(strip=4))
    response = refresh(client, group)
    assert response.status_code == 422, response.text
    assert "КБ2" in response.json()["detail"] and "КБ1" not in response.json()["detail"]
    assert pinned() == [("КБ1", 1), ("КБ2", 1)]
    assert groups(client)[0]["version"] == group["version"]


def test_started_group_is_refused(production_api):
    client, operator = production_api
    cards, group = setup(client)
    assigned = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
                           json={"version": group["version"], "operator_ids": [str(operator)]}).json()["group"]
    with app.state.production_uow_factory() as uow:
        uow.cutting.freeze(uow.cutting.members(UUID(group["id"]))[0].operation_id, NOW)
        uow.commit()
    reissue(cards[0], document(length=1200))
    current = groups(client)[0]
    assert current["state"] == "frozen"
    response = refresh(client, current)
    assert response.status_code == 422, response.text
    assert pinned() == [("КБ1", 1), ("КБ2", 1)]
    assert assigned["id"] == group["id"]


def test_cli_refresh_etk_preview_and_apply(production_api, monkeypatch, capsys):
    from app import database
    from app.workflows.cutting_group_existing import main

    client, _ = production_api
    cards, group = setup(client)
    reissue(cards[0], document(length=1200))
    reissue(cards[1], document(length=1300))
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr(database, "build_engine", lambda url: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(database, "build_production_uow_factory", lambda engine: app.state.production_uow_factory)
    monkeypatch.setattr(database, "build_techcards_uow_factory", lambda engine: app.state.techcards_uow_factory)
    assert main(["--project", "708К.", "--refresh-etk"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] and preview["refreshed"][0]["items"] and pinned() == [("КБ1", 1), ("КБ2", 1)]
    assert main(["--project", "708К.", "--refresh-etk", "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["refreshed"][0]["group_id"] == group["id"] and not applied["refused"]
    assert pinned() == [("КБ1", 2), ("КБ2", 2)]
    assert main(["--project", "708К.", "--refresh-etk", "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["current"]


def test_stale_flag_is_batched_across_groups(production_api, monkeypatch):
    from app.modules.techcards.infrastructure import SqlAlchemyTechcardRepository as Repo

    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2"), issue("КБ3", document(strip=4)), issue("КБ4", document(strip=4))]
    batch(client, cards)
    assert len(groups(client)) == 2
    reissue(cards[3], document(strip=4, length=1500))
    calls = {"for_project": 0, "by_key": 0}
    for name, original in (("for_project", Repo.for_project), ("by_key", Repo.by_key)):
        def wrapper(self, *args, _name=name, _original=original):
            calls[_name] += 1
            return _original(self, *args)
        monkeypatch.setattr(Repo, name, wrapper)
    result = groups(client)
    assert sorted(g["etk_stale"] for g in result) == [False, True]
    assert calls == {"for_project": 1, "by_key": 0}


def test_cli_refresh_etk_reports_failures_and_filters_tasks(production_api, monkeypatch, capsys):
    from app import database
    from app.modules.production.application import ConcurrentProductionUpdate
    from app.workflows import cutting_group_existing as workflow

    client, _ = production_api
    cards, group = setup(client)
    reissue(cards[0], document(length=1200))
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr(database, "build_engine", lambda url: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(database, "build_production_uow_factory", lambda engine: app.state.production_uow_factory)
    monkeypatch.setattr(database, "build_techcards_uow_factory", lambda engine: app.state.techcards_uow_factory)

    def conflict(*args, **kwargs):
        raise ConcurrentProductionUpdate("Группа изменилась")
    monkeypatch.setattr(workflow, "refresh_group_etk", conflict)
    assert workflow.main(["--project", "708К.", "--refresh-etk", "--apply"]) == 0
    failed = json.loads(capsys.readouterr().out)
    assert failed["refused"][0]["group_id"] == group["id"] and "изменилась" in failed["refused"][0]["reason"]
    assert pinned() == [("КБ1", 1), ("КБ2", 1)]
    assert workflow.main(["--project", "708К.", "--refresh-etk", "--task-id", str(uuid4())]) == 0
    other = json.loads(capsys.readouterr().out)
    assert not other["refreshed"] and not other["refused"] and not other["current"]
