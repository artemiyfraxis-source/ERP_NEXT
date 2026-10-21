"""Stage option warehouse.labels over HTTP: settings, preview, test print and a lot's label (Roller_Buyer «этикетка партии
через API»). Tests never print: the printer name stays empty, so every job is only saved as a file."""

import io
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import WarehouseLabelSettingsRow
from app.modules.warehouse.labels_application import lot_label_data
from app.shared import stage_options
from app.shared.events import AuditEvent
from tests.factories import Actor
from tests.view_access import as_viewer
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture

OPTION = "warehouse.labels"


@pytest.fixture()
def stage(monkeypatch, tmp_path):
    path = tmp_path / "data" / "options.json"
    path.parent.mkdir()
    monkeypatch.setenv(stage_options.ENV, str(path))
    monkeypatch.delenv("ERP_LABEL_PRINT_DIR", raising=False)
    return lambda **options: path.write_text(json.dumps(options), encoding="utf-8")


@pytest.fixture()
def labels(warehouse_api, stage):  # noqa: F811
    client, lot = warehouse_api
    stage(**{OPTION: True})
    return client, lot


def test_option_off_hides_every_label_route(warehouse_api, stage):  # noqa: F811
    client, lot = warehouse_api
    stage(**{OPTION: False})
    for method, path in (
        ("GET", "/api/stock/labels/settings"), ("PUT", "/api/stock/labels/settings"),
        ("GET", "/api/stock/labels/printers"), ("GET", "/api/stock/labels/sample.png"),
        ("POST", "/api/stock/labels/test"), ("GET", f"/api/stock/labels/lot?grn={lot.grn}"),
        ("GET", f"/api/stock/batches/{lot.id}/label.png"), ("POST", f"/api/stock/batches/{lot.id}/label"),
    ):
        assert client.request(method, path, json={} if method != "GET" else None).status_code == 404, (method, path)


def test_lot_preview_is_the_label_png_and_a_print_without_printer_saves_the_job(labels, tmp_path):
    client, lot = labels
    png = client.get(f"/api/stock/batches/{lot.id}/label.png")
    assert png.status_code == 200, png.text
    assert png.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(png.content)).size == (685, 354)

    printed = client.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 2})
    assert printed.status_code == 200, printed.text
    body = printed.json()
    assert body["status"] == "saved", "no printer chosen — the job waits as a file"
    assert body["copies"] == 2
    assert "Принтер не выбран" in body["reason"]
    job = Path(body["file"])
    # On the stage the jobs sit in a `print` folder next to the options file.
    assert job.parent == tmp_path / "data" / "print" and job.name.endswith("-FRX.26.0000001.prn")
    content = job.read_bytes()
    assert content.startswith(b"SIZE 58 mm,30 mm\r\nGAP 2 mm,0 mm\r\n")
    assert b"DENSITY 12\r\nSPEED 2\r\n" in content and content.endswith(b"\r\nPRINT 2,1\r\n")


def test_copies_are_one_to_fifty_and_default_to_one(labels):
    client, lot = labels
    assert client.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 0}).status_code == 422
    assert client.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 51}).status_code == 422
    assert client.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 50}).json()["copies"] == 50
    assert client.post(f"/api/stock/batches/{lot.id}/label").json()["copies"] == 1


def test_an_unknown_lot_is_404(labels):
    client, _ = labels
    missing = uuid4()
    assert client.get(f"/api/stock/batches/{missing}/label.png").status_code == 404
    assert client.post(f"/api/stock/batches/{missing}/label", json={"copies": 1}).status_code == 404


def test_settings_are_read_saved_and_journalled(labels):
    client, _ = labels
    first = client.get("/api/stock/labels/settings")
    assert first.status_code == 200, first.text
    assert first.json()["settings"] == {
        "printer_name": "", "gap_mm": 2, "density": 12, "speed": 2, "width_mm": 58, "height_mm": 30, "dpi": 300,
        "code_type": "both", "offset_x_mm": 0, "offset_y_mm": 0,
    }
    assert first.json()["code_types"]["both"] == "QR и Code128"

    saved = client.put("/api/stock/labels/settings", json={
        **first.json()["settings"], "printer_name": "  TSC MB341 ", "gap_mm": 3, "code_type": "qr", "offset_x_mm": -0.5,
    })
    assert saved.status_code == 200, saved.text
    again = client.get("/api/stock/labels/settings").json()["settings"]
    assert (again["printer_name"], again["gap_mm"], again["code_type"], again["offset_x_mm"]) == ("TSC MB341", 3, "qr", -0.5)
    assert client.get("/api/stock/labels/sample.png").status_code == 200

    journal = client.get("/api/stock/journal").json()["rows"]
    assert any(row["event"].startswith("Печать этикеток: настройки — принтер TSC MB341") for row in journal)


@pytest.mark.parametrize("bad", [{"density": 16}, {"speed": 0}, {"dpi": 600}, {"width_mm": 30}, {"code_type": "x"}])
def test_settings_out_of_bounds_are_refused(labels, bad):
    client, _ = labels
    assert client.put("/api/stock/labels/settings", json=bad).status_code == 422


def test_the_database_refuses_a_setting_out_of_bounds_too(labels):
    with app.state.warehouse_uow_factory() as uow:
        uow.session.add(WarehouseLabelSettingsRow(
            id=1, printer_name="", code_type="both", width_mm=58, height_mm=30, dpi=300, gap_mm=2, density=16, speed=2,
            offset_x_mm=0, offset_y_mm=0, updated_at=datetime(2026, 10, 19), updated_by="",
        ))
        with pytest.raises(IntegrityError):
            uow.session.flush()


def test_test_print_without_printer_is_saved(labels):
    client, _ = labels
    body = client.post("/api/stock/labels/test").json()
    assert body["status"] == "saved" and body["copies"] == 1 and body["file"].endswith("FRX.26.0000041.prn")


def test_printers_list_answers_on_any_machine(labels):
    client, _ = labels
    body = client.get("/api/stock/labels/printers").json()
    assert isinstance(body["printers"], list) and isinstance(body["on_windows"], bool)


def test_a_viewer_reads_the_preview_but_cannot_print_or_configure(labels):
    client, lot = labels
    as_viewer("management", "warehouse")
    assert client.get(f"/api/stock/batches/{lot.id}/label.png").status_code == 200
    assert client.get("/api/stock/labels/settings").status_code == 200
    for method, path in (
        ("PUT", "/api/stock/labels/settings"), ("POST", "/api/stock/labels/test"),
        ("POST", f"/api/stock/batches/{lot.id}/label"),
    ):
        assert client.request(method, path, json={}).status_code == 403, (method, path)


def test_a_viewer_is_refused_before_the_option_says_404(warehouse_api, stage):  # noqa: F811
    client, lot = warehouse_api
    stage(**{OPTION: False})
    as_viewer("management", "warehouse")
    assert client.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 1}).status_code == 403


def test_the_receiving_screen_finds_the_lot_by_its_key(labels):
    client, lot = labels
    found = client.get("/api/stock/labels/lot", params={"warehouse": "ЦАР", "grn": lot.grn, "package": ""})
    assert found.status_code == 200 and found.json() == {"lot_id": str(lot.id)}
    later = client.get("/api/stock/labels/lot", params={"warehouse": "ЦАР", "grn": "PPM.26.0000099"})
    assert later.status_code == 404 and "ещё не на складе" in later.json()["detail"]


def test_label_data_is_the_lots_current_balance_package_series_and_arrival_day(labels):
    lot = StockLot.create(
        warehouse="ЦАР", grn="PPM.26.0000002", package="2", name="Клемма JST XH 2.54 мм, 4 pin, розетка",
        article="XHP-4", series="JST-4402", item_type="Комплектующие", purpose="FRX.001", unit="шт",
        quantity=Decimal("120"), storage_location="2B3", source_receiving_ids=("r-1",),
    ).write_off(Decimal("19.5"))
    with app.state.warehouse_uow_factory() as uow:
        uow.warehouse.add(lot)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=lot.id, event_type="warehouse.received",
            actor=Actor(name="ERP Integration", department="System"), occurred_at=datetime(2026, 10, 5, 23, 30),
            details={"grn": lot.grn, "package": "2", "quantity": "120"},
        ))
        uow.commit()
    data = lot_label_data(app.state.warehouse_uow_factory(), lot.id)
    assert data.grn == "PPM.26.0000002-2"  # the scan field reads «ГРН-пакет» back as this lot
    assert (data.sku, data.item_name) == ("XHP-4", "Клемма JST XH 2.54 мм, 4 pin, розетка")
    assert (data.quantity, data.unit, data.manufacturer_batch) == ("100.5", "шт", "JST-4402")
    assert data.received_on.isoformat() == "2026-10-05"


def test_a_lot_without_an_arrival_event_has_no_date(labels):
    _, lot = labels
    data = lot_label_data(app.state.warehouse_uow_factory(), lot.id)
    assert data.received_on is None and data.sku == "" and data.item_name == "Разъём" and data.quantity == "10"
