"""Лист закупки: DATASHEET строки и её файлы РАЗРЕШЕНИЕ / ДОК. КАЧЕСТВА (procurement_files.kind)."""

import base64
from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.procurement.domain import (
    DATASHEET_MAX_LENGTH,
    ProcurementStatus,
    ProcurementValidationError,
    create_line_from_requested,
    split_line,
)
from app.modules.procurement.infrastructure import ProcurementFileRow
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.procurement.test_api import awaiting_invoice, procurement_api, rates  # noqa: F401 — fixtures
from tests.procurement.test_application import requested_message
from tests.procurement.test_invoice_upload import INVOICE, _upload

BUYER = Actor(name="Петров", department="Закупки")
OTHER = Actor(name="Сидоров", department="Закупки")
NOW = datetime(2026, 9, 16, 9, 0)
PDF = b"%PDF-1.4 quality"


def line_in(status: ProcurementStatus, **fields):
    line = create_line_from_requested(requested_message())
    return replace(line, status=status, buyer=None if status is ProcurementStatus.NEW else BUYER, **fields)


# --- domain ----------------------------------------------------------------------------------------------------

def test_datasheet_is_the_buyers_own_field_in_the_note_window():
    line = line_in(ProcurementStatus.IN_WORK)

    saved = line.set_datasheet("  https://example.test/ds.pdf  ", BUYER)
    assert (saved.datasheet, saved.version) == ("https://example.test/ds.pdf", line.version + 1)
    assert saved.set_datasheet("https://example.test/ds.pdf", BUYER) is saved
    assert saved.set_datasheet("   ", BUYER).datasheet is None
    # a free text is kept as typed — the legacy sheet held names of files as well as links
    assert line.set_datasheet("PDF у поставщика по запросу", BUYER).datasheet == "PDF у поставщика по запросу"

    with pytest.raises(ProcurementValidationError, match="длиннее"):
        line.set_datasheet("x" * (DATASHEET_MAX_LENGTH + 1), BUYER)
    with pytest.raises(ProcurementValidationError, match="свои строки"):
        line.set_datasheet("ds", OTHER)
    with pytest.raises(ProcurementValidationError, match="возьмите строку в работу"):
        line_in(ProcurementStatus.NEW).set_datasheet("ds", BUYER)
    with pytest.raises(ProcurementValidationError, match="отменена"):
        line_in(ProcurementStatus.CANCELLED).set_datasheet("ds", BUYER)
    # closed lines keep it editable, as КОММЕНТАРИЙ: the datasheet often comes with the goods
    assert line_in(ProcurementStatus.DONE).set_datasheet("ds", BUYER).datasheet == "ds"


def test_quality_file_turns_the_quality_flag_yes_once_and_a_permit_leaves_the_line():
    line = line_in(ProcurementStatus.IN_WORK, quality_document="Нет")

    with_quality = line.attach_document("quality", BUYER)
    assert (with_quality.quality_document, with_quality.version) == ("Да", line.version + 1)
    assert with_quality.attach_document("quality", BUYER) is with_quality
    assert line.attach_document("permit", BUYER) is line

    with pytest.raises(ProcurementValidationError, match="Вид файла"):
        line.attach_document("other", BUYER)
    with pytest.raises(ProcurementValidationError, match="свои строки"):
        line.attach_document("permit", OTHER)
    with pytest.raises(ProcurementValidationError, match="отменена"):
        line_in(ProcurementStatus.CANCELLED).attach_document("quality", BUYER)


def test_split_copies_keep_the_datasheet():
    line = line_in(ProcurementStatus.IN_WORK, datasheet="https://example.test/ds.pdf")
    copies = split_line(line, 2, [line.legacy_id], NOW.date(), NOW)
    assert [copy.datasheet for copy in copies] == ["https://example.test/ds.pdf"] * 2


# --- API -------------------------------------------------------------------------------------------------------

def taken(client, line) -> dict:
    return client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).json()["lines"][0]


def attach(client, line_id, kind: str, name: str = "паспорт.pdf", content: bytes = PDF):
    return client.post(f"/api/procurement/{line_id}/files", json={
        "kind": kind, "file_name": name, "content_type": "application/pdf",
        "content_base64": base64.b64encode(content).decode(),
    })


def sheet_line(client, line_id) -> dict:
    (found,) = [item for item in client.get("/api/procurement/lines").json()["lines"] if item["id"] == str(line_id)]
    return found


def test_datasheet_is_saved_by_version_and_returned_by_every_list(procurement_api):
    client, line, _ = procurement_api
    current = taken(client, line)

    saved = client.patch(f"/api/procurement/{line.id}/datasheet", json={
        "version": current["version"], "datasheet": "https://example.test/ds.pdf",
    })
    stale = client.patch(f"/api/procurement/{line.id}/datasheet", json={
        "version": current["version"], "datasheet": "другое",
    })

    assert saved.status_code == 200, saved.text
    assert saved.json()["line"]["datasheet"] == "https://example.test/ds.pdf"
    assert stale.status_code == 409
    assert sheet_line(client, line.id)["datasheet"] == "https://example.test/ds.pdf"
    (mine,) = client.get("/api/procurement/mine").json()["lines"]
    assert mine["datasheet"] == "https://example.test/ds.pdf"


def test_permit_and_quality_files_are_listed_by_kind_and_quality_sets_the_flag(procurement_api, rates):
    client, line, sessions = procurement_api
    version = awaiting_invoice(client, line)
    # an invoice file of the line is «прочее»: not listed among the permit and quality files
    assert _upload(client, line.id).status_code == 200
    assert sheet_line(client, line.id)["quality_document"] == "Нет"

    permit = attach(client, line.id, "permit", "Разрешение 12.pdf")
    assert permit.status_code == 200, permit.text
    assert permit.json()["line"]["version"] == version  # a permit changes nothing on the line
    quality = attach(client, line.id, "quality", "Паспорт качества.pdf")
    assert quality.status_code == 200, quality.text
    body = quality.json()

    assert body["file"]["kind"] == "quality" and body["file"]["file_name"] == "Паспорт качества.pdf"
    assert (body["line"]["quality_document"], body["line"]["version"]) == ("Да", version + 1)
    assert [item["file_name"] for item in body["line"]["permit_files"]] == ["Разрешение 12.pdf"]
    assert [item["file_name"] for item in body["line"]["quality_files"]] == ["Паспорт качества.pdf"]
    listed = client.get(f"/api/procurement/{line.id}/files").json()["files"]
    assert [(item["kind"], item["file_name"]) for item in listed] == [
        ("permit", "Разрешение 12.pdf"), ("quality", "Паспорт качества.pdf"),
    ]
    shown = sheet_line(client, line.id)
    assert ([item["url"] for item in shown["permit_files"]], len(shown["quality_files"])) == (
        [permit.json()["file"]["url"]], 1,
    )
    downloaded = client.get(body["file"]["url"])
    assert downloaded.status_code == 200 and downloaded.content == PDF
    # the second quality file adds a file, the line stays as it is
    again = attach(client, line.id, "quality", "Сертификат.pdf").json()
    assert again["line"]["version"] == version + 1 and len(again["line"]["quality_files"]) == 2

    with sessions() as session:
        kinds = sorted(session.scalars(select(ProcurementFileRow.kind)))
        audited = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "procurement.document_file_uploaded")
        ).all()
    assert kinds == ["other", "permit", "quality", "quality"]
    assert [event.details["kind"] for event in audited] == ["permit", "quality", "quality"]


def test_line_files_are_refused_for_a_bad_kind_a_new_or_foreign_line_and_a_bad_file(procurement_api, monkeypatch):
    client, line, sessions = procurement_api

    new_line = attach(client, line.id, "quality")
    assert new_line.status_code == 422 and "возьмите строку в работу" in new_line.json()["detail"]
    taken(client, line)
    assert attach(client, line.id, "other").status_code == 422
    assert attach(client, line.id, "invoice").status_code == 422
    assert attach(client, "00000000-0000-0000-0000-000000000000", "permit").status_code == 404
    damaged = client.post(f"/api/procurement/{line.id}/files", json={
        "kind": "permit", "file_name": "x.pdf", "content_base64": "не base64",
    })
    assert damaged.status_code == 422
    monkeypatch.setattr("app.modules.procurement.application.INVOICE_FILE_LIMIT", 3)
    assert attach(client, line.id, "permit", content=INVOICE).status_code == 422
    monkeypatch.undo()

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=OTHER, email="sidorov@example.test", roles=frozenset({"buyer"}), permissions={"procurement": "edit"},
    )
    foreign = attach(client, line.id, "quality")
    assert foreign.status_code == 422 and "свои строки" in foreign.json()["detail"]
    with sessions() as session:
        assert session.scalars(select(ProcurementFileRow)).all() == []
