"""«Очередь резки» по станку (owner 20.11): open cable cuts of all projects, ordered by setup, «Готово» posts output."""

from uuid import UUID, uuid4

from sqlalchemy import select

from app.main import app
from app.modules.production.cutting_infrastructure import CuttingGroupRow
from app.modules.production.infrastructure import ProductionOutputRow
from app.modules.techcards.project_cards import ProjectTechcard
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_cutting_groups import NOW, batch, document, groups

URL = "/api/production/cutting-queue"


def issue(project, index, doc=None):
    card = ProjectTechcard.create(project=project, item_index=index, product_name=f"Сборка {index}",
        source_calculation="", deadline=None, now=NOW).rebuild(doc or document(), NOW).issue(NOW)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    return card


def with_cable(doc, cable):
    doc["build_config"]["coax"]["cableArt"] = cable
    return doc


def standalone(doc=None):
    """Empty label layout: grouping is refused, the КБ launches with an ordinary (ungrouped) cut PF."""
    doc = doc or document()
    doc["build_config"]["ops"][1]["marks"] = []
    return doc


def on_machine(doc, inventory):
    doc["operations"][0]["card"]["tooling"] = [{"section": "Оборудование", "inventory_number": inventory,
                                                "name": f"Станок {inventory}"}]
    return doc


def queue(client, machine="ОБ.1"):
    response = client.get(URL, params={"machine_id": machine})
    assert response.status_code == 200, response.text
    return response.json()


def done(client, row, quantity, *, key=None, operator=None):
    params = {"on_behalf_of": str(operator)} if operator else {}
    return client.post(f"{URL}/done", params=params, headers={"Idempotency-Key": key or str(uuid4())},
                       json={"task_id": row["task_id"], "operation_id": row["operation_id"], "quantity": quantity})


def test_rows_of_all_projects_ordered_by_wire_then_length_with_setup_banner(production_api):
    client, _ = production_api
    batch(client, [issue("708К.", "КБ1", document(length=2000)), issue("709К.", "КБ1", document(length=1000)),
                   issue("708К.", "КБ2", with_cable(document(length=500), "RG 316"))])
    rows = queue(client)["rows"]
    assert [(row["project"], row["item"], row["wire"], row["length_mm"]) for row in rows] == [
        ("708К.", "КБ2", "RG 316", "500"), ("709К.", "КБ1", "RTK 031", "1000"), ("708К.", "КБ1", "RTK 031", "2000")]
    # Same wire across projects: no setup change between them; a new wire starts a setup.
    assert [row["setup_change"] for row in rows] == [True, True, False]
    assert rows[1]["setup_text"] == ("Заправьте RTK 031 · зачистка А: 3/7,6/12,7 мм, полная · "
                                     "зачистка Б: 3/7,6/12,7 мм, полная")
    first = rows[1]
    assert (first["strip_a"], first["strip_a_mode"], first["strip_a_program"]) == ("3/7,6/12,7", "full", "confirmed")
    assert first["diameters"] == "0,81/2,1/3,2"
    assert first["laser_mark"] is False and first["warnings"] == [] and first["blocked"] is None
    assert (first["planned"], first["done"], first["left"]) == ("10", "0", "10")
    assert first["group_number"] and first["task_number"] and first["machine_id"] == "ОБ.1"


def test_missing_etk_value_keeps_the_row_with_a_warning(production_api):
    client, _ = production_api
    doc = standalone()
    doc["build_config"]["coax"].pop("cableLength")
    no_mode = document(length=300)
    no_mode["operations"][0]["fields"]["coax"].pop("stripMode")
    result = batch(client, [issue("708К.", "КБ1", doc), issue("708К.", "КБ2"), issue("708К.", "КБ3", no_mode)])
    assert result["grouping_refused"]
    rows = queue(client)["rows"]
    assert [row["item"] for row in rows] == ["КБ3", "КБ2", "КБ1"]  # unknown length after the known ones of its setup
    missing = rows[-1]
    assert missing["group_id"] is None and missing["length_mm"] is None
    assert missing["warnings"] == ["нет в ЭТК: длина"]
    assert rows[0]["warnings"] == ["нет в ЭТК: режим зачистки А"] and rows[0]["strip_a_mode"] is None
    assert rows[0]["setup_change"] and rows[1]["setup_change"] and not rows[2]["setup_change"]


def test_machine_filter_and_machine_list(production_api):
    client, _ = production_api
    batch(client, [issue("708К.", "КБ1"), issue("708К.", "КБ2", on_machine(document(), "ОБ.095"))])
    assert [row["item"] for row in queue(client)["rows"]] == ["КБ1"]
    other = queue(client, "ОБ.095")["rows"]
    assert [(row["item"], row["machine_name"]) for row in other] == [("КБ2", "Станок ОБ.095")]
    response = client.get(f"{URL}/machines")
    assert response.status_code == 200, response.text
    assert response.json()["machines"] == [
        {"machine_id": "all", "machine_name": "Все станки", "rows": 2, "left": "20", "projects": 1},
        {"machine_id": "ОБ.095", "machine_name": "Станок ОБ.095", "rows": 1, "left": "10", "projects": 1},
        {"machine_id": "ОБ.1", "machine_name": "", "rows": 1, "left": "10", "projects": 1}]


def several_machines(doc=None):
    """708К. tooling as issued today: the BZW-886+Q2 first (the default), the BZW-50X also listed."""
    doc = on_machine(doc or document(), "ОБ.094")
    doc["operations"][0]["card"]["tooling"][0]["name"] = "BZW-886+Q2"
    doc["operations"][0]["card"]["tooling"].append({"section": "Оборудование", "inventory_number": "ОБ.095",
                                                    "name": "BZW-50X"})
    return doc


def test_card_without_machine_gets_its_own_bucket_and_a_row_is_on_every_machine_its_etk_names(production_api):
    client, _ = production_api
    doc = document()
    doc["operations"][0]["card"]["tooling"] = [{"section": "Инструмент", "name": "Линейка"}]
    batch(client, [issue("708К.", "КБ1", standalone(doc)), issue("709К.", "КБ1", standalone(several_machines()))])
    for machine, name in (("ОБ.094", "BZW-886+Q2"), ("ОБ.095", "BZW-50X")):
        rows = queue(client, machine)["rows"]
        assert [(row["project"], row["machine_id"], row["machine_name"], row["warnings"]) for row in rows] == [
            ("709К.", machine, name, [])]
        assert rows[0]["machines"] == [{"machine_id": "ОБ.094", "machine_name": "BZW-886+Q2"},
                                       {"machine_id": "ОБ.095", "machine_name": "BZW-50X"}]
    unknown = queue(client, "none")
    assert unknown["machine_id"] is None
    assert [(row["project"], row["machine_id"], row["machines"], row["warnings"]) for row in unknown["rows"]] == [
        ("708К.", None, [], ["нет в ЭТК: станок"])]
    machines = client.get(f"{URL}/machines").json()["machines"]
    assert [(item["machine_id"], item["machine_name"], item["rows"]) for item in machines] == [
        ("all", "Все станки", 2), ("ОБ.094", "BZW-886+Q2", 1), ("ОБ.095", "BZW-50X", 1), (None, "Станок не указан", 1)]


def test_default_machine_is_the_first_the_etk_names(production_api):
    client, _ = production_api
    batch(client, [issue("708К.", "КБ1", standalone(several_machines()))])
    row = queue(client, "all")["rows"][0]
    assert (row["machine_id"], row["machine_name"]) == ("ОБ.094", "BZW-886+Q2")


def test_row_under_two_machines_leaves_both_lists_after_done(production_api):
    client, operator = production_api
    batch(client, [issue("708К.", "КБ1", standalone(several_machines()))])
    row = queue(client, "ОБ.095")["rows"][0]
    assert queue(client, "ОБ.094")["rows"][0]["key"] == row["key"]
    assert done(client, row, 10, operator=operator).status_code == 200
    assert queue(client, "ОБ.094")["rows"] == queue(client, "ОБ.095")["rows"] == queue(client, "all")["rows"] == []
    assert client.get(f"{URL}/machines").json()["machines"] == []


def test_all_machines_lists_every_row_once_in_setup_order(production_api):
    client, _ = production_api
    no_machine = document(length=700)
    no_machine["operations"][0]["card"]["tooling"] = []
    batch(client, [issue("708К.", "КБ1", standalone(several_machines(document(length=2000)))),
                   issue("709К.", "КБ1", standalone(on_machine(document(length=1000), "ОБ.095"))),
                   issue("710К.", "КБ1", standalone(with_cable(document(length=500), "RG 316"))),
                   issue("711К.", "КБ1", standalone(no_machine))])
    body = queue(client, "all")
    assert body["machine_id"] == "all"
    assert [(row["project"], row["machine_id"], row["setup_change"]) for row in body["rows"]] == [
        ("710К.", "ОБ.1", True), ("711К.", None, True), ("709К.", "ОБ.095", False), ("708К.", "ОБ.094", False)]
    assert len({row["key"] for row in body["rows"]}) == 4
    total = client.get(f"{URL}/machines").json()["machines"][0]
    assert total == {"machine_id": "all", "machine_name": "Все станки", "rows": 4, "left": "40", "projects": 4}


def test_done_on_a_grouped_row_assigns_the_operator_and_posts_output_to_its_kb(production_api):
    client, operator = production_api
    batch(client, [issue("708К.", "КБ1"), issue("708К.", "КБ3", document(length=1500))])
    row = next(row for row in queue(client)["rows"] if row["item"] == "КБ3")
    assert row["operator_ids"] == []
    response = done(client, row, 4, operator=operator)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notice"] == "Готово: 708К. · КБ3 · 4 шт"
    assert body["assigned"] is True
    assert body["row"]["done"] == "4" and body["row"]["left"] == "6"
    after = next(item for item in queue(client)["rows"] if item["item"] == "КБ3")
    assert after["left"] == "6" and after["operator_ids"] == [str(operator)]
    assert after["group_state"] == "frozen"  # the first manual output freezes the group, as on the group card
    with app.state.production_uow_factory() as uow:
        outputs = uow.session.scalars(select(ProductionOutputRow)).all()
        assert [(o.task_id, o.operation_id, o.operator_id, o.good_quantity) for o in outputs] == [
            (UUID(row["task_id"]), UUID(row["operation_id"]), operator, 4)]
    # The rest of the КБ closes the row.
    assert done(client, after, 6, operator=operator).status_code == 200
    assert [item["item"] for item in queue(client)["rows"]] == ["КБ1"]


def test_done_on_a_standalone_row_posts_output_and_refuses_more_than_left(production_api):
    client, operator = production_api
    batch(client, [issue("708К.", "КБ1", standalone())])
    row = queue(client)["rows"][0]
    assert row["group_id"] is None
    too_many = done(client, row, 11, operator=operator)
    assert too_many.status_code == 422
    assert "осталось 10" in too_many.json()["detail"]
    response = done(client, row, 10, operator=operator)
    assert response.status_code == 200, response.text
    assert response.json()["notice"] == "Готово: 708К. · КБ1 · 10 шт"
    assert response.json()["assigned"] is True
    assert queue(client)["rows"] == []


def test_done_by_a_non_fitter_posts_like_manual_output_without_assignment(production_api):
    client, operator = production_api
    master = UUID("10000000-0000-0000-0000-000000000001")
    batch(client, [issue("708К.", "КБ1", standalone())])
    row = queue(client)["rows"][0]
    response = done(client, row, 4)  # the master himself, not a монтажник
    assert response.status_code == 200, response.text
    assert response.json()["assigned"] is False and response.json()["row"]["left"] == "6"
    with app.state.production_uow_factory() as uow:
        outputs = uow.session.scalars(select(ProductionOutputRow)).all()
        assert [(o.operator_id, o.good_quantity) for o in outputs] == [(master, 4)]
        task = uow.production.get(UUID(row["task_id"]))
        assert task.operation(UUID(row["operation_id"])).operator_ids == ()  # nobody was assigned
    # A монтажник pressing (or worked for) still joins the PF.
    assert done(client, row, 2, operator=operator).json()["assigned"] is True
    assert queue(client)["rows"][0]["operator_ids"] == [str(operator)]


def test_done_receipt_is_the_journal_line_of_the_press(production_api):
    from app.shared.persistence import AuditEventRow
    from app.modules.production.cutting_queue import RECEIPT

    client, operator = production_api
    batch(client, [issue("708К.", "КБ1", standalone())])
    row = queue(client)["rows"][0]
    assert done(client, row, 3, operator=operator).status_code == 200
    with app.state.production_uow_factory() as uow:
        event = uow.session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == RECEIPT)).one()
        details, actor_name = event.details, event.actor_name
    assert actor_name == "Мастер"
    assert {name: details[name] for name in ("project", "item_index", "task_number", "quantity", "performer",
                                             "pressed_by", "operator_id")} == {
        "project": "708К.", "item_index": "КБ1", "task_number": row["task_number"], "quantity": 3,
        "performer": "Оператор", "pressed_by": "Мастер", "operator_id": str(operator)}


def receipt_details():
    from app.shared.persistence import AuditEventRow
    from app.modules.production.cutting_queue import RECEIPT

    with app.state.production_uow_factory() as uow:
        return [event.details for event in uow.session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == RECEIPT))]


def test_non_fitter_done_on_a_grouped_row_posts_without_assignment_and_journals_the_presser(production_api):
    client, _ = production_api
    master = UUID("10000000-0000-0000-0000-000000000001")
    batch(client, [issue("708К.", "КБ1"), issue("708К.", "КБ2")])
    row = queue(client)["rows"][0]
    assert row["group_id"] is not None
    response = done(client, row, 3)
    assert response.status_code == 200, response.text
    assert response.json()["assigned"] is False
    after = next(item for item in queue(client)["rows"] if item["key"] == row["key"])
    assert after["group_state"] == "frozen" and after["operator_ids"] == [] and after["left"] == "7"
    with app.state.production_uow_factory() as uow:
        assert [(o.operator_id, o.good_quantity) for o in uow.session.scalars(select(ProductionOutputRow))] == [(master, 3)]
    [details] = receipt_details()
    assert (details["performer"], details["pressed_by"], details["operator_id"]) == ("Мастер", "Мастер", str(master))


def test_done_on_behalf_of_a_non_fitter_is_refused(production_api):
    from app.modules.identity.infrastructure import IdentityUserRow

    client, _ = production_api
    clerk = uuid4()
    with app.state.production_uow_factory() as uow:
        uow.session.add(IdentityUserRow(id=clerk, login="clerk", email="clerk@example.test", name="Кладовщик",
                                        department="Производство", role="staff", site="ЦАР", active=True, shop_worker=False))
        uow.commit()
    batch(client, [issue("708К.", "КБ1", standalone())])
    row = queue(client)["rows"][0]
    refused = done(client, row, 1, operator=clerk)
    assert refused.status_code == 422 and "не монтажник" in refused.json()["detail"]
    with app.state.production_uow_factory() as uow:
        assert uow.session.scalars(select(ProductionOutputRow)).all() == []
    assert receipt_details() == []


def test_done_by_an_account_without_employee_record_needs_production_edit(production_api):
    from app.shared.http_auth import Principal, authenticated_principal
    from tests.factories import Actor

    client, _ = production_api
    stranger = uuid4()
    batch(client, [issue("708К.", "КБ1", standalone())])
    row = queue(client)["rows"][0]
    previous = app.dependency_overrides[authenticated_principal]

    def as_stranger(permissions):
        app.dependency_overrides[authenticated_principal] = lambda: Principal(
            actor=Actor(id=stranger, name="Гость", department=""), email="guest@example.test",
            roles=frozenset({"staff"}), permissions=permissions)

    try:
        as_stranger({"myops": "edit"})
        refused = done(client, row, 1)
        assert refused.status_code == 403 and "нет в справочнике" in refused.json()["detail"]
        as_stranger({"myops": "edit", "production": "edit"})
        accepted = done(client, row, 1)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["assigned"] is False
    finally:
        app.dependency_overrides[authenticated_principal] = previous
    [details] = receipt_details()
    assert (details["performer"], details["pressed_by"]) == ("Гость", "Гость")


def test_repeated_key_does_not_post_twice_and_a_reused_key_is_refused(production_api):
    client, operator = production_api
    batch(client, [issue("708К.", "КБ1", standalone())])
    row = queue(client)["rows"][0]
    first = done(client, row, 3, key="press-1", operator=operator)
    again = done(client, row, 3, key="press-1", operator=operator)
    assert first.status_code == again.status_code == 200, first.text
    assert again.json() == first.json()
    assert done(client, row, 2, key="press-1", operator=operator).status_code == 409
    with app.state.production_uow_factory() as uow:
        assert len(uow.session.scalars(select(ProductionOutputRow)).all()) == 1
    assert queue(client)["rows"][0]["left"] == "7"


def test_row_of_a_group_running_on_the_group_card_is_listed_but_refused(production_api):
    client, operator = production_api
    batch(client, [issue("708К.", "КБ1"), issue("708К.", "КБ2")])
    group = groups(client)[0]
    assigned = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
                           json={"version": group["version"], "operator_ids": [str(operator)]})
    assert assigned.status_code == 200, assigned.text
    started = client.post(f"/api/production/cutting-groups/{group['id']}/start", params={"on_behalf_of": str(operator)},
                          headers={"Idempotency-Key": "start"}, json={"version": assigned.json()["group"]["version"]})
    assert started.status_code == 200, started.text
    rows = queue(client)["rows"]
    assert len(rows) == 2
    assert rows[0]["blocked"] == f"Группа {group['number']} начата на карточке группы (Оператор) — сдают там"
    refused = done(client, rows[0], 1, operator=operator)
    assert refused.status_code == 422
    assert refused.json()["detail"] == f"Группа {group['number']} начата на карточке — сдайте там"
    with app.state.production_uow_factory() as uow:
        assert not uow.session.scalars(select(ProductionOutputRow)).all()
        assert uow.session.scalars(select(CuttingGroupRow)).one().state == "frozen"


def test_done_refuses_labels_and_tubes_pfs_grouped_or_standalone(production_api):
    from tests.production.test_cutting_groups import split_document

    client, operator = production_api
    batch(client, [issue("708К.", "КБ1", split_document())])
    by_kind = {group["kind"]: group for group in groups(client)}
    assert [row["operation_id"] for row in queue(client)["rows"]] == [by_kind["cut"]["rows"][0]["operation_id"]]
    labels = by_kind["labels"]["rows"][0]
    refused = done(client, labels, 1, operator=operator)
    assert refused.status_code == 422 and "только резка кабеля" in refused.json()["detail"]
    tubes = by_kind["tubes"]
    split = client.post(f"/api/production/cutting-groups/{tubes['id']}/split",
                        json={"version": tubes["version"], "member_ids": [tubes["rows"][0]["id"]]})
    assert split.status_code == 200, split.text
    refused = done(client, tubes["rows"][0], 1, operator=operator)  # now a standalone tubes PF
    assert refused.status_code == 422 and "только резка кабеля" in refused.json()["detail"]
    with app.state.production_uow_factory() as uow:
        assert not uow.session.scalars(select(ProductionOutputRow)).all()
        operations = [op for task in uow.production.list() for op in task.operations]
        assert all(op.operator_ids == () for op in operations)  # nobody was self-assigned


def test_running_cut_without_readable_etk_is_kept_unconfirmed(production_api):
    from app.modules.production.api import techcards_uow_factory

    client, operator = production_api
    batch(client, [issue("708К.", "КБ1", standalone())])
    app.dependency_overrides[techcards_uow_factory] = lambda: None
    try:
        unknown = queue(client, "none")["rows"]
        assert [(row["machine_id"], row["warnings"], row["length_mm"]) for row in unknown] == [
            (None, ["нет в ЭТК: выданная карта"], None)]
        assert unknown[0]["blocked"].startswith("Нет ЭТК задания")
        assert queue(client)["rows"] == []
        refused = done(client, unknown[0], 1, operator=operator)
        assert refused.status_code == 422 and refused.json()["detail"] == unknown[0]["blocked"]
    finally:
        app.dependency_overrides.pop(techcards_uow_factory, None)
    assert len(queue(client)["rows"]) == 1


def test_finished_frozen_groups_are_not_checked_or_read(production_api, monkeypatch):
    from app.modules.production import cutting_queue

    client, operator = production_api
    batch(client, [issue("708К.", "КБ1"), issue("708К.", "КБ2")])
    finished = groups(client)[0]
    for row in queue(client)["rows"]:
        assert done(client, row, 10, operator=operator).status_code == 200
    batch(client, [issue("709К.", "КБ1")])
    visited = []
    original = cutting_queue.check_access
    monkeypatch.setattr(cutting_queue, "check_access",
                        lambda group, uow, actor: (visited.append(str(group.id)), original(group, uow, actor)))
    rows = queue(client)["rows"]
    assert [row["project"] for row in rows] == ["709К."]
    assert finished["id"] not in visited and visited == [rows[0]["group_id"]]


def test_done_is_refused_while_the_pf_has_an_open_work_session(production_api):
    from datetime import timedelta

    from app.modules.production.infrastructure import ProductionWorkSessionRow

    client, operator = production_api
    batch(client, [issue("708К.", "КБ1", standalone())])
    row = queue(client)["rows"][0]
    with app.state.production_uow_factory() as uow:
        uow.session.add(ProductionWorkSessionRow(id=uuid4(), task_id=UUID(row["task_id"]), operation_id=UUID(row["operation_id"]),
                                                 operator_id=operator, started_at=NOW - timedelta(minutes=5), status="В работе",
                                                 pause_started_at=None, pause_seconds=0))
        uow.commit()
    refused = done(client, row, 1, operator=operator)
    assert refused.status_code == 422 and "идёт работа на карточке" in refused.json()["detail"]
    with app.state.production_uow_factory() as uow:
        assert uow.session.scalars(select(ProductionOutputRow)).all() == []
