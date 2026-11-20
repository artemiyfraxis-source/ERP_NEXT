"""Review of PR #977: regrouping never rewrites a group that still holds other recipes, honours the master's separation,
refuses equipment left on moved ETK operations; revalidation needs a current row; the label check sees text changes."""

from uuid import UUID, uuid4

import pytest

from app.main import app
from app.modules.production.resource_assignment_infrastructure import ResourceAllocationMachineRow, ResourceAllocationRow
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_cutting_group_refresh_etk import reissue
from tests.production.test_cutting_groups import NOW, batch, document, groups, issue, split_document
from tests.production.test_cutting_groups_existing import set_share
from tests.production.test_cutting_kinds import _cli, _tasks
from tests.production.test_cutting_refresh_restructure import _print_first_label, marked


def _group(group_id):
    with app.state.production_uow_factory() as uow:
        group = uow.cutting.get(UUID(group_id))
        return group.recipe_key, group.state, {uow.production.get(m.task_id).item for m in uow.cutting.members(group.id)}


def _issued(index, doc):
    """An issued card with its revision 1 recorded, as the application records it on the first issue."""
    from app.modules.techcards.application import _revision
    from tests.factories import Actor

    card = issue(index, doc)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.add_revision(_revision(card, "Первая выдача", Actor(id=uuid4(), name="Технолог", department=""), NOW))
        uow.commit()
    return card


def _active_group_of(operation_id):
    with app.state.production_uow_factory() as uow:
        member = uow.cutting.for_operation(operation_id)
        return str(member.group_id) if member else None


def test_group_keeps_its_recipe_while_a_refused_task_still_holds_a_row(production_api, monkeypatch, capsys):
    client, operator = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    group = groups(client)[0]
    key, _state, items = _group(group["id"])
    assert items == {"КБ1", "КБ2"}
    set_share(_tasks()["КБ2"], operator, started=True)  # КБ2 has work: its restructure is refused
    for card in cards:
        reissue(card, split_document())

    result = _cli(monkeypatch, capsys, "--apply")
    assert [row["item"] for row in result["restructured"]] == ["КБ1"]
    assert "работа" in {row["item"]: row["reason"] for row in result["refused"]}["КБ2"]
    assert _group(group["id"]) == (key, "open", {"КБ2"})  # old mixed recipe kept for the КБ2 row
    cut = _tasks()["КБ1"].operations[0]
    assert _active_group_of(cut.id) not in (None, group["id"])


def test_regroup_does_not_pull_back_a_pf_the_master_split_off(production_api, monkeypatch, capsys):
    client, _ = production_api
    batch(client, [issue("КБ1"), issue("КБ2")])
    group = groups(client)[0]
    row = next(r for r in group["rows"] if r["item"] == "КБ2")
    split = client.post(f"/api/production/cutting-groups/{group['id']}/split",
                        json={"version": group["version"], "member_ids": [row["id"]]})
    assert split.status_code == 200, split.text

    result = _cli(monkeypatch, capsys, "--apply")
    assert not result["restructured"] and not result["refused"]
    assert _group(group["id"])[2] == {"КБ1"}
    assert _active_group_of(UUID(row["operation_id"])) is None


def test_split_pf_whose_recipe_changed_is_regrouped(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    group = groups(client)[0]
    row = next(r for r in group["rows"] if r["item"] == "КБ2")
    client.post(f"/api/production/cutting-groups/{group['id']}/split",
                json={"version": group["version"], "member_ids": [row["id"]]})
    reissue(cards[1], document(strip=4))

    result = _cli(monkeypatch, capsys, "--apply")
    assert [r["item"] for r in result["restructured"]] == ["КБ2"]
    assert _active_group_of(UUID(row["operation_id"])) not in (None, group["id"])


def test_regroup_never_absorbs_a_separately_issued_group(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2"), issue("КБ3")]
    batch(client, cards[:2])
    shared = groups(client)[0]
    alone = client.post("/api/production/tasks", headers={"Idempotency-Key": "separate"},
                        json={"techcard_id": str(cards[2].id), "quantity": 10, "issue_separately": True})
    assert alone.status_code == 200, alone.text
    separate = next(g for g in groups(client) if g["id"] != shared["id"])
    assert [r["item"] for r in separate["rows"]] == ["КБ3"]

    _cli(monkeypatch, capsys, "--apply")
    assert _group(shared["id"])[1:] == ("open", {"КБ1", "КБ2"})
    assert _group(separate["id"])[1:] == ("open", {"КБ3"})


@pytest.mark.parametrize("position,refused", [(1, False), (2, True)])
def test_kept_pf_with_equipment_on_moved_operations_is_refused(production_api, monkeypatch, capsys, position, refused):
    client, _ = production_api
    cards = [issue("КБ1")]
    batch(client, cards)
    task = _tasks()["КБ1"]
    first = task.operations[0]  # the old «Заготовки» PF: ETK operations 1 (cut), 2 (labels), 3 (tubes)
    allocation = uuid4()
    with app.state.production_uow_factory() as uow:
        uow.session.add(ResourceAllocationRow(id=allocation, task_id=task.id, operation_id=first.id, crew=False))
        uow.session.flush()
        uow.session.add(ResourceAllocationMachineRow(allocation_id=allocation, etk_position=position, machine_id=uuid4()))
        uow.commit()
    reissue(cards[0], split_document())

    result = _cli(monkeypatch, capsys, "--apply")
    if refused:
        assert "назначено оборудование" in result["refused"][0]["reason"] and not result["restructured"]
        assert len(_tasks()["КБ1"].operations) == 2
    else:
        assert [r["item"] for r in result["restructured"]] == ["КБ1"] and not result["refused"]


def test_revalidate_refuses_a_row_behind_its_repinned_task(production_api):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    group = groups(client)[0]
    reissue(cards[1], document())  # same recipe, new revision
    row = next(r for r in group["rows"] if r["item"] == "КБ2")
    repinned = client.post(f"/api/production/tasks/{row['task_id']}/norms/refresh", json={"version": row["task_version"]})
    assert repinned.status_code == 200, repinned.text
    stale = groups(client)[0]
    response = client.post(f"/api/production/cutting-groups/{group['id']}/revalidate", json={"version": stale["version"]})
    assert response.status_code == 422, response.text
    assert "КБ2" in response.json()["detail"] and "Обновить из ЭТК" in response.json()["detail"]
    assert groups(client)[0]["version"] == stale["version"]
    refreshed = client.post(f"/api/production/cutting-groups/{group['id']}/refresh-etk", json={"version": stale["version"]})
    assert refreshed.status_code == 200, refreshed.text
    fresh = refreshed.json()["group"]
    assert {r["item"]: r["techcard_revision"] for r in fresh["rows"]} == {"КБ1": 1, "КБ2": 2}
    again = client.post(f"/api/production/cutting-groups/{group['id']}/revalidate", json={"version": fresh["version"]})
    assert again.status_code == 200, again.text


def test_label_check_compares_with_the_pinned_etk(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [_issued("КБ1", marked(document()))]
    batch(client, cards)
    _print_first_label(_tasks()["КБ1"])
    renamed = marked(split_document())
    renamed["operations"][1]["fields"]["marks"] = [{"text": "NEW.{№}", "qty": "1"}]
    reissue(cards[0], renamed)

    result = _cli(monkeypatch, capsys, "--apply")
    assert [r["item"] for r in result["restructured"]] == ["КБ1"]
    assert result["labels_unchanged"] is False
    assert (result["labels"][0]["printed"], result["printed_one_per_kb"]) == (0, 0)


def test_label_check_unchanged_when_the_label_text_stays(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [_issued("КБ1", marked(document()))]
    batch(client, cards)
    _print_first_label(_tasks()["КБ1"])
    reissue(cards[0], marked(split_document()))
    result = _cli(monkeypatch, capsys, "--apply")
    assert result["labels_unchanged"] is True and result["printed_one_per_kb"] == 1
    assert (result["labels"][0]["printed"], result["labels"][0]["remaining"]) == (1, 9)
