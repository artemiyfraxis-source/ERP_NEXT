"""Артикулы RUICHI в БД.КОАКС (docs/data/«Коакс RUICHI — Промэлектроника 20.10 запись в прод.py», владелец 20.10): новые пары
(артикул, кабель) создаются, у строк базы заполняются только пустые ячейки, предложение «Промэлектроники» заменяет прежнее
только когда оно дешевле и позиция в наличии (старое значение — в «Источнике данных»), чужие строки не трогаются, повтор
ничего не пишет (обновления отказываются по старым значениям), откат возвращает прежнее и удаляет созданные строки."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards import infrastructure as _techcards  # noqa: F401  (таблица запросов обновления ЭТК)
from app.shared.persistence import create_schema

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND.parent / "docs" / "data" / "Коакс RUICHI — Промэлектроника 20.10 запись в прод.py"
PAYLOAD = json.loads(re.search(r"PAYLOAD = json\.loads\(r'''(.*?)'''\)", SCRIPT.read_text(encoding="utf-8"), re.S).group(1))
CREATED = [item for item in PAYLOAD if item["mode"] == "create"]
UPDATED = [item for item in PAYLOAD if item["mode"] == "update"]
KEYS = {"coaxArticle", "coaxWire", "coaxType", "coaxMfr"}
# the columns the base already has on prod: a load refuses a column no row carries
COLUMNS = ("Артикул", "Тип/Серия", "Провод", "Производитель", *sorted({name for item in PAYLOAD for name in item["fields"]} - KEYS))


def seed(item):
    """The row as the base had it before the load: every cell the update guards by `old`, the rest empty."""
    row = {name: "" for name in COLUMNS}
    row.update({"Артикул": item["art"], "Провод": item["cable"], "Тип/Серия": "SMA-male", "Производитель": "RUICHI",
                "coaxArticle": item["art"], "coaxWire": item["cable"], "coaxType": "SMA-male", "coaxMfr": "RUICHI"})
    row.update({name: value for name, value in item["old"].items() if value != ""})
    if "coaxMfr" in item["old"]:  # the row had no maker (TNC-C174P): the load names it RUICHI
        row.update({"Производитель": "", "coaxMfr": ""})
    return TechOperationRecord.create(tab=TechOperationTab.COAX, display_text=f"SMA-male | {item['cable']}", search_text="",
                                      source_sheet="БД.КОАКС", sort_key=item["art"], fields=row, edited_in_erp=True)


@pytest.fixture()
def database(tmp_path):
    other = TechOperationRecord.create(
        tab=TechOperationTab.COAX, display_text="N-male | RG-58", search_text="", source_sheet="БД.КОАКС", sort_key="N-111",
        fields={"Артикул": "N-111", "Тип/Серия": "N-male", "Провод": "RG-58", "Производитель": "", "coaxArticle": "N-111",
                "coaxWire": "RG-58", "coaxType": "N-male", "coaxMfr": "", "Цена, ₽": "420", "Ссылка": "https://gsm-repiteri.ru/x"},
        edited_in_erp=True)
    url = f"sqlite+pysqlite:///{(tmp_path / 'ruichi.db').as_posix()}"
    engine = create_engine(url)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with SqlAlchemyReferencesUnitOfWork(sessions) as uow:
        for item in UPDATED:
            uow.tech_operations.save(seed(item))
        uow.tech_operations.save(other)
        uow.commit()
    yield url, engine
    engine.dispose()


def run(url, mode):
    env = {**os.environ, "ERP_DATABASE_URL": url, "PYTHONPATH": str(BACKEND), "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, str(SCRIPT), mode], env=env, cwd=BACKEND, capture_output=True, text=True,
                          encoding="utf-8", timeout=600)
    return done.returncode, done.stdout + done.stderr


def coax_rows(engine):
    with engine.connect() as connection:
        rows = connection.execute(text("select fields from tech_operation_records where tab = 'coax'")).scalars().all()
    parsed = [json.loads(row) if isinstance(row, str) else row for row in rows]
    return {(item["Артикул"], item["Провод"]): item for item in parsed}


def test_payload_is_one_row_per_pair_and_ruichi_only():
    keys = [(item["art"].casefold(), item["cable"]) for item in PAYLOAD]
    assert len(keys) == len(set(keys)) and len(CREATED) == 55 and len(UPDATED) == 21
    for item in CREATED:
        fields = item["fields"]
        assert fields["coaxMfr"] == "RUICHI" and fields["coaxArticle"] == item["art"] and fields["coaxWire"] == item["cable"]
        assert fields["Артикул поставщика"].startswith("promelec.ru: ") and fields["Ссылка"].startswith("https://www.promelec.ru/product/")
        assert "Цена, ₽" not in fields or fields["Дата цены"] == "20.10.2026"
        assert "Поставщик" not in fields  # у БД.КОАКС нет такого столбца: поставщик — в «Артикуле поставщика»


def test_dry_run_writes_nothing_then_apply_creates_updates_and_repeat_is_idle_and_rollback_restores(database):
    url, engine = database
    before = coax_rows(engine)
    code, out = run(url, "dry")
    assert code == 0, out
    assert f"создать {len(CREATED)}, обновить {len(UPDATED)}, без изменений 0, отказ 0" in out
    assert coax_rows(engine) == before

    code, out = run(url, "apply")
    assert code == 0, out
    rows = coax_rows(engine)
    assert len(rows) == len(before) + len(CREATED)
    assert rows[("N-111", "RG-58")] == before[("N-111", "RG-58")]                      # чужая строка не тронута
    for item in CREATED:
        row = rows[(item["art"], item["cable"])]
        assert row["Производитель"] == "RUICHI" and row["Артикул поставщика"] == item["fields"]["Артикул поставщика"]
    for item in UPDATED:
        row, was = rows[(item["art"], item["cable"])], before[(item["art"], item["cable"])]
        for name, value in was.items():
            if value != "" and name not in item["fields"]:
                assert row[name] == value, (item["art"], name)                           # непустое, которого не касались, на месте
        for name, old in item["old"].items():
            assert row[name] == item["fields"][name]
            if old != "" and name in ("Ссылка", "Цена, ₽"):                              # заменено предложение: прежнее — в источнике
                assert old in row["Источник данных"]

    code, again = run(url, "dry")
    assert code == 0, again
    assert f"создать 0, обновить 0, без изменений {len(CREATED)}, отказ {len(UPDATED)}" in again

    code, back = run(url, "rollback")
    assert code == 0, back
    restored = coax_rows(engine)
    assert set(restored) == set(before)
    for key, fields in before.items():
        assert {k: v for k, v in restored[key].items() if v != ""} == {k: v for k, v in fields.items() if v != ""}, key
