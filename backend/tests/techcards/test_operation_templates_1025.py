"""Шаблоны карт операций 25.10.2026 (docs/data/operation-templates-2026-10-25): SDR_QS8, SDR_SA, INS_housing_SA со
строками БД.ОП «по аналогу» и переименование INS_MARK_TUBE_AUTO → PRINT_MARK_TUBE (id карты прежний)."""

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
from app.modules.techcards.td_sto import sto, sto_section
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.new_operation import NewOperationUnitOfWork

ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "docs" / "data" / "operation-templates-2026-10-25"
PAYLOAD = json.loads((FOLDER / "payload.json").read_text(encoding="utf-8"))
MIRROR = ROOT / "backend" / "data" / "reference-mirror"
NEW = {"SDR_QS8", "SDR_SA", "INS_housing_SA"}
ANALOG = {"SDR_QS8": "SDR_XT90", "SDR_SA": "SDR_GX12", "INS_housing_SA": "INS_housing_GX"}
ACTOR = Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство")
NOW = datetime(2026, 10, 25, 18, 0)
MARK_ID = "ins-mark-tube-auto-печать-и-резка-маркировочной-трубки"


def _script():
    spec = importlib.util.spec_from_file_location("apply_templates_1025", FOLDER / "apply_templates.py")
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


def _tsv(name: str) -> list[list[str]]:
    return list(csv.reader((MIRROR / name).open(encoding="utf-8", newline=""), delimiter="\t"))


def _bd_op() -> dict[str, list[str]]:
    return {row[0].strip(): row for row in _tsv("БД.ОП.tsv")[2:] if row and row[0].strip()}


# ── payload ──

def test_payload_is_three_new_codes_and_one_rename():
    assert [item["code"] for item in PAYLOAD] == ["SDR_QS8", "SDR_SA", "INS_housing_SA", "PRINT_MARK_TUBE"]
    assert {item["code"] for item in PAYLOAD if "create" in item} == NEW
    assert [(item["code"], item["rename_from"]) for item in PAYLOAD if "rename_from" in item] == [
        ("PRINT_MARK_TUBE", "INS_MARK_TUBE_AUTO")]
    for item in PAYLOAD:
        edit = normalize_record_edit(item["record"])
        assert edit.steps and any(step.kind == "Контроль" for step in edit.steps), item["code"]
        assert edit.safety.startswith("ВАЖНО! ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:"), item["code"]
    names = {item["code"]: item["record"]["name"] for item in PAYLOAD}
    assert names["SDR_QS8"] == "Пайка разъёма AMASS QS8 / AS150 / XT150"
    assert names["SDR_SA"] == "Пайка разъёма Weipu SA" and names["INS_housing_SA"].startswith("Монтаж корпуса")


def test_new_codes_map_to_sto_sections_and_cite_its_rows():
    assert {code: sto_section(code)["section"] for code in (*sorted(NEW), "PRINT_MARK_TUBE", "INS_MARK_TUBE_AUTO")} == {
        "INS_housing_SA": "4.7", "SDR_QS8": "4.4", "SDR_SA": "4.4", "PRINT_MARK_TUBE": "4.6", "INS_MARK_TUBE_AUTO": "4.6"}
    rows, wrong = _sto_rows(), []
    for item in PAYLOAD:
        text = item["record"]["defects"]
        assert "ред. 3" in text.split("\n", 1)[0] and "ред. 2" not in text, item["code"]
        sections = sto_section(item["code"])["section"].split(", ")
        allowed = tuple(part + "." for section in sections
                        for part in (("4.4.2", "4.4.3") if section == "4.4" else (section,)))
        for code, name in re.findall(r"(?:^|; )(\d+\.\d+\.\d+(?:\.\d+)?) ([^;\n]+?)(?=;|\.\n|\.$)", text, re.M):
            if code not in rows or rows[code]["defect"] != name.strip() or not code.startswith(allowed):
                wrong.append(f"{item['code']}: {code} {name}")
    assert wrong == []
    # XT30/XT60/XT90 — особенность этих разъёмов: к QS8 / AS150 / XT150 её строка не относится.
    assert "4.4.3.10" not in next(i for i in PAYLOAD if i["code"] == "SDR_QS8")["record"]["defects"]


def test_equipment_comes_from_the_register_and_norms_from_the_analog_row():
    register = {row[2].strip() for row in _tsv("БД.ОБ.tsv") if len(row) > 2}
    assert [t["inventory_number"] for item in PAYLOAD for t in item["record"]["tooling"]
            if t["inventory_number"] and t["inventory_number"] not in register] == []
    ops = _bd_op()
    assert "INS_MARK_TUBE_AUTO" not in ops and "PRINT_MARK_TUBE" in ops  # зеркало БД.ОП после переименования
    assert NEW <= set(ops)
    for item in PAYLOAD:
        spec = (item.get("create") or {}).get("row")
        if not spec:
            continue
        analog, mine = ops[spec["copy_of"]], ops[item["code"]]
        assert spec["copy_of"] == ANALOG[item["code"]]
        norms = spec["norms"]
        assert (mine[9], mine[12], mine[10], mine[19]) == (norms["Время Операции"], norms["Время Чел, сек/оп; сек/м"],
                                                           norms["Время подготовки, сек"], norms["Время ручных работ"])
        # Нормы по аналогу, не меньше: массивные чашки / больше контактов / больше деталей, чем у образца.
        as_s = lambda value: float(value.replace(",", "."))
        assert as_s(mine[9]) > as_s(analog[9]) and as_s(mine[10]) >= as_s(analog[10])
        assert [c for i, c in enumerate(mine) if i not in (0, 9, 10, 12, 19)] == [
            c for i, c in enumerate(analog) if i not in (0, 9, 10, 12, 19)]


def test_texts_follow_the_analog_cards_and_carry_the_new_specifics():
    by_code = {item["code"]: json.dumps(item["record"], ensure_ascii=False) for item in PAYLOAD}
    for phrase in ("антиискров", "320 °С", "6–8 AWG", "полярн", "ТУТ", "заполнени", "8620DX-Pro(R)", "ОБ.070.X", "ОБ.071.X"):
        assert phrase in by_code["SDR_QS8"], phrase
    for phrase in ("S4", "P4", "через один", "не более 3 с", "ОБ.071.X", "0,75 мм²"):
        assert phrase in by_code["SDR_SA"], phrase
    for phrase in ("SA810", "SA812", "кабельный зажим", "хвостовик", "муфт"):
        assert phrase in by_code["INS_housing_SA"], phrase
    assert "ГОСТ 23592-96" in by_code["SDR_QS8"] and "ГОСТ Р МЭК 61191-4-2019" in by_code["SDR_SA"]
    assert "INS_MARK_TUBE_AUTO" not in by_code["PRINT_MARK_TUBE"]


# ── скрипт записи ──

@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


NORM_COLUMNS = ("Время Операции", "Время Чел, сек/оп; сек/м", "Время подготовки, сек", "Время ручных работ")


def _library(sessions, cards, rows):
    """Пустые карты `cards` и строки БД.ОП `rows` ({код: [нормы по NORM_COLUMNS]}) — как на проде до записи."""
    uow = NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions))
    with uow:
        for code in cards:
            create_operation_templates_in(new_operation(code, "Операция"), ACTOR, NOW, uow)
        uow.commit()
    for code, norms in rows.items():
        fields = {"Номер": code, "Название": "Пайка", **dict(zip(NORM_COLUMNS, norms))}
        create_record(CreateRecordCommand(TechOperationTab.OPERATION, fields, ACTOR, NOW),
                      SqlAlchemyReferencesUnitOfWork(sessions))


def _op_rows(sessions) -> dict[str, dict]:
    with SqlAlchemyReferencesUnitOfWork(sessions) as uow:
        return {r.fields["opNumber"]: r.fields for r in uow.tech_operations.list(
            tab=TechOperationTab.OPERATION, query="", limit=100_000)}


PROD_ROWS = {"SDR_XT90": ["40,00", "40,00", "120", "30"], "SDR_GX12": ["10,00", "10,00", "120", "5"],
             "INS_housing_GX": ["45,00", "45,00", "120", "45"], "INS_MARK_TUBE_AUTO": ["4,00", "4,00", "600", "0"]}


def _run(sessions, script, payload, *, apply=True, rollback=False):
    uow = NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions))
    with uow:
        if rollback:
            result = script.rollback_payload(payload, uow, who=script.actor(script.ROLLBACK_AUTHOR), now=NOW)
        else:
            result = script.apply_payload(payload, uow, who=script.actor(), now=NOW)
        if apply:
            uow.commit()
    return result


def test_script_creates_three_codes_with_rows_and_renames_the_mark_tube_in_place(sessions):
    script = _script()
    _library(sessions, ["INS_MARK_TUBE_AUTO", "SDR_XT90"], PROD_ROWS)

    dry = _run(sessions, script, PAYLOAD, apply=False)
    assert (sorted(dry["created"]), dry["renamed"], dry["no_row"], dry["missing"]) == (
        sorted(NEW), ["INS_MARK_TUBE_AUTO → PRINT_MARK_TUBE"], [], [])
    with sessions() as session:  # репетиция ничего не оставила
        assert sorted(session.scalars(select(OperationCardTemplateRow.code))) == ["INS_MARK_TUBE_AUTO", "SDR_XT90"]
    assert set(_op_rows(sessions)) == set(PROD_ROWS)

    result = _run(sessions, script, PAYLOAD)
    assert sorted(result["created"]) == sorted(NEW) and result["changed"] == ["PRINT_MARK_TUBE"]
    with sessions() as session:
        rows = {row.code: row for row in session.scalars(select(OperationCardTemplateRow))}
        events = [row.event_type for row in session.scalars(select(AuditEventRow).where(
            AuditEventRow.actor_name == script.AUTHOR))]
    assert set(rows) == {*NEW, "PRINT_MARK_TUBE", "SDR_XT90"}
    mark = rows["PRINT_MARK_TUBE"]
    assert mark.title.startswith("PRINT_MARK_TUBE | ") and mark.raw_rows[0][0] == mark.title
    assert rows["SDR_QS8"].category == "Пайка" and rows["INS_housing_SA"].category == "Монтаж корпуса"
    assert rows["SDR_QS8"].saved_by == rows["SDR_QS8"].edited_by == script.AUTHOR
    assert events.count("techcards.operation_template_created") == 3 and events.count("techcards.operation_template_renamed") == 1

    ops = _op_rows(sessions)
    assert "INS_MARK_TUBE_AUTO" not in ops and ops["PRINT_MARK_TUBE"]["opNumber"] == "PRINT_MARK_TUBE"
    assert [ops["SDR_QS8"][column] for column in NORM_COLUMNS] == ["60,00", "60,00", "180", "45"]
    assert [ops["SDR_SA"][column] for column in NORM_COLUMNS] == ["15,00", "15,00", "120", "8"]
    assert [ops["INS_housing_SA"][column] for column in NORM_COLUMNS] == ["60,00", "60,00", "120", "60"]
    assert ops["SDR_QS8"]["tOp"] == "60,00" and ops["SDR_QS8"]["tPrep"] == "180"
    assert [ops["SDR_XT90"][column] for column in NORM_COLUMNS] == PROD_ROWS["SDR_XT90"]  # образец не тронут

    again = _run(sessions, script, PAYLOAD)
    assert (again["created"], again["renamed"], again["missing"], again["no_row"]) == ([], [], [], [])
    assert sorted(again["unchanged"]) == sorted([*NEW, "PRINT_MARK_TUBE"])


def test_rename_keeps_the_template_id_so_saved_configs_still_resolve(sessions):
    script = _script()
    _library(sessions, ["INS_MARK_TUBE_AUTO"], {"INS_MARK_TUBE_AUTO": PROD_ROWS["INS_MARK_TUBE_AUTO"]})
    with sessions() as session:
        before = session.scalars(select(OperationCardTemplateRow)).one().id
    payload = [item for item in PAYLOAD if item["code"] == "PRINT_MARK_TUBE"]
    _run(sessions, script, payload)
    with sessions() as session:
        [row] = list(session.scalars(select(OperationCardTemplateRow)))
    assert row.id == before and row.code == "PRINT_MARK_TUBE"


def test_rename_finishes_the_row_when_the_server_update_already_renamed_the_card(sessions):
    """Обновление сервера кладёт карту ERP под новым кодом (seed_library --erp-only) раньше, чем идёт этот скрипт."""
    script = _script()
    _library(sessions, ["PRINT_MARK_TUBE"], {"INS_MARK_TUBE_AUTO": PROD_ROWS["INS_MARK_TUBE_AUTO"]})
    payload = [item for item in PAYLOAD if item["code"] == "PRINT_MARK_TUBE"]
    result = _run(sessions, script, payload)
    assert result["renamed"] == ["INS_MARK_TUBE_AUTO → PRINT_MARK_TUBE"]
    assert set(_op_rows(sessions)) == {"PRINT_MARK_TUBE"}


def test_new_code_without_a_row_spec_is_refused_not_created(sessions):
    script = _script()
    _library(sessions, [], {})
    item = {"code": "SDR_QS8", "create": {"category": "Пайка"}, "record": PAYLOAD[0]["record"]}
    result = _run(sessions, script, [item], apply=False)
    assert result["no_row"] == ["SDR_QS8"] and result["created"] == []


def test_rollback_removes_the_created_cards_and_rows_and_restores_the_old_code(sessions):
    script = _script()
    _library(sessions, ["INS_MARK_TUBE_AUTO", "SDR_XT90"], PROD_ROWS)
    _run(sessions, script, PAYLOAD)

    result = _run(sessions, script, PAYLOAD, rollback=True)
    assert result["deleted"] == sorted(NEW) and result["renamed"] == ["PRINT_MARK_TUBE → INS_MARK_TUBE_AUTO"]
    with sessions() as session:
        rows = {row.code: row for row in session.scalars(select(OperationCardTemplateRow))}
    assert set(rows) == {"INS_MARK_TUBE_AUTO", "SDR_XT90"}
    assert rows["INS_MARK_TUBE_AUTO"].title.startswith("INS_MARK_TUBE_AUTO | ")
    assert set(_op_rows(sessions)) == set(PROD_ROWS)


def test_rollback_refuses_to_delete_a_card_edited_after_the_write(sessions):
    script = _script()
    _library(sessions, ["SDR_XT90"], PROD_ROWS)
    payload = [item for item in PAYLOAD if item["code"] == "SDR_QS8"]
    _run(sessions, script, payload)
    with sessions() as session:
        session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == "SDR_QS8")).one().edited_by = "Технолог"
        session.commit()
    with pytest.raises(ValueError, match="SDR_QS8"):
        _run(sessions, script, payload, rollback=True)
    assert "SDR_QS8" in _op_rows(sessions)
