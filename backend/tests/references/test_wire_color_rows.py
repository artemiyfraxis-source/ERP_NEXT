"""«Монтажные провода»: каждый цвет — своя строка (owner 20.10.2026: «имелось ввиду каждый цвет своя строка»).

A монтажный wire row (`cable_kind = 'wire'`) holds ONE colour in «Цвет»; its key is mark + colour. Migration 0130 splits
the colour lists the rows held: the record keeps the first colour (its id, its stock links), each other colour is a new
row with the same values; coax and multicore rows are not touched («Цвета» of a multicore cable — its cores). Every
reader that finds a wire by its mark gives the same values as before (any row of the mark; the colour's row when the
label names it), so no ETK changes. A load and a file take «Цвет» as a part of the key: a new colour is a new row."""

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.main import app
from app.modules.references.application import (
    EditRecordCommand, FieldEdit, SaveHookupCommand, cable_catalog, edit_record, save_hookup,
)
from app.modules.references.cable_structure import HookupWire
from app.modules.references.domain import (
    CABLE_COLUMNS, ReferenceValidationError, TechOperationRecord, TechOperationTab, pick_wire_row, split_colors,
    wire_color_rows,
)
from app.modules.references.importers import WIRE_COLOR_REASON
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.modules.techcards.harness_catalog import _Collector, _wires
from app.shared import bulk_import
from app.shared.bulk_import import ImportRateLimiter
from app.workflows import etk_refresh  # noqa: F401  (its before_flush hook is live while the migration runs)
from app.workflows.item_names import ReferenceNames
from app.workflows.techcard_build import enrich_wire_diameters
from tests.references.test_api import references_api  # noqa: F401  (fixture)
from tests.references.test_cable_structure import _seed_gauges
from tests.references.test_cables import _mirror
from tests.references.test_load_mirror import NOW, sessions, uow  # noqa: F401  (fixtures)
from tests.references.test_roundtrip import _download, _edited, _rows, _upload

BEFORE, AFTER = "0131_prov_cable_kind", "0132_prov_wire_color_rows"
NAMESPACE = uuid5(NAMESPACE_URL, "erp-next:0132_prov_wire_color_rows")


# ── the migration ──────────────────────────────────────────────────────────────────────────────────────────────────

def _config(database: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    return config


ROWS = {  # name → (cable_kind, fields, edited_in_erp)
    "nv4": ("wire", {"Марка": "НВ-4 0,20", "Сечение, мм2": "0,20", "Цвета": "чёрный, красный; белый",
                     "Наружный диаметр, мм": "1,5", "Поставщик": "Снабкабель"}, True),
    "pvam": ("wire", {"Марка": "ПВАМ 0,50", "Цвета": "красный", "Наружный диаметр, мм": "1,6"}, False),
    "mgtf": ("wire", {"Марка": "МГТФ 0,07", "Цвета": "", "Наружный диаметр, мм": "0,75"}, False),
    "twice": ("wire", {"Марка": "ПуГВ 1,0", "Цвета": "синий, Синий, жёлто-зелёный"}, False),
    "liycy": ("multi", {"Марка": "LiYCY 4x0,25", "Число жил": "4", "Цвета": "белый, коричневый, зелёный, жёлтый"}, False),
    "rg316": ("coax", {"Марка": "RG316", "Цвета": "коричневый"}, False),
}


def _seed(connection) -> dict:
    ids = {name: uuid4() for name in ROWS}
    for name, (kind, fields, edited) in ROWS.items():
        connection.execute(
            "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields,"
            " mirror_key, edited_in_erp, cable_kind) VALUES (?, 'prov', ?, ?, 'БД.ПРОВ', ?, ?, ?, ?, ?)",
            (ids[name].hex, fields["Марка"], fields["Марка"].casefold(), fields["Марка"],
             json.dumps(fields, ensure_ascii=False), f"mk-{name}", int(edited), kind))
    lot = uuid4().hex
    connection.execute(
        "INSERT INTO warehouse_lots (id, version, warehouse, grn, package, article, series, name, item_type, purpose,"
        " unit, storage_location, inbound, written_off, reserved, source_receiving_ids)"
        " VALUES (?, 1, 'ЦАР', 'FRX.26.0000042', '', 'НВ-4 0,20', '', 'Провод', '', '', 'м', '', 5, 0, 0, '[]')", (lot,))
    for name in ("nv4", "liycy"):
        connection.execute(
            "INSERT INTO reference_stock_links (id, occurred_at, event, lot_id, warehouse, grn, package, article, name,"
            " record_id, record_tab, record_text, changes, actor_id, actor_name)"
            " VALUES (?, '2026-10-13 11:30:00', 'linked', ?, 'ЦАР', 'FRX.26.0000042', '', 'НВ-4', 'Провод', ?, 'prov',"
            " ?, '[]', ?, 'Кладовщик')", (uuid4().hex, lot, ids[name].hex, ROWS[name][1]["Марка"], uuid4().hex))
    return ids


def _prov(connection) -> dict:
    return {UUID(row[0]): (row[1], json.loads(row[2]), row[3], row[4], row[5], row[6]) for row in connection.execute(
        "SELECT id, cable_kind, fields, mirror_key, edited_in_erp, display_text, sort_key FROM tech_operation_records"
        " WHERE tab = 'prov'")}


def _links(connection) -> list:
    return sorted(connection.execute("SELECT id, record_id FROM reference_stock_links").fetchall())


def test_0130_splits_each_colour_into_its_own_row_and_the_downgrade_merges_them_back(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        ids = _seed(connection)
        before, links = _prov(connection), _links(connection)
        others = connection.execute("SELECT COUNT(*) FROM tech_operation_records WHERE tab <> 'prov'").fetchone()[0]

    command.upgrade(config, AFTER)
    with sqlite3.connect(database) as connection:
        after = _prov(connection)
        assert _links(connection) == links  # stock links: same rows, same records
        assert connection.execute("SELECT COUNT(*) FROM techcard_refresh_requests").fetchone()[0] == 0  # no ETK queued
        assert connection.execute("SELECT COUNT(*) FROM tech_operation_records WHERE tab <> 'prov'").fetchone()[0] == others

    # rows: 3 + 1 + 1 + 2 монтажные (a repeated colour is one row), multi and coax untouched
    kinds = [kind for kind, *_ in after.values()]
    assert (kinds.count("wire"), kinds.count("multi"), kinds.count("coax")) == (7, 1, 1)
    assert set(ids.values()) <= set(after)  # every old id stays
    for name in ("liycy", "rg316"):
        assert after[ids[name]] == before[ids[name]]
    # the record keeps the first colour, in place of «Цвета»; its id, mirror key, «edited in ERP» stay
    nv4 = after[ids["nv4"]]
    assert list(nv4[1]) == ["Марка", "Сечение, мм2", "Цвет", "Наружный диаметр, мм", "Поставщик"]
    assert nv4[1]["Цвет"] == "чёрный" and nv4[2:] == before[ids["nv4"]][2:]
    # each other colour: a new row with every value of the record but the colour, the id uuid5(record, number)
    for number, color in ((1, "красный"), (2, "белый")):
        copy = after[uuid5(NAMESPACE, f"{ids['nv4']}:{number}")]
        assert copy[0] == "wire" and copy[1] == {**nv4[1], "Цвет": color} and copy[2:] == nv4[2:]
    assert after[ids["pvam"]][1] == {"Марка": "ПВАМ 0,50", "Цвет": "красный", "Наружный диаметр, мм": "1,6"}
    assert after[ids["mgtf"]][1] == {"Марка": "МГТФ 0,07", "Цвет": "", "Наружный диаметр, мм": "0,75"}
    twice = sorted(fields["Цвет"] for kind, fields, *_ in after.values() if fields["Марка"] == "ПуГВ 1,0")
    assert twice == ["жёлто-зелёный", "синий"]

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        back = _prov(connection)
        assert _links(connection) == links
    assert set(back) == set(before)
    for name in ("nv4", "pvam", "mgtf", "liycy", "rg316"):
        assert back[ids[name]] == (before[ids[name]] if name != "nv4" else (
            "wire", {**before[ids["nv4"]][1], "Цвета": "чёрный, красный, белый"}, *before[ids["nv4"]][2:]))
    assert back[ids["twice"]][1]["Цвета"] == "синий, жёлто-зелёный"


def test_0130_downgrade_refuses_to_delete_a_colour_row_tied_to_stock(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        ids = _seed(connection)
    command.upgrade(config, AFTER)
    red = uuid5(NAMESPACE, f"{ids['nv4']}:1")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE reference_stock_links SET record_id = ? WHERE record_id = ?", (red.hex, ids["nv4"].hex))

    with pytest.raises(RuntimeError, match="привязаны партии склада.*НВ-4 0,20 красный"):
        command.downgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == AFTER
        assert connection.execute("SELECT COUNT(*) FROM tech_operation_records WHERE cable_kind = 'wire'").fetchone()[0] == 7


# ── the readers: by mark, by mark + colour ─────────────────────────────────────────────────────────────────────────

def test_the_colour_helpers_split_a_list_and_pick_a_row():
    assert split_colors("чёрный, Чёрный; желто-зелёный ,  жёлто-зелёный") == ["чёрный", "желто-зелёный"]
    assert wire_color_rows({"Марка": "НВ-4", "Цвета": "чёрный, красный", "Ø": "1"}) == [
        {"Марка": "НВ-4", "Цвет": "чёрный", "Ø": "1"}, {"Марка": "НВ-4", "Цвет": "красный", "Ø": "1"}]
    assert wire_color_rows({"Марка": "НВ-4", "Цвета": ""}) == [{"Марка": "НВ-4", "Цвет": ""}]
    assert wire_color_rows({"Марка": "НВ-4"}) == [{"Марка": "НВ-4"}]
    rows = [{"Марка": "НВ-4", "Цвет": "чёрный", "n": 1}, {"Марка": "НВ-4", "Цвет": "Жёлтый", "n": 2}]
    assert pick_wire_row(rows, "желтый")["n"] == 2 and pick_wire_row(rows, "")["n"] == 1
    assert pick_wire_row(rows, "синий")["n"] == 1 and pick_wire_row([], "синий") is None


ONE_ROW = [{"Марка": "UL1007 24AWG", "Цвета": "красный, чёрный", "AWG": "24", "Наружный диаметр, мм": "1,6"}]
COLOUR_ROWS = [{"Марка": "UL1007 24AWG", "Цвет": "красный", "AWG": "24", "Наружный диаметр, мм": "1,6"},
               {"Марка": "UL1007 24AWG", "Цвет": "чёрный", "AWG": "24", "Наружный диаметр, мм": "1,6"}]


@pytest.mark.parametrize("label", ["UL1007 24AWG", "UL1007 24AWG красный", "UL1007 24AWG чёрный", "UL1007 24AWG синий"])
def test_the_etk_wire_diameters_are_the_same_with_one_row_per_colour(label):
    config = {"wires": [{"name": label}], "ops": [{"wires": [{"art": label}]}]}
    gauges = [g for g in _gauges() if g.awg == "24"]
    before = enrich_wire_diameters(config, [], gauges, ONE_ROW)
    after = enrich_wire_diameters(config, [], gauges, COLOUR_ROWS)
    assert after == before and after["wires"][0]["outerDiameter"] == 1.6


def _gauges():
    from app.modules.references.cable_structure import WireGauge
    return [WireGauge.create(awg="24", section_mm2="0,2", conductor_d_mm="0,51", position=0)]


def test_the_harness_catalogue_and_the_item_names_see_one_wire_per_mark():
    old, new = _Collector(), _Collector()
    _wires(ONE_ROW, old)
    _wires(COLOUR_ROWS, new)
    assert new.records == old.records and new.diagnostics == old.diagnostics == []
    assert new.records[0]["payload"]["colors"] == "красный, чёрный"

    def records(rows):
        return [TechOperationRecord.create(tab=TechOperationTab.WIRE, display_text=row["Марка"], search_text="",
                                           source_sheet="", sort_key="", fields=row, cable_kind="wire") for row in rows]
    for article in ("UL1007 24AWG", "UL1007 24AWG красный"):
        assert ReferenceNames(records(COLOUR_ROWS)).name(article) == ReferenceNames(records(ONE_ROW)).name(article)


# ── the load, the screen's edits, the file ─────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    monkeypatch.setattr(bulk_import, "RATE_LIMITER", ImportRateLimiter())


SNAPSHOT = [
    {"Марка": "НВ-4 0,20", "Тип провода": "монтажный", "Число жил": "1", "Сечение, мм2": "0,20",
     "Цвета": "чёрный, красный", "Наружный диаметр, мм": "1,5", "Поставщик": ""},
    {"Марка": "RG316", "Тип провода": "коаксиальный", "Число жил": "1", "Цвета": "коричневый"},
    {"Марка": "LiYY 2x0,5", "Тип провода": "многожильный", "Число жил": "2", "Цвета": "белый, коричневый"},
]


def _seed_api(client: TestClient) -> list[dict]:
    assert client.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [
        {"tab": "prov", "display_text": row["Марка"], "source_sheet": "БД.ПРОВ", "fields": row} for row in SNAPSHOT
    ]}).status_code == 200
    return _all(client)


def _all(client: TestClient) -> list[dict]:
    return client.get("/api/references/tech-operations", params={"tab": "prov"}).json()["records"]


def _wire(client: TestClient, color: str) -> dict:
    [record] = [item for item in _all(client) if item["cable_kind"] == "wire" and item["fields"].get("Цвет") == color]
    return record


def test_a_loaded_colour_list_becomes_rows_and_only_the_монтажные_split(references_api):
    records = _seed_api(references_api)
    by_kind = {}
    for record in records:
        by_kind.setdefault(record["cable_kind"], []).append(record["fields"])
    assert sorted(fields["Цвет"] for fields in by_kind["wire"]) == ["красный", "чёрный"]
    assert all("Цвета" not in fields for fields in by_kind["wire"])
    assert by_kind["multi"][0]["Цвета"] == "белый, коричневый" and by_kind["coax"][0]["Цвета"] == "коричневый"
    assert "Цвет" in CABLE_COLUMNS["wire"] and "Цвета" not in CABLE_COLUMNS["wire"] and "Цвет" not in CABLE_COLUMNS["multi"]
    cables = references_api.get("/api/references/cables").json()
    assert cables["counts"]["wire"] == 2


def test_an_edit_keeps_one_colour_per_row_and_the_mark_values_equal_across_its_colours(references_api):
    _seed_api(references_api)
    black, red = _wire(references_api, "чёрный"), _wire(references_api, "красный")

    def patch(record, field, old, value):
        return references_api.patch(f"/api/references/tech-operations/{record['id']}",
                                    json={"edits": [{"field": field, "old": old, "value": value}]})

    two = patch(black, "Цвет", "чёрный", "чёрный, синий")
    assert two.status_code == 422 and "один цвет на строку" in two.json()["detail"]
    same = patch(black, "Цвет", "чёрный", "Красный")
    assert same.status_code == 422 and "уже есть" in same.json()["detail"]
    # a value of the mark follows to its other colours; the seller's offer and the colour are the row's own
    assert patch(black, "Наружный диаметр, мм", "1,5", "1,55").status_code == 200
    assert patch(black, "Поставщик", "", "Паритет").status_code == 200
    assert patch(black, "Цвет", "чёрный", "синий").status_code == 200
    red_now = _wire(references_api, "красный")["fields"]
    assert red_now["Наружный диаметр, мм"] == "1,55" and red_now["Поставщик"] == ""
    assert _wire(references_api, "синий")["fields"]["Поставщик"] == "Паритет"
    assert red["id"] == _wire(references_api, "красный")["id"]


def test_a_new_colour_is_a_new_row_and_a_repeated_one_is_refused(references_api):
    _seed_api(references_api)
    created = references_api.post("/api/references/tech-operations", json={
        "tab": "prov", "cable_kind": "wire", "fields": {"Марка": "НВ-4 0,20", "Цвет": "белый", "Наружный диаметр, мм": "1,5"}})
    assert created.status_code == 201, created.text
    again = references_api.post("/api/references/tech-operations", json={
        "tab": "prov", "cable_kind": "wire", "fields": {"Марка": "нв-4 0,20", "Цвет": "Белый"}})
    assert again.status_code == 422 and "уже есть" in again.json()["detail"]


def _load(client: TestClient, rows: list[dict]) -> dict:
    response = client.post("/api/references/import/prov", params={"dry_run": 0},
                           json={"rows": [{"fields": fields} for fields in rows]})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_row_load_keys_a_монтажный_wire_by_mark_and_colour(references_api):
    _seed_api(references_api)
    schema = references_api.get("/api/references/import/prov/schema").json()
    assert schema["key"] == ["Марка", "Цвет"] and schema["optional_key"] == ["Цвет"]

    report = _load(references_api, [
        {"Марка": "НВ-4 0,20", "Цвет": "красный", "Поставщик": "Снабкабель"},   # the red row
        {"Марка": "НВ-4 0,20", "Цвет": "белый", "Вид кабеля": "wire"},          # a new colour → a new row
        {"Марка": "НВ-4 0,20", "Поставщик": "Паритет"},                          # which colour? refused
        {"Марка": "RG316", "Поставщик": "kroks.ru"},                             # coax: by its mark as before
        {"Марка": "НВ-4 0,20", "Цвет": "синий, зелёный", "Вид кабеля": "wire"},  # a list is no colour
    ])

    assert [item["key"] for item in report["updated"]] == ["НВ-4 0,20 / красный", "RG316"]
    assert report["created"] == ["НВ-4 0,20 / белый"]
    refused = {item["key"]: item["reason"] for item in report["refused"]}
    assert refused["НВ-4 0,20"] == WIRE_COLOR_REASON and "один цвет на строку" in refused["НВ-4 0,20 / синий, зелёный"]
    assert _wire(references_api, "красный")["fields"]["Поставщик"] == "Снабкабель"
    assert _wire(references_api, "чёрный")["fields"]["Поставщик"] == ""
    assert _wire(references_api, "белый")["fields"]["Наружный диаметр, мм"] == ""  # a new row: only what the file gives


def test_the_file_round_trip_a_new_colour_line_is_a_new_row_and_a_mark_value_edited_on_every_colour_applies(references_api):
    _seed_api(references_api)
    content = _download(references_api, kind="wire")

    def edit(rows):
        head = rows[0]
        outer, color = head.index("Наружный диаметр, мм"), head.index("Цвет")
        for row in rows[1:]:
            row[outer] = "1,45"  # the mark's value, typed on both colour lines
        new = [""] * len(head)
        new[head.index("Марка")], new[color], new[outer] = "НВ-4 0,20", "белый", "1,45"
        rows.append(new)

    body = _upload(references_api, _edited(content, edit), dry_run=0).json()
    assert not (body["errors"] or body["conflicts"]), body
    assert len(body["updated"]) == 2 and len(body["created"]) == 1
    wires = [record["fields"] for record in _all(references_api) if record["cable_kind"] == "wire"]
    assert sorted(fields["Цвет"] for fields in wires) == ["белый", "красный", "чёрный"]
    assert {fields["Наружный диаметр, мм"] for fields in wires} == {"1,45"}

    def repeat(rows):
        new = [""] * len(rows[0])
        new[rows[0].index("Марка")], new[rows[0].index("Цвет")] = "НВ-4 0,20", "Чёрный"
        rows.append(new)

    body = _upload(references_api, _edited(_download(references_api, kind="wire"), repeat), dry_run=0).json()
    assert len(body["errors"]) == 1 and "уже есть" in body["errors"][0]["reason"] and not body["created"]
    assert len(_rows(_download(references_api, kind="wire"))) == 1 + 3


def test_the_structure_of_a_mark_writes_its_cells_into_every_colour(tmp_path, uow):
    load_mirror(_mirror(tmp_path, prov="Марка\tТип провода\tСечение, мм2\tЧисло жил\tЦвета\tНаружный диаметр, мм\n"
                                       "ПВАМ 0,50\tсиловой\t0,5\t1\tкрасный, чёрный\t1,6\n"), uow, dry_run=False, now=NOW)
    _seed_gauges(uow)
    rows = [row for row in cable_catalog(uow).rows if row.cable == "ПВАМ 0,50"]
    assert len(rows) == 2 and rows[0].record.fields["Цвет"] != rows[1].record.fields["Цвет"]
    gauge = next(item for item in cable_catalog(uow).gauges if item.section_mm2 == Decimal("0.5"))
    save_hookup(SaveHookupCommand(rows[0].record.id, "ПВАМ", gauge.id, "ПВХ", "1,8", "", "", "", None, ACTOR, NOW), uow)
    after = [row for row in cable_catalog(uow).rows if row.cable == "ПВАМ 0,50"]
    assert {row.record.fields["Наружный диаметр, мм"] for row in after} == {"1,8"}
    assert {row.key for row in after} == {"пвам0,50"} and all(row.record.edited_in_erp for row in after)
    catalog = cable_catalog(uow)
    assert len([wire for wire in catalog.hookups.values() if isinstance(wire, HookupWire)]) == 1

    # an edit of the colour row keeps the structure the mark's: renaming one colour away leaves it to the others
    edit_record(EditRecordCommand(after[1].record.id, (FieldEdit("Марка", "ПВАМ 0,50", "ПВАМ 0,75"),), ACTOR, NOW), uow)
    catalog = cable_catalog(uow)
    assert {"пвам0,50", "пвам0,75"} <= set(catalog.hookups)
    with pytest.raises(ReferenceValidationError, match="уже есть"):
        edit_record(EditRecordCommand(after[1].record.id, (FieldEdit("Марка", "ПВАМ 0,75", "ПВАМ 0,50"),
                                                           FieldEdit("Цвет", after[1].record.fields["Цвет"],
                                                                     after[0].record.fields["Цвет"])), ACTOR, NOW), uow)


def test_moving_a_colour_row_to_another_list_moves_every_colour_of_the_mark(references_api):
    _seed_api(references_api)
    black = _wire(references_api, "чёрный")
    response = references_api.put(f"/api/references/cables/{black['id']}/kind", json={"kind": "multi"})
    assert response.status_code == 200, response.text
    moved = [record for record in _all(references_api) if record["fields"]["Марка"] == "НВ-4 0,20"]
    assert len(moved) == 2 and {record["cable_kind"] for record in moved} == {"multi"}
    assert {record["fields"]["Цвет"] for record in moved} == {"чёрный", "красный"}  # a filled value always stays
