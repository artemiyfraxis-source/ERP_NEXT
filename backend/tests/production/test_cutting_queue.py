"""«Очередь резки» по станку (owner 20.11): open cable cuts of all projects, ordered by setup, «Готово» posts output."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.cutting_infrastructure import CuttingGroupRow
from app.modules.production.infrastructure import ProductionOutputRow
from app.modules.techcards.project_cards import ProjectTechcard
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_cutting_groups import NOW, batch, document, groups

URL = "/api/production/cutting-queue"


@pytest.fixture(autouse=True)
def _no_stale_references():
    """Tests that seed БД.ОБ set the references factory on the shared app; none must leak into the next test."""
    if hasattr(app.state, "references_uow_factory"):
        del app.state.references_uow_factory
    yield
    if hasattr(app.state, "references_uow_factory"):
        del app.state.references_uow_factory


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
        {"machine_id": "ОБ.095", "machine_name": "Станок ОБ.095", "rows": 1, "left": "10", "projects": 1},
        {"machine_id": "ОБ.1", "machine_name": "", "rows": 1, "left": "10", "projects": 1}]


def test_card_without_machine_gets_its_own_bucket_and_several_machines_prefer_bzw(production_api):
    client, _ = production_api
    doc = document()
    doc["operations"][0]["card"]["tooling"] = [{"section": "Инструмент", "name": "Линейка"}]
    several = on_machine(document(), "ОБ.094")
    several["operations"][0]["card"]["tooling"].append({"section": "Оборудование", "inventory_number": "ОБ.095",
                                                         "name": "BZW-50X"})
    batch(client, [issue("708К.", "КБ1", standalone(doc)), issue("709К.", "КБ1", standalone(several))])
    bzw = queue(client, "ОБ.095")["rows"]
    assert [(row["project"], row["warnings"]) for row in bzw] == [("709К.", ["в ЭТК ещё станки: ОБ.094"])]
    unknown = queue(client, "none")
    assert unknown["machine_id"] is None
    assert [(row["project"], row["machine_id"], row["warnings"]) for row in unknown["rows"]] == [
        ("708К.", None, ["нет в ЭТК: станок"])]
    machines = client.get(f"{URL}/machines").json()["machines"]
    assert [(item["machine_id"], item["machine_name"], item["rows"]) for item in machines] == [
        ("ОБ.095", "BZW-50X", 1), (None, "Станок не указан", 1)]


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
    # The acting master is not a fitter: joining the PF is refused by the ordinary assignment rule, nothing is posted.
    master = done(client, row, 1)
    assert master.status_code == 422 and "не монтажник" in master.json()["detail"]
    too_many = done(client, row, 11, operator=operator)
    assert too_many.status_code == 422
    assert "осталось 10" in too_many.json()["detail"]
    response = done(client, row, 10, operator=operator)
    assert response.status_code == 200, response.text
    assert response.json()["notice"] == "Готово: 708К. · КБ1 · 10 шт"
    assert queue(client)["rows"] == []


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
    assert "карточку группы" in refused.json()["detail"]
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


POOL = "ОБ.100.X"


def seed_units():
    """БД.ОБ: three BZW-D2.0S (ЦАР, ЦАР, ЖУК), another model ОБ.101.1, and the pool row «ОБ.100.X» (R)."""
    from app.modules.references.domain import TechOperationRecord, TechOperationTab
    from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork

    sessions = app.state.techcards_uow_factory().session_factory
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    ids = {}
    with app.state.references_uow_factory() as references:
        for number, name, article, site in (("ОБ.100.1", "Автомат", "BZW-D2.0S", "ЦАР"), ("ОБ.100.2", "Автомат", "BZW-D2.0S", "ЦАР"),
                                            ("ОБ.100.3", "Автомат", "BZW-D2.0S", "ЖУК"), ("ОБ.101.1", "Другой", "XYZ-1", "ЦАР"),
                                            (POOL, "Автомат", "BZW-D2.0S(R)", "")):
            record = TechOperationRecord.create(tab=TechOperationTab.EQUIPMENT, display_text=number, search_text=number,
                source_sheet="БД.ОБ", sort_key=number, fields={"Тип": "Оборудование", "Инв. номер": number, "Наименование": name,
                                                              "Артикул": article, "Расположение": site})
            references.tech_operations.save(record)
            ids[number] = record.id
        references.commit()
    return ids


def pooled(doc, inventory=POOL):
    doc["operations"][0]["card"]["tooling"] = [{"section": "Оборудование", "inventory_number": inventory,
                                                "name": "BZW-D2.0S(R) | автомат"}]
    return doc


def test_pool_row_is_keyed_by_the_model_and_labelled_any_free(production_api):
    client, _ = production_api
    ids = seed_units()
    batch(client, [issue("708К.", "КБ1", standalone(pooled(document()))),
                   issue("709К.", "КБ1", standalone(on_machine(document(), "ОБ.100.2")))])
    machines = client.get(f"{URL}/machines").json()["machines"]
    pool = next(item for item in machines if item["machine_name"].endswith("любой свободный (3)"))
    assert pool["machine_name"] == "ОБ.100 BZW-D2.0S · любой свободный (3)" and pool["rows"] == 1
    assert pool["machine_id"] != POOL
    assert queue(client, pool["machine_id"])["rows"][0]["project"] == "708К."
    # A single-unit row keeps its inventory number as the key.
    assert [row["project"] for row in queue(client, "ОБ.100.2")["rows"]] == ["709К."]
    # The unit filter: its own rows plus its model's pool rows, nothing of other units.
    response = client.get(URL, params={"equipment_id": str(ids["ОБ.100.2"])})
    assert response.status_code == 200, response.text
    assert sorted(row["project"] for row in response.json()["rows"]) == ["708К.", "709К."]
    other = client.get(URL, params={"equipment_id": str(ids["ОБ.100.1"])}).json()["rows"]
    assert [row["project"] for row in other] == ["708К."]
    assert client.get(URL, params={"equipment_id": str(ids[POOL])}).status_code == 422
    assert client.get(URL).status_code == 422


def test_done_with_a_unit_checks_model_and_site_and_stamps_the_receipt(production_api):
    client, operator = production_api
    ids = seed_units()
    batch(client, [issue("708К.", "КБ1", standalone(pooled(document())))])
    row = next(item for item in client.get(URL, params={"equipment_id": str(ids["ОБ.100.1"])}).json()["rows"])

    def post(unit, key, quantity=4):
        return client.post(f"{URL}/done", params={"on_behalf_of": str(operator)}, headers={"Idempotency-Key": key},
                           json={"task_id": row["task_id"], "operation_id": row["operation_id"], "quantity": quantity,
                                 "equipment_id": str(unit)})

    wrong_model = post(ids["ОБ.101.1"], "k-model")
    assert wrong_model.status_code == 422 and "другой модели" in wrong_model.json()["detail"]
    assert post(ids[POOL], "k-calc").status_code == 422
    with app.state.production_uow_factory() as uow:
        assert uow.session.scalars(select(ProductionOutputRow)).all() == []
    first = post(ids["ОБ.100.1"], "k-ok")
    assert first.status_code == 200, first.text
    # The PF now carries its ЦАР operator, so the site is known: a ЖУК unit is refused.
    wrong_site = post(ids["ОБ.100.3"], "k-site", 2)
    assert wrong_site.status_code == 422 and "площадки" in wrong_site.json()["detail"]
    assert post(ids["ОБ.100.1"], "k-ok").json() == first.json()  # idempotent replay
    from app.shared.persistence import AuditEventRow

    from app.modules.production.cutting_queue import receipt_id
    with app.state.production_uow_factory() as uow:
        details = uow.session.get(AuditEventRow, receipt_id("k-ok")).details
        assert (details["equipment_id"], details["inventory_number"]) == (str(ids["ОБ.100.1"]), "ОБ.100.1")
        assert len(uow.session.scalars(select(ProductionOutputRow)).all()) == 1


def test_single_unit_model_is_not_a_pool_and_bzw_default_wins_next_to_a_pool(production_api):
    client, _ = production_api
    seed_units()
    several = pooled(document())
    several["operations"][0]["card"]["tooling"].append({"section": "Оборудование", "inventory_number": "ОБ.095", "name": "BZW-50X"})
    one = on_machine(document(), "ОБ.101.X")
    batch(client, [issue("708К.", "КБ1", standalone(several)), issue("709К.", "КБ1", standalone(one))])
    bzw = queue(client, "ОБ.095")["rows"]
    assert [row["project"] for row in bzw] == ["708К."]
    assert bzw[0]["warnings"] == ["в ЭТК ещё станки: ОБ.100 BZW-D2.0S · любой свободный (3)"]
    # ОБ.101 has one physical unit: the row is that unit, not «любой свободный (1)».
    assert [row["project"] for row in queue(client, "ОБ.101.1")["rows"]] == ["709К."]
    machines = client.get(f"{URL}/machines").json()["machines"]
    assert [item["machine_id"] for item in machines] == ["ОБ.095", "ОБ.101.1"]


def test_row_with_only_a_model_id_is_a_pool_and_pools_sort_by_code(production_api):
    client, _ = production_api
    ids = seed_units()
    with app.state.references_uow_factory() as references:
        model = str(references.tech_operations.get(ids["ОБ.100.1"]).equipment_model_id)
    doc = document()
    doc["operations"][0]["card"]["tooling"] = [{"section": "Оборудование", "equipment_model_id": model, "name": "Автомат"}]
    batch(client, [issue("708К.", "КБ1", standalone(doc)), issue("709К.", "КБ1", standalone(on_machine(document(), "ОБ.1")))])
    machines = client.get(f"{URL}/machines").json()["machines"]
    assert [item["machine_id"] for item in machines] == ["ОБ.1", model]
    assert machines[1]["machine_name"] == "ОБ.100 BZW-D2.0S · любой свободный (3)"


def test_done_with_a_unit_on_a_grouped_row(production_api):
    client, operator = production_api
    ids = seed_units()
    batch(client, [issue("708К.", "КБ1", pooled(document())), issue("708К.", "КБ2", pooled(document(length=1500)))])
    row = next(item for item in client.get(URL, params={"equipment_id": str(ids["ОБ.100.2"])}).json()["rows"] if item["item"] == "КБ2")
    assert row["group_id"]
    body = {"task_id": row["task_id"], "operation_id": row["operation_id"], "quantity": 3}
    refused = client.post(f"{URL}/done", params={"on_behalf_of": str(operator)}, headers={"Idempotency-Key": "g-1"},
                          json={**body, "equipment_id": str(ids["ОБ.101.1"])})
    assert refused.status_code == 422 and "другой модели" in refused.json()["detail"]
    ok = client.post(f"{URL}/done", params={"on_behalf_of": str(operator)}, headers={"Idempotency-Key": "g-2"},
                     json={**body, "equipment_id": str(ids["ОБ.100.2"])})
    assert ok.status_code == 200, ok.text
    from app.modules.production.cutting_queue import receipt_id
    from app.shared.persistence import AuditEventRow
    with app.state.production_uow_factory() as uow:
        assert uow.session.get(AuditEventRow, receipt_id("g-2")).details["inventory_number"] == "ОБ.100.2"


def test_first_done_is_refused_when_the_worker_is_on_another_site_than_the_unit(production_api):
    from app.modules.identity.infrastructure import IdentityUserRow

    client, operator = production_api
    ids = seed_units()
    batch(client, [issue("708К.", "КБ1", standalone(pooled(document())))])
    row = queue(client, next(item["machine_id"] for item in client.get(f"{URL}/machines").json()["machines"]))["rows"][0]
    with app.state.production_uow_factory() as uow:
        for person in (operator, UUID("10000000-0000-0000-0000-000000000001")):  # the worker and the acting master
            uow.session.get(IdentityUserRow, person).site = "ЖУК"
        uow.commit()
    body = {"task_id": row["task_id"], "operation_id": row["operation_id"], "quantity": 2}
    params = {"on_behalf_of": str(operator)}
    refused = client.post(f"{URL}/done", params=params, headers={"Idempotency-Key": "s-1"},
                          json={**body, "equipment_id": str(ids["ОБ.100.1"])})
    assert refused.status_code == 422 and "площадки" in refused.json()["detail"]
    # A unit with an unknown site is allowed.
    with app.state.references_uow_factory() as references:
        from dataclasses import replace
        record = references.tech_operations.get(ids["ОБ.100.1"])
        references.tech_operations.save(replace(record, fields={**record.fields, "Расположение": ""}))
        references.commit()
    ok = client.post(f"{URL}/done", params=params, headers={"Idempotency-Key": "s-2"},
                     json={**body, "equipment_id": str(ids["ОБ.100.1"])})
    assert ok.status_code == 200, ok.text


def test_without_the_reference_catalog_pool_rows_keep_the_raw_key_and_a_unit_gets_503(production_api):
    client, operator = production_api
    batch(client, [issue("708К.", "КБ1", standalone(pooled(document())))])
    assert [row["project"] for row in queue(client, POOL)["rows"]] == ["708К."]
    unit = str(uuid4())
    response = client.get(URL, params={"equipment_id": unit})
    assert response.status_code == 503 and "Справочник" in response.json()["detail"]
    row = queue(client, POOL)["rows"][0]
    posted = client.post(f"{URL}/done", params={"on_behalf_of": str(operator)}, headers={"Idempotency-Key": "e-1"},
                         json={"task_id": row["task_id"], "operation_id": row["operation_id"], "quantity": 1, "equipment_id": unit})
    assert posted.status_code == 503
