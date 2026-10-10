"""Server-built documents of the harness drawing (harness/documents.py): connection table, cut list and cut sheets,
specification. Golden rows: the browser builders of TECHMAP-GRAPHER (drawing-documents.ts, cut-diagram.ts,
live-cut-list.ts at e63befb) run on the scenarios of their own tests (drawing-documents.test.ts, cut-diagram.test.ts,
connection-table.test.ts), exported once before the builders were removed — the port must give the same rows."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.modules.techcards.harness.documents import (
    bom_rows, connection_rows, cut_list, documents, excel_text, key_of, table, terminal_article_label,
)

GOLDEN = json.loads((Path(__file__).parent / "harness_documents_golden.json").read_text(encoding="utf-8"))
CASES = {case["name"]: case for case in GOLDEN}


def _bom(row: dict) -> dict:
    return {"key": row["key"], "position": row["position"], "index": row["index"], "designation": row["designation"],
            "name": row["name"], "amount": row["amount"], "unit": row["unit"], "note": row["note"],
            "objectIds": row["object_ids"]}


def _connection(row: dict) -> dict:
    return {"wireId": row["wire_id"], "index": row["index"], "mark": row["mark"], "section": row["section"],
            "marking": row["marking"], "from": row["from"], "to": row["to"], "color": row["color"],
            "colorHex": row["color_hex"], "lengthMm": row["length_mm"], "route": row["route"]}


def _cut_item(item: dict) -> dict:
    return {
        "wireId": item["id"], "sourceKind": item["kind"], "circuit": item["title"],
        "material": item["material"] if item["material"] is not None else "not-pinned",
        "materialSourceKey": item["material_key"], "materialDisplayName": item["material"],
        "sourceLengthMm": item["length_mm"], "endCorrectionFromMm": item["correction_a_mm"],
        "endCorrectionToMm": item["correction_b_mm"], "roundingStepMm": item["rounding_step_mm"],
        "cutLengthMm": item["cut_mm"], "pieces": item["pieces"], "totalMetres": item["total_m"],
        "status": item["status"], "warnings": item["warnings"],
    }


def _end(end: dict) -> dict:
    return {"label": end["label"], "profileName": end["profile"],
            "steps": [{"index": s["index"], "diameterMm": s["diameter_mm"], "cumulativeLengthMm": s["length_mm"],
                       "stepLengthMm": s["step_mm"]} for s in end["steps"]]}


def _sheet(sheet: dict, item: dict, quantity: int) -> dict:
    sheath = sheet["sheath"]
    return {
        "objectId": sheet["object_id"], "memberWireIds": sheet["member_wire_ids"], "title": sheet["title"],
        "kind": sheet["kind"], "material": sheet["material"], "sourceLengthMm": item["length_mm"],
        "correctionA": item["correction_a_mm"], "correctionB": item["correction_b_mm"],
        "roundingMm": item["rounding_step_mm"], "cutLengthMm": item["cut_mm"], "unroundedMm": item["unrounded_mm"],
        "quantity": quantity, "totalMetres": item["total_m"], "a": _end(sheet["a"]), "b": _end(sheet["b"]),
        "sheath": None if sheath is None else {"fromMm": sheath["a_mm"], "toMm": sheath["b_mm"],
                                               "totalMm": sheath["total_mm"], "isComplete": sheath["complete"]},
        "conductors": [{"id": c["id"], "a": _end(c["a"]), "b": _end(c["b"])} for c in sheet["conductors"]],
        "warnings": sheet["warnings"],
    }


@pytest.mark.parametrize("name", list(CASES))
def test_bom_matches_the_browser_builder_row_for_row(name: str) -> None:
    case = CASES[name]
    expected = [{k: v for k, v in row.items() if k != "sourceIdentity"} for row in case["bom"]]
    assert [_bom(row) for row in bom_rows(case["document"], case["quantity"])] == expected


@pytest.mark.parametrize("name", list(CASES))
def test_connection_table_matches_the_browser_builder_row_for_row(name: str) -> None:
    case = CASES[name]
    assert [_connection(row) for row in connection_rows(case["document"])] == case["connections"]


@pytest.mark.parametrize("name", list(CASES))
def test_cut_list_and_sheets_match_the_browser_builders(name: str) -> None:
    case = CASES[name]
    built = cut_list(case["document"], case["quantity"])
    assert [_cut_item(item) for item in built["items"]] == case["cutList"]["items"]
    assert built["status"] == case["cutList"]["status"]
    items = {item["id"]: item for item in built["items"]}
    assert [_sheet(sheet, items[sheet["object_id"]], case["quantity"]) for sheet in built["sheets"]] == case["diagrams"]


# ── the upstream assertions, readable ────────────────────────────────────────────────────────────────────────────


def test_source_versions_stay_separate_and_sum_exactly() -> None:
    rows = [r for r in bom_rows(CASES["source-versions"]["document"], 3) if r["unit"] == "м"]
    assert [(r["amount"], r["object_ids"]) for r in rows] == [(0.6006, ["W1", "W2"]), (0.3003, ["W3"])]
    cable_rows = [r for r in bom_rows(CASES["material-cable"]["document"], 2) if r["unit"] == "м"]
    assert [r["object_ids"] for r in cable_rows] == [["W3"], ["K"]]


def test_signed_corrections_and_cut_arithmetic() -> None:
    row = next(r for r in bom_rows(CASES["signed-corrections"]["document"], 2) if "W1" in r["object_ids"])
    assert (row["amount"], row["unit"]) == (0.202, "м")
    built = cut_list(CASES["cut-profiles"]["document"], 2)
    w1 = next(item for item in built["items"] if item["id"] == "W1")
    assert (w1["cut_mm"], w1["unrounded_mm"], w1["total_m"]) == (101, 100.875, 0.202)
    sheet = next(s for s in built["sheets"] if s["object_id"] == "W1")
    assert [s["step_mm"] for s in sheet["a"]["steps"]] == [10, 10] and sheet["warnings"] == []


def test_cable_is_one_blank_with_its_cores() -> None:
    built = cut_list(CASES["cut-cable"]["document"], 3)
    assert [item["id"] for item in built["items"]] == ["W3", "CABLE"]
    sheet = next(s for s in built["sheets"] if s["object_id"] == "CABLE")
    assert sheet["kind"] == "cable" and sheet["member_wire_ids"] == ["W1", "W2"] and len(sheet["conductors"]) == 2
    assert next(item for item in built["items"] if item["id"] == "CABLE")["total_m"] == 0.603


def test_unknown_length_stays_unknown_and_is_diagnosed() -> None:
    document = deepcopy(CASES["fixture"]["document"])
    document["wires"][0]["lengthMm"] = None
    sheet = next(s for s in cut_list(document)["sheets"] if s["object_id"] == "W1")
    assert sheet["warnings"] == ["Исходная физическая длина не задана.", "Материал заготовки не закреплён.",
                                 "W1: профиль разделки A/B не задан."]
    rows = bom_rows(CASES["unknown-length-covering"]["document"])
    assert all(r["amount"] is None for r in rows if r["unit"] == "м" and "cover" not in r["object_ids"])
    cover = next(r for r in rows if "cover" in r["object_ids"])
    assert (cover["amount"], cover["name"]) == (0.1, "shell")


def test_extra_positions_and_readable_indices() -> None:
    rows = bom_rows(CASES["specification-items"]["document"], 3)
    assert next(r for r in rows if "glue" in r["object_ids"])["amount"] == 7.5
    assert not any("clamp" in r["object_ids"] for r in rows)
    rows = bom_rows(CASES["indices-terminals"]["document"])
    wire = next(r for r in rows if "W1" in r["object_ids"])
    assert (wire["index"], wire["designation"], wire["name"]) == (
        "W1, W2, W3", "UL1061-24AWG — UL1061, 24 AWG, красный", "UL1061 24AWG, цвет красный")
    assert next(r for r in rows if "cover-1" in r["object_ids"])["index"] == "ТУ1"
    terminal = next(r for r in rows if "SPH-002T-P0.5S" in r["designation"])
    assert (terminal["designation"], terminal["name"]) == ("SPH-002T-P0.5S", "JST · PHR · Клемма обжимная")


def test_bom_text_order_and_series() -> None:
    assert bom_rows(CASES["bom-text"]["document"])[0]["name"] == "Edited"
    reverse = bom_rows(CASES["bom-order"]["document"])
    assert reverse[0]["key"] == bom_rows(CASES["fixture"]["document"])[-1]["key"]
    series = bom_rows(CASES["series"]["document"])[0]
    assert (series["index"], series["designation"]) == ("A, B, C", "XS-04")


def test_connection_table_labels_and_colours() -> None:
    rows = connection_rows(CASES["articles"]["document"])
    assert (rows[0]["index"], rows[0]["from"], rows[0]["to"]) == ("W1", "A (ARTICLE-A):1", "B (ARTICLE-B):1")
    yellow = connection_rows(CASES["yellow"]["document"])[0]
    assert (yellow["color"], yellow["color_hex"]) == ("желтый / черный", "#FBC02D")
    tails = connection_rows(CASES["junction-screen"]["document"])[-2:]
    assert [(r["from"], r["to"]) for r in tails] == [("Узел", "Экран"), ("Соединитель:Контакт", "B (B):1")]


def test_terminal_label_and_keys() -> None:
    assert terminal_article_label("3:JST|14:SPH-002T-P0.5S|0:|3:PHR") == "SPH-002T-P0.5S"
    assert terminal_article_label("PIN") == "PIN"
    assert key_of("free", "A", None) == '["free","A",null]'


def test_tables_and_excel_text() -> None:
    title, headers, rows = table("connections", CASES["articles"]["document"])
    assert title == "Таблица соединений" and headers[0] == "Индекс (Wn)" and rows[0][:2] == ["W1", "—"]
    _title, headers, rows = table("bom", CASES["source-versions"]["document"], 3)
    assert headers == ["Поз.", "Индекс", "Обозначение", "Наименование", "Кол-во", "Ед.", "Примечание"]
    assert any(row[4] == "0,6006" for row in rows)
    _title, _headers, rows = table("cut", CASES["fixture"]["document"])
    assert rows[0][-1] == "Нет материала"
    content = excel_text(["А", "Б"], [["1\t2", "x"]])
    assert content.startswith(b"\xff\xfe") and content[2:].decode("utf-16-le") == "А\tБ\r\n1 2\tx\r\n"
    with pytest.raises(ValueError):
        documents("other", {})


def test_a_refused_length_does_not_break_the_documents() -> None:
    document = deepcopy(CASES["fixture"]["document"])
    document["wires"][0]["endCorrectionFromMm"] = -500
    built = cut_list(document)
    w1 = next(item for item in built["items"] if item["id"] == "W1")
    assert w1["cut_mm"] is None and "length-missing" in w1["warnings"]
    assert any("отрицательной" in w for w in next(s for s in built["sheets"] if s["object_id"] == "W1")["warnings"])
    assert next(r for r in bom_rows(document) if "W1" in r["object_ids"])["amount"] is None
