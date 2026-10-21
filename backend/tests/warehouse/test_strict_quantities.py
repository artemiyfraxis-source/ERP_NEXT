"""Stage option warehouse.strict_quantities: the row edit may not move inbound / written_off / reserved by hand."""

import json

import pytest

from app.shared import stage_options
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture


@pytest.fixture()
def stage(monkeypatch, tmp_path):
    path = tmp_path / "options.json"
    monkeypatch.setenv(stage_options.ENV, str(path))
    return lambda **options: path.write_text(json.dumps(options), encoding="utf-8")


def test_off_a_quantity_edit_is_allowed(warehouse_api, stage):  # noqa: F811
    client, lot = warehouse_api
    stage(**{"warehouse.strict_quantities": False})
    response = client.put(f"/api/stock/batches/{lot.id}/row", json={"version": lot.version, "inbound": "12"})
    assert response.status_code == 200, response.text
    assert response.json()["lot"]["inbound"] == "12"


@pytest.mark.parametrize("field,value", [("inbound", "12"), ("written_off", "1"), ("reserved", "1")])
def test_on_a_quantity_edit_is_refused(warehouse_api, stage, field, value):  # noqa: F811
    client, lot = warehouse_api
    stage(**{"warehouse.strict_quantities": True})
    response = client.put(f"/api/stock/batches/{lot.id}/row", json={"version": lot.version, field: value})
    assert response.status_code == 422
    assert response.json()["detail"] == "Количества меняются только движениями: списание, возврат, перемещение"


def test_on_other_fields_and_unchanged_quantities_are_allowed(warehouse_api, stage):  # noqa: F811
    client, lot = warehouse_api
    stage(**{"warehouse.strict_quantities": True})
    response = client.put(f"/api/stock/batches/{lot.id}/row", json={
        "version": lot.version, "manufacturer": "Molex", "inbound": "10", "written_off": "0", "reserved": "0",
    })
    assert response.status_code == 200, response.text
    assert response.json()["lot"]["manufacturer"] == "Molex"
