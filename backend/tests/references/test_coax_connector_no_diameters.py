"""БД.КОАКС без D1–D3 (владелец 16.10.2026: «разъём тянет L1–L3, а провод — свои данные, так что показатель по D1 из
разъёмов можно убирать, оставить только подходящий тип»): строка разъёма не хранит, не показывает и не правит D1–D3 —
они у кабеля (СПР.КАБ / «Коаксиальные кабели»), и ЭТК берёт их оттуда."""

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.references.application import (CreateRecordCommand, EditRecordCommand, FieldEdit, create_record,
                                                 edit_record)
from app.modules.references.domain import (ReferenceValidationError, StaleRecordError, TechOperationRecord,
                                           TechOperationTab)
from app.modules.references.importers import import_rows
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.shared.bulk_import import ImportRowIn
from app.shared.http_auth import Principal, authenticated_principal
from app.workflows import techcard_build
from tests.references.test_load_mirror import sessions  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[2]
MIRROR = ROOT / "data" / "reference-mirror"
NOW = datetime(2026, 10, 16, 12, 0)
D_KEYS = {"D1", "D2", "D3", "d1", "d2", "d3"}


@pytest.fixture()
def base(sessions):  # noqa: F811
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(MIRROR, factory(), dry_run=False, now=NOW)
    return factory


def _coax(factory) -> list[TechOperationRecord]:
    with factory() as uow:
        return list(uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=10_000))


def _old_row(factory, **extra: str) -> TechOperationRecord:
    """A row as stored before 16.10: the sheet's D1–D3 kept on the connector."""
    record = TechOperationRecord.create(
        tab=TechOperationTab.COAX, display_text="SMA-OLD", search_text="", source_sheet="БД.КОАКС", sort_key="SMA-OLD",
        fields={"Артикул": "SMA-OLD", "Провод": "RG-178", "D1": "9", "D2": "9", "D3": "9", "L1": "4",
                "coaxArticle": "SMA-OLD", "coaxWire": "RG-178", **extra}, edited_in_erp=False)
    with factory() as uow:
        uow.tech_operations.save(record)
        uow.commit()
    return record


def test_the_mirror_load_keeps_no_cable_diameters_on_connector_rows(base):
    rows = _coax(base)
    assert rows and not any(D_KEYS & set(record.fields) for record in rows)
    assert any(record.fields.get("L1") for record in rows)  # the connector's own lengths stay


def test_the_coax_tab_shows_no_d_columns(base):
    _old_row(base)
    app.state.references_uow_factory = base
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="engineer@example.test", roles=frozenset({"engineer"}), permissions={"erpref": "view"})
    try:
        body = TestClient(app).get("/api/references/tech-operations", params={"tab": "coax", "limit": 5000}).json()
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
        del app.state.references_uow_factory
    assert "L1" in body["columns"] and not D_KEYS & set(body["columns"])


def test_a_d_value_is_refused_and_an_emptied_d_leaves_the_row(base):
    record = _old_row(base)
    with pytest.raises(StaleRecordError, match="Коаксиальные кабели"):
        edit_record(EditRecordCommand(record.id, (FieldEdit("D1", "9", "1"),), ACTOR, NOW), base())
    updated = edit_record(EditRecordCommand(record.id, tuple(FieldEdit(name, "9", "") for name in ("D1", "D2", "D3")),
                                            ACTOR, NOW), base())
    assert not D_KEYS & set(updated.fields) and updated.fields["L1"] == "4"
    # The field is gone with its value: a second clean-up of the row is refused, nothing is written.
    with pytest.raises(StaleRecordError, match="D1: поля нет в записи"):
        edit_record(EditRecordCommand(record.id, (FieldEdit("D1", "9", ""),), ACTOR, NOW), base())


def test_a_new_row_takes_no_d(base):
    with pytest.raises(ReferenceValidationError, match="Коаксиальные кабели"):
        create_record(CreateRecordCommand(TechOperationTab.COAX, {"Артикул": "SMA-NEW", "Провод": "RG-178", "D1": "1"},
                                          ACTOR, NOW), base())
    created = create_record(CreateRecordCommand(TechOperationTab.COAX, {"Артикул": "SMA-NEW", "Провод": "RG-178", "D1": "",
                                                                        "L1": "3"}, ACTOR, NOW), base())
    assert not D_KEYS & set(created.fields)


def test_the_import_has_no_d_field_for_a_connector(base):
    _old_row(base)  # even while an old row still carries them, D1–D3 are no field of the table
    with base() as uow:
        report = import_rows("coax", uow, [
            ImportRowIn(fields={"coaxArticle": "SMA-IMP", "coaxWire": "RG-178", "D1": "0,25"}),
            ImportRowIn(fields={"coaxArticle": "SMA-IMP2", "coaxWire": "RG-178", "L1": "2"}),
        ], ACTOR, NOW, dry_run=False, source="erp")
    assert report.created == ["SMA-IMP2 / RG-178"]
    assert [(item.key, item.reason) for item in report.refused] == [("SMA-IMP / RG-178", "неизвестное поле: D1")]
    created = next(record for record in _coax(base) if record.fields.get("coaxArticle") == "SMA-IMP2")
    assert not D_KEYS & set(created.fields)


def test_the_etk_build_takes_d_from_the_cable_not_from_the_connector_row(base):
    _old_row(base)
    config = {"mode": "coax", "ops": [], "coax": {"cableArt": "RG-178", "sideA": {"article": "SMA-OLD", "d1": "9"}}}
    built, stamp = techcard_build.with_technology(config, base)
    assert stamp is not None
    side = built["coax"]["sideA"]
    assert (side["d1"], side["d2"], side["d3"]) == ("0,25", "0,87", "1,3")  # СПР.КАБ RG-178
    built, _stamp = techcard_build.with_technology({**config, "coax": {**config["coax"], "cableArt": "НЕТ-КАБЕЛЯ"}}, base)
    assert not {"d1", "d2", "d3"} & set(built["coax"]["sideA"])
