"""Шаблоны карт операций 23.10.2026 (docs/data/operation-templates-2026-10-23) и СТО ФРАК-001-2026 ред. 3:
payload согласован со СТО и td_sto, скрипт записи создаёт недостающие коды и правит существующие одной транзакцией."""

from __future__ import annotations

import csv
import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.references.application import CreateRecordCommand, create_record
from app.modules.references.domain import TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.application import create_operation_templates_in, new_operation
from app.modules.techcards.infrastructure import OperationCardTemplateRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import normalize_record_edit
from app.modules.techcards.td_sto import sto, sto_norms, sto_section
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.new_operation import NewOperationUnitOfWork

ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "docs" / "data" / "operation-templates-2026-10-23"
PAYLOAD = json.loads((FOLDER / "payload.json").read_text(encoding="utf-8"))
BEFORE = json.loads((ROOT / "docs" / "data" / "operation-templates-2026-10-22" / "templates-after.json").read_text(encoding="utf-8"))
MIRROR = ROOT / "backend" / "data" / "reference-mirror"
NEW = {"LASER_MARK", "PRINT_MARK", "INS_MARK_AUTO", "PRS_TERMINAL_press", "SDR_MR30", "TWIST_BAY", "TWST_SHILED",
       "INS_TAPE_AUTO", "Раскладка", "Handjob"}
ACTOR = Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство")
NOW = datetime(2026, 10, 23, 18, 0)


def _script():
    spec = importlib.util.spec_from_file_location("apply_templates_1023", FOLDER / "apply_templates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sto_rows() -> dict[str, dict]:
    found: dict[str, dict] = {}

    def walk(items):
        for item in items:
            for row in item.get("rows") or []:
                found[row["code"]] = row
            walk(item.get("subsections") or [])
    walk(sto()["sections"])
    return found


def _tsv_codes(name: str, column: int) -> set[str]:
    return {row[column].strip() for row in csv.reader((MIRROR / name).open(encoding="utf-8"), delimiter="\t")
            if len(row) > column and row[column].strip()}


# ── СТО ред. 3 ──

def test_sto_revision_3_records_its_change_and_new_rows():
    document = sto()
    rows = _sto_rows()
    assert (document["revision"], document["date"]) == ("3", "2026-10-23")
    assert document["changes"][-1]["change"] == "2" and document["changes"][-1]["date"] == "23.10.2026"
    assert {item.get("change") for item in document["changes_detail"]} == {"1", "2"}
    assert len(rows) == 351
    added = {"4.2.37", *(f"4.3.{n}" for n in range(54, 63)), "4.4.2.21", "4.4.4.8", "4.4.4.9", "4.5.29", "4.6.24",
             "4.7.24", "4.7.25", "4.8.17", *(f"4.9.{n}" for n in range(14, 23)), *(f"4.11.{n}" for n in range(26, 30))}
    assert added <= set(rows)
    assert all(row["class"] in ("Критический", "Значительный", "Малозначительный") and row["basis"] for row in rows.values())


def test_sto_rev3_bases_follow_the_gost_series():
    rows = _sto_rows()
    # IDC — ч. 4 / 3, а не ч. 2; колпачки и сростки — табл. А.11; УЗ-сварка — ч. 9.
    for code in ("4.3.31", "4.3.32", "4.3.33", "4.3.34"):
        assert "ГОСТ Р 72182.4-2025" in rows[code]["basis"] and "72182.2" not in rows[code]["basis"], code
    assert "табл. А.11" in rows["4.3.54"]["basis"] and "колпачок" not in rows["4.3.15"]["criterion"].split(";")[0]
    for code in [f"4.11.{n}" for n in range(17, 30)]:
        assert "ГОСТ Р 72182.9-2025" in rows[code]["basis"], code
    assert "А.7.2" in rows["4.3.14"]["basis"] and "трещотк" in rows["4.3.14"]["criterion"]
    assert "MIL-STD-348B" in rows["4.8.1"]["basis"] and "ГОСТ Р 58416-2019" in rows["4.8.16"]["basis"]
    # IPC — только вслед за ГОСТ / КД, никогда первым основанием там, где ГОСТ есть.
    for code, row in rows.items():
        if row["basis"].startswith("IPC"):
            assert code in {"4.4.2.21"}, code
    norms_docs = {norm["doc"] for code in ("PRS_IDC", "US_WELD", "INS_housing_COAX") for norm in sto_norms(code)}
    assert {"ГОСТ Р 72182.4-2025", "ГОСТ Р 72182.9-2025", "ГОСТ 20271.1-91"} <= norms_docs
    assert not any(doc.startswith(("IPC", "MIL", "IEC")) for doc in norms_docs)  # в ТД печатаются только ГОСТ


def test_new_operation_codes_map_to_sto_sections():
    assert {code: sto_section(code)["section"] for code in sorted(NEW)} == {
        "Handjob": "4.9", "INS_MARK_AUTO": "4.6", "INS_TAPE_AUTO": "4.5", "LASER_MARK": "4.6", "PRINT_MARK": "4.6",
        "PRS_TERMINAL_press": "4.3", "SDR_MR30": "4.4", "TWIST_BAY": "4.9", "TWST_SHILED": "4.2", "Раскладка": "4.9",
    }
    assert sto_section("TWIST")["section"] == "4.10"


# ── payload ──

def test_payload_covers_every_template_and_the_ten_new_codes():
    codes = [item["code"] for item in PAYLOAD]
    assert len(codes) == len(set(codes)) == len(BEFORE) + len(NEW)
    assert {item["code"] for item in BEFORE} | NEW == set(codes)
    assert {item["code"] for item in PAYLOAD if "create" in item} == NEW
    ops = _tsv_codes("БД.ОП.tsv", 0)
    assert NEW <= ops  # строки БД.ОП у новых кодов есть
    for item in PAYLOAD:
        edit = normalize_record_edit(item["record"])
        assert edit.steps and any(step.kind == "Контроль" for step in edit.steps), item["code"]
        assert edit.safety.startswith("ВАЖНО! ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:"), item["code"]


def test_payload_defects_cite_rev3_rows_of_the_operation_section():
    rows = _sto_rows()
    wrong = []
    for item in PAYLOAD:
        text = item["record"]["defects"]
        head = text.split("\n", 1)[0]
        assert "ред. 3" in head and "ред. 2" not in text, item["code"]
        cited = re.findall(r"(?:^|; )(\d+\.\d+\.\d+(?:\.\d+)?) ([^;\n]+?)(?=;|\.\n|\.$)", text, re.M)
        sections = sto_section(item["code"])["section"].split(", ")
        allowed = tuple(part + "." for section in sections
                        for part in (("4.4.2", "4.4.3") if section == "4.4" else (section,)))
        for code, name in cited:
            if code not in rows or rows[code]["defect"] != name.strip():
                wrong.append(f"{item['code']}: {code} {name}")
            elif "+" not in item["code"] and not code.startswith(allowed):
                wrong.append(f"{item['code']}: {code} вне раздела {sections}")
    assert wrong == []


def test_new_templates_take_equipment_from_the_register_and_bd_op():
    register = _tsv_codes("БД.ОБ.tsv", 2)
    missing = sorted({tool["inventory_number"] for item in PAYLOAD if item["code"] in NEW
                      for tool in item["record"]["tooling"]
                      if tool["inventory_number"] and tool["inventory_number"] not in register})
    assert missing == []


def test_revised_templates_carry_the_gost_clauses():
    by_code = {item["code"]: json.dumps(item["record"], ensure_ascii=False) for item in PAYLOAD}
    assert all(phrase in by_code["US_WELD"] for phrase in ("п. 4.1, 4.2", "табл. 2", "50 мм", "95–120 %", "табл. 8"))
    assert "72182.4-2025" in by_code["PRS_IDC"] and "Высота обжима" not in by_code["PRS_IDC"]
    for code in ("PRS_TERMINAL_auto", "PRS_TERMINAL_hand", "PRS_TERMINAL_double", "PRS_TERMINAL_multi",
                 "PRS_TERMINAL_press"):
        assert "рис. А.14" in by_code[code] and "А.2 г)" in by_code[code], code
    assert "5.5.7.7" in by_code["PRS_HKИ/НВИ/РПИ"]


# ── скрипт записи ──

@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def _library(sessions, codes, rows):
    """Библиотека с пустыми картами `codes` и строки БД.ОП `rows`."""
    uow = NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions))
    with uow:
        for code in codes:
            create_operation_templates_in(new_operation(code, "Операция"), ACTOR, NOW, uow)
        uow.commit()
    for code in rows:
        create_record(CreateRecordCommand(TechOperationTab.OPERATION, {"opNumber": code, "opName": "x"}, ACTOR, NOW),
                      SqlAlchemyReferencesUnitOfWork(sessions))


def _run(sessions, script, payload, *, apply=True, rollback=None):
    uow = NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions))
    with uow:
        if rollback is None:
            result = script.apply_payload(payload, uow, who=script.actor(), now=NOW)
        else:
            who = script.actor(script.ROLLBACK_AUTHOR)
            result = {"deleted": script.delete_created([item["code"] for item in payload if "create" in item], uow,
                                                       who=who, now=NOW)}
            result |= script.apply_payload(rollback, uow, who=who, now=NOW)
        if apply:
            uow.commit()
    return result


def test_script_edits_existing_codes_and_creates_missing_ones_in_one_transaction(sessions):
    script = _script()
    existing = ["US_WELD", "PRS_IDC"]
    _library(sessions, existing, ["LASER_MARK", "Раскладка"])
    payload = [item for item in PAYLOAD if item["code"] in {*existing, "LASER_MARK", "Раскладка", "TWIST_BAY"}]

    dry = _run(sessions, script, payload, apply=False)
    assert (sorted(dry["changed"]), sorted(dry["created"]), dry["no_row"]) == (sorted(existing), ["LASER_MARK", "Раскладка"],
                                                                              ["TWIST_BAY"])
    with sessions() as session:  # репетиция ничего не оставила
        assert sorted(session.scalars(select(OperationCardTemplateRow.code))) == sorted(existing)

    result = _run(sessions, script, [item for item in payload if item["code"] != "TWIST_BAY"])
    assert sorted(result["created"]) == ["LASER_MARK", "Раскладка"] and result["missing"] == []
    with sessions() as session:
        rows = {row.code: row for row in session.scalars(select(OperationCardTemplateRow))}
        events = [row.event_type for row in session.scalars(select(AuditEventRow).where(
            AuditEventRow.actor_name == script.AUTHOR))]
    assert set(rows) == {*existing, "LASER_MARK", "Раскладка"}
    assert all(row.edited_by == script.AUTHOR for row in rows.values())
    assert rows["Раскладка"].saved_by == script.AUTHOR and rows["Раскладка"].category == "Раскладка на плазу"
    record = next(item["record"] for item in PAYLOAD if item["code"] == "LASER_MARK")
    assert rows["LASER_MARK"].document["defects"] == record["defects"]
    assert sorted(events) == sorted(["techcards.operation_record_edited"] * 2 + ["techcards.operation_template_created"] * 2)

    again = _run(sessions, script, [item for item in payload if item["code"] != "TWIST_BAY"])
    assert sorted(again["unchanged"]) == sorted([*existing, "LASER_MARK", "Раскладка"])


def test_rollback_deletes_created_cards_and_restores_old_records(sessions):
    script = _script()
    _library(sessions, ["US_WELD"], ["LASER_MARK"])
    payload = [item for item in PAYLOAD if item["code"] in {"US_WELD", "LASER_MARK"}]
    _run(sessions, script, payload)
    old = [item for item in BEFORE if item["code"] == "US_WELD"]

    result = _run(sessions, script, payload, rollback=old)
    assert result["deleted"] == ["LASER_MARK"] and result["changed"] == ["US_WELD"]
    with sessions() as session:
        rows = list(session.scalars(select(OperationCardTemplateRow)))
    assert [row.code for row in rows] == ["US_WELD"] and rows[0].edited_by == script.ROLLBACK_AUTHOR
    assert rows[0].document["defects"] == old[0]["record"]["defects"]


def test_rollback_refuses_to_delete_a_card_edited_after_the_write(sessions):
    script = _script()
    _library(sessions, [], ["LASER_MARK"])
    payload = [item for item in PAYLOAD if item["code"] == "LASER_MARK"]
    _run(sessions, script, payload)
    with sessions() as session:
        session.scalars(select(OperationCardTemplateRow)).one().edited_by = "Технолог"
        session.commit()

    with pytest.raises(ValueError, match="LASER_MARK"):
        _run(sessions, script, payload, rollback=[])
    with sessions() as session:
        assert [row.code for row in session.scalars(select(OperationCardTemplateRow))] == ["LASER_MARK"]
