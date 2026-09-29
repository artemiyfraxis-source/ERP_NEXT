"""WH-IMPORT: SUPPLYMAP warehouse export → ERP lots. Fixtures are tiny invented rows, not the live export."""

import csv
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.database import build_engine, build_warehouse_uow_factory
from app.modules.warehouse import import_stock
from app.modules.warehouse.import_stock import format_report, reconcile, run_import
from app.modules.warehouse.infrastructure import StockLotRow, WarehouseSequenceRow, WriteOffEntryRow
from app.modules.warehouse.stock_export import (
    StockExportError,
    parse_expiry,
    parse_quantity,
    parse_unit_mass,
    read_stock_export,
)
from app.shared.persistence import AuditEventRow

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

CAR_HEADER = ["ТИП", "АРТИКУЛ", "НАИМЕНОВАНИЕ", "СЕРИЯ", "НАЗНАЧЕНИЕ", "БАЛАНС", "РЕЗЕРВ", "ЕД.ИЗМ", "ГРН",
              "МЕСТО ХРАНЕНИЯ", "ПРОИЗВОДИТЕЛЬ", "DATASHEET", "СЕРТИФИКАТ", "СРОК ГОДНОСТИ", "ОПИСАНИЕ",
              "УСЛОВИЯ ХРАНЕНИЯ", "ПРИХОД", "СПИСАНО", "ПАКЕТ", "SYSTEM ID", "МАССА 1 ШТ, Г"]
ZHUK_HEADER = ["ТИП", "НАИМЕНОВАНИЕ", "НАЗНАЧЕНИЕ", "БАЛАНС", "РЕЗЕРВ", "ЕД.ИЗМ", "ГРН", "МЕСТО ХРАНЕНИЯ",
               "ПРОИЗВОДИТЕЛЬ", "DATASHEET", "СЕРТИФИКАТ", "СРОК ГОДНОСТИ", "ОПИСАНИЕ", "УСЛОВИЯ ХРАНЕНИЯ",
               "ПРИХОД", "СПИСАНО", "Столбец 17", "Столбец 18", "ПАКЕТ", "SYSTEM ID", "МАССА 1 ШТ, Г",
               "АРТИКУЛ", "СЕРИЯ"]
JOURNAL_HEADER = ["ДАТА", "СКЛАД", "ГРН", "НАИМЕНОВАНИЕ", "КОЛ-ВО", "ЕД.ИЗМ", "ОСНОВАНИЕ", "НАЗНАЧЕНИЕ",
                  "КТО СПИСАЛ", "ПРОВЕДЕНО", "ОСТАТОК ПОСЛЕ", "ЗАМОК", "ПАКЕТ"]


def car_row(**values):
    row = dict.fromkeys(CAR_HEADER, "")
    row.update({"НАЗНАЧЕНИЕ": "Склад", "ЕД.ИЗМ": "шт"}, **values)
    return [row[name] for name in CAR_HEADER]


def zhuk_row(**values):
    row = dict.fromkeys(ZHUK_HEADER, "")
    row.update({"НАЗНАЧЕНИЕ": "Склад", "ЕД.ИЗМ": "шт"}, **values)
    return [row[name] for name in ZHUK_HEADER]


def journal_row(grn, quantity, reason="Выдача в производство", posted="Да"):
    row = dict.fromkeys(JOURNAL_HEADER, "")
    row.update({"ДАТА": "01.09.2026", "СКЛАД": "ЦАР", "ГРН": grn, "КОЛ-ВО": quantity, "ЕД.ИЗМ": "шт",
                "ОСНОВАНИЕ": reason, "НАЗНАЧЕНИЕ": "999К.1", "КТО СПИСАЛ": "Тестовый Кладовщик",
                "ПРОВЕДЕНО": posted})
    return [row[name] for name in JOURNAL_HEADER]


CLEAN_CAR = [
    car_row(АРТИКУЛ="TEST-A1", НАИМЕНОВАНИЕ="Тестовая вилка", ТИП="Соединитель", ГРН="OLD.26.0000001",
            ПРИХОД="10", СПИСАНО="2", БАЛАНС="8", **{"МЕСТО ХРАНЕНИЯ": "3B2A"}),
    car_row(АРТИКУЛ="TEST-W2", ГРН="OLD.26.0000002", ПРИХОД="2,5", СПИСАНО="0,75", БАЛАНС="1,75",
            **{"ЕД.ИЗМ": "м", "МАССА 1 ШТ, Г": "0,125"}),
    car_row(АРТИКУЛ="TEST-B3", ГРН="OLD.26.0000003", ПРИХОД="5", СПИСАНО="5", БАЛАНС="0", РЕЗЕРВ="0",
            **{"МЕСТО ХРАНЕНИЯ": "Коробка у окна"}),
]
CLEAN_ZHUK = [
    zhuk_row(АРТИКУЛ="TEST-Z9", СЕРИЯ="S-77", НАИМЕНОВАНИЕ="Тестовый терминал", ГРН="OLD.25.0000009",
             ПРИХОД="4", СПИСАНО="0", БАЛАНС="4", РЕЗЕРВ="1",
             **{"МЕСТО ХРАНЕНИЯ": "01A1", "Столбец 17": "мусор"}),
]
CLEAN_JOURNAL = [
    journal_row("OLD.26.0000001", "3"),
    journal_row("OLD.26.0000001", "1", reason="Возврат: не израсходовано"),
    journal_row("OLD.26.0000002", "0,75"),
    journal_row("OLD.26.0000003", "5"),
    journal_row("OLD.26.0000003", "5", posted="недостаточно остатка (есть 0)"),
    journal_row("OLD.26.0000099", "1"),
]


def write_export(directory: Path, car=CLEAN_CAR, zhuk=CLEAN_ZHUK, journal=CLEAN_JOURNAL, manifest_rows=None):
    directory.mkdir(parents=True, exist_ok=True)
    sheets = {"ЦАР": (CAR_HEADER, car), "ЖУК": (ZHUK_HEADER, zhuk), "СПИСАНИЕ": (JOURNAL_HEADER, journal)}
    manifest = {"exportedAt": "2026-09-28T10:00:00.000Z", "sheets": {}}
    for name, (header, rows) in sheets.items():
        with (directory / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows([header, *rows])
        count = (manifest_rows or {}).get(name, len(rows) + 1)
        manifest["sheets"][name] = {"file": f"{name}.csv", "rows": count, "cols": len(header)}
    (directory / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return directory


@pytest.fixture
def database_url(tmp_path):
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    url = f"sqlite+pysqlite:///{(tmp_path / 'import.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return url


def run(directory, database_url, dry_run=False):
    export = read_stock_export(directory)
    engine = build_engine(database_url)
    try:
        result = run_import(export, build_warehouse_uow_factory(engine)(), dry_run=dry_run, now=NOW)
    finally:
        engine.dispose()
    return export, result, reconcile(export, result)


def snapshot(database_url):
    engine = build_engine(database_url)
    try:
        with engine.connect() as connection:
            lots = connection.execute(
                select(StockLotRow.grn, StockLotRow.version, StockLotRow.inbound, StockLotRow.written_off,
                       StockLotRow.storage_location).order_by(StockLotRow.grn)
            ).all()
            audit = connection.scalar(select(func.count()).select_from(AuditEventRow))
            sequences = dict(connection.execute(select(WarehouseSequenceRow.name, WarehouseSequenceRow.value)).all())
    finally:
        engine.dispose()
    return lots, audit, sequences


def test_quantities_are_sheet_display_values_with_comma_decimals():
    assert parse_quantity("1,81") == Decimal("1.81")
    assert parse_quantity("1 234,5") == Decimal("1234.5")
    assert parse_quantity("1 000") == Decimal("1000")
    assert parse_quantity("") == Decimal("0")
    for bad in ("-1", "1,2,3", "abc", "0,0000001"):
        with pytest.raises(ValueError):
            parse_quantity(bad)


def test_both_column_orders_map_by_header_name(tmp_path):
    export = read_stock_export(write_export(tmp_path / "export"))
    lots = {lot.grn: lot for lot in export.lots}

    assert lots["OLD.26.0000001"].article == "TEST-A1"
    assert lots["OLD.26.0000001"].storage_location == "3B2A"
    assert (lots["OLD.26.0000002"].inbound, lots["OLD.26.0000002"].written_off) == (Decimal("2.5"), Decimal("0.75"))
    zhuk = lots["OLD.25.0000009"]
    assert (zhuk.warehouse, zhuk.article, zhuk.series, zhuk.name) == ("ЖУК", "TEST-Z9", "S-77", "Тестовый терминал")
    assert (zhuk.balance, zhuk.reserved, zhuk.storage_location) == (Decimal("4"), Decimal("1"), "01A1")
    assert export.sheets["ЖУК"].skipped_columns["Столбец 17"] == 1
    assert set(export.sheets["ЦАР"].skipped_columns) == {"SYSTEM ID"}  # the passport is read since SK-6-IMPORT
    assert lots["OLD.26.0000002"].unit_mass_g == Decimal("0.125")
    assert len(export.writeoffs) == 6 and not export.rejected


def test_bad_rows_are_reported_and_the_rest_is_read(tmp_path):
    car = [
        *CLEAN_CAR,
        car_row(АРТИКУЛ="TEST-M4", ГРН="OLD.26.0000004", ПРИХОД="10", СПИСАНО="1", БАЛАНС="8"),
        car_row(АРТИКУЛ="TEST-N5", ГРН="OLD.26.0000005", ПРИХОД="abc", БАЛАНС="3"),
        car_row(АРТИКУЛ="TEST-X", ПРИХОД="1", БАЛАНС="1"),
        [""] * len(CAR_HEADER),
    ]
    zhuk = [*CLEAN_ZHUK, zhuk_row(АРТИКУЛ="TEST-DUP", ГРН="OLD.26.0000001", ПРИХОД="1", БАЛАНС="1")]
    journal = [*CLEAN_JOURNAL, journal_row("OLD.26.0000001", "много")]
    export = read_stock_export(write_export(tmp_path / "export", car=car, zhuk=zhuk, journal=journal))

    reasons = {(r.sheet, r.grn): r.reason for r in export.rejected}
    assert reasons[("ЦАР", "OLD.26.0000004")] == "БАЛАНС 8 ≠ ПРИХОД 10 − СПИСАНО 1"
    assert "не число" in reasons[("ЦАР", "OLD.26.0000005")]
    assert reasons[("ЦАР", "")] == "нет ГРН"
    assert "повтор партии" in reasons[("ЦАР", "OLD.26.0000001")]
    assert "повтор партии" in reasons[("ЖУК", "OLD.26.0000001")]
    assert "не число" in reasons[("СПИСАНИЕ", "OLD.26.0000001")]
    assert {lot.grn for lot in export.lots} == {"OLD.26.0000002", "OLD.26.0000003", "OLD.25.0000009"}
    assert export.sheets["ЦАР"].blank_rows == 1


def test_truncated_export_or_missing_column_aborts(tmp_path):
    with pytest.raises(StockExportError, match="неполная"):
        read_stock_export(write_export(tmp_path / "short", manifest_rows={"ЦАР": 10}))
    directory = write_export(tmp_path / "no-column", zhuk=[])
    (directory / "ЖУК.csv").write_text("ТИП,НАИМЕНОВАНИЕ,ГРН\n", encoding="utf-8")
    with pytest.raises(StockExportError, match="нет колонок"):
        read_stock_export(directory)


def test_import_creates_lots_that_reconcile_to_the_unit(tmp_path, database_url):
    export, result, check = run(write_export(tmp_path / "export"), database_url)

    assert result.committed and len(result.created) == 4 and not result.rejected
    assert check.ok, format_report(export, result, check)
    totals = {(t.warehouse, t.unit): (t.sheet_balance, t.erp_balance) for t in check.totals}
    assert totals == {
        ("ЦАР", "шт"): (Decimal("8"), Decimal("8")),
        ("ЦАР", "м"): (Decimal("1.75"), Decimal("1.75")),
        ("ЖУК", "шт"): (Decimal("4"), Decimal("4")),
    }
    lots = {lot.grn: lot for lot in result.erp_lots.values()}
    assert (lots["OLD.26.0000001"].inbound, lots["OLD.26.0000001"].written_off) == (Decimal("10"), Decimal("2"))
    assert lots["OLD.26.0000002"].storage_location == ""
    assert lots["OLD.26.0000003"].storage_location == "Коробка у окна"
    assert lots["OLD.25.0000009"].storage_location == "1A1"
    assert lots["OLD.25.0000009"].reserved == Decimal("0")
    assert [row.grn for row in result.reserve_not_transferred] == ["OLD.25.0000009"]
    assert result.history_imported == 5
    assert [row.grn for row in result.history_without_lot] == ["OLD.26.0000099"]
    assert check.journal_checked == 3 and not check.journal_mismatches
    _, audit, sequences = snapshot(database_url)
    assert audit == 4 + 5
    assert sequences == {"manual:2026": 3, "manual:2025": 9}


def journal_rows(database_url):
    engine = build_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(
                select(WriteOffEntryRow.grn, WriteOffEntryRow.kind, WriteOffEntryRow.quantity, WriteOffEntryRow.reason,
                       WriteOffEntryRow.purpose, WriteOffEntryRow.author, WriteOffEntryRow.posted,
                       WriteOffEntryRow.remaining_after, WriteOffEntryRow.occurred_at, WriteOffEntryRow.source_key)
                .order_by(WriteOffEntryRow.source_key)
            ).all()
    finally:
        engine.dispose()


def test_imported_writeoff_rows_become_rows_of_the_writeoff_journal_once(tmp_path, database_url):
    """SK-13: the СПИСАНИЕ rows of lots created by the import land in the journal as they were, moving no stock."""
    directory = write_export(tmp_path / "export")
    run(directory, database_url)

    rows = journal_rows(database_url)
    assert [(r.grn, r.quantity, r.reason, r.posted) for r in rows] == [
        ("OLD.26.0000001", Decimal("3"), "Выдача в производство", True),
        ("OLD.26.0000001", Decimal("1"), "Возврат: не израсходовано", True),
        ("OLD.26.0000002", Decimal("0.75"), "Выдача в производство", True),
        ("OLD.26.0000003", Decimal("5"), "Выдача в производство", True),
        ("OLD.26.0000003", Decimal("5"), "Выдача в производство", False),
    ]
    for row in rows:
        assert (row.kind, row.purpose, row.author, row.remaining_after) == ("legacy", "999К.1", "Тестовый Кладовщик", None)
        assert row.occurred_at.date().isoformat() == "2026-09-01"
        assert row.source_key.startswith("supplymap-writeoff:SUPPLYMAP ")

    run(directory, database_url)

    assert journal_rows(database_url) == rows


def test_rerun_with_the_same_data_changes_nothing(tmp_path, database_url):
    directory = write_export(tmp_path / "export")
    run(directory, database_url)
    before = snapshot(database_url)

    _, result, check = run(directory, database_url)

    assert not result.created and len(result.unchanged) == 4 and not result.conflicts
    assert result.history_imported == 0 and result.history_for_existing_lots == 5
    assert check.ok
    assert snapshot(database_url) == before


def test_rerun_with_changed_sheet_reports_and_keeps_the_lot(tmp_path, database_url):
    run(write_export(tmp_path / "first"), database_url)
    changed = [car_row(АРТИКУЛ="TEST-A1", НАИМЕНОВАНИЕ="Тестовая вилка", ТИП="Соединитель", ГРН="OLD.26.0000001",
                       ПРИХОД="12", СПИСАНО="2", БАЛАНС="10", **{"МЕСТО ХРАНЕНИЯ": "3B2A"}), *CLEAN_CAR[1:]]
    before = snapshot(database_url)

    export, result, check = run(write_export(tmp_path / "second", car=changed), database_url)

    assert [(row.grn, reason) for row, reason in result.conflicts] == [("OLD.26.0000001", "расходится с ERP: ПРИХОД")]
    assert not check.ok
    assert [(line.grn, line.sheet_balance, line.erp_balance) for line in check.grn_mismatches] == [
        ("OLD.26.0000001", Decimal("10"), Decimal("8"))
    ]
    assert snapshot(database_url) == before
    assert "не изменено ЦАР:2 OLD.26.0000001" in format_report(export, result, check)


def test_rejected_rows_do_not_stop_the_import_and_show_in_reconciliation(tmp_path, database_url):
    car = [*CLEAN_CAR, car_row(АРТИКУЛ="TEST-M4", ГРН="OLD.26.0000004", ПРИХОД="10", СПИСАНО="1", БАЛАНС="8")]

    export, result, check = run(write_export(tmp_path / "export", car=car), database_url)

    assert len(result.created) == 4
    assert not check.ok and check.stock_rejected == 1
    assert [(line.grn, line.sheet_balance, line.erp_balance) for line in check.grn_mismatches] == [
        ("OLD.26.0000004", Decimal("8"), Decimal("0"))
    ]
    assert "отклонено ЦАР:5 OLD.26.0000004: БАЛАНС 8 ≠ ПРИХОД 10 − СПИСАНО 1" in format_report(export, result, check)


def test_dry_run_reports_the_same_reconciliation_and_writes_nothing(tmp_path, database_url, monkeypatch, capsys):
    directory = write_export(tmp_path / "export")
    empty = snapshot(database_url)

    _, result, check = run(directory, database_url, dry_run=True)
    assert not result.committed and len(result.created) == 4 and check.ok
    assert snapshot(database_url) == empty

    monkeypatch.setenv("ERP_DATABASE_URL", database_url)
    assert import_stock.main(["--dir", str(directory), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "ПРОБНЫЙ ПРОГОН" in output and "ИТОГ: всё сошлось" in output
    assert "Тестовый Кладовщик" not in output
    assert snapshot(database_url) == empty


# SK-6-IMPORT: the lot passport columns of the export.

def test_expiry_is_read_as_the_sheet_shows_it():
    assert parse_expiry("31.12.2027") == date(2027, 12, 31)
    assert parse_expiry("1.2.2028") == date(2028, 2, 1)
    assert parse_expiry("2027-12-31") == date(2027, 12, 31)
    assert parse_expiry("  ") is None
    for bad in ("31.02.2027", "до 2027", "31/12/2027", "12.2027", "31.12.27"):
        with pytest.raises(ValueError):
            parse_expiry(bad)


def test_unit_mass_is_read_by_the_supplymap_number_rule():
    assert parse_unit_mass("1,81") == Decimal("1.81")
    assert parse_unit_mass("0.27") == Decimal("0.27")
    assert parse_unit_mass("2,16084") == Decimal("2.16084")
    assert parse_unit_mass("12 г") == Decimal("12")
    assert parse_unit_mass("") is None
    for bad in ("—", "0", "-1", "abc"):
        with pytest.raises(ValueError):
            parse_unit_mass(bad)


PASSPORT_CAR = [
    car_row(АРТИКУЛ="TEST-P1", ГРН="OLD.26.0000011", ПРИХОД="3", БАЛАНС="3", ПРОИЗВОДИТЕЛЬ="Тест-Завод",
            DATASHEET="https://example.test/p1.pdf", СЕРТИФИКАТ="RU C-TEST.001", ОПИСАНИЕ="Тестовое описание",
            **{"СРОК ГОДНОСТИ": "31.12.2027", "УСЛОВИЯ ХРАНЕНИЯ": "Сухо, +5…+25 °C", "МАССА 1 ШТ, Г": "1,81"}),
    car_row(АРТИКУЛ="TEST-P2", ГРН="OLD.26.0000012", ПРИХОД="2", БАЛАНС="2",
            **{"СРОК ГОДНОСТИ": "до конца 2027", "МАССА 1 ШТ, Г": "нет"}),
]
PASSPORT_ZHUK = [
    zhuk_row(АРТИКУЛ="TEST-P3", ГРН="OLD.25.0000013", ПРИХОД="1", БАЛАНС="1", ПРОИЗВОДИТЕЛЬ="Тест-Два",
             **{"СРОК ГОДНОСТИ": "01.06.2026"}),
]


def test_passport_columns_map_to_the_lot_by_header_in_both_orders(tmp_path):
    export = read_stock_export(write_export(tmp_path / "export", car=PASSPORT_CAR, zhuk=PASSPORT_ZHUK, journal=[]))
    lots = {lot.grn: lot for lot in export.lots}

    first = lots["OLD.26.0000011"]
    assert (first.unit_mass_g, first.expiry_date, first.manufacturer) == (Decimal("1.81"), date(2027, 12, 31), "Тест-Завод")
    assert (first.datasheet, first.certificate) == ("https://example.test/p1.pdf", "RU C-TEST.001")
    assert (first.description, first.storage_conditions) == ("Тестовое описание", "Сухо, +5…+25 °C")
    assert (lots["OLD.25.0000013"].expiry_date, lots["OLD.25.0000013"].manufacturer) == (date(2026, 6, 1), "Тест-Два")


def test_unreadable_expiry_keeps_the_lot_with_an_empty_field_and_is_listed(tmp_path):
    export = read_stock_export(write_export(tmp_path / "export", car=PASSPORT_CAR, zhuk=[], journal=[]))
    second = {lot.grn: lot for lot in export.lots}["OLD.26.0000012"]

    assert (second.expiry_date, second.unit_mass_g) == (None, None)
    assert not export.rejected
    assert [(w.row_number, w.grn, w.column, w.text) for w in export.passport_warnings] == [
        (3, "OLD.26.0000012", "СРОК ГОДНОСТИ", "до конца 2027"),
        (3, "OLD.26.0000012", "МАССА 1 ШТ, Г", "нет"),
    ]


def test_too_long_passport_text_rejects_the_row_like_any_long_field(tmp_path):
    car = [car_row(АРТИКУЛ="TEST-L", ГРН="OLD.26.0000014", ПРИХОД="1", БАЛАНС="1", ПРОИЗВОДИТЕЛЬ="Я" * 256)]
    export = read_stock_export(write_export(tmp_path / "export", car=car, zhuk=[], journal=[]))

    assert [(r.grn, r.reason) for r in export.rejected] == [("OLD.26.0000014", "слишком длинное значение: manufacturer")]


def test_import_writes_the_passport_to_the_lot_and_a_rerun_sees_it_unchanged(tmp_path, database_url):
    directory = write_export(tmp_path / "export", car=PASSPORT_CAR, zhuk=PASSPORT_ZHUK, journal=[])
    export, result, check = run(directory, database_url, dry_run=True)
    report = format_report(export, result, check)
    assert "не прочитано ЦАР:3 OLD.26.0000012: СРОК ГОДНОСТИ «до конца 2027»" in report
    assert "партия заведена, поле пустое" in report
    assert "СРОК ГОДНОСТИ 2" in report and "нечитаемых значений паспорта: 2" in report

    _, result, check = run(directory, database_url)
    lots = {lot.grn: lot for lot in result.erp_lots.values()}
    first = lots["OLD.26.0000011"]
    assert (first.unit_mass_g, first.expiry_date, first.certificate) == (Decimal("1.81"), date(2027, 12, 31), "RU C-TEST.001")
    assert (first.storage_conditions, first.datasheet) == ("Сухо, +5…+25 °C", "https://example.test/p1.pdf")
    assert lots["OLD.26.0000012"].expiry_date is None and check.ok

    _, again, _ = run(directory, database_url)
    assert not again.conflicts and len(again.unchanged) == 3


def test_journal_that_disagrees_with_the_sheet_keeps_sheet_balances_and_asks_for_a_recount(tmp_path, database_url):
    """Decided rule (lot OLD.26.0000095 live: sheet СПИСАНО 2387, БАЛАНС 0, journal net 2231): the sheet wins, the
    journal rows go in as they are, and the report asks to recount the lot on the shelf."""
    car = [car_row(АРТИКУЛ="TEST-J", ГРН="OLD.26.0000095", ПРИХОД="2387", СПИСАНО="2387", БАЛАНС="0")]
    journal = [journal_row("OLD.26.0000095", "2000"), journal_row("OLD.26.0000095", "231"),
               journal_row("OLD.26.0000095", "156", posted="")]

    export, result, check = run(write_export(tmp_path / "export", car=car, zhuk=[], journal=journal), database_url)

    lot = {lot.grn: lot for lot in result.erp_lots.values()}["OLD.26.0000095"]
    assert (lot.inbound, lot.written_off, lot.balance) == (Decimal("2387"), Decimal("2387"), Decimal("0"))
    assert result.history_imported == 3 and len(journal_rows(database_url)) == 3
    assert [(m.grn, m.journal_net, m.written_off) for m in check.journal_mismatches] == [
        ("OLD.26.0000095", Decimal("2231"), Decimal("2387")),
    ]
    assert check.ok  # the journal is history: a recount is a task, not a failed import
    report = format_report(export, result, check)
    assert ("пересчитать физически: ЦАР OLD.26.0000095 — журнал 2231 ≠ СПИСАНО 2387, БАЛАНС листа 0") in report
    assert report.endswith("ИТОГ: всё сошлось; пересчитать физически партий: 1")
