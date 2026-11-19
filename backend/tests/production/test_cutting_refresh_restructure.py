"""«Обновить из ЭТК» over HTTP when the reissued ETK changed the PF composition (owner 20.11): the same in-place
rebuild as the CLI restructure, one lock order (project → groups → tasks), printed labels kept, tube rows."""

import json
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.production import cutting_infrastructure, infrastructure
from app.modules.production.cutting_application import row_tubes
from app.modules.production.infrastructure import ProductionSerialRow
from app.modules.warehouse.devices import PrinterRow
from app.modules.warehouse.label_jobs import LabelJobRow
from app.modules.warehouse.marking_profiles import MarkingBatchRow, MarkingProfileRow
from app.modules.warehouse.marking_render import presets
from app.modules.warehouse.scan_agents import ScanAgentRow
from tests.production.test_api import production_api, MASTER_ID  # noqa: F401
from tests.production.test_cutting_group_refresh_etk import pinned, refresh, reissue
from tests.production.test_cutting_groups import NOW, batch, document, groups, issue, split_document
from tests.production.test_cutting_groups_existing import set_share


def marked(doc):
    """A numbered production label (one per item) in the ETK's label operation; it does not change any recipe."""
    doc["operations"][1]["fields"]["marks"] = [{"text": "FRX.{№}", "qty": "1"}]
    return doc


def _tasks():
    with app.state.production_uow_factory() as uow:
        return {task.item: task for task in uow.production.for_project("708К.")}


def _serials(task_id):
    with app.state.production_uow_factory() as uow:
        return uow.session.scalar(select(func.count()).select_from(ProductionSerialRow).where(
            ProductionSerialRow.task_id == task_id))


def _print_first_label(task):
    """The 708К. fact: the label of item №1 of the KB is already printed (its job went to the printer queue)."""
    profile, batch_id = uuid4(), uuid4()
    with app.state.production_uow_factory() as uow:
        uow.session.add_all([
            MarkingProfileRow(id=profile, equipment_id=None, inventory_number="ОБ.101", name="Флажок", version=1,
                              calibrated=True, geometry=presets()[-1][2]),
            ScanAgentRow(machine_id="godex-pc", pc_name="Цех", version="2.11.0", first_seen=NOW, last_seen=NOW),
            PrinterRow(machine_id="godex-pc", name="Godex EZ2350i", driver="Godex", port="USB001", kind="gdi_png",
                       present=True, last_seen=NOW),
        ])
        uow.session.flush()
        uow.session.add(MarkingBatchRow(id=batch_id, created_by=MASTER_ID, created_at=NOW, idempotency_key="printed",
            fingerprint="f" * 64, profile_id=profile, total=1, reason="",
            manifest={"labels": [{"task_id": str(task.id), "serial": 1, "text": "FRX.1", "quantity": 1}]}))
        uow.session.flush()
        uow.session.add(LabelJobRow(id=uuid4(), created_at=NOW, created_by="Мастер", kind="marking", copies=1,
            tspl=b"", status="pending", format="gdi_png", png=b"png", target_machine_id="godex-pc",
            target_printer="Godex EZ2350i", width_mm=30, height_mm=20, orientation="landscape",
            batch_id=batch_id, sequence=1))
        uow.commit()


def _progress(client, task):
    response = client.get("/api/production/marking/progress",
                          params={"task_id": str(task.id), "operation_id": str(task.operations[0].id)})
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    return row["printed"], row["remaining"], row["printed_serials"]


def _open(client):
    return [g for g in groups(client) if g["state"] != "cancelled"]


def test_http_refresh_rebuilds_changed_pf_composition_in_place_and_keeps_printed_label(production_api):
    client, operator = production_api
    cards = [issue("КБ1", marked(document())), issue("КБ2", marked(document(length=1200)))]
    batch(client, cards)
    group = groups(client)[0]
    assigned = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
                           json={"version": group["version"], "operator_ids": [str(operator)]})
    assert assigned.status_code == 200, assigned.text
    group = assigned.json()["group"]
    before = _tasks()
    _print_first_label(before["КБ1"])
    assert _progress(client, before["КБ1"]) == (1, 9, [1])
    serials = {item: _serials(task.id) for item, task in before.items()}
    for card, length in zip(cards, (1000, 1200)):
        reissue(card, marked(split_document(length=length)))
    assert groups(client)[0]["etk_stale"] is True

    response = refresh(client, group)
    assert response.status_code == 200, response.text
    fresh = response.json()["group"]
    assert (fresh["id"], fresh["number"], fresh["kind"]) == (group["id"], group["number"], "cut")
    assert fresh["version"] > group["version"]
    assert sorted((row["item"], row["membership_state"]) for row in fresh["rows"]) == [("КБ1", "active"), ("КБ2", "active")]

    after = _tasks()
    for item, task in after.items():
        old = before[item]
        assert (task.id, task.number, task.quantity, task.techcard_revision) == (old.id, old.number, old.quantity, 2)
        assert len(task.operations) == 4 and task.operations[0].id == old.operations[0].id
        assert task.operations[0].operator_ids == (operator,)
        assert _serials(task.id) == serials[item]
    assert _progress(client, after["КБ1"]) == (1, 9, [1])  # printed = 1, remaining = quantity − 1
    assert _progress(client, after["КБ2"])[:2] == (0, 10)

    current = _open(client)
    assert sorted((g["kind"], len(g["rows"])) for g in current) == [("cut", 2), ("labels", 2), ("tubes", 2)]
    assert all(g["etk_stale"] is False for g in current)
    tubes = next(g for g in current if g["kind"] == "tubes")
    assert [row["tubes"] for row in tubes["rows"]] == [[{"material": "ТУТ 4/2", "length_mm": "60", "quantity": "30"}]] * 2
    assert all(row["tubes"] == [] for g in current if g["kind"] != "tubes" for row in g["rows"])

    again = refresh(client, fresh)
    assert again.status_code == 200, again.text
    assert again.json()["group"]["version"] == fresh["version"]
    assert [g["version"] for g in _open(client)] == [g["version"] for g in current]


def test_http_refresh_composition_change_is_refused_once_work_exists_and_writes_nothing(production_api):
    client, operator = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    group = groups(client)[0]
    set_share(_tasks()["КБ2"], operator, started=True)
    group = groups(client)[0]
    for card in cards:
        reissue(card, split_document())
    response = refresh(client, group)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "КБ2" in detail and "работа" in detail
    assert pinned() == [("КБ1", 1), ("КБ2", 1)]
    assert {item: len(task.operations) for item, task in _tasks().items()} == {"КБ1": 2, "КБ2": 2}
    assert [(g["id"], g["version"]) for g in groups(client)] == [(group["id"], group["version"])]


def test_cli_refresh_etk_rebuilds_changed_composition(production_api, monkeypatch, capsys):
    from app import database
    from app.workflows.cutting_group_existing import main

    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    for card in cards:
        reissue(card, split_document())
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr(database, "build_engine", lambda url: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(database, "build_production_uow_factory", lambda engine: app.state.production_uow_factory)
    monkeypatch.setattr(database, "build_techcards_uow_factory", lambda engine: app.state.techcards_uow_factory)
    assert main(["--project", "708К.", "--refresh-etk"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert sorted(preview["refreshed"][0]["items"]) == ["КБ1", "КБ2"] and not preview["refused"]
    assert pinned() == [("КБ1", 1), ("КБ2", 1)]
    assert main(["--project", "708К.", "--refresh-etk", "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["refreshed"] and not applied["refused"]
    assert all(len(task.operations) == 4 and task.techcard_revision == 2 for task in _tasks().values())
    assert sorted(g["kind"] for g in _open(client)) == ["cut", "labels", "tubes"]


@pytest.mark.parametrize("change", ["regroup", "rebuild"])
def test_refresh_locks_project_then_groups_then_tasks_by_id(production_api, monkeypatch, change):
    """Lock order of every path: project → groups (by id) → tasks (by id). Recorded on SQLite by a spy; the
    PostgreSQL row/advisory locks themselves are not exercised here."""
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2"), issue("КБ3", document(strip=4))]
    batch(client, cards)
    first = next(g for g in groups(client) if len(g["rows"]) == 2)
    if change == "regroup":
        reissue(cards[1], document(strip=4, length=1500))
    else:
        for card in cards[:2]:
            reissue(card, split_document())
    with app.state.production_uow_factory() as uow:
        all_groups = sorted((g.id for g in uow.cutting.list()), key=str)
        members = sorted({m.task_id for m in uow.cutting.members(UUID(first["id"]), active=False)}, key=str)

    seen = []
    original_project, original_get, original_lock = (infrastructure.lock_project_run,
        cutting_infrastructure.SqlAlchemyCuttingRepository.get, infrastructure.SqlAlchemyProductionRepository.lock)

    def project_lock(session, project):
        seen.append(("project", project))
        return original_project(session, project)

    def group_get(self, group_id, *, lock=False):
        if lock:
            seen.append(("group", group_id))
        return original_get(self, group_id, lock=lock)

    def task_lock(self, task_id):
        seen.append(("task", task_id))
        return original_lock(self, task_id)

    monkeypatch.setattr(infrastructure, "lock_project_run", project_lock)
    monkeypatch.setattr(cutting_infrastructure.SqlAlchemyCuttingRepository, "get", group_get)
    monkeypatch.setattr(infrastructure.SqlAlchemyProductionRepository, "lock", task_lock)
    response = refresh(client, first)
    assert response.status_code == 200, response.text

    firsts = list(dict.fromkeys(seen))  # first acquisition; later calls re-enter locks already held
    kinds = [kind for kind, _value in firsts]
    assert kinds[0] == "project" and len({value for kind, value in firsts if kind == "project"}) == 1
    group_ids = [value for kind, value in firsts if kind == "group"]
    task_ids = [value for kind, value in firsts if kind == "task"]
    assert max(i for i, kind in enumerate(kinds) if kind == "group") < min(i for i, kind in enumerate(kinds) if kind == "task")
    assert group_ids[:len(all_groups)] == all_groups and task_ids == members


def test_row_tubes_for_protective_and_plain_tubes():
    def recipe(**op):
        return {"kind": "tubes", "cables_per_assembly": "2", "operations": [op]}

    protective = recipe(parameters={"units": "3", "tube": {"article": "ТУТ 4/2", "lengths": {"A": "35", "B": "35",
                                                                                            "middle": "55"}}},
                        fields={"coaxProtectiveTubes": [{"place": "ends", "length": "35", "units": "4"},
                                                        {"place": "middle", "length": "55", "units": "2"}]})
    assert row_tubes(protective, 10) == [{"material": "ТУТ 4/2", "length_mm": "35", "quantity": "40"},
                                         {"material": "ТУТ 4/2", "length_mm": "55", "quantity": "20"}]
    older = recipe(parameters={"tube": {"article": "ТУТ 4/2", "lengths": {"A": "35", "B": "35", "middle": "55"},
                                        "marks": [{"place": "A"}, {"place": "B"}, {"place": "middle", "scope": "assembly"}]}},
                   fields={})
    assert row_tubes(older, 5) == [{"material": "ТУТ 4/2", "length_mm": "35", "quantity": "20"},
                                   {"material": "ТУТ 4/2", "length_mm": "55", "quantity": "5"}]
    plain = recipe(parameters={"units": "3", "tube": {"article": "ТУТ 4/2", "length": "60"}}, fields={})
    assert row_tubes(plain, 10) == [{"material": "ТУТ 4/2", "length_mm": "60", "quantity": "30"}]
    broken = recipe(parameters={"tube": {"article": "ТУТ 4/2", "length": ""}}, fields={})
    assert row_tubes(broken, 10) == []
    assert row_tubes({**plain, "kind": "labels"}, 10) == [] and row_tubes({"operations": plain["operations"]}, 10) == []
