"""«Свой текст» of the marking printer card (owner 20.11.2026): lines × copies, order, {№} numbering, the same queue."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.sql.selectable import Select

from app.main import app
from app.modules.production.marking_sources import manual_labels
from app.modules.references.infrastructure import EquipmentMachineRow
from app.modules.warehouse.marking_profiles import MarkingProfileRow
from app.modules.warehouse.marking_render import presets
from app.modules.warehouse.label_jobs import LabelJobRow, claim_marking, report_marking
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.production.test_api import production_api, issue_card  # noqa: F401
from tests.production.test_my_readiness import _as_operator
from tests.workflows.test_marking_labels import BASE, NOW, marking  # noqa: F401


def body(marking, lines, **extra):
    payload = marking[2]
    return {"profile_id": payload["profile_id"], "profile_version": payload["profile_version"], "lines": lines, **extra}


def manual_print(marking, request, key="manual"):
    client = marking[0]
    shown = client.post(BASE + "/manual/preview", json=request)
    assert shown.status_code == 200, shown.text
    response = client.post(BASE + "/manual/batches", json={**request, "manifest_hash": shown.json()["manifest_hash"],
                                                            "idempotency_key": key})
    assert response.status_code == 200, response.text
    return response.json(), shown.json()


def test_lines_copies_numbering_and_order_expand_purely():
    rows = manual_labels([("X10", 1), ("X2", 2), ("A{№}", 1)], numbering=(9, 10))
    assert [(row["text"], row["quantity"]) for row in rows] == [("X10", 1), ("X2", 2), ("A9", 1), ("A10", 1)]
    assert all(row["serial"] is None for row in rows)
    ordered = manual_labels([("X10", 1), ("X2", 2), ("A{№:2}", 1)], order="number", numbering=(9, 10))
    assert [row["text"] for row in ordered] == ["A09", "A10", "X2", "X10"]
    with pytest.raises(ValueError, match="Пустая строка №2"):
        manual_labels([("X1", 1), ("   ", 1)])
    with pytest.raises(ValueError, match="укажите номера"):
        manual_labels([("A{№}", 1)])
    with pytest.raises(ValueError, match="не более 500"):
        manual_labels([("A", 300), ("B", 201)])


def test_manual_batch_prints_lines_times_copies_through_the_marking_queue(marking):
    client, factory, _payload, task, *_ = marking
    request = body(marking, [{"text": "ПИТАНИЕ +24В", "quantity": 2}, {"text": "X1-{№}", "quantity": 1}],
                   numbering={"first": 1, "last": 3})
    batch, shown = manual_print(marking, request)
    assert [(row["text"], row["quantity"]) for row in shown["labels"]] == [
        ("ПИТАНИЕ +24В", 2), ("X1-1", 1), ("X1-2", 1), ("X1-3", 1)]
    assert shown["total"] == batch["total"] == 5
    assert [job["text"] for job in batch["jobs"]] == ["ПИТАНИЕ +24В", "ПИТАНИЕ +24В", "X1-1", "X1-2", "X1-3"]
    # The same key answers the same batch; a changed request under it is refused.
    again = client.post(BASE + "/manual/batches", json={**request, "manifest_hash": shown["manifest_hash"], "idempotency_key": "manual"})
    assert again.json()["id"] == batch["id"]
    changed = {**request, "order": "number", "manifest_hash": shown["manifest_hash"], "idempotency_key": "manual"}
    assert client.post(BASE + "/manual/batches", json=changed).status_code == 409
    with factory() as uow:
        assert len(list(uow.session.scalars(select(LabelJobRow)))) == 5
        assert not uow.production.marks_issued([task.id])  # no product serials are spent
        event = uow.session.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "production.marking_manual_queued"))
        assert event.details["lines"] == [{"text": "ПИТАНИЕ +24В", "quantity": 2}, {"text": "X1-{№}", "quantity": 1}]
        assert event.details["total"] == 5 and event.details["numbering"] == [1, 3]
    job = claim_marking(factory(), machine_id="godex-pc", token_id=uuid4(), now=NOW)
    assert job["png_b64"] == shown["labels"][0]["png_b64"] and job["printer"] == "Godex EZ2350i"
    assert client.get(BASE + f"/batches/{batch['id']}").status_code == 200


def test_order_by_number_trial_and_reprint(marking):
    client, factory, *_ = marking
    request = body(marking, [{"text": "X10", "quantity": 1}, {"text": "X2", "quantity": 1}, {"text": "X1", "quantity": 1}],
                   order="number")
    batch, shown = manual_print(marking, request)
    assert [row["text"] for row in shown["labels"]] == ["X1", "X2", "X10"]
    trial, trial_shown = manual_print(marking, {**request, "trial": True}, key="trial")
    assert trial["total"] == 1 and trial_shown["labels"][0]["text"] == "X1" and trial["reason"] == "Пробная печать"
    # «Перепечатать» of own text: a new batch of the stored texts, a reason required.
    token = uuid4()
    while (job := claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)) is not None:
        report_marking(factory(), UUID(job["id"]), machine_id="godex-pc", token_id=token, attempt=job["attempt"],
                       outcome="spooled", message="", spool_job_id=None, now=NOW)
    response = client.post(BASE + f"/batches/{batch['id']}/reprint",
                           json={"sequences": [3], "reason": "Смялась", "idempotency_key": "again"})
    assert response.status_code == 200, response.text
    assert [job["text"] for job in response.json()["jobs"]] == ["X10"]


@pytest.mark.parametrize("lines, extra", [
    ([{"text": "", "quantity": 1}], {}),
    ([{"text": "A", "quantity": 1}, {"text": "  ", "quantity": 1}], {}),
    ([{"text": "A{№}", "quantity": 1}], {}),
    ([{"text": "A", "quantity": 0}], {}),
    ([], {}),
    ([{"text": "A{№}", "quantity": 1}], {"numbering": {"first": 5, "last": 2}}),
])
def test_empty_or_invalid_lines_are_refused_without_jobs(marking, lines, extra):
    client, factory, *_ = marking
    assert client.post(BASE + "/manual/preview", json=body(marking, lines, **extra)).status_code == 422
    with factory() as uow:
        assert uow.session.scalar(select(LabelJobRow.id)) is None


def test_manual_print_needs_the_marking_print_rights(marking):
    client, _factory, _payload, task, *_ = marking
    request = body(marking, [{"text": "A", "quantity": 1}])
    _as_operator(task.operations[0].operator_ids[0])  # Задания, but no «Печать документов»
    assert client.post(BASE + "/manual/preview", json=request).status_code == 403
    assert client.post(BASE + "/manual/batches", json={**request, "manifest_hash": "0" * 64, "idempotency_key": "k"}).status_code == 403


def test_another_operator_cannot_read_my_manual_batch(marking):
    client, *_ = marking
    batch, _shown = manual_print(marking, body(marking, [{"text": "A", "quantity": 1}]))
    _as_operator(uuid4())
    assert client.get(BASE + f"/batches/{batch['id']}").status_code == 403


def test_the_marking_machine_tile_opens_the_marking_card(marking):
    client, _factory, _payload, _task, _card, equipment_id, _operator = marking
    response = client.get("/api/production/machines")
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["rows"] if row["equipment_id"] == str(equipment_id))
    assert row["marking"] is True and row["inventory"] == "ОБ.101"


def test_more_than_500_labels_by_numbering_times_copies_is_a_422_with_a_readable_text(marking):
    client, factory, *_ = marking
    request = body(marking, [{"text": "A{№}", "quantity": 3}], numbering={"first": 1, "last": 200})
    for path in ("/manual/preview", "/manual/batches"):
        response = client.post(BASE + path, json={**request, "manifest_hash": "0" * 64, "idempotency_key": "k"} if "batches" in path else request)
        assert response.status_code == 422, response.text
        assert "не более 500" in response.json()["detail"]
    with factory() as uow:
        assert uow.session.scalar(select(LabelJobRow.id)) is None


def test_two_halves_text_on_a_one_zone_profile_is_a_422(marking):
    client, factory, *_ = marking
    request = body(marking, [{"text": "ЛЕВО | ПРАВО", "quantity": 1}])
    assert client.post(BASE + "/manual/preview", json=request).status_code == 200  # the two-zone flag takes it
    with factory() as uow:
        uow.session.get(MarkingProfileRow, UUID(request["profile_id"])).geometry = presets()[0][2]
        uow.commit()
    response = client.post(BASE + "/manual/preview", json=request)
    assert response.status_code == 422, response.text
    assert "двумя текстовыми зонами" in response.json()["detail"]


def test_preview_takes_no_row_locks_but_enqueue_does(marking, monkeypatch):
    locked = []
    original = Select.with_for_update
    monkeypatch.setattr(Select, "with_for_update", lambda self, *a, **k: (locked.append(1), original(self, *a, **k))[1])
    request = body(marking, [{"text": "A", "quantity": 1}])
    shown = marking[0].post(BASE + "/manual/preview", json=request)
    assert shown.status_code == 200 and locked == []
    response = marking[0].post(BASE + "/manual/batches", json={**request, "manifest_hash": shown.json()["manifest_hash"], "idempotency_key": "k"})
    assert response.status_code == 200 and len(locked) >= 2  # profile and binding


def test_manual_print_only_on_a_printer_of_the_own_site(marking):
    client, factory, _payload, task, _card, equipment_id, operator_id = marking
    request = body(marking, [{"text": "A", "quantity": 1}])
    batch, shown = manual_print(marking, request, key="master")  # the master (production: edit) prints anywhere
    with factory() as uow:
        uow.session.get(EquipmentMachineRow, equipment_id).fields = {"Инв. номер": "ОБ.101", "Расположение": "ЖУК"}
        uow.commit()
    master = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(
        master, actor=replace(master.actor, id=operator_id), permissions={"print": "edit", "myops": "edit"}, roles=frozenset({"operator"}))
    # The operator is of ЦАР, the printer stands in ЖУК: preview, queue, view are refused.
    assert client.post(BASE + "/manual/preview", json=request).status_code == 403
    assert client.post(BASE + "/manual/batches", json={**request, "manifest_hash": shown["manifest_hash"], "idempotency_key": "own"}).status_code == 403
    assert client.get(BASE + f"/batches/{batch['id']}").status_code == 403
    assert client.post(BASE + f"/batches/{batch['id']}/cancel", json={"reason": "x"}).status_code == 403
    with factory() as uow:
        uow.session.get(EquipmentMachineRow, equipment_id).fields = {"Инв. номер": "ОБ.101", "Расположение": "ЦАР"}
        uow.commit()
    assert client.post(BASE + "/manual/preview", json=request).status_code == 200
    assert client.get(BASE + f"/batches/{batch['id']}").status_code == 200


def test_journal_keeps_the_first_hundred_lines_and_says_it_was_cut(marking):
    client, factory, *_ = marking
    lines = [{"text": f"L{index}", "quantity": 1} for index in range(120)]
    manual_print(marking, body(marking, lines[:100]), key="whole")
    manual_print(marking, body(marking, lines), key="cut")
    with factory() as uow:
        details = sorted((event.details for event in uow.session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "production.marking_manual_queued"))),
            key=lambda item: item["lines_total"])
        whole, cut = details
        assert (whole["lines_total"], whole["lines_cut"]) == (100, False)
        assert (cut["lines_total"], cut["lines_cut"], len(cut["lines"])) == (120, True, 100)
