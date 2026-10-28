"""«Данные отправок → УПД сканом или файлом ЭДО» (owner 28.10.2026): the warehouse scan machinery under the SHIPPING edit
right, scoped to a shipment, and the answer landing in the shipment (УПД file, type, №, date, invoice №)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.modules.warehouse.infrastructure import WarehouseReceiptFileRow
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.shipping.test_documents_api import _shipment, _stocked
from tests.warehouse.test_receipts import NOW, stand  # noqa: F401 — fixture
from tests.warehouse.test_scan_jobs import old_agent_alive

EDO = (Path(__file__).parents[1] / "purchasing" / "fixtures" / "edo" / "upd-503.xml").read_bytes()
PDF = b"%PDF-1.4\n% scan of a paper UPD\n"


def _as(role: str = "pm", **permissions: str) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Ульяна", department="Менеджмент"), email="pm@example.test", roles=frozenset({role}),
        permissions=permissions,
    )


@pytest.fixture()
def desk(stand):  # noqa: F811
    client, sessions = stand
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    _as(shipping="edit", scan="edit")
    yield client, sessions
    del app.state.shipping_uow_factory


def _new_shipment(client, **extra):
    _as("admin", shipping="edit", scan="edit")  # packing is the storekeeper's / admin's; the УПД is the manager's
    good = _stocked(client, accepted="5")
    made = _shipment(client, [{"good_id": good["id"], "quantity": "5"}], **{"upd_type": None, "upd_number": "", **extra})
    _as(shipping="edit", scan="edit")
    return made


def _paper_scan(sessions) -> str:
    file_id = uuid4()
    with sessions() as session:
        session.add(WarehouseReceiptFileRow(id=file_id, file_name="УПД.pdf", content_type="application/pdf", size=len(PDF),
                                            content=PDF, uploaded_by="Кладовщик", uploaded_at=NOW, recognition=""))
        session.commit()
    return f"/api/stock/receipts/files/{file_id}"


def _url(shipment, tail: str) -> str:
    return f"/api/shipping/shipments/{shipment['id']}/upd-scan/{tail}"


def _get(client, shipment):
    return next(item for item in client.get("/api/shipping/shipments").json()["shipments"] if item["id"] == shipment["id"])


def test_the_manager_with_the_shipping_right_scans_but_a_storekeeper_only_and_a_viewer_are_refused(desk):
    client, sessions = desk
    shipment = _new_shipment(client)
    old_agent_alive(sessions, SimpleNamespace(now=NOW))

    started = client.post(_url(shipment, "jobs"))
    assert started.status_code == 200 and started.json()["status"] == "pending", started.text
    polled = client.get(_url(shipment, f"jobs/{started.json()['id']}")).json()
    assert polled["status"] == "pending" and polled["result"] is None
    assert client.delete(_url(shipment, f"jobs/{started.json()['id']}")).json()["status"] == "failed"

    for permissions in ({"warehouse": "edit"}, {"shipping": "view"}):
        _as(**permissions)
        for method, tail in (("POST", "jobs"), ("GET", f"jobs/{uuid4()}"), ("DELETE", f"jobs/{uuid4()}"),
                             ("POST", "file"), ("POST", "merge"), ("POST", "apply")):
            assert client.request(method, _url(shipment, tail), json={}).status_code == 403, (permissions, method, tail)
    _as(shipping="edit")  # the shipping right without «Сканирование документов»: no scanner, «Файл…» stays
    for method, tail in (("POST", "jobs"), ("GET", f"jobs/{uuid4()}"), ("DELETE", f"jobs/{uuid4()}")):
        response = client.request(method, _url(shipment, tail), json={})
        assert response.status_code == 403 and "Сканирование" in response.text, (method, tail)
    assert client.post(_url(shipment, "file"), json={}).status_code != 403


def test_no_agent_is_answered_as_the_warehouse_does_and_an_unknown_shipment_is_404(desk):
    client, _ = desk
    shipment = _new_shipment(client)
    assert client.post(_url(shipment, "jobs")).json()["status"] == "no_agent"
    assert client.post(f"/api/shipping/shipments/{uuid4()}/upd-scan/jobs").status_code == 404


def test_an_edo_xml_is_read_exactly_and_lands_in_the_shipment_with_its_number_date_and_the_projects_invoice(desk):
    client, _ = desk
    shipment = _new_shipment(client)

    read = client.post(_url(shipment, "file"), files={"file": ("upd.xml", EDO, "application/xml")})
    assert read.status_code == 200, read.text
    answer = read.json()["result"]
    assert answer["recognised"]["upd_number"] == "РЭ-0915"

    applied = client.post(_url(shipment, "apply"), json={
        "version": shipment["version"], "scan_file": answer["scan_file"], "upd_number": answer["recognised"]["upd_number"],
        "upd_date": answer["recognised"]["upd_date"], "invoice_number": answer["recognised"]["invoice_number"],
    })
    assert applied.status_code == 200, applied.text
    body = applied.json()
    result = body["shipment"]
    assert body["status"] == "applied" and body["invoice"] == "РЭ-777"
    assert (result["upd_type"], result["upd_number"], result["upd_date"]) == ("ЭДО", "РЭ-0915", "2026-09-15")
    assert result["version"] == shipment["version"] + 2
    assert result["upd_url"].startswith("/api/shipping/files/")
    downloaded = client.get(result["upd_url"])
    assert downloaded.status_code == 200 and downloaded.content == EDO
    card = client.get("/api/shipping/project-addressee", params={"project": "591К.3"}).json()["addressee"]
    assert card["invoice"] == "РЭ-777"


def test_a_paper_scan_becomes_the_paper_upd_file_and_a_filled_invoice_is_kept(desk):
    client, sessions = desk
    shipment = _new_shipment(client)
    client.put("/api/shipping/project-addressee", json={"project": "591К.3", "invoice": "СЧ-1"})
    shipment = _get(client, shipment)

    applied = client.post(_url(shipment, "apply"), json={
        "version": shipment["version"], "scan_file": _paper_scan(sessions), "upd_number": "771", "upd_date": "2026-10-20",
        "invoice_number": "СЧ-45",
    }).json()

    assert applied["invoice"] == ""
    result = applied["shipment"]
    assert (result["upd_type"], result["upd_number"], result["upd_date"]) == ("БУМАГА", "771", "2026-10-20")
    assert client.get(result["upd_url"]).content == PDF
    assert client.get("/api/shipping/project-addressee", params={"project": "591К.3"}).json()["addressee"]["invoice"] == "СЧ-1"


def test_a_different_number_already_on_the_shipment_is_never_overwritten_silently(desk):
    client, sessions = desk
    shipment = _new_shipment(client, upd_number="ОТ РУКИ-5")
    scan = _paper_scan(sessions)
    ask = {"version": shipment["version"], "scan_file": scan, "upd_number": "771", "upd_date": "2026-10-20"}

    conflict = client.post(_url(shipment, "apply"), json=ask).json()
    assert conflict == {"status": "conflict", "current": "ОТ РУКИ-5", "found": "771"}
    assert _get(client, shipment)["upd_number"] == "ОТ РУКИ-5" and _get(client, shipment)["upd_url"] == ""

    kept = client.post(_url(shipment, "apply"), json={**ask, "number": "keep"}).json()["shipment"]
    assert (kept["upd_number"], kept["upd_date"], kept["upd_type"]) == ("ОТ РУКИ-5", None, "БУМАГА") and kept["upd_url"]

    replaced = client.post(_url(shipment, "apply"), json={**ask, "version": kept["version"], "number": "replace"}).json()["shipment"]
    assert (replaced["upd_number"], replaced["upd_date"]) == ("771", "2026-10-20")


def test_the_same_number_is_not_a_conflict(desk):
    client, sessions = desk
    shipment = _new_shipment(client, upd_number="771")
    applied = client.post(_url(shipment, "apply"), json={
        "version": shipment["version"], "scan_file": _paper_scan(sessions), "upd_number": "771", "upd_date": "2026-10-20",
    }).json()
    assert applied["status"] == "applied" and applied["shipment"]["upd_date"] == "2026-10-20"


def test_a_stale_version_is_409_and_nothing_changes_and_a_missing_scan_is_404(desk):
    client, sessions = desk
    shipment = _new_shipment(client)
    scan = _paper_scan(sessions)

    stale = client.post(_url(shipment, "apply"), json={"version": shipment["version"] + 5, "scan_file": scan, "upd_number": "1"})
    gone = client.post(_url(shipment, "apply"), json={"version": shipment["version"],
                                                      "scan_file": "/api/stock/receipts/files/" + str(uuid4())})
    assert stale.status_code == 409
    assert gone.status_code == 404
    assert _get(client, shipment)["version"] == shipment["version"] and _get(client, shipment)["upd_url"] == ""


def test_the_apply_is_audited_like_the_upd_file_upload(desk):
    client, sessions = desk
    shipment = _new_shipment(client)
    client.post(_url(shipment, "apply"), json={"version": shipment["version"], "scan_file": _paper_scan(sessions), "upd_number": "9"})
    from app.shared.persistence import AuditEventRow

    with sessions() as session:
        kinds = {row.event_type for row in session.scalars(select(AuditEventRow))}
    assert {"shipping.documents_updated", "shipping.upd_attached"} <= kinds
