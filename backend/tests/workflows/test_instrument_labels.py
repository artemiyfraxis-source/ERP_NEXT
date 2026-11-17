from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID, uuid4

from PIL import Image
import pytest
from sqlalchemy import select

from app.main import app
from app.modules.warehouse.infrastructure import SqlAlchemyLabelSettings, SqlAlchemyWarehouseUnitOfWork
from app.modules.warehouse.label_jobs import LabelAgentRow, LabelJobRow
from app.modules.warehouse.labels.render import render
from app.modules.warehouse.labels.settings import PrintSettings
from app.modules.warehouse.labels.template import LabelContent, parse_template
from app.modules.warehouse.labels_application import effective_label_settings, tspl_job
from app.shared.persistence import AuditEventRow
from app.workflows import instrument_labels
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_instruments_api import _as, create

BASE = "/api/production/instruments"
NOW = datetime(2026, 9, 22, 9)


def setup(client, *, online=True):
    _as({"quality": "edit", "production": "view"})
    item = create(client, "СИ.034", name="SMA розетка – SMA розетка, S-322", serial_number="N8B139")
    sessions = app.state.production_uow_factory().session_factory
    if online:
        with sessions.begin() as session:
            session.add(LabelAgentRow(id=1, last_seen=NOW, host="Склад", printer="TSC"))
    return item, sessions


def jobs_and_audit(sessions):
    with sessions() as session:
        return (list(session.scalars(select(LabelJobRow))), list(session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "production.instrument_label_queued"))))


def test_single_copies_are_distinct_bitmap_jobs_and_audited(production_api):
    client, _ = production_api
    item, sessions = setup(client)
    response = client.post(f"{BASE}/{item['id']}/label", json={"copies": 3})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued" and body["copies"] == 3
    assert len(set(body["job_ids"])) == 3
    jobs, events = jobs_and_audit(sessions)
    assert {str(job.id) for job in jobs} == set(body["job_ids"])
    assert all(job.kind == "instrument" and job.copies == 1 and job.lot_id is None for job in jobs)
    assert all(b"BITMAP" in job.tspl and b"PRINT 1,1" in job.tspl for job in jobs)
    assert len(events) == 1 and events[0].aggregate_id == UUID(item["id"])
    assert events[0].details["job_ids"] == body["job_ids"]


def test_bulk_succeeds_without_warehouse_permissions(production_api):
    client, _ = production_api
    first, sessions = setup(client)
    second = create(client, "СИ.035", kind="Принадлежность", status="Действует", serial_number="ФР-035")
    response = client.post(f"{BASE}/labels", json={"ids": [first["id"], second["id"]], "copies": 2})
    assert response.status_code == 200, response.text
    jobs, events = jobs_and_audit(sessions)
    assert len(jobs) == len(response.json()["job_ids"]) == 4
    assert len(events) == 2
    assert client.get(f"{BASE}/label-agent").json()["online"] is True


def test_preview_is_png_in_default_58_by_30_and_does_not_enqueue(production_api):
    client, _ = production_api
    item, sessions = setup(client, online=False)
    response = client.get(f"{BASE}/{item['id']}/label.png")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    image = Image.open(BytesIO(response.content))
    assert image.width == round(58 * 300 / 25.4)
    assert image.height == round(30 * 300 / 25.4)
    assert jobs_and_audit(sessions) == ([], [])


@pytest.mark.parametrize("saved_size", [None, (58, 40), (100, 50)])
def test_designer_effective_format_saved_template_preview_and_print_agree(production_api, monkeypatch, saved_size):
    client, _ = production_api
    item, sessions = setup(client)
    monkeypatch.setattr(app.state, "warehouse_uow_factory", lambda: SqlAlchemyWarehouseUnitOfWork(sessions), raising=False)
    _as({"quality": "edit", "production": "view", "warehouse": "edit"})
    saved_settings = PrintSettings(width_mm=saved_size[0], height_mm=saved_size[1]) if saved_size else None
    if saved_settings is not None:
        with sessions.begin() as session:
            SqlAlchemyLabelSettings(session).save(saved_settings, NOW, "ОТК")
    view = client.get("/api/stock/labels/templates/instrument").json()
    settings = effective_label_settings(saved_settings, "instrument")
    assert view["current_format"] == f"{settings.width_mm:g}x{settings.height_mm:g}"
    if saved_size is None:
        assert view["current_format"] == "58x30"
        assert client.get("/api/stock/labels/templates/lot").json()["current_format"] == "58x40"
    template = {"elements": [{"id": "saved", "kind": "text", "text": "Сохранённый шаблон",
                              "x_mm": 2, "y_mm": 2, "w_mm": 54, "h_mm": 8, "font_mm": 2}]}
    route = f"/api/stock/labels/templates/instrument/{view['current_format']}"
    assert client.put(route, json={"template": template, "version": None}).status_code == 200
    designer_preview = client.post(route + "/preview", json={"template": template})
    actual_preview = client.get(f"{BASE}/{item['id']}/label.png")
    assert actual_preview.status_code == designer_preview.status_code == 200
    assert actual_preview.content == designer_preview.content
    image = Image.open(BytesIO(actual_preview.content))
    assert image.size == (round(settings.width_mm * settings.dpi / 25.4), round(settings.height_mm * settings.dpi / 25.4))
    assert client.post(f"{BASE}/{item['id']}/label", json={}).status_code == 200
    layout = render(parse_template(template, "instrument", view["current_format"]),
                    LabelContent("instrument", item["inventory_number"], {}), settings)
    jobs, _ = jobs_and_audit(sessions)
    assert len(jobs) == 1 and jobs[0].tspl == tspl_job(layout, settings, 1)


@pytest.mark.parametrize("case", ["absent", "old", "blank_printer", "future"])
def test_offline_agent_prevents_single_and_bulk_jobs(production_api, case):
    client, _ = production_api
    item, sessions = setup(client, online=False)
    if case != "absent":
        with sessions.begin() as session:
            session.add(LabelAgentRow(id=1, last_seen=NOW + timedelta(seconds=1) if case == "future" else NOW - timedelta(minutes=3),
                                      host="Склад", printer="" if case == "blank_printer" else "TSC"))
    assert client.get(f"{BASE}/label-agent").json()["online"] is False
    for path, data in [(f"/{item['id']}/label", {}), ("/labels", {"ids": [item["id"]]})]:
        response = client.post(BASE + path, json=data)
        assert response.status_code == 409 and response.json()["detail"] == instrument_labels.OFFLINE
    assert jobs_and_audit(sessions) == ([], [])


@pytest.mark.parametrize("permissions", [{"production": "edit"}, {"production": "view", "quality": "view"}, {}])
def test_print_and_agent_status_require_quality_edit(production_api, permissions):
    client, _ = production_api
    item, sessions = setup(client)
    _as(permissions)
    assert client.get(f"{BASE}/label-agent").status_code == 403
    assert client.post(f"{BASE}/{item['id']}/label", json={}).status_code == 403
    assert client.post(f"{BASE}/labels", json={"ids": [item["id"]]}).status_code == 403
    assert jobs_and_audit(sessions) == ([], [])


def test_preview_uses_registry_read_auth_and_missing_returns_404(production_api):
    client, _ = production_api
    item, _ = setup(client)
    _as({"production": "view"})
    assert client.get(f"{BASE}/{item['id']}/label.png").status_code == 200
    assert client.get(f"{BASE}/{uuid4()}/label.png").status_code == 404
    _as({})
    assert client.get(f"{BASE}/{item['id']}/label.png").status_code == 403


@pytest.mark.parametrize("copies", [0, 6, 1.5, True, "2"])
def test_invalid_copies_rejected(production_api, copies):
    client, _ = production_api
    item, sessions = setup(client)
    assert client.post(f"{BASE}/{item['id']}/label", json={"copies": copies}).status_code == 422
    assert client.post(f"{BASE}/labels", json={"ids": [item["id"]], "copies": copies}).status_code == 422
    assert jobs_and_audit(sessions) == ([], [])


@pytest.mark.parametrize("case", ["empty", "duplicates", "ids_cap", "physical_cap"])
def test_bulk_bounds_rejected_without_writes(production_api, case):
    client, _ = production_api
    item, sessions = setup(client)
    body = {"ids": [], "copies": 1}
    if case == "duplicates": body["ids"] = [item["id"], item["id"]]
    if case == "ids_cap": body["ids"] = [str(uuid4()) for _ in range(201)]
    if case == "physical_cap": body = {"ids": [str(uuid4()) for _ in range(41)], "copies": 5}
    assert client.post(f"{BASE}/labels", json=body).status_code == 422
    assert jobs_and_audit(sessions) == ([], [])


def test_missing_item_rolls_back_prior_jobs_and_audit(production_api):
    client, _ = production_api
    item, sessions = setup(client)
    response = client.post(f"{BASE}/labels", json={"ids": [item["id"], str(uuid4())]})
    assert response.status_code == 404
    assert jobs_and_audit(sessions) == ([], [])


def test_second_enqueue_failure_rolls_back_entire_batch(production_api, monkeypatch):
    client, _ = production_api
    item, sessions = setup(client)
    original = instrument_labels.add_job
    calls = 0
    def fail(session, **kwargs):
        nonlocal calls
        calls += 1
        job = original(session, **kwargs)
        session.flush()
        if calls == 2:
            raise ValueError("Ошибка печати")
        return job
    monkeypatch.setattr(instrument_labels, "add_job", fail)
    assert client.post(f"{BASE}/{item['id']}/label", json={"copies": 2}).status_code == 422
    assert jobs_and_audit(sessions) == ([], [])


def test_agent_aware_and_naive_time_match(production_api):
    client, _ = production_api
    _, sessions = setup(client)
    with sessions() as session:
        assert instrument_labels.label_agent_in(session, datetime(2026, 9, 22, 6, tzinfo=UTC))["online"]
        assert instrument_labels.label_agent_in(session, NOW + timedelta(minutes=2))["online"]
        assert not instrument_labels.label_agent_in(session, NOW + timedelta(minutes=2, seconds=1))["online"]
