"""ERP deletion history remains authoritative when a frozen mirror is reloaded."""

from dataclasses import replace

import pytest
from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.modules.references.application import (
    EditRecordCommand, FieldEdit, MoveRecordCommand, ReplaceSnapshotCommand, TechRecordInput, delete_record,
    edit_record, move_record, replace_snapshot,
)
from app.modules.references.domain import EQUIPMENT_TABS, TechOperationTab
from app.modules.references.infrastructure import (
    EQUIPMENT_ROWS, EquipmentUnitRow, SqlAlchemyReferencesUnitOfWork, TechOperationRecordRow,
)
from app.modules.references.load_mirror import format_report, load_mirror, read_mirror
from app.shared.persistence import AuditEventRow
from tests.references.test_load_mirror import (
    ACTOR, FILES, NOW, _erp_record, mirror, rows, sessions, uow,  # noqa: F401
)
from tests.references.test_equipment_catalogs import cutover_db  # noqa: F401


@pytest.mark.parametrize("kind,tab", tuple(zip(
    ("Оборудование", "Средства измерений", "Обеспечивающее оборудование", "Аппликаторы", "Матрицы", "Инструмент"),
    EQUIPMENT_TABS,
)))
def test_reload_respects_deletions_in_every_equipment_catalog(tmp_path, uow, sessions, kind, tab):
    source = mirror(tmp_path, **{"БД.ОБ": FILES["БД.ОБ"].replace("Инструмент", kind)})
    load_mirror(source, uow, dry_run=False, now=NOW)
    gone = _erp_record(uow, tab, "DT-8920/Дифманометр")
    delete_record(gone.id, ACTOR, NOW, uow)

    with sessions() as session:
        audit_count = session.scalar(select(func.count()).select_from(AuditEventRow))
    preview = load_mirror(source, uow, dry_run=True, now=NOW)
    report = format_report(preview)
    assert "DT-8920/Дифманометр" in report and "удалено в ERP — пропущено" in report
    assert preview.skipped_deleted["ob"] == [gone.display_text]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == audit_count

    for _ in range(2):
        result = load_mirror(source, uow, dry_run=False, now=NOW)
        assert result.skipped_deleted == preview.skipped_deleted
        assert rows(uow, EQUIPMENT_TABS) == []
        assert rows(uow, TechOperationTab.MATERIAL) == ["ТУТ 4/2"]
        assert sum(result.skipped_edited.values()) == 0
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(EquipmentUnitRow)) == 0
            assert session.scalar(select(func.count()).select_from(EQUIPMENT_ROWS[tab.value])) == 0


@pytest.mark.parametrize("tab,display,family", [
    (TechOperationTab.MATERIAL, "ТУТ 4/2", "mat"),
    (TechOperationTab.OPERATION, "Резка | CUT_WIRE_auto", "op"),
    (TechOperationTab.COAX, "BNC-male | RG58", "coax"),
    (TechOperationTab.TERMINAL, "Molex | 43020, 43640 | Micro-Fit 3.0", "ter"),
    (TechOperationTab.LUG, "КВТ | НВИ 1.5 | НВИ", "ter"),
    (TechOperationTab.WIRE, "UL1061", "prov"),
    (TechOperationTab.CAB_COAX, "RG316", "prov"),
])
def test_reload_respects_ordinary_reference_deletions(tmp_path, uow, tab, display, family):
    source = mirror(tmp_path)
    load_mirror(source, uow, dry_run=False, now=NOW)
    gone = _erp_record(uow, tab, display)
    delete_record(gone.id, ACTOR, NOW, uow)

    result = load_mirror(source, uow, dry_run=False, now=NOW)

    assert display not in rows(uow, tab)
    assert result.skipped_deleted[family] == [display]
    assert sum(result.skipped_edited.values()) == 0
    with uow:
        assert uow.tech_operations.metadata()["record_count"] == 7


@pytest.mark.parametrize("tab,display,field", [
    (TechOperationTab.TOOLS, "DT-8920/Дифманометр", "Инв. номер"),
    (TechOperationTab.OPERATION, "Резка | CUT_WIRE_auto", "opNumber"),
])
def test_natural_identity_still_blocks_a_deleted_row_when_source_provenance_changes(tmp_path, uow, tab, display, field):
    source = mirror(tmp_path)
    load_mirror(source, uow, dry_run=False, now=NOW)
    gone = _erp_record(uow, tab, display)
    delete_record(gone.id, ACTOR, NOW, uow)
    snapshot = read_mirror(source)
    inputs = tuple(replace(item, display_text=item.display_text + " новое имя", sort_key="новая строка")
                   if item.fields.get(field) == gone.fields[field] else item
                   for records in snapshot.tables.values() for item in records)

    replace_snapshot(ReplaceSnapshotCommand(14, None, inputs, ACTOR, NOW, preserve_erp_edits=True), uow)

    assert rows(uow, tab) == []


def test_explicit_replacement_without_preservation_can_load_a_deleted_row(tmp_path, uow):
    source = mirror(tmp_path)
    load_mirror(source, uow, dry_run=False, now=NOW)
    gone = _erp_record(uow, TechOperationTab.MATERIAL, "ТУТ 4/2")
    delete_record(gone.id, ACTOR, NOW, uow)
    snapshot = read_mirror(source)
    replace_snapshot(ReplaceSnapshotCommand(
        14, None, tuple(item for records in snapshot.tables.values() for item in records), ACTOR, NOW,
    ), uow)
    assert rows(uow, TechOperationTab.MATERIAL) == ["ТУТ 4/2"]


def test_a_moved_and_renamed_row_stays_deleted_by_its_original_source_key(tmp_path, uow):
    source = mirror(tmp_path)
    load_mirror(source, uow, dry_run=False, now=NOW)
    gone = _erp_record(uow, TechOperationTab.WIRE, "UL1061")
    move_record(MoveRecordCommand(gone.id, TechOperationTab.CAB_MULTI, ACTOR, NOW), uow)
    edit_record(EditRecordCommand(gone.id, (FieldEdit("Марка", "UL1061", "Новое имя"),), ACTOR, NOW), uow)
    delete_record(gone.id, ACTOR, NOW, uow)

    result = load_mirror(source, uow, dry_run=False, now=NOW)

    assert rows(uow, TechOperationTab.WIRE) == rows(uow, TechOperationTab.CAB_MULTI) == []
    assert result.skipped_deleted["prov"] == ["UL1061"]


def test_deleting_one_source_duplicate_does_not_suppress_the_other(tmp_path, uow):
    source = mirror(tmp_path, **{"БД.МАТ": FILES["БД.МАТ"] + "ТУТ 4/2\tТУТ\r\n"})
    load_mirror(source, uow, dry_run=False, now=NOW)
    with uow:
        first, second = uow.tech_operations.list(tab=TechOperationTab.MATERIAL, query="", limit=10)
    assert first.mirror_key != second.mirror_key
    delete_record(first.id, ACTOR, NOW, uow)

    load_mirror(source, uow, dry_run=False, now=NOW)

    assert rows(uow, TechOperationTab.MATERIAL) == ["ТУТ 4/2"]
    assert _erp_record(uow, TechOperationTab.MATERIAL, "ТУТ 4/2").mirror_key == second.mirror_key


@pytest.mark.parametrize("edited_color", [False, True])
def test_deleting_one_wire_color_preserves_the_other_color(tmp_path, uow, edited_color):
    source = mirror(tmp_path, **{"БД.ПРОВ": "Марка\tAWG\tЦвета\nUL1061\t28\tкрасный, чёрный\n"})
    load_mirror(source, uow, dry_run=False, now=NOW)
    with uow:
        wires = uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=10)
    gone = next(row for row in wires if row.fields["Цвет"] == "красный")
    if edited_color:
        edit_record(EditRecordCommand(gone.id, (FieldEdit("Цвет", "красный", "синий"),), ACTOR, NOW), uow)
    delete_record(gone.id, ACTOR, NOW, uow)

    preview = load_mirror(source, uow, dry_run=True, now=NOW)
    result = load_mirror(source, uow, dry_run=False, now=NOW)

    with uow:
        (kept,) = uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=10)
    assert kept.fields["Цвет"] == "чёрный"
    assert preview.skipped_deleted == result.skipped_deleted == {"prov": ["UL1061"]}
    assert format_report(preview).count("удалено в ERP — пропущено") == 1


@pytest.mark.parametrize("tab", [TechOperationTab.KAB_COAX, TechOperationTab.KAB_AWG])
def test_readonly_spr_kab_rows_are_not_deleted_and_still_reload(tmp_path, uow, tab):
    from tests.references.test_spr_kab import SHEET
    from app.modules.references.domain import ReferenceValidationError

    source = mirror(tmp_path, **{"СПР.КАБ": SHEET})
    load_mirror(source, uow, dry_run=False, now=NOW)
    with uow:
        before = uow.tech_operations.list(tab=tab, query="", limit=100)
    with pytest.raises(ReferenceValidationError, match="только для просмотра"):
        delete_record(before[0].id, ACTOR, NOW, uow)

    result = load_mirror(source, uow, dry_run=False, now=NOW)

    assert len(rows(uow, tab)) == len(before)
    assert result.skipped_deleted == {}


@pytest.mark.parametrize("include_deleted", [False, True])
def test_live_equipment_provenance_wins_over_deleted_inventory_fallback(tmp_path, uow, include_deleted):
    snapshot = read_mirror(mirror(tmp_path))
    original = snapshot.tables["ob"][0]
    first = replace(original, display_text="A", sort_key="A")
    second = replace(original, display_text="B", sort_key="B")
    replace_snapshot(ReplaceSnapshotCommand(14, None, (first, second), ACTOR, NOW), uow)
    kept = _erp_record(uow, TechOperationTab.TOOLS, "A")
    gone = _erp_record(uow, TechOperationTab.TOOLS, "B")
    delete_record(gone.id, ACTOR, NOW, uow)

    replace_snapshot(ReplaceSnapshotCommand(
        14, None, (first, second) if include_deleted else (first,), ACTOR, NOW, preserve_erp_edits=True,
    ), uow)

    assert rows(uow, TechOperationTab.TOOLS) == ["A"]
    assert _erp_record(uow, TechOperationTab.TOOLS, "A").id == kept.id


def test_frozen_pre_cutover_equipment_does_not_override_a_live_deletion(cutover_db):
    path, config, ids = cutover_db
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        unit_of_work = SqlAlchemyReferencesUnitOfWork(factory)
        with unit_of_work:
            gone = unit_of_work.tech_operations.get(ids[-1])
        delete_record(gone.id, ACTOR, NOW, unit_of_work)
        incoming = TechRecordInput(TechOperationTab.EQUIPMENT, gone.display_text, "", gone.source_sheet,
                                   gone.sort_key, gone.fields)

        replace_snapshot(ReplaceSnapshotCommand(
            14, None, (incoming,), ACTOR, NOW, keep_other_tabs=True, preserve_erp_edits=True,
        ), unit_of_work)

        with unit_of_work:
            assert unit_of_work.tech_operations.get(gone.id) is None
            assert all(row.fields["Инв. номер"] != gone.fields["Инв. номер"]
                       for row in unit_of_work.tech_operations.list(tab=EQUIPMENT_TABS, query="", limit=100))
        with factory() as session:
            assert session.get(TechOperationRecordRow, gone.id) is not None  # frozen legacy archive remains
            assert session.get(EquipmentUnitRow, gone.id) is None
    finally:
        engine.dispose()
