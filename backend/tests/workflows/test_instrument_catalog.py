"""Workflow composition, stale Excel, frozen writers and linked model descriptions."""

from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.references.infrastructure import EquipmentMeasuringRow, EquipmentTemplateToolRow, EquipmentUnitLinkRow
from app.modules.references.domain import ReferenceValidationError
from app.shared.persistence import AuditEventRow
from app.workflows.search_api import _database, _equipment
from tests.references.test_instrument_catalog import (  # noqa: F401
    baseline, database, catalog_api, first, as_user, ROOT, MANIFEST,
)
from tests.references.test_roundtrip import _download, _edited, _upload


def test_linked_models_templates_and_search_use_live_descriptions(catalog_api):
    entry = MANIFEST["entries"][0]
    old = catalog_api.get(ROOT + "/instruments/" + entry["unit_id"]).json()["instrument"]
    response = catalog_api.patch(ROOT + "/instruments/" + old["id"], json={
        "version": old["version"], "name": "Каноническое имя", "article": "Живой артикул", "inventory_number": "СИ.999"})
    assert response.status_code == 200, response.text
    with app.state.references_uow_factory() as uow:
        record = uow.tech_operations.get(UUID(entry["unit_id"]))
        model = next(item for item in uow.equipment.models() if item["id"] == str(record.equipment_model_id))
        assert "Каноническое имя" in model["display"] and model["article"] == "Живой артикул"
        assert "СИ.999" in model["inventory_numbers"]
        uow.session.add(EquipmentTemplateToolRow(template_id="catalog-test", position=0, tool_name="Старое имя",
            inventory_number="СИ.000", model_id=record.equipment_model_id, candidates=1))
        uow.session.flush()
        tool = next(item for item in uow.equipment.template_tools() if item["template_id"] == "catalog-test")
        assert "Каноническое имя" in tool["tool_name"] and tool["inventory_number"] == "СИ.999"
        results = _database(uow.session, ["каноническое"], [], {"erpref"})
        assert any(item["params"].get("instrument") == old["id"] for item in results)
        assert all(item["params"].get("equipment") != entry["unit_id"] for item in _equipment(uow.session, ["каноническое"]))


@pytest.mark.parametrize("service", [False, True])
def test_excel_custom_values_use_canonical_audit_and_identity_is_denied(catalog_api, service):
    client = catalog_api
    assert client.post(ROOT + "/tech-operations/columns", json={"tab": "ob_measuring", "name": "Полка"}).status_code == 201
    as_user({"erpref": "edit"}, service=service)
    content = _download(client, "ob_measuring")
    edited = _edited(content, lambda rows: rows[1].__setitem__(rows[0].index("Полка"), "Верхняя"))
    preview = _upload(client, edited, dry_run=1, table="ob_measuring").json()
    assert len(preview["updated"]) == 1 and not preview["errors"], preview
    result = _upload(client, edited, dry_run=0, table="ob_measuring").json()
    assert len(result["updated"]) == 1 and not result["errors"], result
    assert _upload(client, edited, dry_run=0, table="ob_measuring").json()["conflicts"]
    content = _download(client, "ob_measuring")
    edited = _edited(content, lambda rows: rows[1].__setitem__(rows[0].index("Наименование"), "Недопустимо"))
    result = _upload(client, edited, dry_run=0, table="ob_measuring").json()
    assert result["errors"] and not result["updated"], result
    with app.state.references_uow_factory() as uow:
        assert any(event.details.get("source") == "excel" and event.details.get("after", {}).get("custom_fields", {}).get("Полка") == "Верхняя"
                   for event in uow.session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.instrument_updated")))


def test_excel_quality_identity_change_and_invisible_version_conflict(catalog_api):
    client = catalog_api
    content = _download(client, "ob_measuring")
    changed = _edited(content, lambda rows: rows[1].__setitem__(rows[0].index("Наименование"), "Из файла ОТК"))
    result = _upload(client, changed, dry_run=0, table="ob_measuring").json()
    assert len(result["updated"]) == 1 and not result["errors"], result
    content = _download(client, "ob_measuring")
    item = first(client)
    assert client.patch(ROOT + "/instruments/" + item["id"], json={"version": item["version"], "vna_app": ""}).status_code == 200
    changed = _edited(content, lambda rows: rows[1].__setitem__(rows[0].index("Наименование"), "Устарело"))
    assert _upload(client, changed, dry_run=0, table="ob_measuring").json()["conflicts"]


def test_snapshot_reloads_cannot_wipe_or_resurrect_measuring_rows(catalog_api):
    client = catalog_api
    before = client.get(ROOT + "/instruments").json()["instruments"]
    # The legacy snapshot endpoint is admin-only; exercise its transaction-neutral storage path.
    with app.state.references_uow_factory() as uow:
        units = list(uow.session.scalars(select(EquipmentMeasuringRow)))
        uow.session.add(EquipmentUnitLinkRow(unit_id=units[0].id, kind="tool", target_id=units[1].id))
        uow.commit()
        source, target = units[0].id, units[1].id
    with app.state.references_uow_factory() as uow:
        uow.tech_operations.replace((), schema_version=14, source_updated_at=None,
            imported_at=datetime(2026, 11, 23))
        uow.commit()
    assert client.get(ROOT + "/instruments").json()["instruments"] == before
    with app.state.references_uow_factory() as uow:
        assert len(list(uow.session.scalars(select(EquipmentMeasuringRow)))) == 15
        link = uow.session.get(EquipmentUnitLinkRow, (source, "tool", target))
        assert link is not None and link.target_id == target
        row = uow.session.scalars(select(EquipmentMeasuringRow)).first()
        row.fields = {**row.fields, "Наименование": "Запрещено"}
        with pytest.raises(ReferenceValidationError, match="неизменяем"):
            uow.session.flush()


def test_existing_bindings_remain_idempotent_after_canonical_kind_change(catalog_api):
    from app.workflows.equipment_instruments import bind_reviewed, binding_report, instrument_passports
    from tests.references.test_instrument_catalog import ACTOR
    entry = next(item for item in MANIFEST["entries"] if item["outcome"] == "new")
    row = catalog_api.get(ROOT + "/instruments/" + entry["unit_id"]).json()["instrument"]
    response = catalog_api.patch(ROOT + "/instruments/" + row["id"], json={"version": row["version"],
        "kind": "Принадлежность", "status": "Не используется", "quantity": 4})
    assert response.status_code == 200, response.text
    with app.state.references_uow_factory() as uow:
        assert bind_reviewed(uow.session, [entry], ACTOR) == 0
        assert next(row for row in binding_report(uow.session) if row["unit_id"] == entry["unit_id"])["resolved"]
        assert instrument_passports(uow.session, [UUID(entry["unit_id"])])[UUID(entry["unit_id"])]["instrument_id"] == entry["instrument_id"]
