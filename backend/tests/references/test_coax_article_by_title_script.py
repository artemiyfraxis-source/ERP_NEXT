"""Артикулы БД.КОАКС по названию товара (docs/data/«КОАКС — артикулы по названию товара 20.10 запись в прод.py», владелец
20.10): помеченные «дубль → …» строки удаляются (с событием и полной старой строкой), дубль по коду поставщика на том же
кабеле сливается в R и удаляется, обобщённая строка без пары получает номер детали из названия и значения артикула, строка
с живой ссылкой или партией склада не трогается, остатки склада на месте, повтор ничего не меняет, откат возвращает всё."""

import csv
import io
import json
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
SCRIPT = BACKEND.parent / "docs" / "data" / "КОАКС — артикулы по названию товара 20.10 запись в прод.py"


def coax(article, cable, **fields):
    row = {"Артикул": article, "Тип/Серия": "N-female", "Провод": cable, "Производитель": "", "Тип пина": "", "L1": "",
           "Тип экрана": "", "Артикул поставщика": "", "Наименование": "", "Ссылка": "", "Цена, ₽": "",
           "Источник данных": "", "Примечание": "", "КСВ": "", "Пол": "", **fields}
    return TechOperationRecord.create(tab=TechOperationTab.COAX, display_text=f"{row['Тип/Серия']} | {cable}", search_text="",
                                      source_sheet="БД.КОАКС", sort_key=article, fields=row, edited_in_erp=True)


def lot(grn, article, quantity):
    return StockLot.create(warehouse="ЦАР", grn=grn, package="", name=article, item_type="Коаксиальный соединитель",
                           purpose="Склад", unit="шт", quantity=Decimal(quantity), storage_location="1F4", article=article,
                           series="MCX", source_receiving_ids=("r-1",))


@pytest.fixture()
def database(tmp_path):
    records = [
        # помеченные дубли: первый уходит, у второго живая ссылка (запись ОП) — остаётся
        coax("SMA-female RG-174", "RG-174", **{"Примечание": "дубль → S-211L (19.10.2026)"}),
        coax("SMA-male RG-178", "RG-178", **{"Примечание": "дубль → S-115M (19.10.2026)"}),
        coax("S-211L", "RG-174"), coax("S-115M", "RG-178"),
        # дубль по коду поставщика: R побеждает, пустое у R берётся у G, старое G уходит в «Источник данных» R
        coax("N-female RG-316", "RG-174", **{"Тип пина": "обжим", "КСВ": "<2", "Артикул поставщика": "kroks.ru: 656"}),
        coax("N-211L", "RG-174", **{"Тип пина": "пайка", "Артикул поставщика": "gsm-repiteri.ru: 2362"}),
        # переименование: у N-211F есть строка на другом кабеле — значения артикула одни
        coax("N-female RG-142", "RG-142", **{"L1": "4", "Тип пина": "", "Артикул поставщика": "gsm-repiteri.ru: 0299",
                                              "Наименование": "Разъём N-211F (N-female, обжимной, на кабель RG-58)"}),
        coax("N-211F", "RG-58", **{"L1": "5", "Пол": "гнездо", "Тип пина": "пайка",
                                    "Артикул поставщика": "gsm-repiteri.ru: 0299; kroks.ru: 302"}),
        # переименование, но на артикул G есть партия склада — остаётся
        coax("MCX-male RG-316", "RG-316"), coax("11-111L", "RG-174"),
    ]
    op = TechOperationRecord.create(
        tab=TechOperationTab.OPERATION, display_text="Обжим SMA", search_text="", source_sheet="БД.ОП", sort_key="x",
        fields={"Название": "Обжим SMA-male RG-178", "Номер": "ОП.1"})
    url = f"sqlite+pysqlite:///{(tmp_path / 'by_title.db').as_posix()}"
    engine = create_engine(url)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with SqlAlchemyReferencesUnitOfWork(sessions) as uow:
        for record in (*records, op):
            uow.tech_operations.save(record)
        uow.commit()
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        uow.warehouse.add(lot("FRX.26.1", "MCX-male RG-316", "40"))
        uow.commit()
    yield url, engine
    engine.dispose()


def run(url, *args):
    env = {**os.environ, "ERP_DATABASE_URL": url, "PYTHONPATH": str(BACKEND), "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, cwd=BACKEND, capture_output=True, text=True,
                          encoding="utf-8", timeout=300)
    return done.returncode, done.stdout + done.stderr


def coax_rows(engine):
    with engine.connect() as connection:
        rows = connection.execute(text("select fields from tech_operation_records where tab = 'coax'")).scalars().all()
    parsed = [json.loads(row) if isinstance(row, str) else row for row in rows]
    return {(item["Артикул"], item["Провод"]): item for item in parsed}


def lots_table(engine):
    with engine.connect() as connection:
        return connection.execute(text("select grn, article, version, inbound, written_off, reserved from warehouse_lots")).all()


def deleted_events(engine):
    with engine.connect() as connection:
        rows = connection.execute(text("select details from audit_events where event_type = 'references.record_deleted'")).scalars().all()
    return [json.loads(row) if isinstance(row, str) else row for row in rows]


def test_dry_run_writes_nothing(database):
    url, engine = database
    before_rows, before_lots = coax_rows(engine), lots_table(engine)
    code, out = run(url, "dry")
    assert code == 0, out
    assert coax_rows(engine) == before_rows and lots_table(engine) == before_lots and not deleted_events(engine)
    assert "удалена (пометка «дубль»): SMA-female RG-174 × RG-174" in out
    assert "НЕ удалена (пометка «дубль»): SMA-male RG-178" in out and "tech_operation_records" in out
    assert "переименована: N-female RG-142 × RG-142 → N-211F" in out
    assert "НЕ переименована (ссылки): MCX-male RG-316 × RG-316" in out and "партий склада: 1" in out


def test_apply_deletes_merges_and_renames_then_repeat_changes_nothing_and_rollback_restores(database):
    url, engine = database
    before_rows, before_lots = coax_rows(engine), lots_table(engine)
    code, out = run(url, "apply")
    assert code == 0, out
    rows = coax_rows(engine)
    assert ("SMA-female RG-174", "RG-174") not in rows                       # помеченный дубль удалён
    assert ("SMA-male RG-178", "RG-178") in rows                              # с живой ссылкой остался
    assert ("N-female RG-316", "RG-174") not in rows                          # дубль по коду слит и удалён
    merged = rows[("N-211L", "RG-174")]
    assert merged["КСВ"] == "<2" and merged["Тип пина"] == "пайка"            # пустое взято у G, спорное — значение R
    assert "было у «N-female RG-316» «обжим», оставлено «пайка»" in merged["Источник данных"]
    assert "kroks.ru: 656" in merged["Артикул поставщика"] and "gsm-repiteri.ru: 2362" in merged["Артикул поставщика"]
    assert ("N-female RG-142", "RG-142") not in rows
    renamed = rows[("N-211F", "RG-142")]                                       # переименована, значения как у артикула
    assert renamed["coaxArticle"] == "N-211F" and renamed["L1"] == "5" and renamed["Пол"] == "гнездо"
    assert renamed["Тип пина"] == "пайка" and "kroks.ru: 302" in renamed["Артикул поставщика"]
    assert "«N-female RG-142» → «N-211F»" in renamed["Источник данных"] and "L1: было «4»" in renamed["Источник данных"]
    assert ("MCX-male RG-316", "RG-316") in rows and ("11-111L", "RG-316") not in rows  # партия — строка на месте
    assert lots_table(engine) == before_lots                                   # склад не тронут
    events = deleted_events(engine)
    assert len(events) == 2 and {e["fields"]["Артикул"] for e in events} == {"SMA-female RG-174", "N-female RG-316"}
    assert all(e["fields"]["Провод"] for e in events)

    code, again = run(url, "dry")
    assert code == 0, again
    assert "ИТОГО {" in again and '"удалено"' not in again and '"переименовано"' not in again

    log = out.split("== CSV-журнал отката ==\n", 1)[1].rsplit("ИТОГО {", 1)[0]
    csv_path = Path(engine.url.database).with_name("journal.csv")
    csv_path.write_text(log, encoding="utf-8", newline="")
    assert len(list(csv.DictReader(io.StringIO(log)))) > 4
    code, back = run(url, "rollback", str(csv_path))
    assert code == 0, back
    restored = coax_rows(engine)
    assert set(restored) == set(before_rows)
    for key, fields in before_rows.items():
        same = lambda item: {k: v for k, v in item.items() if v != "" and not k.startswith("coax")}  # noqa: E731
        assert same(restored[key]) == same(fields), key
