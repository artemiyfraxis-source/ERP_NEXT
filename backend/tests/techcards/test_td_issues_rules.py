"""Комплект ТД: обозначения, нормоконтроль и разделы СТО — чистые правила (td_issues.py, td_sto.py)."""

import csv
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.modules.techcards.td_issues import (
    TdIssue, TdIssueBlocked, TdIssueConflict, TdIssueInvalid, designations, normcontrol, parse_input, sto_sections,
)
from app.modules.techcards.td_sto import outside_sto, sto, sto_section

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
OPS_TSV = Path(__file__).resolve().parents[2] / "data" / "reference-mirror" / "БД.ОП.tsv"


def _people(**override) -> dict:
    people = {
        "developer": {"name": "Иванов", "position": "Технолог"},
        "checker": {"name": "Петров", "position": "Ведущий технолог"},
        "normcontrol": {"name": "Сидорова", "position": "Нормоконтролёр"},
        "approver": {"name": "Васильев", "position": "Главный инженер"},
        "agreed_vp": {"name": "Козлов", "position": "Представитель ВП"},
    }
    people.update(override)
    return people


def _operation(**override) -> dict:
    operation = {
        "position": 1, "code": "PRS_TERMINAL_auto", "connector": "A", "side": "A", "title": "Опрессовка контактов",
        "card": {"name": "Опрессовка", "steps": [
            {"number": "1", "kind": "Подготовка", "text": "Проверить аппликатор", "tolerance": ""},
            {"number": "2", "kind": "Контроль", "text": "Высота обжима", "tolerance": "1,20 мм"},
        ]},
        "components": [{"art": "SHF-001T-0.8BS", "kd": "BL2018TS", "name": "Контакт"}],
        "result": [{"name": "Провод с контактом", "norm": "1", "unit": "Шт."}],
        "times": {"operation_seconds": "4", "setup_seconds": "", "machine_seconds": ""},
    }
    operation.update(override)
    return operation


def _issue(operations=None, **signatories) -> TdIssue:
    snapshot = {"revision": 3, "document": {"operations": operations if operations is not None else [_operation()]}}
    return TdIssue.open(
        card_id=uuid4(), value=parse_input({"litera": "О1", "method_code": "85", "signatories": _people(**signatories)}),
        snapshot=snapshot, existing=[], next_sequence=1, actor="Инженер", now=NOW,
    )


def test_designations_follow_gost_3_1201():
    found = designations("ФРАК", "85", 1)

    assert found == {
        "vtd": "ФРАК.40185.00001", "mk": "ФРАК.10185.00001", "ke": "ФРАК.20185.00001", "ok": "ФРАК.60185.00001",
        "ok_tk": "ФРАК.60102.00001", "vm": "ФРАК.43185.00001", "vo": "ФРАК.42185.00001",
    }
    assert designations("ФРАК", "88", 123)["mk"] == "ФРАК.10188.00123"


def test_clean_issue_has_no_findings():
    assert normcontrol(_issue()) == []


def test_missing_signatories_and_same_normcontrol_block():
    issue = _issue(checker={"name": "", "position": ""}, agreed_vp={"name": "", "position": ""})
    codes = {(item["code"], item["severity"]) for item in normcontrol(issue)}

    assert ("signatory_missing", "blocking") in codes
    assert ("agreed_vp_missing", "warning") in codes
    with pytest.raises(TdIssueBlocked) as blocked:
        issue.moved("signing", NOW)
    assert all(item["severity"] == "blocking" for item in blocked.value.detail["findings"])


def test_normcontrol_equal_to_developer_is_rejected_on_input_and_flagged_in_the_snapshot():
    with pytest.raises(TdIssueInvalid, match="Н.контр."):
        parse_input({"method_code": "85", "signatories": _people(normcontrol={"name": " иванов ", "position": ""})})
    # Подписанты, записанные мимо входа (старый JSON), нормоконтроль всё равно не пропустит.
    issue = _issue()
    forged = TdIssue(**{**issue.__dict__, "signatories": _people(normcontrol={"name": "Петров", "position": ""})})
    assert [item["code"] for item in normcontrol(forged)] == ["normcontrol_same_person"]


def test_unset_tolerance_and_latin_codes_block():
    operation = _operation(
        title="Опрессовка cutWireStrip",
        card={"name": "Опрессовка", "steps": [
            {"number": "2", "kind": "Контроль", "text": "Обжать по HEX_TABLE", "tolerance": "Размер HEX не задан"},
        ]},
        result=[{"name": "ПФ INS_housing_GX", "norm": "1", "unit": "Шт."}],
    )
    findings = normcontrol(_issue([operation]))
    blocking = {item["code"]: item for item in findings if item["severity"] == "blocking"}

    assert set(blocking) == {"tolerance_missing", "internal_code"}
    assert "внутренний код HEX_TABLE, INS_housing_GX —" in blocking["internal_code"]["text"]  # cutWireStrip без «_»
    assert blocking["internal_code"]["operation_key"] == "PRS_TERMINAL_auto::A::A"


def test_operation_code_field_itself_is_not_a_printed_text():
    assert not [item for item in normcontrol(_issue([_operation(code="CUT_WIRE+1strip")])) if item["code"] == "internal_code"]


def test_template_code_prefix_in_operation_name_is_not_a_finding():
    # Шаблоны ЭТК называются «CUT_WIRE_auto | Резка»; печать убирает префикс — нормоконтроль не блокирует выпуск.
    operation = _operation(title="CUT_WIRE_auto | Резка", card={"name": "CUT_WIRE+1strip | Резка + зачистка", "steps": [
        {"number": "1", "kind": "Контроль", "text": "Контролировать длину", "tolerance": "±5 мм"},
    ]})
    findings = normcontrol(_issue([operation]))
    assert not [item for item in findings if item["code"] == "internal_code"]


def test_warnings_for_missing_control_time_kd_and_sto_section():
    operation = _operation(
        code="Handjob", card={"name": "Сборка", "steps": [{"number": "1", "kind": "Работа", "text": "Собрать", "tolerance": ""}]},
        components=[{"art": "39012080", "kd": "", "name": "Корпус"}, {"art": "39012080", "kd": "", "name": "Корпус"}],
        times={"operation_seconds": "0"},
    )
    findings = normcontrol(_issue([operation, _operation(code="OTK_visual", connector="B")]))
    by_key = {}
    for item in findings:
        by_key.setdefault(item["operation_key"], []).append(item["code"])

    assert sorted(by_key["Handjob::A::A"]) == ["component_without_kd", "no_control_step", "no_sto_section", "no_time_norm"]
    assert "OTK_visual::B::A" not in by_key  # ОТК — вне СТО, раздел не требуется
    assert all(item["severity"] == "warning" for item in findings)


def test_transitions_and_draft_only_edits():
    issue = _issue()
    value = parse_input({"method_code": "88", "signatories": _people()})

    signing = issue.moved("signing", NOW)
    with pytest.raises(TdIssueConflict):
        signing.edited(value)
    with pytest.raises(TdIssueConflict):
        signing.moved("annulled", NOW)
    active = signing.moved("active", NOW)
    assert active.approved_at == NOW and active.version == issue.version + 2
    with pytest.raises(TdIssueConflict):
        active.moved("draft", NOW)
    assert active.moved("annulled", NOW).annulled_at == NOW
    assert issue.moved("annulled", NOW).status == "annulled"
    assert signing.moved("draft", NOW).status == "draft"
    with pytest.raises(TdIssueConflict):
        issue.moved("active", NOW)


def test_change_needs_notice_and_reason():
    first = _issue()
    value = parse_input({"method_code": "85", "signatories": _people()})
    with pytest.raises(TdIssueInvalid, match="извещения"):
        TdIssue.open(card_id=first.project_card_id, value=value, snapshot=first.snapshot,
                     existing=[first.moved("annulled", NOW)], next_sequence=7, actor="И", now=NOW)
    second = TdIssue.open(
        card_id=first.project_card_id, value=parse_input({**value.__dict__, "notice_no": "ИИ-1", "reason": "Новая ревизия"}),
        snapshot=first.snapshot, existing=[first.moved("annulled", NOW)], next_sequence=7, actor="И", now=NOW,
    )
    assert (second.change_no, second.sequence_no) == (1, first.sequence_no)


def test_every_known_operation_family_has_an_sto_section():
    codes = {row[0] for row in csv.reader(OPS_TSV.open(encoding="utf-8"), delimiter="\t") if row and row[0]}
    expected = {
        "CUT_WIRE+1strip": "4.1, 4.2", "CUT_COAX+2strip": "4.1, 4.2", "CUT_гофра": "4.1", "STRIP_F/FTP_ethernet": "4.2",
        "PRS_TERMINAL_auto": "4.3", "PRS_SLEEVE_coax": "4.3", "PRS_НШВИ": "4.3", "SDR_tin": "4.4.1", "SDR_W_W": "4.4.4",
        "SDR_COAX_pin_R/A": "4.4.5", "SDR_GX12": "4.4", "HEAT_TYT_short": "4.5", "INS_TUT": "4.5", "INS_SLEEVE_coax": "4.5",
        "INS_WRAP": "4.5", "INS_BRAID": "4.5", "INS_NEYLON": "4.5", "INS_CAP": "4.5", "INS_END_CAP": "4.5",
        "INS_tape": "4.5", "INS_TAPE_AUTO": "4.5", "INS_MARK": "4.6", "INS_MARK_TUBE_AUTO": "4.6", "INS_ПМЛ": "4.6",
        "LASER_MARK": "4.6", "PRINT_MARK": "4.6", "INS_housing_GX": "4.7", "INS_housing_DB/DH/СНП": "4.7",
        "INS_terminal_2,54-6,3": "4.7", "INS_lock": "4.7", "INS_RJ_cat6/7": "4.7", "INS_housing_COAX": "4.8",
        "INS_housing_COAX_clamp": "4.8", "PRS_CAP_COAX": "4.8", "Str-Str long": "4.8", "INS_стяжки": "4.9",
        "INS_гофра_разрез": "4.9", "TWIST": "4.10", "TWIST_BAY": "4.10", "TWST_SHILED": "4.10", "MOLD_auto": "4.11",
        "SEAL": "4.11", "PLASM": "4.11", "US_WELD": "4.11", "PREP_FOLD": "4.12",
    }
    assert set(expected) <= codes, set(expected) - codes  # примеры — настоящие коды БД.ОП
    assert {code: (sto_section(code) or {}).get("section") for code in expected} == expected
    families = ("CUT_", "STRIP_", "PRS_", "SDR_", "HEAT_", "INS_", "TWIST", "MOLD_")
    unresolved = sorted(code for code in codes if code.startswith(families) and sto_section(code) is None)
    assert unresolved == []
    assert sto_section("PRS_TERMINAL_auto") == {"section": "4.3", "title": "Опрессовка (обжим) контактов и наконечников"}
    assert sto_section("SDR_tin")["title"] == "Лужение"
    # Совмещённая операция — все её разделы СТО по порядку.
    assert sto_section("CUT_WIRE+1crimp+tin") == {
        "section": "4.1, 4.3, 4.4.1", "title": "Резка; Опрессовка (обжим) контактов и наконечников; Лужение",
    }
    assert sto_section("") is None and outside_sto("OTK_auto") and outside_sto("BOX") and not outside_sto("Handjob")


def test_unknown_code_has_no_section_and_a_warning():
    findings = normcontrol(_issue([_operation(code="ZZZ_new")]))

    assert sto_section("ZZZ_new") is None
    assert [item["code"] for item in findings] == ["no_sto_section"]
    assert sto_sections({"document": {"operations": [_operation(code="ZZZ_new")]}}) == {"ZZZ_new::A::A": None}


def test_sto_file_is_the_standard():
    assert sto()["designation"] == "СТО ФРАК-001-2026"
