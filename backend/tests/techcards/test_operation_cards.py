import json
from pathlib import Path

import pytest

from app.modules.techcards.operation_cards import (
    OperationCardImage,
    normalize_record_edit,
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
    rows = _rows("PRS_TERMINAL_auto | Опрессовка терминалов")
    card = parse_operation_template(
        source_id="prs-terminal",
        title=rows[0][0],
        category="Опрессовка терминалов",
        rows=rows,
    )

    assert card.code == "PRS_TERMINAL_auto"
    assert card.name == "Опрессовка терминалов"
    assert "side" not in card.document()
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


def test_bundled_library_is_fully_parseable():
    backend = Path(__file__).resolve().parents[2]
    payload = json.loads((backend / "data" / "techcard-library.json").read_text(encoding="utf-8"))
    cards = parse_operation_library(payload["templates"])

    # 0192: the 11 (A)/(B) pairs merged — one template per code (the БД.ОП-ТК mirror's 714 steps / 309 tooling rows)
    assert len(cards) == len({card.code for card in cards}) == 107
    assert sum(len(card.steps) for card in cards) == 714
    assert sum(len(card.tooling) for card in cards) == 309


def _bundled_equipment():
    backend = Path(__file__).resolve().parents[2]
    payload = json.loads((backend / "data" / "techcard-library.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in payload["templates"]}

    def equipment(template_id):
        return [
            (row[2].split(" | ")[0], row[5] if len(row) > 5 else "")
            for row in by_id[template_id]["rows"] if row and row[0] == "Оборудование" and len(row) > 2
        ]

    return by_id, equipment


def test_bundled_library_has_two_machine_equipment_rows():
    """DRIFT-tc-bfef60d + TC-TSV-IMPORT. The library is built from the БД.ТК mirror
    (_local/db/БД.ТК/*.tsv), where CUT_COAX/STRIP_WIRE/PRS_ТМЛ carry two «Оборудование» rows (machine
    by cable size, chosen by the operator) and CUT_NEYLON one."""
    _, equipment = _bundled_equipment()

    assert equipment("cut-coax-1strip-резка-с-разделкой") == [("BZW-886+Q2", "ОБ.094"), ("BZW-50X", "ОБ.095")]
    assert equipment("cut-coax-2strip-резка-с-разделкой") == [("BZW-886+Q2", "ОБ.094"), ("BZW-50X", "ОБ.095")]
    assert equipment("strip-wire-разделка") == [("EW-1090", "ОБ.007"), ("BZW-D2.0S(R)", "ОБ.100.X")]
    assert equipment("prs-тмл-опрессовка-наконечников") == [("YQK-70", "ОБ.056"), ("BZW-20TS", "ОБ.093")]
    assert equipment("cut-neylon-резка-линейных-материалов") == [("BZW-200+H", "ОБ.096")]


def test_bundled_library_two_machine_rows_reach_parsed_tooling():
    backend = Path(__file__).resolve().parents[2]
    payload = json.loads((backend / "data" / "techcard-library.json").read_text(encoding="utf-8"))
    cards = {card.source_id: card for card in parse_operation_library(payload["templates"])}

    for template_id in ("cut-coax-1strip-резка-с-разделкой", "prs-тмл-опрессовка-наконечников"):
        machines = [tool for tool in cards[template_id].tooling if tool.section == "Оборудование"]
        assert len(machines) == 2
        assert [tool.number for tool in machines] == ["1", "2"]


def test_bundled_library_has_the_three_new_solder_templates():
    """TC-TSV-IMPORT: sdr-banana / sdr-pcb / sdr-нк exist only in the mirror, not in the old fixture."""
    backend = Path(__file__).resolve().parents[2]
    payload = json.loads((backend / "data" / "techcard-library.json").read_text(encoding="utf-8"))
    cards = {card.source_id: card for card in parse_operation_library(payload["templates"])}

    assert {(cards[i].code, cards[i].category) for i in (
        "sdr-banana-пайка", "sdr-pcb-пайка-на-плату", "sdr-нк-пайка",
    )} == {("SDR_BANANA", "Пайка"), ("SDR_PCB", "Пайка на плату"), ("SDR_НК", "Пайка")}
    assert all(cards[i].steps and cards[i].tooling for i in cards if i.startswith(("sdr-banana", "sdr-pcb", "sdr-нк")))


def test_bundled_library_strip_templates_without_equipment_keep_the_mirror_state():
    """TC-TSV-IMPORT: 12 strip templates have a bare «Оборудование» header in the mirror (the fixture's
    EW-1165 coax stripper was dropped there; the task text said 11). Kept as the mirror has it — no
    tooling row, no error."""
    backend = Path(__file__).resolve().parents[2]
    payload = json.loads((backend / "data" / "techcard-library.json").read_text(encoding="utf-8"))
    cards = {card.source_id: card for card in parse_operation_library(payload["templates"])}
    _, equipment = _bundled_equipment()

    bare = [
        "strip-coax-hand-разделка", "strip-cable-sheld-разделка", "strip-cable-usb-2-0-разделка",
        "strip-cable-usb-3-2-разделка",
    ] + [f"strip-{kind}-ethernet-разделка" for kind in (
        "u-utp", "f-utp", "s-utp", "sf-utp", "u-ftp", "f-ftp", "s-ftp", "sf-ftp",
    )]
    assert len(bare) == 12
    for template_id in bare:
        assert equipment(template_id) == []
        assert not [tool for tool in cards[template_id].tooling if tool.section == "Оборудование"]


def test_bundled_libraries_have_one_template_per_code():
    """Owner 17.11.2026 «шаблоны (A)/(B) искоренять»: the bundled libraries carry one template per operation code,
    no side suffix in titles or ids (a fresh database must satisfy UNIQUE(code) of migration 0192)."""
    backend = Path(__file__).resolve().parents[2]
    cards = []
    for name in ("techcard-library.json", "techcard-library-erp.json"):
        payload = json.loads((backend / "data" / name).read_text(encoding="utf-8"))
        cards.extend(parse_operation_library(payload["templates"]))

    codes = [card.code for card in cards]
    assert len(codes) == len(set(codes)) == 147  # +PRS_PIN_coax (свод 708К. 4/5), +PRINT_MARK (TP80E, 20.11)
    assert not [card.title for card in cards if card.title.endswith(("(A)", "(B)", "(А)", "(В)"))]
    assert not [card.source_id for card in cards if card.source_id.endswith(("-a", "-b"))]
    housing = next(card for card in cards if card.code == "SDR_COAX_housing")
    assert housing.source_id == "sdr-coax-housing-пайка-экрана"
    # owner 17.09: side B's text owns SDR_COAX_housing; side A's picture is kept
    assert any("5,0 сек" in step.tolerance for step in housing.steps)
    assert len(housing.images) == 1


def test_library_with_two_templates_of_one_code_is_refused():
    rows = _rows("CUT_WIRE | Резка провода")
    items = [{"id": "cut-wire-1", "title": rows[0][0], "category": "Резка", "rows": rows},
             {"id": "cut-wire-2", "title": "cut_wire | Резка провода", "category": "Резка", "rows": rows}]
    with pytest.raises(ValueError, match="больше одного шаблона: CUT_WIRE"):
        parse_operation_library(items)


def test_side_suffix_is_no_longer_a_side():
    card = _template("PRS_TERMINAL_auto | Опрессовка терминалов (A)", "prs-terminal-a")
    assert (card.code, card.name) == ("PRS_TERMINAL_auto", "Опрессовка терминалов (A)")


def test_every_bundled_record_saves_back_through_the_record_editor_with_its_step_kinds():
    """SUPPLYMAP 95251c0/f2d87f8 tcRecordResyncFromTemplate: the template parser put the step kind in `section` while
    tcRecordSave_ read `type`, so resync failed on the first step of any code. In ERP the parser and the record editor
    share one step shape (`kind`): a record built from any bundled template must pass the editor's normalization
    unchanged in kinds and texts."""
    backend = Path(__file__).resolve().parents[2]
    payload = json.loads((backend / "data" / "techcard-library.json").read_text(encoding="utf-8"))
    records = parse_operation_library(payload["templates"])

    for record in records:
        edit = normalize_record_edit({
            "name": record.name or record.code, "safety": record.safety, "defects": record.defects,
            "revision": record.revision,
            "steps": [step.__dict__ for step in record.steps],
            "tooling": [tool.__dict__ for tool in record.tooling],
        })
        assert [(step.kind, step.text) for step in edit.steps] == [(step.kind, step.text) for step in record.steps], record.code


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
