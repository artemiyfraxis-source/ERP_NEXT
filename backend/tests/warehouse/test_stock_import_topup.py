"""WH-IMPORT top-up («грузим сейчас, потом догружаем»): run 1 over export A, ERP works, run 2 over a newer export B.

Run 2 adds only the difference: new lots, new СПИСАНИЕ rows, Google changes of lots ERP never moved. A lot ERP has moved
is never overwritten. Runs 3+ over B change nothing; a dry run of B writes nothing and shows the numbers the real run
then applies. The real-export variant runs when ERP_REAL_EXPORT_DIR points at a SUPPLYMAP export (data/import)."""

import csv
import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.database import build_engine, build_warehouse_uow_factory
from app.modules.warehouse.application import MoveLotCommand, WriteOffCommand, move_lot, write_off
from app.modules.warehouse.import_stock import format_report
from app.modules.warehouse.infrastructure import StockLotRow, WriteOffEntryRow
from app.modules.warehouse.stock_export import read_stock_export
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.warehouse.test_stock_import import (
    CLEAN_CAR,
    CLEAN_JOURNAL,
    CLEAN_ZHUK,
    NOW,
    car_row,
    database_url,  # noqa: F401 — the fixture
    journal_row,
    run,
    write_export,
    zhuk_row,
)

STOREKEEPER = Actor(name="Кладовщик", department="Склад")

# Export A: the clean fixture plus two lots nobody will touch in ERP.
A_CAR = [
    *CLEAN_CAR,
    car_row(АРТИКУЛ="TEST-T5", ГРН="OLD.26.0000005", ПРИХОД="10", СПИСАНО="0", БАЛАНС="10"),
    car_row(АРТИКУЛ="TEST-G6", ГРН="OLD.26.0000006", ПРИХОД="6", СПИСАНО="1", БАЛАНС="5", **{"МЕСТО ХРАНЕНИЯ": "3B2C"}),
]
A_JOURNAL = [*CLEAN_JOURNAL, journal_row("OLD.26.0000006", "1")]

# Export B, a week later in Google:
#   OLD.26.0000002 — Google wrote off 0,5 м more, ERP also wrote off 0,25 м: both moved → ERP kept, reported;
#   OLD.26.0000005 — Google wrote off 3 (ERP never touched it) → topped up with its СПИСАНИЕ row;
#   OLD.26.0000006 — Google moved the lot to ЖУК (ERP never touched it) → the same lot follows, no second lot;
#   OLD.26.0000007 — a new receipt with its own СПИСАНИЕ row → created;
#   OLD.26.0000001 and OLD.25.0000009 — unchanged in Google, ERP wrote one off / moved the other to ЦАР.
B_CAR = [
    CLEAN_CAR[0],
    car_row(АРТИКУЛ="TEST-W2", ГРН="OLD.26.0000002", ПРИХОД="2,5", СПИСАНО="1,25", БАЛАНС="1,25",
            **{"ЕД.ИЗМ": "м", "МАССА 1 ШТ, Г": "0,125"}),
    CLEAN_CAR[2],
    car_row(АРТИКУЛ="TEST-T5", ГРН="OLD.26.0000005", ПРИХОД="10", СПИСАНО="3", БАЛАНС="7"),
    car_row(АРТИКУЛ="TEST-N7", ГРН="OLD.26.0000007", ПРИХОД="7", СПИСАНО="2", БАЛАНС="5", **{"МЕСТО ХРАНЕНИЯ": "3B2D"}),
]
B_ZHUK = [
    *CLEAN_ZHUK,
    zhuk_row(АРТИКУЛ="TEST-G6", ГРН="OLD.26.0000006", ПРИХОД="6", СПИСАНО="1", БАЛАНС="5", **{"МЕСТО ХРАНЕНИЯ": "01A2"}),
]
B_JOURNAL = [
    *A_JOURNAL,
    journal_row("OLD.26.0000002", "0,5", reason="Брак"),
    journal_row("OLD.26.0000005", "3"),
    journal_row("OLD.26.0000007", "2"),
]


def erp_session(database_url, action):
    engine = build_engine(database_url)
    try:
        return action(build_warehouse_uow_factory(engine))
    finally:
        engine.dispose()


def lot_at(factory, warehouse, grn):
    uow = factory()
    with uow:
        return uow.warehouse.by_key(warehouse, grn, "")


def erp_writes_off(database_url, warehouse, grn, quantity):
    def action(factory):
        lot = lot_at(factory, warehouse, grn)
        write_off(WriteOffCommand(lot_id=lot.id, expected_version=lot.version, quantity=Decimal(quantity),
                                  purpose="999К.1", reason="", actor=STOREKEEPER, now=NOW), factory())
    erp_session(database_url, action)


def erp_transfers(database_url, warehouse, grn, target):
    def action(factory):
        lot = lot_at(factory, warehouse, grn)
        move_lot(MoveLotCommand(lot_id=lot.id, expected_version=lot.version, warehouse=target, storage_location="",
                                actor=STOREKEEPER, now=NOW), factory())
    erp_session(database_url, action)


def state(database_url):
    """Everything a rerun could disturb: lots (with id and version), the СПИСАНИЕ journal, the audit count."""
    engine = build_engine(database_url)
    try:
        with engine.connect() as connection:
            lots = connection.execute(
                select(StockLotRow.id, StockLotRow.version, StockLotRow.warehouse, StockLotRow.grn, StockLotRow.package,
                       StockLotRow.inbound, StockLotRow.written_off, StockLotRow.storage_location)
                .order_by(StockLotRow.grn, StockLotRow.package)
            ).all()
            journal = connection.execute(
                select(WriteOffEntryRow.lot_id, WriteOffEntryRow.kind, WriteOffEntryRow.quantity,
                       WriteOffEntryRow.reason, WriteOffEntryRow.source_key)
                .order_by(WriteOffEntryRow.source_key, WriteOffEntryRow.kind, WriteOffEntryRow.quantity)
            ).all()
            audit = connection.scalar(select(func.count()).select_from(AuditEventRow))
    finally:
        engine.dispose()
    return lots, journal, audit


def balances(database_url):
    return {(warehouse, grn): inbound - written_off
            for _, _, warehouse, grn, _, inbound, written_off, _ in state(database_url)[0]}


def counters(result, check):
    """The numbers the report prints — the dry run must show exactly what the real run then applies."""
    return {
        "created": sorted(row.grn for row in result.created),
        "synced": sorted((row.grn, tuple(change[0] for change in changes)) for row, changes in result.synced),
        "unchanged": len(result.unchanged),
        "conflicts": sorted(row.grn for row, _ in result.conflicts),
        "erp_ahead": sorted(row.grn for row, _ in result.erp_ahead),
        "rejected": len(result.rejected),
        "history_imported": result.history_imported,
        "history_synced": result.history_synced,
        "history_for_existing_lots": result.history_for_existing_lots,
        "history_not_synced": sorted((row.grn, row.reason) for row in result.history_not_synced),
        "missing_in_sheet": sorted(lot.grn for lot in result.missing_in_sheet),
        "grn_mismatches": [(line.grn, line.sheet_balance, line.erp_balance) for line in check.grn_mismatches],
        "ok": check.ok,
    }


def run_one_then_erp_works(tmp_path, database_url):
    _, first, check = run(write_export(tmp_path / "A", car=A_CAR, journal=A_JOURNAL), database_url)
    assert len(first.created) == 6 and check.ok
    erp_writes_off(database_url, "ЦАР", "OLD.26.0000001", "1")
    erp_writes_off(database_url, "ЦАР", "OLD.26.0000002", "0.25")
    erp_transfers(database_url, "ЖУК", "OLD.25.0000009", "ЦАР")
    return write_export(tmp_path / "B", car=B_CAR, zhuk=B_ZHUK, journal=B_JOURNAL)


def test_second_run_adds_only_the_difference_and_keeps_what_erp_did(tmp_path, database_url):
    b = run_one_then_erp_works(tmp_path, database_url)
    lots_before, journal_before, _ = state(database_url)
    ids_before = {(grn, package): lot_id for lot_id, _, _, grn, package, *_ in lots_before}

    export, result, check = run(b, database_url)

    lots, journal, _ = state(database_url)
    assert len(lots) == 7  # six from A + one new, no lot twice
    assert {(grn, package): lot_id for lot_id, _, _, grn, package, *_ in lots if grn != "OLD.26.0000007"} == ids_before
    assert balances(database_url) == {
        ("ЦАР", "OLD.26.0000001"): Decimal("7"),     # ERP wrote off 1, Google still says 8
        ("ЦАР", "OLD.26.0000002"): Decimal("1.5"),   # both moved: ERP kept, not Google's 1,25
        ("ЦАР", "OLD.26.0000003"): Decimal("0"),
        ("ЦАР", "OLD.25.0000009"): Decimal("4"),     # ERP moved it ЖУК → ЦАР; Google's ЖУК row made no copy
        ("ЦАР", "OLD.26.0000005"): Decimal("7"),     # topped up from Google
        ("ЖУК", "OLD.26.0000006"): Decimal("5"),     # moved to ЖУК as in Google
        ("ЦАР", "OLD.26.0000007"): Decimal("5"),     # new
    }
    assert len(journal) == len(journal_before) + 2  # СПИСАНИЕ of OLD.26.0000005 and of the new OLD.26.0000007
    new_rows = sorted((r.quantity, r.reason) for r in journal if r not in journal_before)
    assert new_rows == [(Decimal("2"), "Выдача в производство"), (Decimal("3"), "Выдача в производство")]

    assert counters(result, check) == {
        "created": ["OLD.26.0000007"],
        "synced": [("OLD.26.0000005", ("written_off", "balance")),
                   ("OLD.26.0000006", ("warehouse", "storage_location"))],
        "unchanged": 1,
        "conflicts": ["OLD.26.0000002"],
        "erp_ahead": ["OLD.25.0000009", "OLD.26.0000001"],
        "rejected": 0,
        "history_imported": 1,
        "history_synced": 1,
        "history_for_existing_lots": 6,
        "history_not_synced": [("OLD.26.0000002", "Брак")],
        "missing_in_sheet": [],
        "grn_mismatches": [("OLD.26.0000002", Decimal("1.25"), Decimal("1.5"))],
        "ok": False,
    }
    [(_, reason)] = result.conflicts
    assert reason.startswith("в ERP уже были движения (событий 1, списаний и выдач 1)")
    assert "в Google после загрузки: СПИСАНО 0,75 → 1,25" in reason
    report = format_report(export, result, check)
    assert "ERP впереди Google: ЦАР OLD.26.0000001 — лист 8 / ERP 7 шт" in report
    assert "догрузка из Google ЖУК:3 OLD.26.0000006: СКЛАД ЦАР → ЖУК" in report
    assert "не внесено СПИСАНИЕ:" in report and "OLD.26.0000002 0,5 шт «Брак»" in report
    totals = {(t.warehouse, t.unit): t for t in check.totals}
    assert totals[("ЦАР", "шт")].ok and totals[("ЦАР", "шт")].erp_moves == Decimal("-1")
    assert not totals[("ЦАР", "м")].ok


def test_third_run_over_the_same_export_changes_nothing(tmp_path, database_url):
    b = run_one_then_erp_works(tmp_path, database_url)
    _, second, check_second = run(b, database_url)
    after_second = state(database_url)

    for _ in range(2):
        _, again, check = run(b, database_url)
        assert state(database_url) == after_second
        assert not again.created and not again.synced and again.history_synced == 0 and again.history_imported == 0
        numbers = counters(again, check)
        assert numbers["conflicts"] == ["OLD.26.0000002"] and numbers["erp_ahead"] == ["OLD.25.0000009", "OLD.26.0000001"]
        assert numbers["history_not_synced"] == counters(second, check_second)["history_not_synced"]
        assert numbers["grn_mismatches"] == counters(second, check_second)["grn_mismatches"]


def test_dry_run_of_the_newer_export_writes_nothing_and_shows_what_the_real_run_applies(tmp_path, database_url):
    b = run_one_then_erp_works(tmp_path, database_url)
    before = state(database_url)

    export, dry, dry_check = run(b, database_url, dry_run=True)

    assert state(database_url) == before and not dry.committed
    _, real, real_check = run(b, database_url)
    assert real.committed and counters(dry, dry_check) == counters(real, real_check)
    assert "ПРОБНЫЙ ПРОГОН" in format_report(export, dry, dry_check)


def test_lot_erp_moved_to_the_other_warehouse_is_not_created_again(tmp_path, database_url):
    """A whole-lot transfer changes the lot key (СКЛАД, ГРН, ПАКЕТ); the import finds its lot by the import mark."""
    directory = write_export(tmp_path / "A")
    run(directory, database_url)
    erp_transfers(database_url, "ЖУК", "OLD.25.0000009", "ЦАР")
    before = state(database_url)

    _, result, check = run(directory, database_url)

    assert state(database_url) == before
    assert not result.created and not result.conflicts and not result.missing_in_sheet
    assert [row.grn for row, _ in result.erp_ahead] == ["OLD.25.0000009"]
    assert check.ok


def test_google_change_of_a_lot_erp_moved_only_in_place_keeps_erp_and_reports_it(tmp_path, database_url):
    """ERP moved the lot to another shelf, Google wrote 2 off: quantities differ on both sides → ERP is kept."""
    run(write_export(tmp_path / "A"), database_url)
    erp_session(database_url, lambda factory: move_lot(MoveLotCommand(
        lot_id=lot_at(factory, "ЦАР", "OLD.26.0000001").id, expected_version=2, warehouse="",
        storage_location="3B2D", actor=STOREKEEPER, now=NOW), factory()))
    changed = car_row(АРТИКУЛ="TEST-A1", НАИМЕНОВАНИЕ="Тестовая вилка", ТИП="Соединитель", ГРН="OLD.26.0000001",
                      ПРИХОД="10", СПИСАНО="4", БАЛАНС="6", **{"МЕСТО ХРАНЕНИЯ": "3B2A"})

    _, result, check = run(write_export(tmp_path / "B", car=[changed, *CLEAN_CAR[1:]]), database_url)

    [(row, reason)] = result.conflicts
    assert row.grn == "OLD.26.0000001" and "в Google после загрузки: СПИСАНО 2 → 4" in reason
    assert balances(database_url)[("ЦАР", "OLD.26.0000001")] == Decimal("8") and not check.ok


def test_google_moved_a_lot_erp_wrote_off_from_keeps_erp_and_makes_no_second_lot(tmp_path, database_url):
    run(write_export(tmp_path / "A", car=A_CAR, journal=A_JOURNAL), database_url)
    erp_writes_off(database_url, "ЦАР", "OLD.26.0000006", "1")
    lots_before = state(database_url)[0]

    _, result, check = run(write_export(tmp_path / "B", car=A_CAR[:-1], zhuk=B_ZHUK, journal=A_JOURNAL), database_url)

    assert state(database_url)[0] == lots_before and not result.created
    [(row, reason)] = result.conflicts
    assert row.grn == "OLD.26.0000006" and "в Google после загрузки: СКЛАД ЦАР → ЖУК" in reason
    assert not result.missing_in_sheet and not check.ok


# The real SUPPLYMAP export as A (data/import is gitignored: set ERP_REAL_EXPORT_DIR to run this locally).

REAL_EXPORT = os.getenv("ERP_REAL_EXPORT_DIR", "").strip()


def _read_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def _number(value):
    return format(value.normalize(), "f").replace(".", ",") if value else "0"


def newer_real_export(source, target, topped_grn):
    """B = the real export + a new lot with its СПИСАНИЕ row + 1 more written off in Google on `topped_grn`."""
    shutil.copytree(source, target)
    car = _read_csv(target / "ЦАР.csv")
    header = car[0]
    column = {name: header.index(name) for name in header}
    for line in car[1:]:
        if line[column["ГРН"]] == topped_grn and not line[column["ПАКЕТ"]]:
            line[column["СПИСАНО"]] = _number(read_quantity(line[column["СПИСАНО"]]) + 1)
            line[column["БАЛАНС"]] = _number(read_quantity(line[column["БАЛАНС"]]) - 1)
    new = [""] * len(header)
    for name, value in {"АРТИКУЛ": "TEST-NEW", "НАИМЕНОВАНИЕ": "Тестовая новая партия", "НАЗНАЧЕНИЕ": "Склад",
                        "ЕД.ИЗМ": "шт", "ГРН": "OLD.26.9999901", "ПРИХОД": "12", "СПИСАНО": "2", "БАЛАНС": "10"}.items():
        new[column[name]] = value
    car.append(new)
    _write_csv(target / "ЦАР.csv", car)
    journal = _read_csv(target / "СПИСАНИЕ.csv")
    journal.append(journal_row(topped_grn, "1", reason="Тестовая догрузка"))
    journal.append(journal_row("OLD.26.9999901", "2", reason="Тестовая догрузка"))
    _write_csv(target / "СПИСАНИЕ.csv", journal)
    manifest = json.loads((target / "_manifest.json").read_text(encoding="utf-8"))
    manifest["exportedAt"] = "2026-10-03T09:00:00.000Z"
    manifest["sheets"]["ЦАР"]["rows"] = len(car)
    manifest["sheets"]["СПИСАНИЕ"]["rows"] = len(journal)
    (target / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return target


def read_quantity(text):
    from app.modules.warehouse.stock_export import parse_quantity
    return parse_quantity(text)


@pytest.mark.skipif(not REAL_EXPORT or not Path(REAL_EXPORT).is_dir(), reason="ERP_REAL_EXPORT_DIR не задан")
def test_real_export_then_a_newer_one_tops_up_only_the_difference(tmp_path, database_url, capsys):
    source = Path(REAL_EXPORT)
    a = tmp_path / "A"
    shutil.copytree(source, a)
    export_a, first, check_a = run(a, database_url)
    lots_a, journal_a, _ = state(database_url)
    assert len(lots_a) == len(first.created) == len(export_a.lots) - len(first.rejected)

    # ERP works: writes 1 off the first lot with stock; Google writes 1 off another one.
    movable = [lot for lot in export_a.lots if lot.balance >= 2 and not lot.package and lot.warehouse == "ЦАР"]
    erp_lot, topped = movable[0], movable[1]
    erp_writes_off(database_url, "ЦАР", erp_lot.grn, "1")
    b = newer_real_export(source, tmp_path / "B", topped.grn)

    _, dry, dry_check = run(b, database_url, dry_run=True)
    export_b, second, check_b = run(b, database_url)
    lots_b, journal_b, _ = state(database_url)

    assert counters(dry, dry_check) == counters(second, check_b)
    assert [row.grn for row in second.created] == ["OLD.26.9999901"]
    assert [row.grn for row, _ in second.synced] == [topped.grn]
    assert [row.grn for row, _ in second.erp_ahead] == [erp_lot.grn]
    assert not second.conflicts and not second.missing_in_sheet
    assert len(lots_b) == len(lots_a) + 1
    assert len(journal_b) == len(journal_a) + 1 + 2  # ERP's write-off + two new СПИСАНИЕ rows
    sheet = {lot.key: lot.balance for lot in export_b.lots}
    erp = {(warehouse, grn, package): inbound - written_off
           for _, _, warehouse, grn, package, inbound, written_off, _ in lots_b}
    differs = {key: (sheet.get(key), erp[key]) for key in erp if sheet.get(key) != erp[key]}
    assert differs == {erp_lot.key: (erp_lot.balance, erp_lot.balance - 1)}
    assert [(m.grn, m.sheet_balance) for m in check_b.grn_mismatches] == [
        (m.grn, m.sheet_balance) for m in check_a.grn_mismatches]
    assert check_b.stock_rejected == check_a.stock_rejected

    third_before = state(database_url)
    _, third, check_c = run(b, database_url)
    assert state(database_url) == third_before
    assert not third.created and not third.synced and third.history_synced == third.history_imported == 0

    with capsys.disabled():
        print(f"\n[real export] A: lots {len(lots_a)}, journal rows {len(journal_a)}, rejected {check_a.stock_rejected}, "
              f"ГРН {check_a.grn_count} (mismatch {len(check_a.grn_mismatches)}), ok {check_a.ok}; "
              f"B run 2: created {len(second.created)}, synced {len(second.synced)}, erp_ahead {len(second.erp_ahead)}, "
              f"history +{second.history_imported + second.history_synced}, lots {len(lots_b)}, journal {len(journal_b)}, "
              f"ok {check_b.ok}; run 3: changes 0, ok {check_c.ok}")
