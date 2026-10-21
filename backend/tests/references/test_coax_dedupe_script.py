"""Слияние обобщённых строк БД.КОАКС со строками настоящего артикула (docs/data/«КОАКС — слияние дублей обобщённых строк
19.10 запись в прод.py», владелец 19.10: один артикул — одна строка): пары находит сам, пустые ячейки R берёт у G, разное —
разногласие и G не сливается, партии идут только через edit_lot_identity (количества на месте), G с живой ссылкой не
трогается, повторный запуск ничего не меняет, откат по журналу возвращает всё."""

import csv
import io
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.modules.techcards import infrastructure as _techcards  # noqa: F401  (таблица запросов обновления ЭТК)
from app.shared.persistence import create_schema

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND.parent / "docs" / "data" / "КОАКС — слияние дублей обобщённых строк 19.10 запись в прод.py"


def coax(article, cable, **fields):
    row = {"Артикул": article, "Тип/Серия": "SMA-male", "Провод": cable, "Производитель": "", "Тип пина": "", "L1": "",
           "Тип экрана": "", "Артикул поставщика": "", "Наименование": "", "Ссылка": "", "Цена, ₽": "", **fields}
    return TechOperationRecord.create(tab=TechOperationTab.COAX, display_text=f"{row['Тип/Серия']} | {cable}", search_text="",
                                      source_sheet="БД.КОАКС", sort_key=article, fields=row, edited_in_erp=True)


def lot(grn, article, quantity, written_off=0, reserve=0):
    made = StockLot.create(warehouse="ЦАР", grn=grn, package="", name="SMA-male RG-402", item_type="Коаксиальный соединитель",
                           purpose="Склад", unit="шт", quantity=Decimal(quantity), storage_location="1F4", article=article,
                           series="SMA", source_receiving_ids=("r-1",))
    if written_off:
        made = made.write_off(Decimal(written_off))
    if reserve:
        made = made.reserve(Decimal(reserve))
    return made


def name(part, rest):
    return f"Разъём {part} ({rest})"


def make_db(tmp_path, records, lots, extra=()):
    url = f"sqlite+pysqlite:///{(tmp_path / 'dedupe.db').as_posix()}"
    engine = create_engine(url)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with SqlAlchemyReferencesUnitOfWork(sessions) as uow:
        for record in (*records, *extra):
            uow.tech_operations.save(record)
        uow.commit()
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        for item in lots:
            uow.warehouse.add(item)
        uow.commit()
    return url, engine


@pytest.fixture()
def database(tmp_path):
    records = [
        # пара 1: у G есть то, чего нет у R; у R есть соседняя строка артикула (другой кабель)
        coax("SMA-male RG-402", "RG-402", **{"Наименование": name("S-115-141", "SMA-male, напаиваемый"), "Тип пина": "пайка",
                                              "L1": "4", "Артикул поставщика": "gsm-repiteri.ru: 3449",
                                              "Источник данных": "L1–L3: по Huber+Suhner 11_SMA-50-3-15/111_NE"}),
        coax("S-115-141", "RG-402"),
        coax("S-115-141", "RG-405"),
        # пара 2: разные L1 — не сливается
        coax("SMA-male RG-174", "RG-174", **{"Наименование": name("S-111L", "SMA-male"), "L1": "5"}),
        coax("S-111L", "RG-174", **{"L1": "6", "Тип пина": "пайка"}),
        # пара 3: на G есть живая ссылка (запись ОП) — ничего не пишется в партии и пометку
        coax("SMA-male RG-316", "RG-316", **{"Наименование": name("S-111F", "SMA-male"), "Тип экрана": "обжим"}),
        coax("S-111F", "RG-316"),
        # без пары: R нет
        coax("SMA-male RG-178", "RG-178", **{"Наименование": name("S-115M", "SMA-male")}),
    ]
    op = TechOperationRecord.create(
        tab=TechOperationTab.OPERATION, display_text="Обжим SMA", search_text="", source_sheet="БД.ОП", sort_key="x",
        fields={"Название": "Обжим SMA-male RG-316", "Номер": "ОП.1"})
    lots = [lot("FRX.26.1", "SMA-male RG-402", "10", reserve="2"), lot("FRX.26.2", "sma-male  rg-402", "5", written_off="1"),
            lot("FRX.26.3", "S-115-141", "7"), lot("FRX.26.4", "SMA-male RG-174", "9"), lot("FRX.26.5", "SMA-male RG-316", "3")]
    url, engine = make_db(tmp_path, records, lots, (op,))
    yield url, engine
    engine.dispose()


def run(url, *args):
    env = {**os.environ, "ERP_DATABASE_URL": url, "PYTHONPATH": str(BACKEND), "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, cwd=BACKEND, capture_output=True, text=True,
                          encoding="utf-8", timeout=300)
    return done.returncode, done.stdout + done.stderr


def coax_fields(engine):
    with engine.connect() as connection:
        rows = connection.execute(text("select fields from tech_operation_records where tab = 'coax'")).scalars().all()
    import json
    parsed = [json.loads(row) if isinstance(row, str) else row for row in rows]
    return {(item["Артикул"], item["Провод"]): item for item in parsed}


def lots_table(engine):
    with engine.connect() as connection:
        return {row[0]: row[1:] for row in connection.execute(text(
            "select grn, article, version, inbound, written_off, reserved, name, series from warehouse_lots")).all()}


def test_dry_run_writes_nothing_and_lists_everything(database):
    url, engine = database
    before_cells, before_lots = coax_fields(engine), lots_table(engine)
    code, out = run(url, "dry")
    assert code == 0, out
    assert coax_fields(engine) == before_cells and lots_table(engine) == before_lots
    assert "«SMA-male RG-402» → «S-115-141» × RG-402" in out
    assert "ячейка R: Тип пина: «» → «пайка»" in out
    assert "РАЗНОГЛАСИЕ (не сливается): «SMA-male RG-174» → «S-111L» × RG-174" in out
    assert "L1: у G «5», у R «6»" in out
    assert "ссылка (живая): tech_operation_records" in out
    assert "«SMA-male RG-178» × RG-178 → номер детали «S-115M»" in out  # без пары
    assert "ИТОГО РАВНО" in out


def test_apply_merges_cells_moves_lots_by_app_operation_and_marks_the_duplicate(database):
    url, engine = database
    lots_before = lots_table(engine)
    code, out = run(url, "apply")
    assert code == 0, out
    cells = coax_fields(engine)
    real = cells[("S-115-141", "RG-402")]
    assert (real["Тип пина"], real["L1"], real["Артикул поставщика"]) == ("пайка", "4", "gsm-repiteri.ru: 3449")
    assert cells[("S-115-141", "RG-405")]["Тип пина"] == "пайка"  # одни значения у артикула — и соседняя строка
    assert real["Наименование"] == ""  # своё поле R не трогается
    assert cells[("SMA-male RG-402", "RG-402")]["Примечание"] == "дубль → S-115-141 (19.10.2026)"
    # разногласие: ничего не записано, G не помечена
    assert cells[("S-111L", "RG-174")]["L1"] == "6" and "Примечание" not in cells[("SMA-male RG-174", "RG-174")]
    # живая ссылка: G не помечена
    assert "Примечание" not in cells[("SMA-male RG-316", "RG-316")]
    lots = lots_table(engine)
    # партии G (в том числе записанная иначе по регистру и пробелам) — теперь на артикуле R, количества те же, версия +1
    assert lots["FRX.26.1"][0] == "S-115-141" and lots["FRX.26.2"][0] == "S-115-141"
    assert lots["FRX.26.1"][1] == lots_before["FRX.26.1"][1] + 1 and lots["FRX.26.2"][1] == lots_before["FRX.26.2"][1] + 1
    assert lots["FRX.26.4"][1] == lots_before["FRX.26.4"][1]
    assert (Decimal(str(lots["FRX.26.1"][2])), Decimal(str(lots["FRX.26.1"][4]))) == (Decimal("10"), Decimal("2"))
    assert Decimal(str(lots["FRX.26.2"][3])) == Decimal("1")
    assert lots["FRX.26.4"][0] == "SMA-male RG-174" and lots["FRX.26.5"][0] == "SMA-male RG-316"  # не тронуты
    assert lots["FRX.26.1"][-2:] == ("SMA-male RG-402", "SMA")  # наименование и серия как были
    with engine.connect() as connection:
        events = connection.execute(text("select count(*) from audit_events where event_type = 'warehouse.identity_edited'")).scalar()
    assert events == 2
    assert "все партии: 5 → 5, количества, места и движения без изменений: ДА" in out
    assert "вид,id,артикул G" in out


def test_a_second_run_changes_nothing(database):
    url, engine = database
    assert run(url, "apply")[0] == 0
    cells, lots = coax_fields(engine), lots_table(engine)
    code, out = run(url, "apply")
    assert code == 0, out
    assert coax_fields(engine) == cells and lots_table(engine) == lots
    assert "уже слито: «SMA-male RG-402» → «S-115-141»" in out
    assert "ячеек к записи\": 0" in out


def test_rollback_restores_cells_and_lot_articles(database, tmp_path):
    url, engine = database
    cells_before, lots_before = coax_fields(engine), lots_table(engine)
    code, out = run(url, "apply")
    assert code == 0, out
    journal = out.split("== CSV-журнал отката")[1].split("\n", 1)[1].split("ИТОГО")[0]
    rows = list(csv.DictReader(io.StringIO(journal)))
    assert {row["вид"] for row in rows} == {"ячейка", "партия"}
    path = tmp_path / "journal.csv"
    path.write_text(journal, encoding="utf-8", newline="")
    code, out = run(url, "rollback", str(path))
    assert code == 0, out
    after_cells = coax_fields(engine)
    for key, fields in cells_before.items():
        for name, value in fields.items():
            assert after_cells[key].get(name, "") == value, (key, name)
    assert after_cells[("SMA-male RG-402", "RG-402")].get("Примечание", "") == ""
    assert {grn: row[0] for grn, row in lots_table(engine).items()} == {grn: row[0] for grn, row in lots_before.items()}


def test_a_value_that_differs_on_another_row_of_the_real_article_is_a_disagreement_and_lots_stay(tmp_path):
    records = [
        coax("SMA-male RG-402", "RG-402", **{"Наименование": name("S-115-141", "x"), "Тип пина": "пайка"}),
        coax("SMA-male RG-402", "RG-405", **{"Наименование": name("S-115-141", "x")}),  # вторая строка G — своя пара на другом кабеле
        coax("S-115-141", "RG-402"),
        coax("S-115-141", "RG-405", **{"Тип пина": "обжим"}),
    ]
    url, engine = make_db(tmp_path, records, [lot("FRX.26.1", "SMA-male RG-402", "10")])
    code, out = run(url, "apply")
    assert code == 0, out
    assert "Тип пина: у G «пайка», у R «обжим (у другой строки артикула S-115-141)»" in out
    assert lots_table(engine)["FRX.26.1"][0] == "SMA-male RG-402"
    assert coax_fields(engine)[("S-115-141", "RG-402")]["Тип пина"] == ""


def test_seller_codes_are_merged_by_seller_and_a_different_code_of_one_seller_is_a_disagreement(tmp_path):
    records = [
        coax("SMA-male RG-402", "RG-402", **{"Наименование": name("S-115-141", "x"),
                                              "Артикул поставщика": "gsm-repiteri.ru: 3449; kroks.ru: 12"}),
        coax("S-115-141", "RG-402", **{"Артикул поставщика": "gsm-repiteri.ru: 3449"}),
        coax("SMA-male RG-174", "RG-174", **{"Наименование": name("S-111L", "x"), "Артикул поставщика": "gsm-repiteri.ru: 1"}),
        coax("S-111L", "RG-174", **{"Артикул поставщика": "gsm-repiteri.ru: 2"}),
    ]
    url, engine = make_db(tmp_path, records, [])
    code, out = run(url, "apply")
    assert code == 0, out
    cells = coax_fields(engine)
    assert cells[("S-115-141", "RG-402")]["Артикул поставщика"] == "gsm-repiteri.ru: 3449; kroks.ru: 12"
    assert cells[("S-111L", "RG-174")]["Артикул поставщика"] == "gsm-repiteri.ru: 2"
    assert "Артикул поставщика (gsm-repiteri.ru): у G «1», у R «2»" in out


def test_a_g_row_with_its_own_article_in_the_name_is_not_a_duplicate_and_an_undocumented_length_is_not_copied(tmp_path):
    records = [
        # «S-231L NGT» — настоящий артикул G стоит в имени целиком: свой товар (никелированный), в пары не берётся
        coax("S-231L NGT", "RG-174", **{"Наименование": name("S-231L NGT", "SMA-female, никелированный"), "Тип пина": "пайка"}),
        coax("S-231L", "RG-174"),
        # у G длина цеховая, без документа: в пустую ячейку R не переносится, остальное сливается
        coax("SMA-male RG-402", "RG-402", **{"Наименование": name("S-115-141", "x"), "L1": "2", "Тип пина": "пайка"}),
        coax("S-115-141", "RG-402"),
    ]
    url, engine = make_db(tmp_path, records, [])
    code, out = run(url, "apply")
    assert code == 0, out
    cells = coax_fields(engine)
    assert "«S-231L NGT» →" not in out and "Примечание" not in cells[("S-231L NGT", "RG-174")]
    assert cells[("S-231L", "RG-174")]["Тип пина"] == ""
    assert cells[("S-115-141", "RG-402")]["Тип пина"] == "пайка" and cells[("S-115-141", "RG-402")]["L1"] == ""


FIX_SCRIPT = BACKEND.parent / "docs" / "data" / "КОАКС — слияние дублей разногласия 19.10 запись в прод.py"


def run_fix(url, *args):
    env = {**os.environ, "ERP_DATABASE_URL": url, "PYTHONPATH": str(BACKEND), "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, str(FIX_SCRIPT), *args], env=env, cwd=BACKEND, capture_output=True, text=True,
                          encoding="utf-8", timeout=300)
    return done.returncode, done.stdout + done.stderr


def fix_payload():
    import json
    import re
    source = FIX_SCRIPT.read_text(encoding="utf-8")
    return json.loads(re.search(r"PAYLOAD = json.loads\(r'''(.*?)'''\)", source, re.S).group(1))


def test_disagreement_script_writes_the_planned_cells_once_guarded_and_rolls_back(tmp_path):
    payload = fix_payload()
    assert payload and all(e["edits"] for e in payload)
    records = []
    for item in payload:
        base = {name: old for name, old, _ in item["edits"]}
        records.append(coax(item["article"], item["cable"], **base))
    url, engine = make_db(tmp_path, records, [])
    before = coax_fields(engine)
    code, out = run_fix(url, "dry")
    assert code == 0, out
    assert coax_fields(engine) == before  # проверка ничего не пишет
    code, out = run_fix(url, "apply")
    assert code == 0, out
    cells = coax_fields(engine)
    for item in payload:
        row = cells[(item["article"], item["cable"])]
        for name, _, new in item["edits"]:
            assert row[name] == new, (item["article"], name)
    journal = out.split("== CSV-журнал отката")[1].split("\n", 1)[1].split("ИТОГО")[0]
    code, again = run_fix(url, "apply")  # повтор: всё «уже так», ничего не меняется
    assert code == 0, again
    assert coax_fields(engine) == cells and "уже так" in again
    path = tmp_path / "journal.csv"
    path.write_text(journal, encoding="utf-8", newline="")
    code, out = run_fix(url, "rollback", str(path))
    assert code == 0, out
    restored = coax_fields(engine)
    for key, fields in before.items():
        for name, value in fields.items():
            assert restored[key].get(name, "") == value, (key, name)


def test_disagreement_script_refuses_a_cell_that_changed_meanwhile(tmp_path):
    item = next(e for e in fix_payload() if e["article"] == "SMA-female RG-174")
    base = {name: old for name, old, _ in item["edits"]}
    base["L2"] = "9"  # кто-то поправил ячейку — ни старое, ни новое
    url, engine = make_db(tmp_path, [coax(item["article"], item["cable"], **base)], [])
    code, out = run_fix(url, "apply")
    assert code == 1 and "ОТКАЗ" in out
    assert coax_fields(engine)[(item["article"], item["cable"])]["L3"] == base["L3"]
