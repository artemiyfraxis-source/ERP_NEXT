"""«Отметить напечатанные вне ERP» (owner 20.11): numbered labels printed outside the ERP count as printed."""

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from app.modules.production.cutting_infrastructure import CuttingGroupRow, CuttingMemberRow
from app.modules.production.domain import link_inputs
from app.modules.production.marking_outside import MarkingOutsidePrintRow
from app.modules.techcards.infrastructure import ProjectTechcardRow
from app.shared.persistence import AuditEventRow
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_my_readiness import _as_operator, _launch
from tests.production.test_network_execution import _op
from tests.workflows.test_marking_labels import NOW, marking  # noqa: F401

OUTSIDE = "/api/production/marking/outside"
EVENT = "production.marking_printed_outside"


def make_group(marking, *extra_keys):
    """A labels group of the fixture task (+ more tasks of the same ETK): КБ-{№:3} ×1 and АБВ ×2 per item."""
    _client, factory, _payload, task, card, _equipment, operator = marking
    route = link_inputs((_op(1, "Нарезка"), _op(2, "Маркировка")), {2: ((1, Decimal(1)),)})
    # Every КБ numbers its own items from 1 (708К.: КБ3…КБ10, №1–15 each).
    tasks = [task] + [_launch(factory, operator, key, route, item=f"591K.3.КБ{number}")
                      for number, key in enumerate(extra_keys, 2)]
    group_id = uuid4()
    with factory() as uow:
        document = uow.session.get(ProjectTechcardRow, card.id).document
        uow.session.add(CuttingGroupRow(id=group_id, number="РЗ-0023", idempotency_key="group", project_key=task.project,
            site="ЦАР", recipe_key="test", recipe_version=1, recipe={}, version=1, state="frozen", joinable=False,
            created_at=NOW))
        for position, entry in enumerate(tasks, 1):
            uow.session.add(CuttingMemberRow(id=uuid4(), group_id=group_id, task_id=entry.id,
                operation_id=entry.operations[0].id, position=position, assembly_quantity=entry.quantity,
                blank_quantity=entry.quantity, row_snapshot={"document": document, "techcard_revision": entry.techcard_revision},
                state="active"))
        uow.commit()
    return str(group_id), tasks


def body(group_id, **extra):
    return {"group_id": group_id, "version": 1, "serials": [1], "comment": "Напечатано на старом принтере", **extra}


def _events(factory):
    with factory() as uow:
        return [(row.details, row.actor_name) for row in uow.session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == EVENT))]


def _marks(factory):
    with factory() as uow:
        return uow.session.scalar(select(func.count()).select_from(MarkingOutsidePrintRow))


def test_marks_only_numbered_labels_of_the_serials_and_a_repeat_changes_nothing(marking):
    client, factory, *_ = marking
    group_id, tasks = make_group(marking, "second")
    shown = client.post(OUTSIDE, json=body(group_id, dry_run=True))
    assert shown.status_code == 200, shown.text
    assert [(row["text"], row["serial"], row["quantity"], row["state"]) for row in shown.json()["rows"]] == [
        ("КБ-001", 1, 1, "new"), ("КБ-001", 1, 1, "new")]  # the fixed «АБВ» is never marked
    assert _marks(factory) == 0 and not _events(factory)
    done = client.post(OUTSIDE, json=body(group_id))
    assert done.status_code == 200, done.text
    assert (done.json()["added"], done.json()["existing"]) == (2, 0)
    with factory() as uow:
        assert set(uow.production.marks_issued([task.id for task in tasks])) == {task.id for task in tasks}
    again = client.post(OUTSIDE, json=body(group_id))
    assert again.status_code == 200, again.text
    assert (again.json()["added"], again.json()["existing"]) == (0, 2)
    assert _marks(factory) == 2
    [(details, actor)] = _events(factory)
    assert details == {"change": "mark", "group_number": "РЗ-0023", "serials": [1], "items": sorted(task.item for task in tasks),
                             "labels": 2, "comment": "Напечатано на старом принтере"}
    assert actor == "Мастер"
    listed = client.get(OUTSIDE, params={"group_id": group_id}).json()["rows"]
    assert [(row["text"], row["serial"], row["actor"]) for row in listed] == [("КБ-001", 1, "Мастер")] * 2


def test_refuses_serials_outside_the_range_and_more_pieces_than_the_etk(marking):
    client, factory, _payload, task, *_ = marking
    group_id, _tasks = make_group(marking)
    beyond = client.post(OUTSIDE, json=body(group_id, serials=[1, task.quantity + 1]))
    assert beyond.status_code == 422 and f"номер {task.quantity + 1} вне номеров изделий 1–{task.quantity}" in beyond.json()["detail"]
    assert client.post(OUTSIDE, json=body(group_id, serials=[0])).status_code == 422
    assert client.post(OUTSIDE, json=body(group_id, serials=[])).status_code == 422
    over = client.post(OUTSIDE, json=body(group_id, quantity=2))
    assert over.status_code == 422 and "в ЭТК 1 шт., отметить 2 нельзя" in over.json()["detail"], over.text
    assert client.post(OUTSIDE, json=body(group_id, quantity=1)).status_code == 200
    assert _marks(factory) == 1


def test_only_the_chosen_kb_of_the_group_and_a_stale_group_is_refused(marking):
    client, factory, *_ = marking
    group_id, tasks = make_group(marking, "second")
    stranger = client.post(OUTSIDE, json=body(group_id, task_ids=[str(uuid4())]))
    assert stranger.status_code == 422 and "не входит в группу" in stranger.json()["detail"]
    assert client.post(OUTSIDE, json=body(group_id, version=2)).status_code == 409
    one = client.post(OUTSIDE, json=body(group_id, task_ids=[str(tasks[1].id)], serials=[2, 3]))
    assert one.status_code == 200, one.text
    assert {(row["task_id"], row["serial"]) for row in one.json()["rows"]} == {(str(tasks[1].id), 2), (str(tasks[1].id), 3)}
    assert _marks(factory) == 2


def test_undo_takes_the_marks_back_and_is_journaled(marking):
    client, factory, *_ = marking
    group_id, _tasks = make_group(marking)
    assert client.post(OUTSIDE, json=body(group_id, serials=[1, 2])).status_code == 200
    removed = client.post(OUTSIDE + "/undo", json=body(group_id, serials=[2], comment="Ошибся номером"))
    assert removed.status_code == 200, removed.text
    assert removed.json()["removed"] == 1 and removed.json()["rows"][0]["text"] == "КБ-002"
    assert _marks(factory) == 1
    nothing = client.post(OUTSIDE + "/undo", json=body(group_id, serials=[2]))
    assert nothing.status_code == 200 and nothing.json()["removed"] == 0
    assert sorted((details["change"], details["labels"], details["comment"]) for details, _actor in _events(factory)) == [
        ("mark", 2, "Напечатано на старом принтере"), ("undo", 1, "Ошибся номером")]


def test_marking_needs_the_print_right(marking):
    client, _factory, _payload, task, *_ = marking
    group_id, _tasks = make_group(marking)
    _as_operator(task.operations[0].operator_ids[0])
    assert client.post(OUTSIDE, json=body(group_id)).status_code == 403
    assert client.post(OUTSIDE + "/undo", json=body(group_id)).status_code == 403
