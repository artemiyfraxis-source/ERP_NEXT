"""«Ручная работа», slice S3: the material of a finished job — «Списать с партии» through the warehouse's own write-off
(one transaction with the job's compare-and-swap, source key «manual-job:{job}:{lot}», once), «Не списывать» with a
reason, rights (only «Склад» on edit writes off), the «Ждёт решения склада» queue of the journal."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.production import manual_jobs as jobs
from app.modules.references.infrastructure import EquipmentMachineRow
from app.modules.warehouse.application import ConcurrentWarehouseUpdate, WriteOffCommand, write_off
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseRepository, StockLotRow, WriteOffEntryRow
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from app.workflows.manual_job_stock import _StockUnitOfWork
from tests.factories import Actor
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.workflows.test_machines_api import machines_api  # noqa: F401

BASE = "/api/production/manual-jobs"


def _post(client, path, body):
    return client.post(path, json=body, headers=_csrf(client))


def _lot(sessions, warehouse, grn, quantity, *, article="ПВАМ 0,35", unit="м", reserved="0") -> UUID:
    lot = StockLot.create(warehouse=warehouse, grn=grn, package="", name=f"Провод {article}", item_type="Провод",
                          purpose="Склад", unit=unit, quantity=Decimal(quantity), storage_location="",
                          source_receiving_ids=("test",), article=article, location_as_is=True)
    if Decimal(reserved):
        lot = lot.reserve(Decimal(reserved))
    with sessions.begin() as session:
        SqlAlchemyWarehouseRepository(session).add(lot)
    return lot.id


def _balance(sessions, lot_id) -> tuple[Decimal, int]:
    with sessions() as session:
        row = session.get(StockLotRow, lot_id)
        return row.inbound - row.written_off, row.version


def _writeoffs(sessions) -> list[WriteOffEntryRow]:
    with sessions() as session:
        return list(session.scalars(select(WriteOffEntryRow).order_by(WriteOffEntryRow.quantity)))


def _done_job(admin, equipment_id, *, good=18, defect=2, material="pending", **values) -> dict:
    job = _post(admin, BASE, {"equipment_id": str(equipment_id), "purpose": "Проба на образцах", "wire": "ПВАМ 0,35",
                              "length_mm": "500", **values}).json()
    body = {"version": 1, "good": good, "defect": defect, "defect_reason": "надрез" if defect else "",
            "material": material, "material_note": "обрезки" if material == "not_issued" else ""}
    done = _post(admin, f"{BASE}/{job['id']}/finish", body)
    assert done.status_code == 200, done.text
    return done.json()


def _site(sessions, equipment_id, site):
    with sessions.begin() as session:
        row = session.get(EquipmentMachineRow, equipment_id)
        row.fields = {**row.fields, "Расположение": site}


def _line(lot_id, version, quantity):
    return {"lot_id": str(lot_id), "lot_version": version, "quantity": quantity}


@pytest.fixture
def as_person():
    """A client acting as a person with exactly these sections (no session cookie, so no CSRF)."""

    def use(permissions: dict[str, str], level: str = "operator") -> TestClient:
        principal = Principal(actor=Actor(name="Кладовщик Тест", department="Склад"), email="p@example.test",
                              roles=frozenset({level}), permissions=permissions)
        app.dependency_overrides[authenticated_principal] = lambda: principal
        return TestClient(app)

    yield use
    app.dependency_overrides.pop(authenticated_principal, None)


def test_issue_writes_each_lot_off_once_through_the_warehouse(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    _site(sessions, ob019, "ЦАР")
    zhuk = _lot(sessions, "ЖУК", "Г-002", "50")
    car = _lot(sessions, "ЦАР", "Г-001", "100")
    _lot(sessions, "ЦАР", "Г-003", "40", article="МКЭШ 3x0,5")  # another wire: never offered
    job = _done_job(admin, ob019)
    material = admin.get(f"{BASE}/{job['id']}/material").json()
    assert (material["pieces"], material["length_mm"], material["site"]) == (20, "500", "ЦАР")
    assert [lot["grn"] for lot in material["wire"]["lots"]] == ["Г-001", "Г-002"]  # the machine's warehouse first
    assert material["wire"]["lots"][0]["here"] is True and material["can_issue"] and material["can_decide"]
    assert material["issues"] == [] and material["contact"]["lots"] == []
    assert [lot["grn"] for lot in admin.get(f"{BASE}/{job['id']}/material", params={"q": "МКЭШ"}).json()["found"]] \
        == ["Г-003"]

    body = {"version": 2, "lines": [_line(car, 1, "6.5"), _line(zhuk, 1, "4")]}
    answer = _post(admin, f"{BASE}/{job['id']}/material/issue", body)
    assert answer.status_code == 200, answer.text
    answer = answer.json()
    assert (answer["job"]["material"], answer["job"]["material_text"], answer["job"]["version"]) == \
        ("issued", "Списано с партии", 3)
    assert sorted((item["grn"], item["quantity"], item["unit"]) for item in answer["issues"]) == \
        [("Г-001", "6.5", "м"), ("Г-002", "4", "м")]
    assert answer["wire"]["lots"] == []  # decided: nothing more to pick
    assert _balance(sessions, car) == (Decimal("93.5"), 2) and _balance(sessions, zhuk) == (Decimal("46"), 2)
    rows = _writeoffs(sessions)
    assert {row.source_key for row in rows} == {f"manual-job:{job['id']}:{car}", f"manual-job:{job['id']}:{zhuk}"}
    assert {(row.kind, row.purpose) for row in rows} == {("writeoff", "Ручная работа Р-000001")}
    assert rows[0].reason == "Ручная работа Р-000001, ОБ.019: Проба на образцах"
    with sessions() as session:
        issues = session.scalars(select(jobs.ManualJobIssueRow)).all()
        assert {issue.writeoff_id for issue in issues} == {row.id for row in rows}
        events = dict(session.execute(select(AuditEventRow.event_type, func.count()).group_by(
            AuditEventRow.event_type)).all())
        text = session.scalar(select(AuditEventRow.details).where(
            AuditEventRow.event_type == "production.manual_job_material_issued"))["text"]
    assert events["warehouse.written_off"] == 2 and events["production.manual_job_material_issued"] == 1
    assert text == "Списано с партии по Р-000001: ЦАР Г-001 — 6,5 м; ЖУК Г-002 — 4 м"

    # The same request again (a lost answer): the same job back, nothing written off twice.
    again = _post(admin, f"{BASE}/{job['id']}/material/issue", body)
    assert again.status_code == 200 and again.json()["job"]["version"] == 3
    assert len(_writeoffs(sessions)) == 2 and _balance(sessions, car) == (Decimal("93.5"), 2)
    other = _post(admin, f"{BASE}/{job['id']}/material/issue", {"version": 2, "lines": [_line(car, 2, "1")]})
    assert other.status_code == 409 and "уже списан" in other.json()["detail"]
    late = _post(admin, f"{BASE}/{job['id']}/material/not-issued", {"version": 2, "note": "обрезки"})
    assert late.status_code == 409 and "уже решён" in late.json()["detail"]
    assert len(_writeoffs(sessions)) == 2


def test_any_refusal_rolls_the_whole_issue_back(machines_api):
    sessions, clock, admin, ob019, ob101 = machines_api
    first = _lot(sessions, "ЦАР", "Г-001", "100")
    reserved = _lot(sessions, "ЦАР", "Г-002", "10", reserved="8")
    job = _done_job(admin, ob019)
    url = f"{BASE}/{job['id']}/material/issue"

    def refused(lines, version=2):
        return _post(admin, url, {"version": version, "lines": lines})

    over = refused([_line(first, 1, "5"), _line(reserved, 2, "3")])  # only 2 m of Г-002 are not reserved
    assert over.status_code == 422 and "доступно 2 м, а списываете 3 м" in over.json()["detail"]
    over_balance = refused([_line(first, 1, "100.5")])
    assert over_balance.status_code == 422
    stale_lot = refused([_line(first, 1, "5"), _line(reserved, 1, "1")])
    assert stale_lot.status_code == 409 and "изменилась" in stale_lot.json()["detail"]
    assert refused([_line(first, 1, "1"), _line(first, 1, "2")]).status_code == 422  # one lot twice
    assert refused([_line(uuid4(), 1, "1")]).status_code == 404
    assert refused([_line(first, 1, "0")]).status_code == 422
    stale_job = refused([_line(first, 1, "5")], version=1)
    assert stale_job.status_code == 409 and "обновите" in stale_job.json()["detail"]
    # Nothing of any refused request stayed: no write-off, the lots and the job as they were.
    assert _writeoffs(sessions) == [] and _balance(sessions, first) == (Decimal("100"), 1)
    with sessions() as session:
        row = session.get(jobs.ManualJobRow, UUID(job["id"]))
        assert (row.material, row.version) == ("pending", 2)
        assert session.scalar(select(func.count()).select_from(jobs.ManualJobIssueRow)) == 0

    running = _post(admin, BASE, {"equipment_id": str(ob101), "purpose": "Проба"}).json()
    early = _post(admin, f"{BASE}/{running['id']}/material/issue", {"version": 1, "lines": [_line(first, 1, "1")]})
    assert early.status_code == 409 and "ещё идёт" in early.json()["detail"]
    cancelled = _post(admin, f"{BASE}/{running['id']}/cancel", {"version": 1, "reason": "не тот провод"}).json()
    gone = _post(admin, f"{BASE}/{running['id']}/material/issue",
                 {"version": cancelled["version"], "lines": [_line(first, 1, "1")]})
    assert gone.status_code == 409 and "отменена" in gone.json()["detail"]
    assert _post(admin, f"{BASE}/{uuid4()}/material/issue", {"version": 1, "lines": [_line(first, 1, "1")]}) \
        .status_code == 404
    assert _writeoffs(sessions) == []


def test_two_decisions_at_once_one_stands(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    lot = _lot(sessions, "ЦАР", "Г-001", "100")
    job = _done_job(admin, ob019)
    job_id = UUID(job["id"])
    # Session A read the job (done, pending, version 2) …
    slow = sessions()
    assert slow.get(jobs.ManualJobRow, job_id).version == 2
    # … session B decided meanwhile …
    assert _post(admin, f"{BASE}/{job['id']}/material/issue",
                 {"version": 2, "lines": [_line(lot, 1, "5")]}).status_code == 200
    # … so A's compare-and-swap finds the version moved: 409, nothing of A is written.
    with pytest.raises(jobs.ManualJobConflict):
        jobs.decide_material(slow, job_id, 2, "not_issued", "обрезки")
    slow.rollback()
    slow.close()
    with sessions() as session:
        row = session.get(jobs.ManualJobRow, job_id)
        assert (row.material, row.material_note, row.version) == ("issued", "", 3)
    second = _post(admin, f"{BASE}/{job['id']}/material/not-issued", {"version": 2, "note": "обрезки"})
    assert second.status_code == 409
    assert len(_writeoffs(sessions)) == 1 and _balance(sessions, lot) == (Decimal("95"), 2)


def test_a_source_key_already_journalled_refuses_the_write_off(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    lot = _lot(sessions, "ЦАР", "Г-001", "100")

    def run(version):
        with sessions.begin() as session:
            write_off(WriteOffCommand(lot_id=lot, expected_version=version, quantity=Decimal("3"),
                                      purpose="Ручная работа Р-000009", reason="",
                                      actor=Actor(name="Тест", department="Склад"),
                                      now=clock.now, source_key="manual-job:x:y"), _StockUnitOfWork(session))

    run(1)
    with pytest.raises(ConcurrentWarehouseUpdate):
        run(2)
    assert _balance(sessions, lot) == (Decimal("97"), 2) and len(_writeoffs(sessions)) == 1


def test_not_issued_needs_a_reason(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    job = _done_job(admin, ob019, defect=0)
    url = f"{BASE}/{job['id']}/material/not-issued"
    assert _post(admin, url, {"version": 2, "note": "   "}).status_code == 422
    assert _post(admin, url, {"version": 2, "note": ""}).status_code == 422
    done = _post(admin, url, {"version": 2, "note": "обрезки  из отходов"})
    assert done.status_code == 200, done.text
    assert (done.json()["job"]["material_text"], done.json()["job"]["material_note"]) == \
        ("Не списывать", "обрезки из отходов")
    assert _post(admin, url, {"version": 2, "note": "обрезки из отходов"}).status_code == 200  # the same again
    with sessions() as session:
        text = session.scalar(select(AuditEventRow.details).where(
            AuditEventRow.event_type == "production.manual_job_material_not_issued"))["text"]
    assert text == "Материал по Р-000001 не списывается: обрезки из отходов"
    assert _writeoffs(sessions) == []


def test_only_the_warehouse_on_edit_writes_off(machines_api, as_person):
    sessions, clock, admin, ob019, ob101 = machines_api
    lot = _lot(sessions, "ЦАР", "Г-001", "100")
    first = _done_job(admin, ob019)
    second = _done_job(admin, ob101)
    issue = {"version": 2, "lines": [_line(lot, 1, "5")]}

    master = as_person({"production": "edit"})
    material = master.get(f"{BASE}/{first['id']}/material").json()
    assert (material["can_issue"], material["can_decide"]) == (False, True)
    refused = master.post(f"{BASE}/{first['id']}/material/issue", json=issue)
    assert refused.status_code == 403 and refused.json()["detail"] == "Недостаточно прав для раздела"
    assert master.post(f"{BASE}/{first['id']}/material/not-issued", json={"version": 2, "note": "обрезки"}) \
        .status_code == 200

    viewer = as_person({"warehouse": "view"})
    assert viewer.get(f"{BASE}/{second['id']}/material").status_code == 200
    assert viewer.post(f"{BASE}/{second['id']}/material/issue", json=issue).status_code == 403
    assert viewer.post(f"{BASE}/{second['id']}/material/not-issued", json={"version": 2, "note": "x"}).status_code == 403
    assert as_person({"projects": "edit"}).get(f"{BASE}/{second['id']}/material").status_code == 403

    storekeeper = as_person({"warehouse": "edit"})
    listed = storekeeper.get(BASE).json()
    assert (listed["can_edit"], listed["can_issue"], listed["can_decide"]) == (False, True, True)
    assert storekeeper.get(f"{BASE}/options").status_code == 403  # starting a job stays Производство / Задания
    done = storekeeper.post(f"{BASE}/{second['id']}/material/issue", json=issue)
    assert done.status_code == 200, done.text
    assert _writeoffs(sessions)[0].actor_name == "Кладовщик Тест"


def test_the_journal_lists_every_job_waiting_for_the_warehouse(machines_api):
    sessions, clock, admin, ob019, ob101 = machines_api
    lot = _lot(sessions, "ЦАР", "Г-001", "100")
    old = _done_job(admin, ob019)
    clock.now += timedelta(days=1)
    _done_job(admin, ob019, material="not_issued")
    fresh = _done_job(admin, ob101)
    _post(admin, BASE, {"equipment_id": str(ob019), "purpose": "Идёт"})
    today = admin.get(BASE).json()
    assert today["waiting_count"] == 2 and len(today["rows"]) == 3  # the old job is outside today's period
    waiting = admin.get(BASE, params={"waiting": "true"}).json()
    assert [row["number"] for row in waiting["rows"]] == [fresh["number"], old["number"]]
    assert all(row["material_text"] == "Ждёт решения склада" for row in waiting["rows"])
    assert _post(admin, f"{BASE}/{old['id']}/material/issue",
                 {"version": 2, "lines": [_line(lot, 1, "10")]}).status_code == 200
    assert admin.get(BASE, params={"waiting": "true"}).json()["waiting_count"] == 1
