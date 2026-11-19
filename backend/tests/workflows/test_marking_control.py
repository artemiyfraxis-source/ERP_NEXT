"""Marking printer control from ERP (owner 19.11.2026): cancel the rest of a batch, reprint issued serials, clear the
printer, peel-off flag of the profile."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.modules.production.domain import MARKS_ISSUED_EVENT
from app.modules.warehouse.label_jobs import LabelJobRow, claim_marking, report_marking
from app.modules.warehouse.marking_profiles import MarkingBatchRow
from app.modules.warehouse.marking_render import presets
from app.modules.warehouse.printer_commands import NOT_TAKEN, PrinterCommandRow, claim_command, report_command
from app.modules.warehouse.scan_agents import ScanAgentRow
from app.shared.persistence import AuditEventRow
from tests.production.test_api import production_api  # noqa: F401
from tests.workflows.test_marking_labels import BASE, NOW, enqueue, marking  # noqa: F401


def spool_all(factory, token):
    while (job := claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)) is not None:
        report_marking(factory(), UUID(job["id"]), machine_id="godex-pc", token_id=token, attempt=job["attempt"],
                       outcome="spooled", message="Передано принтеру", spool_job_id=1, now=NOW)


class Event:
    def __init__(self, details):
        self.details = details


def events(factory, kind):
    with factory() as uow:
        return [Event(dict(details)) for details in uow.session.scalars(select(AuditEventRow.details).where(AuditEventRow.event_type == kind))]


def test_cancel_rest_cancels_only_never_handed_out_jobs_and_is_journaled(marking):
    client, factory, *_ = marking
    batch, _, _ = enqueue(marking)
    token = uuid4()
    taken = claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)
    path = BASE + f"/batches/{batch['id']}/cancel"
    assert client.post(path, json={"reason": "  "}).status_code == 422
    response = client.post(path, json={"reason": "Флажки мнутся, останавливаю"})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["withdrawn"] == 14 and view["pending"] == 0
    assert [job["status"] for job in view["jobs"]] == ["taken"] + ["cancelled"] * 14
    assert view["jobs"][1]["status_label"] == "Отменено"
    assert view["jobs"][1]["message"] == "Отменено: Флажки мнутся, останавливаю"
    # The agent finishes the job it holds; nothing cancelled is handed out again.
    report_marking(factory(), UUID(taken["id"]), machine_id="godex-pc", token_id=token, attempt=taken["attempt"],
                   outcome="spooled", message="", spool_job_id=1, now=NOW)
    assert claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW) is None
    assert client.post(path, json={"reason": "Ещё раз"}).status_code == 409
    [event] = events(factory, "production.marking_cancelled")
    assert event.details["count"] == 14 and event.details["sequences"] == list(range(2, 16))
    done = client.get(BASE + f"/batches/{batch['id']}").json()
    assert done["status"] == "spooled" and done["withdrawn"] == 14


def test_cancelled_status_is_held_by_constraints(marking):
    _client, factory, *_ = marking
    enqueue(marking)
    job = claim_marking(factory(), machine_id="godex-pc", token_id=uuid4(), now=NOW)
    with factory() as uow:
        # A job the agent took can never become «cancelled» — its delivery is unknown.
        with pytest.raises(IntegrityError):
            uow.session.execute(update(LabelJobRow).where(LabelJobRow.id == UUID(job["id"]))
                                .values(status="cancelled", lease_until=None, finished_at=NOW, error="x"))
            uow.session.flush()
    with factory() as uow:
        pending = uow.session.scalar(select(LabelJobRow.id).where(LabelJobRow.status == "pending").limit(1))
        with pytest.raises(IntegrityError):  # a reason is required
            uow.session.execute(update(LabelJobRow).where(LabelJobRow.id == pending).values(status="cancelled", finished_at=NOW))
            uow.session.flush()


def test_reprint_serial_range_reuses_issued_serials_as_a_new_batch(marking):
    client, factory, _payload, task, *_ = marking
    batch, _, _ = enqueue(marking)
    path = BASE + f"/batches/{batch['id']}/reprint"
    body = {"serial_from": 2, "serial_to": 3, "reason": "Смялись при выдаче", "idempotency_key": "rp-1"}
    # Labels still in the queue are not reprinted.
    assert client.post(path, json=body).status_code == 409
    spool_all(factory, uuid4())
    issued_before = len(events(factory, MARKS_ISSUED_EVENT))
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    copy = response.json()
    assert copy["reprint_of"] == batch["id"] and copy["total"] == 2 and copy["reason"] == "Смялись при выдаче"
    assert [(job["text"], job["serial"]) for job in copy["jobs"]] == [("КБ-002", 2), ("КБ-003", 3)]
    assert client.post(path, json=body).json()["id"] == copy["id"]  # replay
    assert client.post(path, json={**body, "serial_to": 4}).status_code == 409  # key reused for another request
    assert len(events(factory, MARKS_ISSUED_EVENT)) == issued_before  # no new serial issuance
    [event] = events(factory, "production.marking_reprinted")
    assert event.details["serials"] == [2, 3] and event.details["sequences"] == [2, 3]
    with factory() as uow:
        stored = uow.session.get(MarkingBatchRow, UUID(copy["id"]))
        assert stored.manifest["reprint"] == {"of": batch["id"], "sequences": [2, 3]}
        assert stored.manifest["source_snapshot"] == uow.session.get(MarkingBatchRow, UUID(batch["id"])).manifest["source_snapshot"]
    fixed = client.post(path, json={"sequences": [6, 7], "reason": "Нет ХС", "idempotency_key": "rp-2"})
    assert fixed.status_code == 200, fixed.text
    assert [job["text"] for job in fixed.json()["jobs"]] == ["АБВ", "АБВ"]


@pytest.mark.parametrize("body,status", [
    ({"serial_from": 40, "serial_to": 41}, 422),
    ({"sequences": [99]}, 422),
    ({"sequences": [1], "serial_from": 1, "serial_to": 1}, 422),
    ({"serial_from": 3, "serial_to": 2}, 422),
    ({"sequences": [1, 1]}, 422),
])
def test_reprint_rejects_wrong_selection(marking, body, status):
    client, factory, *_ = marking
    batch, _, _ = enqueue(marking)
    spool_all(factory, uuid4())
    response = client.post(BASE + f"/batches/{batch['id']}/reprint", json={**body, "reason": "Брак", "idempotency_key": "x"})
    assert response.status_code == status, response.text


def test_reprint_requires_a_reason(marking):
    client, factory, *_ = marking
    batch, _, _ = enqueue(marking)
    spool_all(factory, uuid4())
    response = client.post(BASE + f"/batches/{batch['id']}/reprint", json={"sequences": [1], "reason": " ", "idempotency_key": "x"})
    assert response.status_code == 422


def test_clear_printer_command_holds_the_queue_until_the_agent_reports(marking):
    client, factory, _payload, _task, _card, equipment_id, _ = marking
    enqueue(marking)
    path = BASE + f"/machines/{equipment_id}/printer/clear"
    body = {"reason": "Старые флажки в буфере"}
    refused = client.post(path, json=body)
    assert refused.status_code == 409 and "2.10.0" in refused.json()["detail"]  # the fixture agent is 2.9.0
    with factory() as uow:
        uow.session.get(ScanAgentRow, "godex-pc").version = "2.10.0"
        uow.commit()
    assert client.post(path, json={"reason": ""}).status_code == 422
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending" and response.json()["status_label"] == "Ждёт агента"
    assert client.post(path, json=body).status_code == 409  # one open command per queue
    token = uuid4()
    assert claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW) is None  # nothing spooled meanwhile
    command = claim_command(factory(), machine_id="godex-pc", token_id=token, now=NOW)
    assert command["kind"] == "clear" and command["printer"] == "Godex EZ2350i"
    report_command(factory(), UUID(command["id"]), machine_id="godex-pc", token_id=token, attempt=command["attempt"],
                   outcome="done", message="Удалено заданий: 2; буфер очищен", result={"removed_jobs": 2, "buffer_cleared": True}, now=NOW)
    machines = client.get(BASE + "/machines").json()["machines"]
    shown = next(item for item in machines if item["equipment_id"] == str(equipment_id))["binding"]["command"]
    assert shown["status"] == "done" and shown["result"] == {"removed_jobs": 2, "buffer_cleared": True}
    assert claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW) is not None
    [event] = events(factory, "production.marking_printer_clear_requested")
    assert event.details["reason"] == "Старые флажки в буфере"


def test_clear_printer_command_not_taken_expires_as_failed(marking):
    client, factory, _payload, _task, _card, equipment_id, _ = marking
    with factory() as uow:
        uow.session.get(ScanAgentRow, "godex-pc").version = "2.10.0"
        uow.commit()
    assert client.post(BASE + f"/machines/{equipment_id}/printer/clear", json={"reason": "Сбой"}).status_code == 200
    assert claim_command(factory(), machine_id="godex-pc", token_id=uuid4(), now=NOW + timedelta(minutes=3)) is None
    with factory() as uow:
        row = uow.session.scalar(select(PrinterCommandRow))
        assert (row.status, row.message) == ("failed", NOT_TAKEN)
        assert uow.session.scalar(select(func.count()).select_from(PrinterCommandRow)) == 1


def test_peel_flag_travels_to_the_agent_and_does_not_reset_calibration(marking):
    from dataclasses import replace

    from app.main import app
    from app.shared.http_auth import authenticated_principal

    client, factory, payload, *_ = marking
    assert presets()[-1][2]["peel"] is True  # ОБ.101 flags are handed out through the dispenser
    enqueue(marking)
    job = claim_marking(factory(), machine_id="godex-pc", token_id=uuid4(), now=NOW)
    assert job["peel"] is True
    principal = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(principal, permissions={**principal.permissions, "erpref": "edit"})
    response = client.put(BASE + "/profiles/" + payload["profile_id"],
                          json={"version": 1, "calibrated": True, "geometry": {**presets()[-1][2], "peel": False}})
    assert response.status_code == 200, response.text
    assert response.json()["calibrated"] is True and response.json()["geometry"]["peel"] is False
    [event] = events(factory, "production.marking_profile_saved")
    assert event.details["peel"] is False
