"""«Сделать эталонной кривой»: one .s2p reference curve per instrument, audited, enforced by the database."""

import base64
import sqlite3
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.production.infrastructure import ProductionInstrumentFileRow
from app.shared.persistence import AuditEventRow
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_box_luhn_migration import _config
from tests.production.test_instruments_api import _as, create


def upload(client, item, name):
    response = client.post(f"/api/production/instruments/{item['id']}/files", json={
        "kind": "Прочее", "file_name": name, "content_base64": base64.b64encode(b"! test\n# Hz S RI R 50\n").decode()})
    assert response.status_code == 200, response.text
    return response.json()["file"]


def mark(client, item, file):
    return client.post(f"/api/production/instruments/{item['id']}/files/{file['id']}/reference")


def files_of(client, item):
    listed = client.get("/api/production/instruments").json()["instruments"]
    return {file["file_name"]: file["is_reference"] for file in next(i for i in listed if i["id"] == item["id"])["files"]}


def test_reference_moves_between_files_of_one_instrument_and_is_audited(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client, "СИ.009")
    first = upload(client, item, "Замер ВАЦ S50180, калибровка АКМ, 16.11.2026, зав. N8B140.s2p")
    second = upload(client, item, "Замер ВАЦ S50180, 18.11.2026, СИ.009.s2p")
    assert files_of(client, item) == {first["file_name"]: False, second["file_name"]: False}
    response = mark(client, item, first)
    assert response.status_code == 200, response.text
    assert response.json()["file"]["is_reference"] is True
    assert mark(client, item, second).status_code == 200
    assert files_of(client, item) == {first["file_name"]: False, second["file_name"]: True}
    assert mark(client, item, second).status_code == 200  # repeating the same mark changes nothing
    with app.state.production_uow_factory().session_factory() as session:
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.instrument_reference_curve_set")).all()
    assert [event.details["previous_file_id"] for event in events] == [None, first["id"]]


def test_only_touchstone_files_of_this_instrument_and_editors(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client, "СИ.009")
    other = create(client, "СИ.008")
    passport = upload(client, item, "паспорт.pdf")
    foreign = upload(client, other, "Замер ВАЦ СИ.008.s2p")
    refused = mark(client, item, passport)
    assert refused.status_code == 422 and ".s2p" in refused.json()["detail"]
    assert mark(client, item, foreign).status_code == 404
    _as({"quality": "view", "production": "view"})
    assert mark(client, other, foreign).status_code == 403


def test_database_allows_one_reference_per_instrument_and_only_s2p(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client, "СИ.009")
    first, second = upload(client, item, "a.s2p"), upload(client, item, "b.s2p")
    pdf = upload(client, item, "c.pdf")
    row = ProductionInstrumentFileRow
    factory = app.state.production_uow_factory().session_factory
    for ids in ([first["id"], second["id"]], [pdf["id"]]):
        with factory() as session:
            with pytest.raises(IntegrityError):
                session.execute(update(row).where(row.id.in_([UUID(value) for value in ids])).values(is_reference=True))
                session.flush()


def test_migration_adds_unmarked_column_with_constraints(tmp_path):
    path = tmp_path / "reference.db"
    config = _config(path)
    command.upgrade(config, "0200_instrument_reference_curve")
    with sqlite3.connect(path) as connection:
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(production_instrument_files)")}
        assert "is_reference" in columns and columns["is_reference"][3] == 1
    command.downgrade(config, "0199_equipment_setup_model")
