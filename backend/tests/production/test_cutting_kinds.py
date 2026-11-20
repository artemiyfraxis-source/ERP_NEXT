"""Owner 20.11: cable cut, labels and tubes are separate grouped tasks; unstarted tasks are rebuilt in place."""

import json
from types import SimpleNamespace

import pytest

from app.main import app
from app.modules.production.cutting import CUT, LABELS, TUBES, pf_kind, printed
from app.workflows import cutting_group_existing as workflow
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_cutting_group_refresh_etk import refresh, reissue
from tests.production.test_cutting_groups import (
    batch, document, groups, issue, recipe, recipes, split_document,
)
from tests.production.test_cutting_groups_existing import set_share


def _cli(monkeypatch, capsys, *args):
    from app import database

    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr(database, "build_engine", lambda url: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(database, "build_production_uow_factory", lambda engine: app.state.production_uow_factory)
    monkeypatch.setattr(database, "build_techcards_uow_factory", lambda engine: app.state.techcards_uow_factory)
    assert workflow.main(["--project", "708К.", "--restructure", *args]) == 0
    return json.loads(capsys.readouterr().out)


def _tasks():
    with app.state.production_uow_factory() as uow:
        return {task.item: task for task in uow.production.for_project("708К.")}


def _open_groups(client):
    return [g for g in groups(client) if g["state"] != "cancelled"]


def test_old_mixed_pf_keeps_its_one_cut_recipe_and_key():
    mixed = recipes(document())
    assert [item.kind for item in mixed] == [CUT]
    assert mixed[0].snapshot["version"] == 1 and "kind" not in mixed[0].snapshot
    assert mixed[0].key == recipe(document())[0].key
    assert [op["type"] for op in mixed[0].snapshot["operations"]] == ["coaxCut", "coaxCutTut", "coaxCutTut"]


def test_split_etk_has_one_recipe_per_kind_and_only_the_cut_depends_on_the_cable():
    first, second = recipes(split_document()), recipes(split_document(length=2000, strip=4))
    assert [item.kind for item in first] == [CUT, LABELS, TUBES]
    assert [item.snapshot["version"] for item in first] == [1, 2, 2]
    assert len({item.key for item in first}) == 3 and first[0].key != recipes(document())[0].key
    assert first[0].key != second[0].key  # strip settings
    assert [item.key for item in first[1:]] == [item.key for item in second[1:]]
    assert [len(item.row["labels"]) for item in first] == [0, 3, 0]
    assert first[1].row["cut_length_mm"] == "1000" and second[1].row["cut_length_mm"] == "2000"


def test_kind_rule_for_pf_without_cut():
    assert pf_kind([("coaxCut", False), ("coaxCutTut", True)]) == CUT
    assert pf_kind([("coaxCutTut", True)]) == LABELS
    assert pf_kind([("cutTut", False)]) == TUBES
    assert printed({"markMaterial": "tape"}) and printed({}, "PRINT_MARK_TUBE") and not printed({}, "CUT_TUT_short")
    with pytest.raises(ValueError):
        pf_kind([("coaxCutTut", True), ("coaxCutTut", False)])


def test_launch_groups_every_kind_separately_and_never_mixes_kinds(production_api):
    client, _ = production_api
    result = batch(client, [issue("КБ1", split_document()), issue("КБ2", split_document(length=1200)),
                            issue("КБ3", split_document(strip=4))])
    assert not result["refused"] and not result["grouping_refused"]
    current = groups(client)
    shape = sorted((g["kind"], sorted(row["item"] for row in g["rows"])) for g in current)
    assert shape == [("cut", ["КБ1", "КБ2"]), ("cut", ["КБ3"]),
                     ("labels", ["КБ1", "КБ2", "КБ3"]), ("tubes", ["КБ1", "КБ2", "КБ3"])]
    with app.state.production_uow_factory() as uow:
        for group in uow.cutting.list():
            ops = {op["type"] for op in group.recipe["operations"]}
            assert len({"coaxCut"} & ops) == (group.recipe.get("kind", "cut") == "cut")


def test_restructure_rebuilds_in_place_keeps_identity_and_regroups_by_kind(production_api, monkeypatch, capsys):
    client, operator = production_api
    cards = [issue("КБ1"), issue("КБ2", document(length=1200))]
    batch(client, cards)
    group = groups(client)[0]
    assigned = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
                           json={"version": group["version"], "operator_ids": [str(operator)]})
    assert assigned.status_code == 200, assigned.text
    before = _tasks()
    for card, length in zip(cards, (1000, 1200)):
        reissue(card, split_document(length=length))

    preview = _cli(monkeypatch, capsys)
    assert preview["dry_run"] and len(preview["restructured"]) == 2 and not preview["refused"]
    assert sorted(g["kind"] for g in preview["groups"]) == ["cut", "labels", "tubes"]
    assert all(preview["invariants"].values())
    assert preview["counts"]["before"]["operations"] == 4 and preview["counts"]["after"]["operations"] == 8
    assert _tasks() == before  # rolled back

    applied = _cli(monkeypatch, capsys, "--apply")
    assert all(applied["invariants"].values()) and not applied["refused"] and applied["labels_unchanged"]
    after = _tasks()
    for item, task in after.items():
        old = before[item]
        assert (task.id, task.number, task.quantity, task.techcard_revision) == (old.id, old.number, old.quantity, 2)
        assert len(task.operations) == 4
        cut = task.operations[0]
        assert cut.id == old.operations[0].id and cut.operator_ids == (operator,) and cut.planned_start
        assert [op.operator_ids for op in task.operations[1:3]] == [(operator,), (operator,)]
        assert task.operations[3].id == old.operations[1].id
    current = _open_groups(client)
    assert sorted((g["kind"], len(g["rows"])) for g in current) == [("cut", 2), ("labels", 2), ("tubes", 2)]
    cut_group = next(g for g in current if g["kind"] == "cut")
    assert (cut_group["id"], cut_group["number"]) == (group["id"], group["number"])
    assert all(row["membership_state"] == "active" for row in cut_group["rows"])

    again = _cli(monkeypatch, capsys, "--apply")
    assert not again["restructured"] and not any(g["created"] for g in again["groups"])
    assert [g["version"] for g in _open_groups(client)] == [g["version"] for g in current]


def test_restructure_is_refused_once_work_exists(production_api, monkeypatch, capsys):
    client, operator = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    with app.state.production_uow_factory() as uow:
        task = next(t for t in uow.production.for_project("708К.") if t.item == "КБ2")
    group = groups(client)[0]
    client.post(f"/api/production/cutting-groups/{group['id']}/split",
                json={"version": group["version"], "member_ids": [r["id"] for r in group["rows"] if r["item"] == "КБ2"]})
    set_share(task, operator, started=True)
    for card in cards:
        reissue(card, split_document())
    result = _cli(monkeypatch, capsys, "--apply")
    refused = {row["item"]: row["reason"] for row in result["refused"]}
    assert "работа" in refused["КБ2"]
    assert [row["item"] for row in result["restructured"]] == ["КБ1"]
    assert len(_tasks()["КБ2"].operations) == 2 and _tasks()["КБ2"].techcard_revision == 1


def test_repin_of_a_grouped_row_then_refresh_moves_it(production_api):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    group = groups(client)[0]
    reissue(cards[1], document(strip=4))
    row = next(r for r in group["rows"] if r["item"] == "КБ2")
    repinned = client.post(f"/api/production/tasks/{row['task_id']}/norms/refresh", json={"version": row["task_version"]})
    assert repinned.status_code == 200, repinned.text
    stale = groups(client)[0]
    assert stale["etk_stale"] is True and len(stale["rows"]) == 2
    response = refresh(client, stale)
    assert response.status_code == 200, response.text
    assert {r["item"]: r["membership_state"] for r in response.json()["group"]["rows"]} == {"КБ1": "active", "КБ2": "split"}
    moved = next(g for g in _open_groups(client) if g["id"] != group["id"])
    assert [r["item"] for r in moved["rows"]] == ["КБ2"] and moved["rows"][0]["techcard_revision"] == 2
