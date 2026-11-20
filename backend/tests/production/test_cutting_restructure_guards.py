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


def _bind(task, operation, *positions, current=True):
    """Equipment bound to ETK operations of the PF (an allocation with one member; `current=False` — a retired one)."""
    from app.modules.production.resource_assignment_infrastructure import ResourceAllocationMemberRow

    allocation = uuid4()
    with app.state.production_uow_factory() as uow:
        uow.session.add(ResourceAllocationRow(id=allocation, task_id=task.id, operation_id=operation.id, crew=False))
        uow.session.flush()
        uow.session.add_all([ResourceAllocationMachineRow(allocation_id=allocation, etk_position=position,
                                                          machine_id=uuid4()) for position in positions])
        uow.session.add(ResourceAllocationMemberRow(id=uuid4(), allocation_id=allocation, operation_id=operation.id,
                                                    operator_id=uuid4(), current=current))
        uow.commit()
    return allocation


def _bound(allocation):
    with app.state.production_uow_factory() as uow:
        return sorted(row.etk_position for row in uow.session.query(ResourceAllocationMachineRow).filter_by(
            allocation_id=allocation))


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
    _bind(task, first, position)
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


# --- second review round ---------------------------------------------------------------------------------------------

def _separate(group_id):
    from app.modules.production.cutting_application import issued_separately

    with app.state.production_uow_factory() as uow:
        return issued_separately(uow.cutting.get(UUID(group_id)))


def _memberships(task):
    """{preparation PF stage: its active group id or None} of one task (the assembly PF is not grouped)."""
    with app.state.production_uow_factory() as uow:
        current = uow.production.get(task.id)
        result = {}
        for operation in current.operations:
            if operation.inputs:
                continue
            member = uow.cutting.for_operation(operation.id)
            result[operation.stage] = str(member.group_id) if member else None
        return result


def _versions():
    with app.state.production_uow_factory() as uow:
        return sorted((str(g.id), g.version, g.state) for g in uow.cutting.list())


def test_revalidated_group_then_repinned_task_needs_refresh_before_start(production_api):
    from tests.production.test_cutting_execution import action

    client, operator = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    group = groups(client)[0]
    group = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
                        json={"version": group["version"], "operator_ids": [str(operator)]}).json()["group"]
    reissue(cards[1], document())  # same recipe, revision 2
    revalidated = client.post(f"/api/production/cutting-groups/{group['id']}/revalidate", json={"version": group["version"]})
    assert revalidated.status_code == 200, revalidated.text
    row = next(r for r in revalidated.json()["group"]["rows"] if r["item"] == "КБ2")
    repinned = client.post(f"/api/production/tasks/{row['task_id']}/norms/refresh", json={"version": row["task_version"]})
    assert repinned.status_code == 200, repinned.text  # the task on revision 2, its row still on 1
    stale = groups(client)[0]
    assert stale["etk_stale"] is True
    single = client.post(f"/api/production/tasks/{row['task_id']}/operations/{row['operation_id']}/work/start",
                         params={"on_behalf_of": str(operator)}, json={})
    assert single.status_code == 409, single.text
    refused = action(client, operator, stale, "start")
    assert refused.status_code == 422 and "Обновить из ЭТК" in refused.json()["detail"], refused.text

    refreshed = client.post(f"/api/production/cutting-groups/{group['id']}/refresh-etk", json={"version": stale["version"]})
    assert refreshed.status_code == 200, refreshed.text
    fresh = refreshed.json()["group"]
    assert fresh["version"] > stale["version"]
    assert {r["item"]: r["techcard_revision"] for r in fresh["rows"]} == {"КБ1": 1, "КБ2": 2}
    assert groups(client)[0]["etk_stale"] is False
    started = action(client, operator, fresh, "start")
    assert started.status_code == 200, started.text


def _inserted_operation(doc):
    """The split ETK with a new operation (its own PF) inserted before the assembly: «ASM» moves from №4 to №5."""
    from copy import deepcopy

    doc["build_config"]["ops"].insert(3, {"type": "coaxHousing", "opNumber": "CHECK", "templateId": "check", "tOp": 5})
    extra = deepcopy(doc["operations"][3])
    extra.update(code="CHECK", template_id="check", fields={})
    doc["operations"].insert(3, extra)
    for number, operation in enumerate(doc["operations"], 1):
        operation["position"] = number
    route = doc["route"]
    route["blocks"] = [*route["blocks"][:3], {"key": "check", "ops": [4], "name": "Проверка"},
                       {**route["blocks"][3], "ops": [5]}]
    route["edges"] = [*route["edges"], {"from": "check", "to": "assembly", "per_unit": 1}]
    return doc


def test_equipment_follows_its_etk_operation_when_numbers_shift(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1")]
    batch(client, cards)
    task = _tasks()["КБ1"]
    assembly = task.operations[1]  # ETK №4 «ASM»
    allocation = _bind(task, assembly, 4)
    retired = _bind(task, assembly, 4, current=False)
    reissue(cards[0], _inserted_operation(split_document()))

    result = _cli(monkeypatch, capsys, "--apply")
    assert [r["item"] for r in result["restructured"]] == ["КБ1"] and not result["refused"]
    rebuilt = _tasks()["КБ1"].operation(assembly.id)
    assert [(item.position, item.code) for item in rebuilt.etk_ops] == [(5, "ASM")]
    assert _bound(allocation) == [5]
    assert _bound(retired) == [4]  # history is not rewritten


def test_pfs_split_out_of_a_separately_issued_task_stay_separate(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2"), issue("КБ3")]
    batch(client, cards[:2])
    shared = groups(client)[0]
    alone = client.post("/api/production/tasks", headers={"Idempotency-Key": "separate"},
                        json={"techcard_id": str(cards[2].id), "quantity": 10, "issue_separately": True})
    assert alone.status_code == 200, alone.text
    separate = next(g for g in groups(client) if g["id"] != shared["id"])
    for card in cards:
        reissue(card, split_document())

    _cli(monkeypatch, capsys, "--apply")
    tasks = _tasks()
    own = _memberships(tasks["КБ3"])
    assert len(own) == 3 and own[tasks["КБ3"].operations[0].stage] == separate["id"]
    assert all(group_id and _separate(group_id) and _group(group_id)[2] == {"КБ3"} for group_id in own.values())
    shared_ids = set(_memberships(tasks["КБ1"]).values())
    assert shared_ids == set(_memberships(tasks["КБ2"]).values()) and not shared_ids & set(own.values())
    before = _versions()
    _cli(monkeypatch, capsys, "--apply")
    assert _versions() == before


def test_pfs_split_out_of_a_pf_the_master_split_off_stay_standalone(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    group = groups(client)[0]
    row = next(r for r in group["rows"] if r["item"] == "КБ2")
    client.post(f"/api/production/cutting-groups/{group['id']}/split",
                json={"version": group["version"], "member_ids": [row["id"]]})
    for card in cards:
        reissue(card, split_document())

    result = _cli(monkeypatch, capsys, "--apply")
    assert sorted(r["item"] for r in result["restructured"]) == ["КБ1", "КБ2"]
    tasks = _tasks()
    assert len(_memberships(tasks["КБ2"])) == 3 and set(_memberships(tasks["КБ2"]).values()) == {None}
    assert None not in _memberships(tasks["КБ1"]).values()
    again = _cli(monkeypatch, capsys, "--apply")
    assert not again["restructured"]
    assert set(_memberships(tasks["КБ2"]).values()) == {None}  # the rerun keeps them standalone


def test_separate_group_replaced_keeps_the_separation_on_rerun(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2"), issue("КБ3")]
    batch(client, cards[:2])
    shared = groups(client)[0]
    client.post("/api/production/tasks", headers={"Idempotency-Key": "separate"},
                json={"techcard_id": str(cards[2].id), "quantity": 10, "issue_separately": True})
    separate = next(g for g in groups(client) if g["id"] != shared["id"])
    with app.state.production_uow_factory() as uow:
        uow.cutting.get(UUID(separate["id"])).site = "ЖУК"  # it can no longer take its PF back
        uow.commit()

    _cli(monkeypatch, capsys, "--apply")
    cut = _tasks()["КБ3"].operations[0]
    replacement = _active_group_of(cut.id)
    assert replacement not in (None, shared["id"], separate["id"]) and _separate(replacement)
    before = _versions()
    _cli(monkeypatch, capsys, "--apply")
    assert _active_group_of(cut.id) == replacement and _versions() == before


def test_pf_left_ungrouped_by_an_earlier_rebuild_is_regrouped_on_rerun(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1")]
    batch(client, cards)
    broken = split_document()
    broken["operations"][0]["card"]["tooling"] = [{"section": "Оснастка", "name": "нож"}]  # no cut recipe
    reissue(cards[0], broken)
    first = _cli(monkeypatch, capsys, "--apply")
    assert [r["item"] for r in first["restructured"]] == ["КБ1"]
    cut = _tasks()["КБ1"].operations[0]
    assert _active_group_of(cut.id) is None

    reissue(cards[0], split_document())
    _cli(monkeypatch, capsys, "--apply")
    assert _active_group_of(cut.id) is not None  # a new cut group: the old one was cancelled when it emptied


def test_label_check_sees_a_changed_fixed_label(production_api, monkeypatch, capsys):
    from app.modules.warehouse.label_jobs import LabelJobRow
    from app.modules.warehouse.marking_profiles import MarkingBatchRow

    client, _ = production_api
    doc = marked(document())
    doc["operations"][1]["fields"]["marks"].append({"text": "XS1", "qty": "1"})
    cards = [_issued("КБ1", doc)]
    batch(client, cards)
    task = _tasks()["КБ1"]
    _print_first_label(task)
    with app.state.production_uow_factory() as uow:  # the fixed label XS1 of item 1 is printed too
        printed = uow.session.query(MarkingBatchRow).one()
        printed.manifest = {"labels": [*printed.manifest["labels"],
                                       {"task_id": str(task.id), "serial": None, "text": "XS1", "quantity": 1}]}
        job = uow.session.query(LabelJobRow).one()
        copy = {column.name: getattr(job, column.name) for column in LabelJobRow.__table__.columns}
        uow.session.add(LabelJobRow(**{**copy, "id": uuid4(), "sequence": 2}))
        uow.commit()
    renamed = marked(split_document())
    renamed["operations"][1]["fields"]["marks"].append({"text": "XS2", "qty": "1"})
    reissue(cards[0], renamed)

    result = _cli(monkeypatch, capsys, "--apply")
    assert (result["labels"][0]["printed"], result["labels"][0]["remaining"]) == (1, 9)  # numbered unchanged
    assert result["labels"][0]["remaining_labels"] == 19 and result["labels_unchanged"] is False


# --- third review round ----------------------------------------------------------------------------------------------

def _tail_document(tail, *, labels=(), cut=(), tubes=()):
    """The split ETK whose operations after the labels/tubes are `tail` [(code, block «check»|«assembly»)]; `cut`,
    `labels`, `tubes` — codes of extra operations appended to those PFs (they shift every later number)."""
    from copy import deepcopy

    doc = split_document()
    raw, ops = doc["build_config"]["ops"], doc["operations"]
    plan = [(raw[0], ops[0], "cut"), *[(raw[3], ops[3], "cut", code) for code in cut],
            (raw[1], ops[1], "labels"), *[(raw[3], ops[3], "labels", code) for code in labels],
            (raw[2], ops[2], "tubes"), *[(raw[3], ops[3], "tubes", code) for code in tubes],
            *[(raw[3], ops[3], block, code) for code, block in tail]]
    doc["build_config"]["ops"], doc["operations"], blocks = [], [], {}
    for number, item in enumerate(plan, 1):
        source_raw, source_op, block = item[:3]
        new_raw, new_op = deepcopy(source_raw), deepcopy(source_op)
        if len(item) == 4:
            new_raw["opNumber"], new_op["code"], new_op["fields"] = item[3], item[3], {}
        new_op["position"] = number
        if len(item) == 4:
            new_op["side"] = f"S{number}"  # code + side is unique in an ETK: ЗАЧ-A, ЗАЧ-B
        doc["build_config"]["ops"].append(new_raw)
        doc["operations"].append(new_op)
        blocks.setdefault(block, []).append(number)
    names = {"cut": "Нарезанный и разделанный кабель", "labels": "Этикетки", "tubes": "Нарезанные трубки",
             "check": "Проверка", "assembly": "Сборка"}
    doc["route"]["blocks"] = [{"key": key, "ops": numbers, "name": names[key], **({"final": True} if key == "assembly"
                                                                                else {})} for key, numbers in blocks.items()]
    doc["route"]["edges"] = [{"from": key, "to": "assembly", "per_unit": 1} for key in blocks if key != "assembly"]
    return doc


ZACH = [("ЗАЧ", "assembly"), ("ЗАЧ", "assembly"), ("ASM", "assembly")]  # assembly PF: №4 ЗАЧ-A, №5 ЗАЧ-B, №6 ASM


@pytest.mark.parametrize("change,bound,refused,after", [
    ("drop", 4, True, None),            # ЗАЧ-A dropped: the binding of №4 would land on ЗАЧ-B
    ("insert_same", 4, True, None),     # a third ЗАЧ in the PF: which one is meant is not known
    ("insert_other", 5, False, [6]),    # a ЗАЧ in the tubes PF shifts numbers only: ЗАЧ-B №5 → №6
])
def test_equipment_matching_counts_same_code_operations_inside_the_pf(
        production_api, monkeypatch, capsys, change, bound, refused, after):
    client, _ = production_api
    cards = [issue("КБ1", _tail_document(ZACH))]
    batch(client, cards)
    task = _tasks()["КБ1"]
    assembly = next(op for op in task.operations if [item.code for item in op.etk_ops] == ["ЗАЧ", "ЗАЧ", "ASM"])
    allocation = _bind(task, assembly, bound)
    new = {"drop": _tail_document(ZACH[1:]), "insert_same": _tail_document([("ЗАЧ", "assembly"), *ZACH]),
           "insert_other": _tail_document(ZACH, tubes=["ЗАЧ"])}[change]
    reissue(cards[0], new)

    result = _cli(monkeypatch, capsys, "--apply")
    if refused:
        assert not result["restructured"] and "ЗАЧ" in result["refused"][0]["reason"], result["refused"]
        assert _bound(allocation) == [bound]
    else:
        assert [r["item"] for r in result["restructured"]] == ["КБ1"] and not result["refused"]
        assert _bound(allocation) == after


def test_binding_outside_the_old_pf_operations_is_kept_while_the_number_exists(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1")]
    batch(client, cards)
    task = _tasks()["КБ1"]
    assembly = task.operations[1]  # ETK №4 only; the binding names №5
    allocation = _bind(task, assembly, 5)
    reissue(cards[0], _tail_document([("CHECK", "assembly"), ("ASM", "assembly")]))  # assembly PF: №4, №5

    result = _cli(monkeypatch, capsys, "--apply")
    assert [r["item"] for r in result["restructured"]] == ["КБ1"] and not result["refused"]
    assert _bound(allocation) == [5]


def test_retired_equipment_on_a_moving_operation_does_not_refuse(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1")]
    batch(client, cards)
    task = _tasks()["КБ1"]
    retired = _bind(task, task.operations[0], 2, current=False)  # labels operation of the old «Заготовки»
    reissue(cards[0], split_document())

    result = _cli(monkeypatch, capsys, "--apply")
    assert [r["item"] for r in result["restructured"]] == ["КБ1"] and not result["refused"]
    assert _bound(retired) == [2]


def test_separated_task_keeps_its_tubes_pf_when_an_inserted_operation_shifts_numbers(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2"), issue("КБ3")]
    batch(client, cards[:2])
    alone = client.post("/api/production/tasks", headers={"Idempotency-Key": "separate"},
                        json={"techcard_id": str(cards[2].id), "quantity": 10, "issue_separately": True})
    assert alone.status_code == 200, alone.text
    for card in cards:
        reissue(card, _tail_document([("ASM", "assembly")], cut=["CHECK"]))  # tubes №3 → №4

    _cli(monkeypatch, capsys, "--apply")
    tasks = _tasks()
    tubes = next(op for op in tasks["КБ3"].operations if [item.code for item in op.etk_ops] == ["CUT_TUT_short"])
    group_id = _active_group_of(tubes.id)
    assert group_id and _separate(group_id) and _group(group_id)[2] == {"КБ3"}


# --- prod-copy rehearsal: --release-moved-equipment -------------------------------------------------------------------

def _released_events(task):
    from sqlalchemy import select

    from app.shared.persistence import AuditEventRow

    with app.state.production_uow_factory() as uow:
        return [row.details for row in uow.session.scalars(select(AuditEventRow).where(
            AuditEventRow.aggregate_id == task.id, AuditEventRow.event_type == "production.equipment_released"))]


def test_release_moved_equipment_removes_only_bindings_of_moved_operations(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1")]
    batch(client, cards)
    task = _tasks()["КБ1"]
    first = task.operations[0]  # the old «Заготовки»: №1 cut, №2 labels, №3 tubes
    allocation = _bind(task, first, 1, 2, 3)
    retired = _bind(task, first, 2, current=False)
    reissue(cards[0], split_document())

    off = _cli(monkeypatch, capsys, "--apply")  # default: refusal as before
    assert not off["restructured"] and "назначено оборудование" in off["refused"][0]["reason"]
    assert _bound(allocation) == [1, 2, 3]

    preview = _cli(monkeypatch, capsys, "--release-moved-equipment")
    assert [(row["item"], row["etk_position"], row["code"]) for row in preview["released"]] == [
        ("КБ1", 2, "PRINT_MARK_LABEL"), ("КБ1", 3, "CUT_TUT_short")]
    assert _bound(allocation) == [1, 2, 3] and not _released_events(task)  # dry run rolled back

    applied = _cli(monkeypatch, capsys, "--apply", "--release-moved-equipment")
    assert [r["item"] for r in applied["restructured"]] == ["КБ1"] and not applied["refused"]
    assert _bound(allocation) == [1] and _bound(retired) == [2]
    events = _released_events(task)
    assert sorted((e["code"], e["etk_position"], e["stage"]) for e in events) == [
        ("CUT_TUT_short", 3, first.stage), ("PRINT_MARK_LABEL", 2, first.stage)]
    assert all(e["machine_id"] and e["task_id"] == str(task.id) and e["task_number"] == task.number for e in events)
    rebuilt = _tasks()["КБ1"]
    landed = {op.etk_ops[0].code: (str(op.id), op.etk_ops[0].position) for op in rebuilt.operations if op.etk_ops}
    assert {(e["code"], e["to_operation_id"], e["to_etk_position"]) for e in events} == {
        ("PRINT_MARK_LABEL", *landed["PRINT_MARK_LABEL"]), ("CUT_TUT_short", *landed["CUT_TUT_short"])}
    with app.state.production_uow_factory() as uow:
        from app.modules.production.resource_assignment_infrastructure import ResourceAllocationRow as Allocation
        moved = [op.id for op in _tasks()["КБ1"].operations if op.id != first.id]
        assert not uow.session.query(Allocation).filter(Allocation.operation_id.in_(moved)).all()  # new PFs unbound


def test_release_moved_equipment_still_refuses_an_ambiguous_binding(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1", _tail_document(ZACH))]
    batch(client, cards)
    task = _tasks()["КБ1"]
    assembly = next(op for op in task.operations if [item.code for item in op.etk_ops] == ["ЗАЧ", "ЗАЧ", "ASM"])
    allocation = _bind(task, assembly, 4)
    reissue(cards[0], _tail_document([("ЗАЧ", "assembly"), *ZACH]))
    result = _cli(monkeypatch, capsys, "--apply", "--release-moved-equipment")
    assert not result["restructured"] and "ЗАЧ" in result["refused"][0]["reason"] and not result["released"]
    assert _bound(allocation) == [4]


def test_release_never_frees_an_operation_that_lands_in_a_kept_pf(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1", _tail_document([("ASM", "assembly")], cut=["ЗАЧ"]))]  # cut PF: №1 cut, №2 ЗАЧ
    batch(client, cards)
    task = _tasks()["КБ1"]
    cut = task.operations[0]
    allocation = _bind(task, cut, 2)
    moved = _tail_document([("ASM", "assembly")], cut=["ЗАЧ"])
    blocks = {block["key"]: block for block in moved["route"]["blocks"]}
    blocks["cut"]["ops"], blocks["assembly"]["ops"] = [1], [2, 5]  # ЗАЧ №2 goes to the kept assembly PF
    reissue(cards[0], moved)
    result = _cli(monkeypatch, capsys, "--apply", "--release-moved-equipment")
    assert not result["restructured"] and "ЗАЧ" in result["refused"][0]["reason"] and not result["released"]
    assert _bound(allocation) == [2]


def test_renumbering_onto_a_kept_stale_number_is_refused(production_api, monkeypatch, capsys):
    client, _ = production_api
    cards = [issue("КБ1")]
    batch(client, cards)
    task = _tasks()["КБ1"]
    assembly = task.operations[1]  # ETK №4 «ASM»; the allocation also holds a stale №5
    allocation = _bind(task, assembly, 4, 5)
    reissue(cards[0], _inserted_operation(split_document()))  # «ASM» №4 → №5
    result = _cli(monkeypatch, capsys, "--apply")
    assert not result["restructured"] and "№5" in result["refused"][0]["reason"], result["refused"]
    assert _bound(allocation) == [4, 5]


def test_release_ignores_a_same_code_operation_of_another_old_pf(production_api, monkeypatch, capsys):
    """Cut ЗАЧ №2 is dropped; assembly ЗАЧ №6 moves to a new PF split out of the assembly — not the bound one."""
    client, _ = production_api
    cards = [issue("КБ1", _tail_document([("ASM", "assembly"), ("ЗАЧ", "assembly")], cut=["ЗАЧ"]))]
    batch(client, cards)
    task = _tasks()["КБ1"]
    cut = task.operations[0]
    allocation = _bind(task, cut, 2)
    reissue(cards[0], _tail_document([("ASM", "assembly"), ("ЗАЧ", "check")], cut=["OTHER"]))
    result = _cli(monkeypatch, capsys, "--apply", "--release-moved-equipment")
    assert not result["restructured"] and "ЗАЧ" in result["refused"][0]["reason"] and not result["released"]
    assert _bound(allocation) == [2]
