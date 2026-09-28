import json
from pathlib import Path

import pytest

from app.modules.techcards.operation_cards import (
    OperationCardImage,
    build_operation_records,
    parse_operation_library,
    parse_operation_template,
)


def _rows(title: str, *, revision: str = "2", step_text: str = "Обжать контакт") -> list[list[str]]:
    rows = [[title]] + [[] for _ in range(22)]
    rows[1] = ["ПРЕДПРИЯТИЕ", "", "ОБОЗНАЧЕНИЕ ДОКУМЕНТА", "", "", "РЕВИЗИЯ", "РМ", "ВАЖНО! ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:\nОтключить станок."]
    rows[2] = ["ООО «ЗЭП «ФРАКСИС»", "", "ТК.01", "", "", revision]
    rows[5] = ["Обозначение", "", "Содержание операции", "", "", "", "", "ТИПОВЫЕ ДЕФЕКТЫ: недожим."]
    rows[8] = ["Инструмент", "1", "Кримпер", "", "", "", "ОБ.001"]
    rows[9] = ["Оборудование", "1", "Пресс", "", "", "ОБ.002", "P-1"]
    rows[11] = ["Операция", "1", step_text, "", "", "", "0,8±0,05 мм"]
    rows[12] = ["Контроль", "2", "Проверить высоту", "", "", "", "КД"]
    return rows


def _template(title: str, source_id: str, *, revision: str = "2", step_text: str = "Обжать контакт", images=None):
    rows = _rows(title, revision=revision, step_text=step_text)
    return parse_operation_template(
        source_id=source_id, title=rows[0][0], category="Опрессовка терминалов", rows=rows, images=images,
    )


def test_parses_operation_card_semantics_without_losing_raw_rows():
    rows = _rows("PRS_TERMINAL_auto | Опрессовка терминалов (A)")
    card = parse_operation_template(
        source_id="prs-terminal-a",
        title=rows[0][0],
        category="Опрессовка терминалов",
        rows=rows,
    )

    assert card.code == "PRS_TERMINAL_auto"
    assert card.side == "A"
    assert card.name == "Опрессовка терминалов"
    assert card.safety.endswith("Отключить станок.")
    assert card.defects.endswith("недожим.")
    assert [step.kind for step in card.steps] == ["Операция", "Контроль"]
    assert card.tooling[1].inventory_number == "ОБ.002"
    assert card.tooling[1].program == "P-1"
    assert card.raw_rows == tuple(tuple(cell for cell in row) for row in rows)


def test_special_source_title_maps_to_own_operation_code():
    rows = _rows("PRS_CAP | Опрессовка крышки коакс")
    card = parse_operation_template(
        source_id="prs-cap-coax",
        title=rows[0][0],
        category="Опрессовка",
        rows=rows,
    )
    assert card.code == "PRS_CAP_COAX"


def test_library_rejects_duplicate_source_ids():
    rows = _rows("CUT_WIRE | Резка")
    item = {"id": "cut-wire", "title": rows[0][0], "category": "Резка", "rows": rows}
    try:
        parse_operation_library([item, item])
    except ValueError as error:
        assert "повтор" in str(error)
    else:
        raise AssertionError("duplicate source id was accepted")


def test_bundled_current_supplymap_library_is_fully_parseable():
    backend = Path(__file__).resolve().parents[2]
    payload = json.loads((backend / "data" / "techcard-library.json").read_text(encoding="utf-8"))
    metadata = json.loads((backend / "app" / "modules" / "techcards" / "SOURCE.json").read_text(encoding="utf-8"))
    cards = parse_operation_library(payload["templates"])

    assert payload["sourceCommit"] == metadata["operationLibrary"]["sourceCommit"]
    assert len(cards) == 115
    assert len({card.code for card in cards}) == 104
    assert sum(len(card.steps) for card in cards) == 768
    assert sum(len(card.tooling) for card in cards) == 334


def test_bundled_library_predates_two_machine_equipment_rows():
    """DRIFT-tc-bfef60d. SUPPLYMAP bfef60d gave CUT_COAX/STRIP_WIRE/PRS_ТМЛ two «Оборудование» rows
    (machine by cable size) in the БД.ТК mirror (_local/db/БД.ТК/*.tsv), but the bundled library comes
    from the older fixture tc-library.json, which still has one row. Pins that gap: when the library
    is re-imported from the TSV mirror this test must flip to two rows (ОБ.094/ОБ.095 for CUT_COAX)."""
    backend = Path(__file__).resolve().parents[2]
    payload = json.loads((backend / "data" / "techcard-library.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in payload["templates"]}

    def equipment(template_id):
        return [row[2].split(" | ")[0] for row in by_id[template_id]["rows"] if row and row[0] == "Оборудование"]

    assert equipment("cut-coax-1strip-резка-с-разделкой") == ["AUTO_CUT_STRIP_COAX_BIG"]
    assert equipment("cut-coax-2strip-резка-с-разделкой") == ["AUTO_CUT_STRIP_COAX_BIG"]
    assert equipment("strip-wire-разделка") == ["EW-1165"]
    assert equipment("prs-тмл-опрессовка-наконечников") == ["YQK-70"]


def test_bundled_library_resolves_to_one_record_per_code_without_error():
    """SUPPLYMAP TcRecord.gs tcRecordsByCode_ — the same grouping the bundled library must
    already satisfy: 11 codes come as an (A)/(B) pair, the rest are singles, and every pair
    either agrees or has a RECORD_SIDE_PICK owner (no unresolved divergence)."""
    backend = Path(__file__).resolve().parents[2]
    payload = json.loads((backend / "data" / "techcard-library.json").read_text(encoding="utf-8"))
    cards = parse_operation_library(payload["templates"])

    records = build_operation_records(cards)

    assert len(records) == 104
    paired = {record.code: record.sides for record in records if len(record.sides) == 2}
    assert paired == {
        "INS_housing_COAX": ("A", "B"), "INS_terminal_1.00": ("A", "B"), "INS_terminal_1.25": ("A", "B"),
        "INS_terminal_1.5": ("A", "B"), "INS_terminal_2": ("A", "B"), "INS_terminal_2,54-6,3": ("A", "B"),
        "PRS_TERMINAL_auto": ("A", "B"), "SDR_COAX_housing": ("A", "B"), "SDR_COAX_pin": ("A", "B"),
        "SDR_COAX_pin_R/A": ("A", "B"), "STRIP_COAX_auto": ("A", "B"),
    }


def test_revision_placeholder_becomes_empty():
    """SUPPLYMAP TcRecord.gs tcRecordNormalize_ / TcRecordDb.gs tcRecordDbRows_: a placeholder
    revision like «‹рев.›» is not a real value — it is dropped, not stored."""
    card = _template("PRS_TERMINAL_auto | Опрессовка терминалов", "prs-terminal", revision="‹рев.›")
    assert card.revision == ""


def test_real_revision_is_preserved():
    card = _template("PRS_TERMINAL_auto | Опрессовка терминалов", "prs-terminal", revision="7")
    assert card.revision == "7"


def test_template_images_are_carried_through_as_metadata():
    """SUPPLYMAP TcTemplates.gs tcLibraryBlocks_ anchors pictures over the template grid.
    ERP has no Drive-backed file storage for them yet, so only the placement metadata is kept."""
    images = [{"row": 1, "col": 2, "driveFileId": "1AbCdEf", "width": 320, "height": 180}]
    card = _template("PRS_TERMINAL_auto | Опрессовка терминалов", "prs-terminal", images=images)

    assert card.images == (OperationCardImage(row=1, col=2, drive_file_id="1AbCdEf", width=320, height=180),)
    assert card.document()["images"] == [
        {"row": 1, "col": 2, "drive_file_id": "1AbCdEf", "width": 320, "height": 180},
    ]


def test_library_item_without_images_key_has_no_images():
    rows = _rows("PRS_TERMINAL_auto | Опрессовка терминалов")
    item = {"id": "prs-terminal", "title": rows[0][0], "category": "Опрессовка", "rows": rows}
    cards = parse_operation_library([item])
    assert cards[0].images == ()


def test_non_pair_code_is_unaffected_by_grouping():
    card = _template("CUT_WIRE | Резка провода", "cut-wire")
    assert card.side == ""
    records = build_operation_records([card])
    assert len(records) == 1
    assert records[0].sides == ()
    assert records[0].code == "CUT_WIRE"


def test_pair_with_one_side_missing_keeps_a_single_record():
    """Only the (A) block of a code exists (the (B) block was never imported) — behaves like a
    single-sided record, not an error."""
    card = _template("PRS_TERMINAL_auto | Опрессовка терминалов (A)", "prs-terminal-a")
    records = build_operation_records([card])
    assert len(records) == 1
    assert records[0].sides == ("A",)


def test_matching_ab_pair_merges_into_one_record_with_name_without_side_suffix():
    card_a = _template("PRS_TERMINAL_auto | Опрессовка терминалов (A)", "prs-terminal-a")
    card_b = _template("PRS_TERMINAL_auto | Опрессовка терминалов (B)", "prs-terminal-b")

    records = build_operation_records([card_a, card_b])

    assert len(records) == 1
    record = records[0]
    assert record.code == "PRS_TERMINAL_auto"
    assert record.name == "Опрессовка терминалов"
    assert record.sides == ("A", "B")


def test_special_codes_pair_merges_regardless_of_side():
    """PRS_CAP_COAX / INS_MARK_HARNESS get their own code from the title regardless of which
    side the block is on (SPECIAL_CODES applied before side stripping) — the pair still merges
    like any other matching (A)/(B) pair once grouped by that code."""
    card_a = _template("PRS_CAP | Опрессовка крышки коакс (A)", "prs-cap-a")
    card_b = _template("PRS_CAP | Опрессовка крышки коакс (B)", "prs-cap-b")
    assert card_a.code == card_b.code == "PRS_CAP_COAX"

    records = build_operation_records([card_a, card_b])

    assert len(records) == 1
    assert records[0].sides == ("A", "B")


def test_divergent_sides_use_the_configured_owner():
    """RECORD_SIDE_PICK (SUPPLYMAP TC_REC_SIDE_PICK_) resolves a real SUPPLYMAP divergence:
    SDR_COAX_housing's B side owns the record when A and B disagree."""
    card_a = _template("SDR_COAX_housing | Пайка (A)", "sdr-housing-a", step_text="Пайка без просушки")
    card_b = _template("SDR_COAX_housing | Пайка (B)", "sdr-housing-b", step_text="Пайка с просушкой")

    records = build_operation_records([card_a, card_b])

    assert len(records) == 1
    record = records[0]
    assert record.sides == ("A", "B")
    assert record.steps[0].text == "Пайка с просушкой"


def test_divergent_sides_without_an_owner_raise():
    card_a = _template("CUSTOM_CODE | Операция (A)", "custom-a", step_text="Шаг A")
    card_b = _template("CUSTOM_CODE | Операция (B)", "custom-b", step_text="Шаг B")

    with pytest.raises(ValueError, match="без выбора владельца|расходятся"):
        build_operation_records([card_a, card_b])


def test_duplicate_code_without_side_information_raises():
    card_1 = _template("CUT_WIRE | Резка провода", "cut-wire-1")
    card_2 = _template("CUT_WIRE | Резка провода", "cut-wire-2")

    with pytest.raises(ValueError, match="без сторон"):
        build_operation_records([card_1, card_2])
