"""WH-IMPORT: SUPPLYMAP warehouse export → ERP lots. Fixtures are tiny invented rows, not the live export."""

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.database import build_engine, build_warehouse_uow_factory
from app.modules.warehouse import import_stock
from app.modules.warehouse.import_stock import format_report, reconcile, run_import
from app.modules.warehouse.infrastructure import StockLotRow, WarehouseSequenceRow
from app.modules.warehouse.stock_export import StockExportError, parse_quantity, read_stock_export
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
    assert export.sheets["ЦАР"].skipped_columns["МАССА 1 ШТ, Г"] == 1
    assert "СРОК ГОДНОСТИ" in export.sheets["ЦАР"].skipped_columns
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
