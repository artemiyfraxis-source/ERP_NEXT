"""The label print queue (owner 22.10.2026): every print queues a TSPL job; the agent of the warehouse PC (API token
`labels:print`) claims the oldest one, prints it and reports. A lease that runs out re-offers the job, a person's
session never takes jobs, the database refuses a job in an impossible state."""

import base64
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.label_jobs import LEASE, LabelJobRow, claim, enqueue, report
from app.modules.warehouse.labels_api import AGENT_ONLY
from app.shared.clock import MOSCOW
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401  (fixture)

CLAIM = "/api/stock/labels/agent/claim"
STATUS = "/api/stock/labels/agent/status"
AGENT = {"printer": "TSC MB341", "host": "SKLAD-PC"}


def _result(job_id: str) -> str:
    return f"/api/stock/labels/agent/jobs/{job_id}/result"


@pytest.fixture()
def queue(tokens_api):  # noqa: F811
    sessions, clock = tokens_api
    lot = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000007", package="", name="Разъём", item_type="Комплектующие",
        purpose="FRX.001", unit="шт", quantity=Decimal("10"), storage_location="1F4",
        source_receiving_ids=("20000000-0000-0000-0000-000000000001",),
    )
    with app.state.warehouse_uow_factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    admin = _login("admin")
    agent = _bearer(_issue(admin, scopes=("labels:print",), name="Агент печати склада")["token"])
    return admin, agent, lot, clock, sessions


def _print_lot(admin: TestClient, lot, copies: int) -> dict:
    response = admin.post(f"/api/stock/batches/{lot.id}/label", json={"copies": copies}, headers=_csrf(admin))
    assert response.status_code == 200, response.text
    return response.json()


def test_the_agent_takes_jobs_oldest_first_and_reports_them(queue):
    admin, agent, lot, clock, _sessions = queue
    service = TestClient(app)
    queued = _print_lot(admin, lot, 2)
    assert queued["status"] == "queued" and queued["copies"] == 2 and queued["reason"] == "В очереди принтера."
    clock.now += timedelta(seconds=1)
    sample = admin.post("/api/stock/labels/test", headers=_csrf(admin)).json()
    assert sample["status"] == "queued" and sample["job_id"] != queued["job_id"]

    before = admin.get(STATUS).json()
    assert before == {"last_seen": None, "host": "", "printer": "", "queue": 2, "last_error": None, "last_job": None}

    first = service.post(CLAIM, json=AGENT, headers=agent)
    assert first.status_code == 200, first.text
    job = first.json()["job"]
    assert job["id"] == queued["job_id"] and job["copies"] == 2
    tspl = base64.b64decode(job["tspl_b64"])
    assert tspl.startswith(b"SIZE 58 mm,40 mm\r\n") and tspl.endswith(b"\r\nPRINT 2,1\r\n")  # copies are in the job

    assert service.post(_result(job["id"]), json={"ok": True, "error": ""}, headers=agent).json() == {"ok": True}
    clock.now += timedelta(seconds=1)
    second = service.post(CLAIM, json=AGENT, headers=agent).json()["job"]
    assert second["id"] == sample["job_id"] and second["copies"] == 1
    failed = service.post(_result(second["id"]), json={"ok": False, "error": "  Нет \n бумаги "}, headers=agent)
    assert failed.status_code == 200, failed.text
    assert service.post(CLAIM, json=AGENT, headers=agent).json() == {"job": None}

    status = admin.get(STATUS).json()
    assert (status["host"], status["printer"], status["queue"]) == ("SKLAD-PC", "TSC MB341", 0)
    assert status["last_seen"].startswith("2026-10-05T10:00:02")
    assert status["last_error"] == "Нет бумаги"
    assert status["last_job"]["status"] == "failed" and status["last_job"]["error"] == "Нет бумаги"
    assert admin.get("/api/stock/labels/printers").json() == {"printers": ["TSC MB341"], "on_windows": True}

    # A printed job clears the agent's last error.
    _print_lot(admin, lot, 1)
    clock.now += timedelta(seconds=1)
    third = service.post(CLAIM, json=AGENT, headers=agent).json()["job"]
    service.post(_result(third["id"]), json={"ok": True, "error": "ignored"}, headers=agent)
    status = admin.get(STATUS).json()
    assert status["last_error"] is None and status["last_job"] == {
        "status": "printed", "finished_at": status["last_job"]["finished_at"], "error": None,
    }


def test_an_expired_lease_offers_the_job_again(queue):
    admin, agent, lot, clock, _sessions = queue
    service = TestClient(app)
    queued = _print_lot(admin, lot, 1)
    taken = service.post(CLAIM, json=AGENT, headers=agent).json()["job"]
    assert taken["id"] == queued["job_id"]
    clock.now += LEASE - timedelta(seconds=1)
    assert service.post(CLAIM, json=AGENT, headers=agent).json() == {"job": None}, "the lease still holds it"
    assert admin.get(STATUS).json()["queue"] == 1
    clock.now += timedelta(seconds=2)
    again = service.post(CLAIM, json=AGENT, headers=agent).json()["job"]
    assert again["id"] == queued["job_id"], "a duplicate label beats a lost one"

    assert service.post(_result(again["id"]), json={"ok": True}, headers=agent).status_code == 200
    repeated = service.post(_result(again["id"]), json={"ok": True}, headers=agent)
    assert repeated.status_code == 409 and "уже закрыто" in repeated.json()["detail"]
    assert service.post(_result(str(uuid4())), json={"ok": True}, headers=agent).status_code == 404


def test_a_result_for_a_job_never_handed_out_is_refused(queue):
    admin, agent, lot, _clock, _sessions = queue
    queued = _print_lot(admin, lot, 1)
    response = TestClient(app).post(_result(queued["job_id"]), json={"ok": True}, headers=agent)
    assert response.status_code == 409 and "не выдано" in response.json()["detail"]
    assert admin.get(STATUS).json()["queue"] == 1


def test_only_the_agents_token_reaches_the_queue(queue):
    admin, agent, lot, _clock, _sessions = queue
    queued = _print_lot(admin, lot, 1)
    anonymous = TestClient(app)
    assert anonymous.post(CLAIM, json=AGENT).status_code == 401
    assert anonymous.post(_result(queued["job_id"]), json={"ok": True}).status_code == 401

    # A person's session — even the administrator's, with the CSRF token — never takes a job.
    for path in (CLAIM, _result(queued["job_id"])):
        refused = admin.post(path, json={**AGENT, "ok": True}, headers=_csrf(admin))
        assert refused.status_code == 403 and refused.json()["detail"] == AGENT_ONLY, path

    other = _bearer(_issue(admin, scopes=("refs:read",), name="Чужой токен")["token"])
    assert TestClient(app).post(CLAIM, json=AGENT, headers=other).status_code == 403
    # The agent's token opens the queue and nothing else: not the status, not a print, not the stock.
    service = TestClient(app)
    assert service.get(STATUS, headers=agent).status_code == 403
    assert service.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 1}, headers=agent).status_code == 403
    assert service.get("/api/stock/labels/settings", headers=agent).status_code == 403
    assert admin.get(STATUS).json()["queue"] == 1, "nothing was taken"


def test_finished_jobs_older_than_thirty_days_are_purged_on_claim(queue):
    _admin, _agent, lot, _clock, sessions = queue
    factory = app.state.warehouse_uow_factory
    start = datetime(2026, 9, 1, 9, 0, tzinfo=MOSCOW)
    old = enqueue(factory(), tspl=b"PRINT 1,1\r\n", copies=1, kind="lot", lot_id=lot.id, created_by="x", now=start)
    assert claim(factory(), printer="P", host="H", now=start).id == old
    report(factory(), old, ok=True, error="", now=start)
    recent_at = start + timedelta(days=20)
    recent = enqueue(factory(), tspl=b"PRINT 1,1\r\n", copies=1, kind="sample", lot_id=None, created_by="x", now=recent_at)
    claim(factory(), printer="P", host="H", now=recent_at)
    report(factory(), recent, ok=False, error="", now=recent_at)

    assert claim(factory(), printer="P", host="H", now=start + timedelta(days=31)) is None
    with sessions() as session:
        rows = {row.id: row for row in session.scalars(select(LabelJobRow))}
    assert old not in rows and rows[recent].status == "failed"
    assert rows[recent].error == "Агент печати не напечатал этикетку и не сказал почему"


def test_copies_out_of_range_are_never_queued(queue):
    factory = app.state.warehouse_uow_factory
    now = datetime(2026, 10, 5, 10, 0, tzinfo=MOSCOW)
    for copies in (0, 51):
        with pytest.raises(ValueError):
            enqueue(factory(), tspl=b"x", copies=copies, kind="sample", lot_id=None, created_by="", now=now)


NOW = datetime(2026, 10, 5, 10, 0)
LATER = NOW + timedelta(minutes=2)
GOOD = {"created_at": NOW, "created_by": "", "kind": "sample", "lot_id": None, "copies": 1, "tspl": b"x",
        "status": "pending", "taken_at": None, "lease_until": None, "finished_at": None, "error": None}


@pytest.mark.parametrize("bad", [
    {"copies": 0}, {"copies": 51}, {"tspl": b""}, {"kind": "box"}, {"status": "done"},
    {"lot_id": uuid4()},  # a sample has no lot
    {"kind": "lot"},  # a lot's label names its lot
    {"taken_at": NOW},  # a pending job was never taken
    {"status": "taken", "taken_at": NOW},  # taken without a lease
    {"status": "taken", "taken_at": NOW, "lease_until": NOW},  # the lease ends after the take
    {"lease_until": LATER},  # a lease only on a taken job
    {"status": "printed", "taken_at": NOW},  # final without its finish time
    {"status": "printed", "finished_at": LATER},  # final without ever being taken
    {"finished_at": LATER},  # a pending job is not finished
    {"status": "failed", "taken_at": NOW, "finished_at": LATER},  # failed without the reason
    {"status": "printed", "taken_at": NOW, "finished_at": LATER, "error": "x"},  # an error only on a failed job
])
def test_the_database_refuses_an_impossible_job(queue, bad):
    _admin, _agent, _lot, _clock, sessions = queue
    with sessions() as session:
        session.add(LabelJobRow(id=uuid4(), **{**GOOD, **bad}))
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize("good", [
    {},
    {"kind": "lot", "lot_id": uuid4()},
    {"status": "taken", "taken_at": NOW, "lease_until": LATER},
    {"status": "printed", "taken_at": NOW, "finished_at": LATER},
    {"status": "failed", "taken_at": NOW, "finished_at": LATER, "error": "Нет бумаги"},
])
def test_the_database_takes_every_possible_job(queue, good):
    _admin, _agent, _lot, _clock, sessions = queue
    with sessions() as session:
        session.add(LabelJobRow(id=uuid4(), **{**GOOD, **good}))
        session.flush()
