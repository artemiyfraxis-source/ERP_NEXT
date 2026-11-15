"""Explicit physical-unit mapping, shared live-reference rewrite and atomic registry sync."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.modules.production.infrastructure import ProductionInstrumentRow, SqlAlchemyInstrumentRepository
from app.modules.references.equipment import MEASURING_TYPE, SUPPORTING_TYPE
from app.modules.references.infrastructure import EquipmentModelRow, EquipmentRecordRow
from app.workflows.equipment_reclassify import AUDIT_EVENT, format_report, main, run, validate_mapping
from tests.workflows.test_applicator_inventory_fix import engine, seed, snapshot, unit  # noqa: F401


def mapping(before="ОБ.106", after="СИ.106", kind=MEASURING_TYPE, **extra):
    return {"from": before, "to": after, "type": kind, **extra}


def instrument(number):
    return ProductionInstrumentRow(id=uuid4(), version=3, inventory_number=number, name="Прибор",
        instrument_type="Мультиметр", status="Не используется", verification_number=None,
        verified_at=None, verification_due=None, serial_number="SERIAL-123", manufacturer="Изготовитель")


@pytest.mark.parametrize("value", [
    {}, [None], [{}], [{"from": "ОБ.1", "extra": "unknown"}],
    [mapping(before=None)], [mapping(before="ОБ.1.X")], [mapping(before="ОБ.0")],
    [mapping(before="ОБ.1.0")], [mapping(before="ОБ.1.2.3")], [mapping(after="")],
    [mapping(kind="Случайный тип")], [mapping(after=None, kind=None)],
    [mapping(registry_existing="true")], [mapping(after=None, registry_existing=True)],
    [mapping(), mapping(before="об.106")], [mapping(), mapping(before="ОБ.107")],
    [mapping(after="ОБ.107"), mapping(before="ОБ.107", after="СИ.107")],
    [mapping(after="ОБ.107"), mapping(before="ОБ.107", after="ОБ.106")],
    [mapping(after="ОБ.106")], [{"role": "operational", "type": SUPPORTING_TYPE}],
    [{"role": "supporting", "type": SUPPORTING_TYPE, "to": None}],
    [{"role": "supporting", "type": SUPPORTING_TYPE}] * 2,
])
def test_invalid_mapping_is_rejected(value):
    with pytest.raises(ValueError):
        validate_mapping(value)


@pytest.mark.parametrize("numbers, entries, message", [
    (["ОБ.107"], [mapping()], "не найден"),
    (["ОБ.106", "ОБ.106"], [mapping()], "неоднозначен"),
    (["ОБ.106", "СИ.106"], [mapping()], "уже занят"),
    (["СИ.106"], [mapping()], "не найден"),
])
def test_bad_catalog_mapping_aborts_without_writes(engine, numbers, entries, message):
    with Session(engine) as session, session.begin():
        session.add_all(unit(session, number, kind="Оборудование") for number in numbers)
    before = snapshot(engine)
    with pytest.raises(ValueError, match=message):
        run(engine, entries, apply=True)
    assert snapshot(engine) == before


def test_dry_run_has_no_writes_and_reports_types_registry_and_issued_etks(engine):
    seed(engine)
    with Session(engine) as session, session.begin():
        session.add(instrument("ОБ.106"))
    before = snapshot(engine)
    writes = []

    def record(_connection, _cursor, statement, _params, _context, _many):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    report = run(engine, [mapping()])
    event.remove(engine, "before_cursor_execute", record)
    assert writes == [] and snapshot(engine) == before
    assert report.tables["production_instruments"].changed == 1
    assert report.tables["td_issues"].changed == 1
    assert report.issued_documents_to_refresh == 1
    text = format_report(report)
    assert "ОБ.106 → СИ.106" in text and "Тип: Аппликаторы → Средства измерений" in text
    assert "Реестр СИ: ОБ.106 → СИ.106" in text and "Таблица | Просмотрено строк" in text


def test_shared_rewrite_and_registry_keep_uuids_and_frozen_documents_and_repeat_is_noop(engine, monkeypatch):
    ids = seed(engine)
    with Session(engine) as session, session.begin():
        row = instrument("ОБ.106")
        registry_id = row.id
        session.add(row)
    before = snapshot(engine)
    calls = []
    save = SqlAlchemyInstrumentRepository.save

    def tracked_save(self, value):
        calls.append(value.id)
        return save(self, value)

    monkeypatch.setattr(SqlAlchemyInstrumentRepository, "save", tracked_save)
    planned = run(engine, [mapping()])
    assert run(engine, [mapping()], apply=True) == planned
    assert calls == [registry_id]
    after = snapshot(engine)
    for table in ("equipment_unit_links", "equipment_operation_links", "techcard_revisions",
                  "production_resource_reservations", "production_active_resources"):
        assert after[table] == before[table]
    assert [event for event in after["audit_events"] if event["event_type"] != AUDIT_EVENT] == before["audit_events"]
    assert len([event for event in after["audit_events"] if event["event_type"] == AUDIT_EVENT]) == 1
    for old in before["techcard_projects"]:
        new = next(item for item in after["techcard_projects"] if item["id"] == old["id"])
        assert new["document"] == old["document"] and new["revision"] == old["revision"]
        assert "СИ.106" in str(new["inputs"]) and new["version"] == old["version"] + 1
    for old in before["td_issues"]:
        new = next(item for item in after["td_issues"] if item["id"] == old["id"])
        assert (old == new) == (old["status"] != "draft")
    with Session(engine) as session:
        row = session.get(EquipmentRecordRow, ids["unit"])
        assert row.fields["Тип"] == MEASURING_TYPE and row.fields["Инв. номер"] == "СИ.106"
        assert row.fields["Код в базе терминалов"] == " m 10 "  # only applicator repair changes its code
        assert row.mirror_key == ids["mirror"] and row.equipment_model_id == ids["model"]
        assert row.edited_in_erp and "си.106" in row.normalized_search and "средства измерений" in row.normalized_search
        registered = session.get(ProductionInstrumentRow, registry_id)
        assert registered.inventory_number == "СИ.106" and registered.version == 4
        assert registered.serial_number == "SERIAL-123"
    repeated = run(engine, [mapping()], apply=True)
    assert repeated.already_applied == ["ОБ.106"]
    assert not repeated.changes and not repeated.type_changes and not repeated.registry_changes
    assert all(count.changed == 0 for count in repeated.tables.values())
    assert snapshot(engine) == after


def test_physical_units_are_mapped_separately_and_only_whole_tokens_change(engine):
    with Session(engine) as session, session.begin():
        first = unit(session, "ОБ.033.1", kind="Оборудование", **{"Описание": (
            "ОБ.033.1, ОБ.033.2; ОБ.033.3; ОБ.033.10; xОБ.033.1; ОБ.033.1.X; ОБ.033.1.2; ОБ.033.1.")})
        session.add_all([first, unit(session, "ОБ.033.2", kind="Оборудование"), unit(session, "ОБ.033.3", kind="Оборудование")])
        first_id = first.id
    run(engine, [mapping("ОБ.033.1", "СИ.112"), mapping("ОБ.033.2", "СИ.113")], apply=True)
    with Session(engine) as session:
        row = session.get(EquipmentRecordRow, first_id)
        assert row.fields["Описание"] == "СИ.112, СИ.113; ОБ.033.3; ОБ.033.10; xОБ.033.1; ОБ.033.1.X; ОБ.033.1.2; СИ.112."
        assert {item.fields["Инв. номер"] for item in session.scalars(select(EquipmentRecordRow))} == {"СИ.112", "СИ.113", "ОБ.033.3"}


def test_parent_number_does_not_generate_unmapped_child_numbers(engine):
    with Session(engine) as session, session.begin():
        session.add(unit(session, "ОБ.033", kind="Оборудование", **{"Описание": "ОБ.033.1 и ОБ.033"}))
    run(engine, [mapping("ОБ.033", "СИ.112")], apply=True)
    with Session(engine) as session:
        assert session.scalar(select(EquipmentRecordRow)).fields["Описание"] == "ОБ.033.1 и СИ.112"


def test_edited_unrelated_target_is_never_mistaken_for_a_completed_mapping(engine):
    with Session(engine) as session, session.begin():
        unrelated = unit(session, "СИ.106", kind=MEASURING_TYPE)
        unrelated.edited_in_erp = True
        session.add_all([unrelated, instrument("ОБ.040")])
    before = snapshot(engine)
    with pytest.raises(ValueError, match="не найден"):
        run(engine, [mapping("ОБ.040", "СИ.106")], apply=True)
    assert snapshot(engine) == before


def test_audit_does_not_authorize_a_different_target_uuid(engine):
    with Session(engine) as session, session.begin():
        session.add(unit(session, "ОБ.040", kind="Оборудование"))
    entries = [mapping("ОБ.040", "СИ.106")]
    run(engine, entries, apply=True)
    with Session(engine) as session, session.begin():
        original = session.scalar(select(EquipmentRecordRow))
        original.fields = {**original.fields, "Инв. номер": "СИ.999"}
        other = unit(session, "СИ.106", kind=MEASURING_TYPE)
        other.edited_in_erp = True
        session.add(other)
    before = snapshot(engine)
    with pytest.raises(ValueError, match="не найден"):
        run(engine, entries, apply=True)
    assert snapshot(engine) == before


def test_registry_drift_after_atomic_apply_is_rejected(engine):
    with Session(engine) as session, session.begin():
        session.add_all([unit(session, "ОБ.040", kind="Оборудование"), instrument("ОБ.040")])
    entries = [mapping("ОБ.040", "СИ.106")]
    run(engine, entries, apply=True)
    with Session(engine) as session, session.begin():
        session.scalar(select(ProductionInstrumentRow)).inventory_number = "ОБ.040"
    before = snapshot(engine)
    with pytest.raises(ValueError, match="после переклассификации"):
        run(engine, entries, apply=True)
    assert snapshot(engine) == before


def test_nonstandard_source_spelling_aborts_before_forcing_the_unit_number(engine):
    with Session(engine) as session, session.begin():
        session.add(unit(session, "ОБ. 040", kind="Оборудование", **{"Описание": "Прибор ОБ. 040"}))
    before = snapshot(engine)
    with pytest.raises(ValueError, match="нестандартно"):
        run(engine, [mapping("ОБ.040", "СИ.106")], apply=True)
    assert snapshot(engine) == before


def test_rule_changes_only_supporting_equipment_type_preserving_numbers_and_roles(engine):
    with Session(engine) as session, session.begin():
        # ОБ.201 keeps the legacy type «Оборудование (ПР)»: still a machine, so the supporting rule applies to it too.
        items = [unit(session, "ОБ.200", kind="Оборудование"), unit(session, "ОБ.201", kind="Оборудование (ПР)"),
                 unit(session, "ОБ.202", kind="Инструмент"), unit(session, "ОБ.203", kind="Оборудование")]
        session.add_all(items)
        session.flush()
        for item in items[:3]:
            session.get(EquipmentModelRow, item.equipment_model_id).role = "supporting"
    before = snapshot(engine)
    rules = [{"role": "supporting", "type": SUPPORTING_TYPE}]
    report = run(engine, rules, apply=True)
    assert len(report.type_changes) == 2 and not report.changes
    after = snapshot(engine)
    assert before["equipment_models"] == after["equipment_models"]
    with Session(engine) as session:
        types = {row.fields["Инв. номер"]: row.fields["Тип"] for row in session.scalars(select(EquipmentRecordRow))}
    assert types == {"ОБ.200": SUPPORTING_TYPE, "ОБ.201": SUPPORTING_TYPE, "ОБ.202": "Инструмент", "ОБ.203": "Оборудование"}
    assert not run(engine, rules, apply=True).type_changes
    assert snapshot(engine) == after


def test_type_only_entry_and_explicit_mapping_take_precedence_over_role_rule(engine):
    with Session(engine) as session, session.begin():
        row = unit(session, "ОБ.040", kind="Оборудование")
        session.add(row)
        session.flush()
        session.get(EquipmentModelRow, row.equipment_model_id).role = "supporting"
    rules = [mapping("ОБ.040", None), {"role": "supporting", "type": SUPPORTING_TYPE}]
    report = run(engine, rules, apply=True)
    assert len(report.type_changes) == 1 and report.type_changes[0].after == MEASURING_TYPE
    assert not report.changes
    after = snapshot(engine)
    assert not run(engine, rules, apply=True).type_changes
    assert snapshot(engine) == after


def test_inventory_and_type_aliases_stay_consistent(engine):
    with Session(engine) as session, session.begin():
        row = unit(session, "ОБ.040", kind="Оборудование")
        row.fields = {"inventoryNumber": "ОБ.040", "obType": "Оборудование", "Наменование": "Прибор"}
        session.add(row)
    run(engine, [mapping("ОБ.040", "СИ.106")], apply=True)
    with Session(engine) as session:
        fields = session.scalar(select(EquipmentRecordRow)).fields
    assert fields["Инв. номер"] == fields["inventoryNumber"] == "СИ.106"
    assert fields["Тип"] == fields["obType"] == MEASURING_TYPE


@pytest.mark.parametrize("allow", [False, True])
def test_existing_registry_number_requires_explicit_allow_list(engine, allow):
    with Session(engine) as session, session.begin():
        session.add_all([unit(session, "ОБ.105", kind="Оборудование"), instrument("СИ.001")])
    before = snapshot(engine)
    entries = [mapping("ОБ.105", "СИ.001", registry_existing=allow)]
    if not allow:
        with pytest.raises(ValueError, match="registry_existing"):
            run(engine, entries, apply=True)
        assert snapshot(engine) == before
    else:
        report = run(engine, entries, apply=True)
        assert not report.registry_changes
        assert snapshot(engine)["production_instruments"] == before["production_instruments"]


def test_allow_list_never_merges_two_registry_rows(engine):
    with Session(engine) as session, session.begin():
        session.add_all([unit(session, "ОБ.105", kind="Оборудование"), instrument("ОБ.105"), instrument("СИ.001")])
    before = snapshot(engine)
    with pytest.raises(ValueError, match="оба номера"):
        run(engine, [mapping("ОБ.105", "СИ.001", registry_existing=True)], apply=True)
    assert snapshot(engine) == before


def test_registry_and_live_references_rollback_together_on_late_failure(engine):
    seed(engine)
    with Session(engine) as session, session.begin():
        session.add(instrument("ОБ.106"))
    before = snapshot(engine)

    def fail(_connection, _cursor, statement, _params, _context, _many):
        if statement.startswith("UPDATE td_issues"):
            raise RuntimeError("write failed")

    event.listen(engine, "before_cursor_execute", fail)
    with pytest.raises(RuntimeError, match="write failed"):
        run(engine, [mapping()], apply=True)
    assert snapshot(engine) == before


def test_cli_defaults_to_read_only_and_returns_russian_validation_errors(engine, tmp_path, monkeypatch, capsys):
    seed(engine)
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps([mapping()], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("ERP_DATABASE_URL", str(engine.url))
    before = snapshot(engine)
    assert main(["--mapping", str(path)]) == 0
    assert "Проверка без записи" in capsys.readouterr().out
    assert snapshot(engine) == before
    assert main(["--mapping", str(path), "--apply"]) == 0
    assert "Изменения записаны" in capsys.readouterr().out
    assert main(["--mapping", str(path), "--apply"]) == 0
    assert "Уже применено: 1" in capsys.readouterr().out
    path.write_text("{}", encoding="utf-8")
    assert main(["--mapping", str(path)]) == 2
    assert "список записей" in capsys.readouterr().err
    monkeypatch.delenv("ERP_DATABASE_URL")
    assert main(["--mapping", str(path)]) == 2
    assert "ERP_DATABASE_URL" in capsys.readouterr().err


def test_production_mapping_contains_exact_unit_pairs_and_only_two_registry_exceptions():
    path = Path(__file__).resolve().parents[3] / "docs/data/equipment-reclassify-2026-11-12.json"
    entries, supporting = validate_mapping(json.loads(path.read_text(encoding="utf-8")))
    assert supporting and len(entries) == 15
    assert {item.before: item.after for item in entries} == {
        "ОБ.105": "СИ.001", "ОБ.039": "СИ.105", "ОБ.040": "СИ.106", "ОБ.042": "СИ.107",
        "ОБ.086": "СИ.108", "ОБ.068": "СИ.109", "ОБ.034.1": "СИ.110", "ОБ.034.2": "СИ.111",
        "ОБ.033.1": "СИ.112", "ОБ.033.2": "СИ.113", "ОБ.061": "СИ.114", "ОБ.048": "СИ.115",
        "ОБ.050": "СИ.116", "ОБ.045": "СИ.117", "ОБ.087": "СИ.118",
    }
    assert {item.before for item in entries if item.registry_existing} == {"ОБ.105", "ОБ.039"}
    assert all(item.type == MEASURING_TYPE for item in entries)
