"""Readable operation cards (ETK generator review 23.11.2026, slice 2: docs/data/operation-templates-etk-readability).

The paragraph steps of 24 production templates become one action per step plus separate check rows; MARK_CONN_PAINT loses
its «Вход:» / «Результат:» pseudo-steps and «BOM». Nothing else in a card changes, no fact of an old step is lost, the
builder still finds its anchors, and the guarded script writes only the content the production ETKs were built from."""

from __future__ import annotations

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

from app.modules.techcards.application import ERP_TEMPLATE_AUTHOR, SeedErpTemplatesCommand, seed_erp_templates
from app.modules.techcards.etk_builder import _resolve_card
from app.modules.techcards.infrastructure import OperationCardTemplateRow, RefreshRequestRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import (
    OperationCardTemplate, normalize_record_edit, parse_operation_library, parse_operation_template,
)
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.new_operation import NewOperationUnitOfWork

ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "docs" / "data" / "operation-templates-etk-readability"
PAYLOAD = json.loads((FOLDER / "payload.json").read_text(encoding="utf-8"))
BY_CODE = {item["code"]: item for item in PAYLOAD}
BASE = json.loads((ROOT / "backend/data/techcard-library.json").read_text(encoding="utf-8"))["templates"]
ERP = json.loads((ROOT / "backend/data/techcard-library-erp.json").read_text(encoding="utf-8"))["templates"]
NOW = datetime(2026, 11, 23, 18, 0)
PSEUDO = re.compile(r"^\s*(Вход|Результат)\s*:", re.IGNORECASE)
STEP_REF = re.compile(r"переход(?:у|ам|ов|а)?\s+(\d+)(?:\s*[–-]\s*(\d+))?(?:,\s*(\d+))?", re.IGNORECASE)


def _script():
    spec = importlib.util.spec_from_file_location("apply_readability_1123", FOLDER / "apply_readability.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _script()


def _before(item) -> dict:
    """The production record now (prod copy 26.11): what the script must find before it writes."""
    return item["expected_before"]


def _source(item) -> dict:
    """The delivery that wrote this content (`before_source`), before later in-place production rewrites."""
    data = json.loads((ROOT / item["before_source"]).read_text(encoding="utf-8"))
    if isinstance(data, dict):  # the 18.11 VSWR delivery: the template sheet itself
        card = parse_operation_template(source_id=data["id"], title=data["title"], category=data["category"], rows=data["rows"])
        return {"name": card.name, "safety": card.safety, "defects": card.defects, "revision": card.revision,
                "steps": [step.__dict__ for step in card.steps], "tooling": [tool.__dict__ for tool in card.tooling]}
    return next(entry for entry in data if entry["code"] == item["code"])["record"]


def _card(record, item) -> OperationCardTemplate:
    edit = normalize_record_edit(record)
    return OperationCardTemplate(
        source_id=item["template_id"], code=item["code"], title=f"{item['code']} | {edit.name}", name=edit.name,
        category="", designation="", revision=edit.revision, safety=edit.safety, defects=edit.defects,
        steps=edit.steps, tooling=edit.tooling, raw_rows=())


# ── payload ──

def test_payload_is_the_production_content_and_its_rewrite():
    assert len(PAYLOAD) == 24
    assert sum(item["evidence"]["operations"] for item in PAYLOAD) == 195
    assert sum(item["evidence"]["long_step_operations"] for item in PAYLOAD) == 161
    assert BY_CODE["MARK_CONN_PAINT"]["evidence"]["pseudo_step_operations"] == 28
    for item in PAYLOAD:
        # The guard hashes exactly the recorded production content.
        assert SCRIPT.record_hash(item["expected_before"]) == item["expected_before_hash"], item["code"]
        assert normalize_record_edit(item["expected_before"]).as_json() == item["expected_before"], item["code"]
        # Provenance: the delivery's record plus only the documented in-place inventory renumbering of production
        # (equipment_inventory_rewrite changed the record edits without touching edited_at).
        source = normalize_record_edit(_source(item)).as_json()
        rewrites = item["prod_rewrites"].get("inventory_numbers", {}) if item["prod_rewrites"] else {}
        for tool in source["tooling"]:
            tool["inventory_number"] = rewrites.get(tool["inventory_number"], tool["inventory_number"])
        assert source == item["expected_before"], item["code"]
        assert SCRIPT.record_hash(item["record"]) == item["after_hash"] != item["expected_before_hash"], item["code"]
        assert len(item["step_origin"]) == len(item["record"]["steps"])
        assert item["step_origin"] == sorted(item["step_origin"])


@pytest.mark.parametrize("item", PAYLOAD, ids=lambda item: item["code"])
def test_only_the_steps_change(item):
    before, after = normalize_record_edit(_before(item)).as_json(), normalize_record_edit(item["record"]).as_json()
    if item["code"] == "MARK_CONN_PAINT":  # E1: the marker rows say «по ЭТК / КД», not the internal «BOM»
        before["tooling"] = json.loads(json.dumps(before["tooling"], ensure_ascii=False).replace("ЭТК / BOM", "ЭТК / КД"))
    assert {key: value for key, value in before.items() if key != "steps"} == \
           {key: value for key, value in after.items() if key != "steps"}


def test_no_bom_anywhere_in_the_new_records_or_the_erp_seed_sheets():
    for item in PAYLOAD:
        assert "BOM" not in json.dumps(item["record"], ensure_ascii=False), item["code"]
    for template in ERP:
        if template["title"].split(" | ", 1)[0] in BY_CODE:  # every row of the sheet: input, tooling, consumables
            assert "BOM" not in json.dumps(template["rows"], ensure_ascii=False), template["title"]
    paint = json.dumps([template for template in ERP if template["title"].startswith("MARK_CONN_PAINT |")],
                       ensure_ascii=False)
    assert paint.count("ЭТК / КД") >= 3


@pytest.mark.parametrize("item", PAYLOAD, ids=lambda item: item["code"])
def test_steps_are_short_actions_and_checks_without_pseudo_steps(item):
    steps = item["record"]["steps"]
    assert all(len(step["text"]) <= 300 for step in steps)
    assert not any(PSEUDO.match(step["text"]) for step in steps)
    assert not any("BOM" in f"{step['text']} {step['tolerance']}" for step in steps)
    assert {"Операция", "Контроль"} >= {step["kind"] for step in steps}
    assert any(step["kind"] == "Контроль" for step in steps)
    # A replaced paragraph becomes several rows; untouched steps keep their text.
    before = item["expected_before"]["steps"]
    for index, step in enumerate(before, start=1):
        rows = [row for row, origin in zip(steps, item["step_origin"]) if origin == index]
        if len(step["text"]) > 300:
            assert len(rows) >= 2 and all(row["text"] != step["text"] for row in rows), (item["code"], index)
        if len(rows) == 1 and not STEP_REF.search(f"{step['text']} {step['tolerance']}"):
            assert rows == [{**step, "number": rows[0]["number"]}], (item["code"], index)


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
CITATIONS = [re.compile(pattern) for pattern in (
    r"ГОСТ(?: Р)?(?: МЭК)? [\d.\-]+", r"IEC [\d\-]+", r"MIL-STD-\w+", r"СТО ФРАК-[\d\-]+", r"табл\. \d+",
    r"рис\. [А-ЯA-Z]?\.?\d+", r"прил\. [А-Я]", r"\bп\. [\d.]+", r"(?:ОБ|СИ)\.\d+(?:\.\w+)?")]
KEEP_WORDS = ("100 %", "мастер", "лупой", "×", "ДИ", "КД", "ТИ", "не допуска", "Испытанный", "не задано",
              "совместимость", "функциональные поверхности", "до касания", "протирании")


def _facts(steps) -> set[str]:
    text = " ".join(f"{step['text']} {step['tolerance']}" for step in steps)
    text = re.sub(r"переход\w*\s+\d+(?:\s*[–,-]\s*\d+)*", " ", text, flags=re.IGNORECASE)
    found = set(NUMBER.findall(text))
    for pattern in CITATIONS:
        found |= {match.rstrip(".") for match in pattern.findall(text)}
    return found | {word for word in KEEP_WORDS if word in text}


@pytest.mark.parametrize("item", PAYLOAD, ids=lambda item: item["code"])
def test_every_limit_unit_reference_and_coverage_survives(item):
    before = item["expected_before"]["steps"]
    if item["code"] == "MARK_CONN_PAINT":
        # The dropped declarations: input = the kit rows, result = the checks; the norm prose is the time block's.
        before = [step for step in before if not PSEUDO.match(step["text"])]
    assert _facts(before) <= _facts(item["record"]["steps"])


def test_paint_marking_keeps_compatibility_drying_and_clean_surfaces_without_declarations():
    steps = BY_CODE["MARK_CONN_PAINT"]["record"]["steps"]
    text = " ".join(step["text"] for step in steps)
    assert [step["kind"] for step in steps] == ["Операция"] * 5 + ["Контроль"] * 2
    assert "Время и температура сушки — не задано" in text and "совместимость краски с материалом корпуса" in text
    assert "Проверить отсутствие краски на контактах" in text and "СТО ФРАК-001-2026, раздел 4.6" in text
    assert "Вход" not in text and "Результат" not in text and "Норма времени" not in text


# Exact (quantity, unit) pairs per row, in order: a swapped limit (15 Н ↔ 35 Н) or a limit moved to another row fails.
QUANTITY = re.compile(r"(\d+(?:,\d+)?(?:\s*±\s*\d+(?:,\d+)?)?(?:\s*[–…]\s*\d+(?:,\d+)?)?)\)?\s*"
                      r"(мм²|мм/мин|мм|Н·м|Н|%|мин|кОм|Ом|°С|с)(?![а-яА-Я])"
                      r"|×\s*(\d+(?:,\d+)?)|(\d+(?:,\d+)?(?:\s*[–…]\s*\d+(?:,\d+)?)?)×")
QUANTITIES = {
    "PRS_TERMINAL_auto": {6: [("1,5", "мм")], 16: [("100", "%")]},
    "US_WELD": {
        1: [("0,08–200", "мм²")], 2: [("0,35", "мм²"), ("1,00", "мм²"), ("50", "мм")],
        6: [("0,08–0,35", "мм²"), ("10×", ""), ("0,35–0,75", "мм²"), ("3–7,5×", ""), ("0,75–1,5", "мм²"), ("1,5–3×", ""),
            ("1,5", "мм²"), ("1,5×", "")],
        10: [("×100", ""), ("95–120", "%")], 11: [("50 ± 5", "мм/мин")],
        12: [("0,5", "мм²"), ("15", "Н"), ("1,0", "мм²"), ("35", "Н"), ("2,5", "мм²"), ("70", "Н")],
        13: [("0,5", "мм²"), ("80", "Н"), ("1,0", "мм²"), ("160", "Н"), ("2,5", "мм²"), ("275", "Н")],
        18: [("100", "%")], 21: [("400", "мм"), ("10×", "")], 23: [("80", "Н")], 24: [("0,35", "мм²")]},
    "SDR_COAX_pin": {2: [("250", "°С"), ("10", "°С"), ("240–280", "°С")], 3: [("10", "°С")], 8: [("3", "с"), ("5", "с")],
                     11: [("100", "%")], 12: [("100", "%"), ("75", "%"), ("75", "%")], 13: [("100", "%")]},
    "STRIP_COAX_auto": {},
    "INS_housing_COAX": {4: [("100", "%"), ("3,5", "мм"), ("2,92", "мм")], 5: [("100", "%")],
                         6: [("100", "%"), ("10×", ""), ("4×", "")]},
}
# Exact standard and clause references per row, in order: a citation must stay with the criterion it supports.
CLAUSE = re.compile(r"\b(?:п|табл|прил|рис)\. [А-ЯA-Z]?\.?[\d.]*\d(?:[–-][\d.]*\d)?|ГОСТ(?: Р)?(?: МЭК)? [\d.]+-\d+")
CLAUSES = {
    "PRS_TERMINAL_auto": {
        1: ["ГОСТ Р 72182.2-2025", "п. 5.2"], 2: ["ГОСТ Р 72182.2-2025", "п. 5.4.6"],
        5: ["ГОСТ Р 72182.2-2025", "п. 7.1", "табл. 4", "табл. 1", "ГОСТ Р 72182.2-2025", "п. 5.4.6", "табл. 1"],
        6: ["ГОСТ 23592-96", "п. 4.6.7", "ГОСТ Р 72182.2-2025", "п. 5.5.2"], 8: ["ГОСТ Р 72182.2-2025", "п. 7.2.7"],
        10: ["ГОСТ 23592-96", "п. 4.6.8"], 11: ["ГОСТ Р 72182.2-2025", "рис. А.14"],
        12: ["ГОСТ Р 72182.2-2025", "рис. А.13"], 13: ["табл. 5", "ГОСТ Р 72182.2-2025", "табл. 5", "п. 7.2.1"],
        14: ["ГОСТ Р 72182.2-2025", "п. 7.3.1", "табл. 7"],
        16: ["ГОСТ Р 72182.2-2025", "ГОСТ Р 72182.2-2025", "п. 7.1", "табл. 4"], 17: ["ГОСТ Р 72182.2-2025"]},
    "STRIP_COAX_auto": {1: ["ГОСТ 3.1129-93", "п. 6.3"], 3: ["ГОСТ 23592-96", "п. 4.2.2"], 4: ["ГОСТ 3.1129-93", "п. 6.3"],
                        5: ["ГОСТ 3.1129-93", "п. 6.3"], 6: ["ГОСТ 3.1129-93", "п. 6.3"]},
}


def _quantities(step):
    return [(m.group(1), m.group(2)) if m.group(1) else (f"×{m.group(3)}", "") if m.group(3) else (f"{m.group(4)}×", "")
            for m in QUANTITY.finditer(f"{step['text']} || {step['tolerance']}")]


@pytest.mark.parametrize("code", sorted(QUANTITIES))
def test_key_cards_keep_each_limit_with_its_unit_on_its_row(code):
    steps = BY_CODE[code]["record"]["steps"]
    assert {int(step["number"]): _quantities(step) for step in steps if _quantities(step)} == QUANTITIES[code]
    if code in CLAUSES:
        found = {int(step["number"]): CLAUSE.findall(f"{step['text']} || {step['tolerance']}") for step in steps}
        assert {number: values for number, values in found.items() if values} == CLAUSES[code]


def _ranges(steps):
    for number, step in enumerate(steps, start=1):
        for match in STEP_REF.finditer(f"{step['text']} {step['tolerance']}"):
            first, last, extra = (int(value) if value else None for value in match.groups())
            yield number, list(range(first, (last or first) + 1)) + ([extra] if extra else [])


@pytest.mark.parametrize("item", PAYLOAD, ids=lambda item: item["code"])
def test_step_references_stay_inside_the_card_and_before_the_step(item):
    steps = item["record"]["steps"]
    for number, targets in _ranges(steps):
        assert all(1 <= target < number for target in targets), (item["code"], number, targets)


@pytest.mark.parametrize(("code", "number", "references", "word"), [
    ("PRS_TERMINAL_auto", 15, [[4], list(range(5, 15))], None),
    ("PRS_TERMINAL_auto", 16, [list(range(5, 11))], None),
    ("PRS_TERMINAL_auto", 17, [list(range(11, 14))], "обжим"),
    ("PRS_НШВИ", 11, [[3], list(range(4, 11))], None),
    ("PRS_НШВИ", 12, [list(range(4, 7))], "втулк"),
    ("PRS_НШВИ", 13, [list(range(7, 10))], "обжим"),
    ("STRIP_COAX_auto", 9, [list(range(3, 8))], None),
    ("US_WELD", 18, [list(range(6, 10))], None),
    ("US_WELD", 25, [list(range(6, 14))], None),
])
def test_renumbered_references_name_the_moved_checks(code, number, references, word):
    steps = BY_CODE[code]["record"]["steps"]
    assert [targets for at, targets in _ranges(steps) if at == number] == references
    checks = references[-1]
    assert all(steps[target - 1]["kind"] == "Контроль" for target in checks), (code, number)
    if word:
        assert all(word in steps[target - 1]["text"] for target in checks), (code, number)


# ── builder anchors (etk_builder._resolve_card) ──

def _resolved(code, fields, which="record"):
    item = BY_CODE[code]
    record = item["record"] if which == "record" else _before(item)
    return _resolve_card(_card(record, item), {}, fields)


@pytest.mark.parametrize("code", ["PRS_TERMINAL_auto", "PRS_НШВИ", "CUT_WIRE+1crimp", "CUT_WIRE+2crimp",
                                  "CUT_WIRE+1crimp+tin"])
def test_crimp_height_and_pull_force_land_on_the_same_checks_as_before(code):
    fields = {"crimpHeight": "0,40–0,44", "pullForce": "9,8", "cutTolerance": "±3 мм"}
    filled = {}
    for which in ("before", "record"):
        steps = _resolved(code, fields, which)["steps"]
        heights = [step["text"] for step in steps if step["tolerance"] == "0,40–0,44 мм"]
        pulls = [step["text"] for step in steps if step["tolerance"] == "9,8 Н"]
        assert all("геометр" in text for text in heights) and all("разрыв" in text for text in pulls), code
        filled[which] = (len(heights), len(pulls))
    assert filled["record"] == filled["before"], code


@pytest.mark.parametrize("code", ["INS_housing_COAX", "INS_housing_COAX_clamp"])
def test_contact_depth_goes_to_the_measuring_check_and_stays_short(code):
    steps = _resolved(code, {"contactDepth": "5,33…5,84", "collet": False})["steps"]
    [first] = [step for step in steps if step["kind"] == "Контроль"][:1]
    assert first["text"].startswith("Измерить заглубление центрального контакта")
    assert first["text"].endswith("Проверить заглубление контакта от торца корпуса: 5,33…5,84 мм.")
    assert first["tolerance"].startswith("по КД; при отсутствии в КД — по интерфейсу серии")
    assert all(len(step["text"]) <= 300 for step in steps)
    assert sum("заглубление контакта от торца корпуса:" in step["text"] for step in steps) == 1


def test_crimped_pin_wording_and_the_single_vswr_check_still_resolve():
    housing = _resolved("INS_housing_COAX", {"crimpedPin": True})["steps"]
    assert any("обжатый центральный контакт" in step["text"] for step in housing)
    vswr = BY_CODE["CTRL_VSWR_coax"]["record"]["steps"]
    assert [step["kind"] for step in vswr] == ["Операция", "Операция", "Операция", "Контроль", "Операция"]
    assert [step for step in vswr if "КСВН" in step["text"] and step["kind"] == "Контроль"] == [vswr[3]]
    [connect] = [step for step in vswr if "к порту 2" in step["text"]]
    assert connect["tolerance"] == "N — 1,1–1,5 Н·м; 3,5 мм и SMA — 0,8–1,0 Н·м"


# ── seeds ──

def _cards(templates):
    return {card.code: card for card in parse_operation_library(templates)}


def test_seeds_parse_and_carry_no_long_or_pseudo_steps_for_the_revised_codes():
    base, erp = _cards(BASE), _cards(ERP)
    for item in PAYLOAD:
        for library in (base, erp):
            card = library.get(item["code"])
            if card is not None:
                assert card.source_id == item["template_id"], item["code"]
        card = erp.get(item["code"])
        if card is not None:
            assert all(len(step.text) <= 300 and not PSEUDO.match(step.text) and "BOM" not in step.text
                       for step in card.steps), item["code"]
    for code in ("CTRL_VSWR_coax", "MARK_CONN_PAINT"):  # the ERP seed IS the production delivery: same content
        assert SCRIPT.card_hash(erp[code]) == BY_CODE[code]["after_hash"], code
    # The base seed is the SUPPLYMAP БД.ОП-ТК snapshot (test_operation_cards: 714 steps) and only fills an empty
    # library; production left it with the 22.10/23.10 audits. It is not edited here.
    assert sum(len(card.steps) for card in base.values()) == 714


def test_artifacts_have_lf_line_endings():
    for path in (FOLDER / "payload.json", FOLDER / "apply_readability.py", FOLDER / "README.md",
                 ROOT / "backend/data/techcard-library-erp.json"):
        assert b"\r\n" not in path.read_bytes(), path.name


# ── guarded script ──

ACTOR = Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство")
SUBSET = ["PRS_TERMINAL_auto", "INS_housing_COAX", "MARK_CONN_PAINT", "CTRL_VSWR_coax"]
VSWR_SOURCE = ROOT / "docs/data/etk-vswr-ctrl-1118/payload.json"


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def _uow(sessions):
    return NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions))


def _production(sessions, codes=SUBSET, editor="Аудит ГОСТ Р 23.10.2026"):
    """The library as production holds it: the base cards with the audit record edits; the VSWR card as the 18.11
    sheet signed by the ERP delivery; MARK_CONN_PAINT created and edited by the 16.11 marking delivery."""
    templates = {template["id"]: template for template in [*BASE, *ERP]}
    vswr = json.loads(VSWR_SOURCE.read_text(encoding="utf-8"))
    with _uow(sessions) as uow:
        cards = []
        for code in codes:
            if code == "CTRL_VSWR_coax":
                continue
            template = templates[BY_CODE[code]["template_id"]]
            cards.append(parse_operation_template(source_id=template["id"], title=template["title"],
                                                  category=template.get("category", ""), rows=template["rows"]))
        uow.operation_cards.load(tuple(cards), imported_at=NOW)
        for code in codes:
            if code == "CTRL_VSWR_coax":
                card = parse_operation_template(source_id=vswr["id"], title=vswr["title"], category=vswr["category"],
                                                rows=vswr["rows"])
                uow.operation_cards.save_sheet(card, description="Шаблон ERP", saved_at=NOW, saved_by=ERP_TEMPLATE_AUTHOR)
                continue
            who = "Маркировка — карты 16.11.2026" if code == "MARK_CONN_PAINT" else editor
            uow.operation_cards.edit(code, normalize_record_edit(_before(BY_CODE[code])), changed_at=NOW, changed_by=who)
        uow.commit()


def _payload(codes=SUBSET):
    return [BY_CODE[code] for code in codes]


def _run(sessions, *, codes=SUBSET, expect=None, commit=True):
    with _uow(sessions) as uow:
        result, plan, rollback = SCRIPT.apply_readability(uow, who=SCRIPT.actor(), now=NOW, payload=_payload(codes),
                                                          expect=expect, lock=commit)
        if commit:
            uow.commit()
    return result, plan, rollback


def _hashes(sessions):
    with _uow(sessions) as uow:
        return {card.code: SCRIPT.card_hash(card) for card in uow.operation_cards.list()}


def test_dry_run_plan_then_apply_writes_exactly_the_new_content(sessions):
    _production(sessions)
    result, plan, _ = _run(sessions, commit=False)
    assert [len(result[key]) for key in SCRIPT.OUTCOMES] == [4, 0, 0, 0, 0]
    assert _hashes(sessions) == {code: BY_CODE[code]["expected_before_hash"] for code in SUBSET}  # rolled back
    assert {entry["code"]: entry["mode"] for entry in plan["templates"]} == {
        "PRS_TERMINAL_auto": "record", "INS_housing_COAX": "record", "MARK_CONN_PAINT": "record",
        "CTRL_VSWR_coax": "sheet"}

    result, _, rollback = _run(sessions, expect=plan)
    assert len(result["changed"]) == 4 and len(rollback["templates"]) == 4
    assert _hashes(sessions) == {code: BY_CODE[code]["after_hash"] for code in SUBSET}
    with sessions() as session:
        events = session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "techcards.operation_template_readability")).all()
        assert sorted(event.details["code"] for event in events) == sorted(SUBSET)
        assert session.scalar(select(RefreshRequestRow).where(RefreshRequestRow.reason == "template")) is not None
        vswr = session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == "CTRL_VSWR_coax")).one()
        assert vswr.edited is None and vswr.saved_by == ERP_TEMPLATE_AUTHOR  # the seed keeps owning the sheet
        paint = session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == "MARK_CONN_PAINT")).one()
        assert paint.edited_by == SCRIPT.AUTHOR

    again, _, _ = _run(sessions)
    assert [len(again[key]) for key in SCRIPT.OUTCOMES] == [0, 4, 0, 0, 0]
    seeded = seed_erp_templates(SeedErpTemplatesCommand(tuple(ERP), ACTOR, NOW), SqlAlchemyTechcardsUnitOfWork(sessions))
    assert "CTRL_VSWR_coax | Контроль КСВН" not in seeded["updated"]  # the release seed finds its own content


def test_unknown_content_and_a_persons_edit_are_refused_and_kept(sessions):
    _production(sessions)
    with _uow(sessions) as uow:
        drifted = normalize_record_edit(_before(BY_CODE["PRS_TERMINAL_auto"]))
        drifted = drifted.__class__(**{**drifted.__dict__, "steps": drifted.steps[:-1]})
        uow.operation_cards.edit("PRS_TERMINAL_auto", drifted, changed_at=NOW, changed_by="Аудит ГОСТ Р 23.10.2026")
        uow.operation_cards.edit("INS_housing_COAX", normalize_record_edit(_before(BY_CODE["INS_housing_COAX"])),
                                 changed_at=NOW, changed_by="Иванов И. И.")
        uow.commit()
    before = _hashes(sessions)
    result, plan, _ = _run(sessions)
    assert [entry["outcome"] for entry in plan["templates"]] == ["drifted", "protected", "changed", "changed"]
    after = _hashes(sessions)
    assert after["PRS_TERMINAL_auto"] == before["PRS_TERMINAL_auto"]
    assert after["INS_housing_COAX"] == before["INS_housing_COAX"] == BY_CODE["INS_housing_COAX"]["expected_before_hash"]
    assert len(result["drifted"]) == len(result["protected"]) == 1


def test_a_plan_that_no_longer_matches_writes_nothing(sessions):
    _production(sessions)
    _, plan, _ = _run(sessions, commit=False)
    with _uow(sessions) as uow:
        uow.operation_cards.edit("MARK_CONN_PAINT", normalize_record_edit(_before(BY_CODE["MARK_CONN_PAINT"])),
                                 changed_at=datetime(2026, 11, 24, 9, 0), changed_by="Маркировка — карты 16.11.2026")
        uow.commit()
    with pytest.raises(ValueError, match="изменились после пробного прогона"):
        _run(sessions, expect=plan)
    assert _hashes(sessions) == {code: BY_CODE[code]["expected_before_hash"] for code in SUBSET}


def test_a_missing_or_renamed_template_is_reported_not_created(sessions):
    _production(sessions, codes=["PRS_TERMINAL_auto"])
    result, plan, _ = _run(sessions, codes=["PRS_TERMINAL_auto", "SDR_tin"])
    assert [entry["outcome"] for entry in plan["templates"]] == ["changed", "missing"]
    assert _hashes(sessions) == {"PRS_TERMINAL_auto": BY_CODE["PRS_TERMINAL_auto"]["after_hash"]}


def test_rollback_restores_the_columns_and_refuses_after_a_later_edit(sessions):
    _production(sessions)
    _, _, rollback = _run(sessions)
    rollback = json.loads(json.dumps(rollback, ensure_ascii=False))  # as the file round-trips it
    with _uow(sessions) as uow:
        assert sorted(SCRIPT.rollback_readability(rollback, uow, who=SCRIPT.actor(SCRIPT.ROLLBACK_AUTHOR), now=NOW)) == sorted(SUBSET)
        uow.commit()
    assert _hashes(sessions) == {code: BY_CODE[code]["expected_before_hash"] for code in SUBSET}
    with sessions() as session:
        vswr = session.scalars(select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == "CTRL_VSWR_coax")).one()
        assert vswr.edited is None and vswr.saved_by == ERP_TEMPLATE_AUTHOR

    _, _, rollback = _run(sessions)
    with _uow(sessions) as uow:
        uow.operation_cards.edit("PRS_TERMINAL_auto", normalize_record_edit(_before(BY_CODE["PRS_TERMINAL_auto"])),
                                 changed_at=NOW, changed_by="Иванов И. И.")
        uow.commit()
    with _uow(sessions) as uow, pytest.raises(ValueError, match="изменился после записи"):
        SCRIPT.rollback_readability(rollback, uow, who=SCRIPT.actor(SCRIPT.ROLLBACK_AUTHOR), now=NOW)


def test_seed_owned_cards_already_rewritten_by_the_release_seed_are_unchanged(sessions):
    """On production both ERP cards are seed sheets (no record edit): the deploy seed writes them first; the script then
    finds the target content and writes nothing — no event, no second write."""
    seeded = seed_erp_templates(SeedErpTemplatesCommand(tuple(ERP), ACTOR, NOW), SqlAlchemyTechcardsUnitOfWork(sessions))
    assert {"CTRL_VSWR_coax | Контроль КСВН", "MARK_CONN_PAINT | Цветовая маркировка разъёмов маркером-краской"} <= set(
        seeded["created"])
    codes = ["CTRL_VSWR_coax", "MARK_CONN_PAINT"]
    result, plan, rollback = _run(sessions, codes=codes)
    assert [(entry["outcome"], entry["mode"]) for entry in plan["templates"]] == [("unchanged", "sheet")] * 2
    assert result["unchanged"] and not result["changed"] and rollback == {"templates": []}
    with sessions() as session:
        assert not session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "techcards.operation_template_readability")).all()


@pytest.mark.parametrize("item", [item for item in PAYLOAD if item["prod_rewrites"]], ids=lambda item: item["code"])
def test_the_new_records_keep_the_production_instrument_numbers(item):
    """Production renumbered the instruments in place; the delivery file's old numbers must not come back."""
    rewrites = item["prod_rewrites"]["inventory_numbers"]
    numbers = {tool["inventory_number"] for tool in item["record"]["tooling"]}
    assert set(rewrites.values()) <= numbers and not set(rewrites) & numbers
