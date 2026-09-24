import json
from pathlib import Path

from app.modules.techcards.operation_cards import parse_operation_library, parse_operation_template


def _rows(title: str) -> list[list[str]]:
    rows = [[title]] + [[] for _ in range(22)]
    rows[1] = ["ПРЕДПРИЯТИЕ", "", "ОБОЗНАЧЕНИЕ ДОКУМЕНТА", "", "", "РЕВИЗИЯ", "РМ", "ВАЖНО! ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:\nОтключить станок."]
    rows[2] = ["ООО «ЗЭП «ФРАКСИС»", "", "ТК.01", "", "", "2"]
    rows[5] = ["Обозначение", "", "Содержание операции", "", "", "", "", "ТИПОВЫЕ ДЕФЕКТЫ: недожим."]
    rows[8] = ["Инструмент", "1", "Кримпер", "", "", "", "ОБ.001"]
    rows[9] = ["Оборудование", "1", "Пресс", "", "", "ОБ.002", "P-1"]
    rows[11] = ["Операция", "1", "Обжать контакт", "", "", "", "0,8±0,05 мм"]
    rows[12] = ["Контроль", "2", "Проверить высоту", "", "", "", "КД"]
    return rows


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
