"""Atomic applicator renumbering: live text copies, UUID links and frozen documents."""

from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, event, inspect, select
from sqlalchemy.orm import Session

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.resource_infrastructure import ActiveResourceRow, ResourceReservationRow
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import (
    EquipmentModelRow, EquipmentOperationLinkRow, EquipmentTemplateToolRow, EquipmentUnitLinkRow,
    SqlAlchemyTechOperationRepository, TechOperationRecordRow, EquipmentRecordRow,
)
from app.modules.techcards.assembly_drafts_infrastructure import AssemblyDraftRow
from app.modules.techcards.generator_drafts_infrastructure import GeneratorDraftRow
from app.modules.techcards.infrastructure import (
    OperationCardDraftRow, OperationCardTemplateRow, ProjectTechcardRow, RefreshRequestRow,
    SqlAlchemyOperationCardRepository, TechcardRevisionRow,
)
from app.modules.techcards.etk_builder import _linked_tool
from app.modules.techcards.operation_cards import parse_operation_template
from app.modules.techcards.project_cards import TechcardStatus
from app.modules.techcards.td_issues_infrastructure import TdIssueRow
from app.shared.persistence import AuditEventRow, Base, create_schema
from app.workflows.applicator_inventory_fix import format_report, main, run

NOW = datetime(2026, 11, 12, 12)


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'repair.db').as_posix()}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    create_schema(engine)
    yield engine
    engine.dispose()


def unit(session, number, code="", kind="Аппликаторы", **extra):
    return SqlAlchemyTechOperationRepository(session)._put(TechOperationRecord(
        id=uuid4(), tab=TechOperationTab.EQUIPMENT, display_text=f"Аппликатор {number}",
        source_sheet="БД.ОБ", sort_key=number, normalized_search=f"аппликатор {number.lower()}",
        mirror_key=uuid4().hex, edited_in_erp=False,
        fields={"Тип": kind, "Инв. номер": number, "Код в базе терминалов": code,
                "Наменование": f"Оснастка {number}", **extra}))



def snapshot(engine):
    existing = set(inspect(engine).get_table_names())  # metadata may hold tables other test modules registered
    with engine.connect() as connection:
        return {table.name: [dict(row) for row in connection.execute(select(table)).mappings()]
                for table in Base.metadata.sorted_tables if table.name in existing}


def seed(engine):
    ids = {}
    with Session(engine) as session, session.begin():
        applicator = unit(session, "ОБ.106", " m 10 ")
        matrix = unit(session, "ОСН.001", kind="Матрицы", **{"Для инструмента": "ОБ.106"})
        ordinary = unit(session, "ОБ.107", kind="Оборудование")
        session.add_all([applicator, matrix, ordinary])
        session.flush()
        ids.update(unit=applicator.id, matrix=matrix.id, ordinary=ordinary.id, model=applicator.equipment_model_id,
                   mirror=applicator.mirror_key)
        op = TechOperationRecordRow(id=uuid4(), tab="op", display_text="Опрессовка ОБ.106", source_sheet="БД.ОП",
            sort_key="PRESS", normalized_search="об.106", mirror_key=uuid4().hex, fields={"Машина": "ОБ.106", "Инструмент": "ОБ.107",
                "Апликаторы / модули": "ОБ.106.2; ОБ.1060; xОБ.106; ОБ.106.X; ОБ.106.2.3", "Номер": "PRESS"})
        session.add(op)
        session.flush()
        ids["operation"] = op.id
        session.add(EquipmentOperationLinkRow(operation_id=op.id, op_column="Машина", position=0, model_id=ids["model"]))
        session.add(EquipmentUnitLinkRow(unit_id=matrix.id, kind="tool", target_id=applicator.id))
        session.add(EquipmentTemplateToolRow(template_id="press", position=0, tool_name="Аппликатор ОБ.106",
            inventory_number="ОБ.106", candidates=1, model_id=ids["model"]))
        rows = [["Операция", "1", "Установить ОБ.106"], ["Оборудование", "1", "Аппликатор ОБ.106", "", "", "ОБ.106"]]
        template = parse_operation_template(source_id="press", title="PRESS | Опрессовка", category="Опрессовка", rows=rows)
        document = template.document()
        session.add(OperationCardTemplateRow(id="press", code="PRESS", side="", title=template.title, name=template.name,
            category=template.category, document=document, raw_rows=rows, images=[], source_commit="frozen-source",
            imported_at=NOW, edited=deepcopy(document), edited_at=NOW, edited_by="Технолог", description="Применить ОБ.106"))
        draft = OperationCardDraftRow(id=uuid4(), name="Черновик ОБ.106", existing_template_id="press", title=template.title,
            category=template.category, description="ОБ.106", rows=rows, images=[], created_at=NOW, created_by="Технолог", updated_at=NOW)
        session.add(draft)
        ids["template_draft"] = draft.id
        card = ProjectTechcardRow(id=uuid4(), version=4, project="ПР1", item_index="КБ1", product_name="Жгут",
            source_calculation="", status=TechcardStatus.ISSUED.value, revision=3, document={"operations": [{"card": document}]},
            manual_operation_keys=[], created_at=NOW, updated_at=NOW, issued_at=NOW, derived_hash="old-hash",
            inputs={"ops": [{"machine": {"inventory": "ОБ.106", "equipment_model_id": str(ids["model"])}}]},
            overrides={"steps": {"ОБ.106": {"text": "Установить ОБ.106.2"}}})
        session.add(card)
        session.flush()
        ids["card"] = card.id
        session.add(TechcardRevisionRow(id=uuid4(), project_card_id=card.id, revision=3, reason="Выпуск",
            actor_name="Технолог", occurred_at=NOW, document=card.document, source="manual"))
        ids["issues"] = {}
        for change, status in enumerate(("draft", "signing", "active", "annulled")):
            issue = TdIssueRow(id=uuid4(), version=2, project_card_id=card.id, change_no=change, sequence_no=1,
                org_code="ФРАК", method_code="85", litera="", status=status, etk_revision=3, snapshot=card.document,
                signatories={}, notice_no="ИЗВ" if change else "", reason="Изменение" if change else "",
                created_at=NOW, created_by="Технолог", workshop="", site="")
            session.add(issue)
            ids["issues"][status] = issue.id
        user = IdentityUserRow(id=uuid4(), login="technologist", email="test@example.test", name="Технолог", department="ERP", role="admin")
        session.add(user)
        session.flush()
        session.add(GeneratorDraftRow(id=uuid4(), user_id=user.id, project="ПР1", item_index="КБ1", payload=card.inputs,
            version=2, build_fingerprint="old-fingerprint", created_at=NOW, updated_at=NOW))
        for status in ("draft", "final"):
            session.add(AssemblyDraftRow(id=uuid4(), project="ПР1", item_index="КБ1", source="manual", author="Технолог",
                draft_config=card.inputs, final_config=card.inputs if status == "final" else None,
                final_revision=3 if status == "final" else None, status=status, lesson="", created_at=NOW, updated_at=NOW))
        session.add(AuditEventRow(id=uuid4(), aggregate_id=applicator.id, event_type="references.tech_record_edited",
            actor_id=user.id, actor_name="Технолог", actor_department="ERP", occurred_at=NOW, details={"inventory": "ОБ.106"}))
        session.add(ResourceReservationRow(owner_kind="setup", owner_id=uuid4(), resource_kind="machine",
            resource_key=str(applicator.id), machine_id=applicator.id, start=NOW, finish=NOW + timedelta(hours=1)))
        session.add(ActiveResourceRow(resource_kind="machine", resource_key=str(applicator.id), machine_id=applicator.id,
            owner_kind="setup", owner_id=uuid4(), execution_id=uuid4(), activated_at=NOW))
    # The seed itself queues refreshes. The repair must queue its own only on apply.
    with engine.begin() as connection:
        connection.execute(delete(RefreshRequestRow))
    return ids


def test_dry_run_is_read_only_and_reports_every_live_table(engine):
    seed(engine)
    before = snapshot(engine)
    writes = []

    def record_writes(_connection, _cursor, statement, _params, _context, _many):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", record_writes)
    report = run(engine)
    assert writes == []
    assert snapshot(engine) == before
    assert [(change.before, change.after) for change in report.changes] == [("ОБ.106", "M10")]
    assert report.tables["tech_operation_records"].scanned == 1
    assert report.tables["tech_operation_records"].changed == 1
    assert report.tables["equipment_applicators"].changed == 1
    assert report.tables["equipment_dies"].changed == 1
    assert report.tables["td_issues"].scanned == 4 and report.tables["td_issues"].changed == 1
    assert all(count.occurrences > 0 for count in report.tables.values() if count.changed)
    assert "ОБ.106 → M10" in format_report(report)
    assert report.issued_documents_to_refresh == 1
    assert "Выданных ЭТК для автоматического обновления: 1" in format_report(report)


def test_apply_updates_live_copies_preserves_uuid_links_and_history_and_is_idempotent(engine):
    ids = seed(engine)
    before = snapshot(engine)
    planned = run(engine)
    applied = run(engine, apply=True)
    assert applied == planned
    after = snapshot(engine)
    for table in ("equipment_unit_links", "equipment_operation_links", "techcard_revisions", "audit_events",
                  "production_resource_reservations", "production_active_resources"):
        assert after[table] == before[table]
    with Session(engine) as session:
        app = session.get(EquipmentRecordRow, ids["unit"])
        assert app.fields["Инв. номер"] == app.fields["Код в базе терминалов"] == "M10"
        assert app.equipment_model_id == ids["model"] and app.mirror_key == ids["mirror"] and app.edited_in_erp
        assert "об.106" not in app.normalized_search and "m10" in app.normalized_search
        assert session.get(EquipmentRecordRow, ids["matrix"]).fields["Для инструмента"] == "M10"
        assert session.get(EquipmentRecordRow, ids["ordinary"]).fields["Инв. номер"] == "ОБ.107"
        op = session.get(TechOperationRecordRow, ids["operation"])
        assert op.fields["Машина"] == "M10" and op.fields["Инструмент"] == "ОБ.107"
        assert op.fields["Апликаторы / модули"] == "M10.2; ОБ.1060; xОБ.106; ОБ.106.X; ОБ.106.2.3"
        template = SqlAlchemyOperationCardRepository(session).get("press")
        tool = template.tooling[0]
        link = session.get(EquipmentTemplateToolRow, ("press", 0))
        assert tool.inventory_number == link.inventory_number == "M10"
        assert tool.name == link.tool_name == "Аппликатор M10"
        linked = _linked_tool(tool, {}, {"tool": link.tool_name, "inventory_number": link.inventory_number,
            "inventory": "M10", "equipment_model_id": str(link.model_id)}, str)
        assert linked["equipment_model_id"] == str(ids["model"])
        stored_template = session.get(OperationCardTemplateRow, "press")
        assert "ОБ.106" not in str(stored_template.document) + str(stored_template.edited) + str(stored_template.raw_rows)
        assert stored_template.source_commit == "frozen-source" and stored_template.imported_at == NOW
        draft = session.get(OperationCardDraftRow, ids["template_draft"])
        assert draft.name == "Черновик M10" and "ОБ.106" not in str(draft.rows)
        card = session.get(ProjectTechcardRow, ids["card"])
        assert card.version == 5 and card.revision == 3
        original = next(row for row in before["techcard_projects"] if row["id"] == card.id)
        assert card.document == original["document"]
        assert card.issued_at == NOW and card.derived_hash == "old-hash"
        assert "ОБ.106" not in str(card.inputs) + str(card.overrides)
        assert card.inputs["ops"][0]["machine"]["equipment_model_id"] == str(ids["model"])
        for status, issue_id in ids["issues"].items():
            issue = session.get(TdIssueRow, issue_id)
            assert issue.version == (3 if status == "draft" else 2)
            assert ("ОБ.106" in str(issue.snapshot)) == (status != "draft")
        generator = session.scalar(select(GeneratorDraftRow))
        assert generator.version == 3 and generator.payload == card.inputs
        for assembly in session.scalars(select(AssemblyDraftRow)):
            assert ("ОБ.106" in str(assembly.draft_config)) == (assembly.status == "final")
        assert {row.reason for row in session.scalars(select(RefreshRequestRow))} == {"reference", "template"}
    second = run(engine, apply=True)
    assert second.issued_documents_to_refresh == 0
    assert second.changes == [] and all(count.changed == count.occurrences == 0 for count in second.tables.values())
    assert snapshot(engine) == after


@pytest.mark.parametrize("issued", [False, True])
def test_document_only_references_are_counted_but_only_drafts_are_rewritten(engine, issued):
    ids = seed(engine)
    with Session(engine) as session, session.begin():
        card = session.get(ProjectTechcardRow, ids["card"])
        card.status = TechcardStatus.ISSUED.value if issued else TechcardStatus.DRAFT.value
        card.inputs = {"unrelated": "ОБ.107"}
        card.overrides = {}
    before = snapshot(engine)["techcard_projects"]
    report = run(engine)
    assert report.issued_documents_to_refresh == int(issued)
    assert report.tables["techcard_projects"].changed == int(not issued)
    assert snapshot(engine)["techcard_projects"] == before
    assert run(engine, apply=True) == report
    after = snapshot(engine)["techcard_projects"]
    if issued:
        assert after == before
    else:
        assert "ОБ.106" not in str(after[0]["document"])
        assert after[0]["version"] == 5


def test_issued_document_count_excludes_unaffected_cards(engine):
    ids = seed(engine)
    with Session(engine) as session, session.begin():
        card = session.get(ProjectTechcardRow, ids["card"])
        card.document = {"inventory": "ОБ.107"}
        card.inputs = {}
        card.overrides = {}
    report = run(engine)
    assert report.issued_documents_to_refresh == 0
    assert report.tables["techcard_projects"].changed == 0


def test_issued_documents_without_generator_inputs_are_reported_for_recovery(engine):
    ids = seed(engine)
    with Session(engine) as session, session.begin():
        card = session.get(ProjectTechcardRow, ids["card"])
        card.inputs = None
    report = run(engine)
    assert report.issued_documents_to_refresh == 0
    assert report.issued_documents_without_inputs == [ids["card"]]
    assert "автообновление невозможно): 1" in format_report(report)


def test_legacy_manual_operation_is_migrated_as_an_override_without_changing_issued_document(engine):
    ids = seed(engine)
    document = {"source": {"templates": {"press": "version-before"}}, "operations": [{
        "code": "PRESS", "template_id": "press", "card": {
            "steps": [{"text": "Ручная правка технолога"}],
            "tooling": [{"name": "Аппликатор", "inventory_number": "ОБ.106"}]}}]}
    with Session(engine) as session, session.begin():
        card = session.get(ProjectTechcardRow, ids["card"])
        card.document = deepcopy(document)
        card.manual_operation_keys = ["PRESS::::"]
        card.overrides = {}
    report = run(engine)
    assert report.issued_documents_to_refresh == 1
    run(engine, apply=True)
    with Session(engine) as session:
        card = session.get(ProjectTechcardRow, ids["card"])
        assert card.document == document and card.revision == 3
        edit = card.overrides["operations"]["PRESS::::"]
        assert edit["base_template_version"] == "version-before"
        assert edit["operation"]["card"]["tooling"][0]["inventory_number"] == "M10"
        assert edit["operation"]["card"]["steps"] == document["operations"][0]["card"]["steps"]
    assert run(engine, apply=True).tables["techcard_projects"].changed == 0


def test_apply_rolls_back_every_table_on_a_late_write_failure(engine):
    seed(engine)
    before = snapshot(engine)

    def fail(_connection, _cursor, statement, _params, _context, _many):
        if statement.startswith("UPDATE td_issues"):
            raise RuntimeError("write failed")

    event.listen(engine, "before_cursor_execute", fail)
    with pytest.raises(RuntimeError, match="write failed"):
        run(engine, apply=True)
    assert snapshot(engine) == before


def test_missing_invalid_colliding_and_ambiguous_codes_are_skipped(engine):
    with Session(engine) as session, session.begin():
        session.add_all([unit(session, "ОБ.126"), unit(session, "ОБ.127", "X1"), unit(session, "ОБ.128", "S3"), unit(session, "ОБ.129", " s 3 "),
            unit(session, "ОБ.130", "A1"), unit(session, "A1", kind="Инструмент"), unit(session, "ОБ.131", "M2"),
            unit(session, "ОБ.131", kind="Оборудование"), unit(session, "ОБ.132", "M3")])
    before = snapshot(engine)["tech_operation_records"]
    report = run(engine, apply=True)
    assert [(item.before, item.after) for item in report.changes] == [("ОБ.132", "M3")]
    assert len(report.skipped) == 6
    assert {item.reason for item in report.skipped} >= {"нет кода", "коллизия: старый инв. номер у нескольких записей"}
    after = snapshot(engine)["tech_operation_records"]
    skipped_ids = {item.row_id for item in report.skipped}
    assert [row for row in after if row["id"] in skipped_ids] == [row for row in before if row["id"] in skipped_ids]
    assert len(run(engine, apply=True).skipped) == 6


def test_suffix_references_respect_actual_non_applicator_and_skipped_rows(engine):
    with Session(engine) as session, session.begin():
        session.add_all([unit(session, "ОБ.106", "A1"), unit(session, "ОБ.106.1", "M2"), unit(session, "ОБ.106.2", kind="Оборудование"),
                        unit(session, "ОБ.106.3"), unit(session, "ОБ.107", "S1", **{"Описание": "ОБ.106/ОБ.106.1/ОБ.106.2/ОБ.106.3/ОБ.106.4"})])
    run(engine, apply=True)
    with Session(engine) as session:
        rows = {row.fields["Инв. номер"]: row for row in session.scalars(select(EquipmentRecordRow))}
        assert set(rows) == {"A1", "M2", "ОБ.106.2", "ОБ.106.3", "S1"}
        assert rows["S1"].fields["Описание"] == "A1/M2/ОБ.106.2/ОБ.106.3/A1.4"


def test_normal_mirror_reload_preserves_correction_and_uuids(engine):
    ids = seed(engine)
    with Session(engine) as session:
        source = [SqlAlchemyTechOperationRepository._domain(row) for row in [*session.scalars(select(TechOperationRecordRow)), *session.scalars(select(EquipmentRecordRow))]]
    run(engine, apply=True)
    with Session(engine) as session, session.begin():
        repo = SqlAlchemyTechOperationRepository(session)
        repo.replace(tuple(source), schema_version=14, source_updated_at=None, imported_at=NOW, preserve_erp_edits=True)
    with Session(engine) as session:
        row = session.get(EquipmentRecordRow, ids["unit"])
        assert row.fields["Инв. номер"] == "M10" and row.equipment_model_id == ids["model"] and row.mirror_key == ids["mirror"]
        assert session.get(EquipmentUnitLinkRow, (ids["matrix"], "tool", ids["unit"])) is not None


def test_json_key_collision_aborts_before_any_write(engine):
    ids = seed(engine)
    with Session(engine) as session, session.begin():
        session.get(ProjectTechcardRow, ids["card"]).overrides = {"ОБ.106": "one", "M10": "two"}
    before = snapshot(engine)
    with pytest.raises(ValueError, match="Коллизия ключей JSON"):
        run(engine, apply=True)
    assert snapshot(engine) == before


def test_cli_defaults_to_dry_run_and_apply_is_explicit(engine, monkeypatch, capsys):
    seed(engine)
    before = snapshot(engine)
    monkeypatch.setenv("ERP_DATABASE_URL", str(engine.url))
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "Проверка без записи" in output and "ОБ.106 → M10" in output and "Просмотрено строк" in output
    assert snapshot(engine) == before
    assert main(["--apply"]) == 0
    assert "Изменения записаны" in capsys.readouterr().out
    assert main(["--apply"]) == 0
    assert "Перенумеровать: 0" in capsys.readouterr().out


def test_cli_requires_explicit_database_url(monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert main([]) == 2
    assert "ERP_DATABASE_URL" in capsys.readouterr().err
