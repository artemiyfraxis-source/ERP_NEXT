"""Global search (stage option ui.search): groups, look-alike folding, section rights, options, the speed on a few
hundred rows."""

import json
import time
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, Numeric, Uuid, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing.infrastructure import (
    InternalProjectRow, ItemAliasRow, ItemRow, RfqRow, SqlAlchemyPurchasingUnitOfWork, SupplierRow,
)
from app.modules.warehouse.infrastructure import StockLotRow
from app.shared import stage_options
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor

NOW = datetime(2026, 10, 6, 10, 0)
URL = "/api/search"
ALL = {"procurement": "view", "projects": "view", "warehouse": "view"}


def fill(model, **fields):
    """A row with every NOT NULL column without a default filled by its type."""
    values = {}
    for column in model.__table__.columns:
        if column.nullable or column.default is not None or column.server_default is not None:
            continue
        kind = column.type
        values[column.name] = (
            uuid4() if isinstance(kind, Uuid) else 1 if isinstance(kind, Integer) else False if isinstance(kind, Boolean)
            else Decimal(0) if isinstance(kind, Numeric) else [] if isinstance(kind, JSON)
            else NOW if isinstance(kind, DateTime) else date(2026, 10, 1) if isinstance(kind, Date) else uuid4().hex if column.unique else "x"
        )
    values.update(fields)
    return model(**values)


def line(legacy, name, **fields):
    return fill(ProcurementLineRow, legacy_id=legacy, calculated_name=name, status="В работе", created_at=NOW,
                version=1, **fields)


def lot(grn, name, package="", article="A-1", place="1F4", **fields):
    return fill(StockLotRow, grn=grn, package=package, name=name, article=article, storage_location=place,
                inbound=Decimal(10), written_off=Decimal(0), reserved=Decimal(0), warehouse="ЦАР", version=1,
                unit="шт", series="", purpose="Склад", item_type="Комплектующие", source_receiving_ids=[], **fields)


def _as(permissions):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"), email="p@example.test", roles=frozenset({"buyer"}),
        permissions=permissions,
    )


def _options(monkeypatch, tmp_path, **on):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"ui.search": True, **{key.replace("__", "."): value for key, value in on.items()}}),
                    encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))


class Registry:
    def open_projects(self):
        return ["591К.3", "ВП-001", "412.1"]


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    app.state.project_registry = Registry()
    _as(ALL)
    _options(monkeypatch, tmp_path, references__items=True, references__suppliers=True, procurement__rfq=True,
             projects__internal=True)

    def seed(*rows):
        with sessions() as session:
            session.add_all(rows)
            session.commit()

    yield TestClient(app), seed
    app.dependency_overrides.pop(authenticated_principal, None)
    for name in ("purchasing_uow_factory", "project_registry"):
        delattr(app.state, name)
    engine.dispose()


def groups(response):
    assert response.status_code == 200, response.text
    return {item["group"] for item in response.json()["results"]}


def titles(response, group):
    return [item["title"] for item in response.json()["results"] if item["group"] == group]


def seed_all(seed):
    item = fill(ItemRow, name="Резистор 10 кОм", part_number="RC0603-10K", category="other", unit="шт", version=1,
                created_at=NOW)
    seed(
        line("SM-0042", "Резистор 10 кОм 0603", order_number="З-17", supplier="ООО Ромашка", project_reference="591К.3"),
        item,
        fill(ItemAliasRow, item_id=item.id, supplier_id=None, alias_key="x", alias_text="Рез. 10к SMD", created_at=NOW),
        fill(SupplierRow, name="Ромашка Электро", name_key="ромашка электро", inn="7701234567", is_key=True,
             version=1, created_at=NOW),
        fill(InternalProjectRow, number="ВП-001", name="Стенд испытаний резисторов", purpose="RND", version=1,
             created_at=NOW),
        fill(RfqRow, number="КП-2026-001", title="Резисторы на квартал", version=1, created_at=NOW),
        lot("FRX.26.0000003", "Резистор 10 кОм", package="П2", article="RC0603-10K"),
        lot("PPM.26.0000001", "Разъём Molex"),
    )


def test_every_group_answers_and_tells_where_to_go(env):
    client, seed = env
    seed_all(seed)
    body = client.get(URL, params={"q": "резистор"}).json()
    assert {item["group"] for item in body["results"]} == {
        "Строки закупки", "Проекты", "Номенклатура", "Склад", "Запросы КП",
    }
    by_group = {item["group"]: item for item in body["results"]}
    assert by_group["Строки закупки"]["section"] == "procurement"
    assert by_group["Строки закупки"]["params"] == {"tab": "sheet", "q": "SM-0042"}
    assert by_group["Проекты"]["params"] == {"tab": "internal"}
    assert by_group["Склад"]["section"] == "stock" and by_group["Склад"]["params"] == {"q": "FRX.26.0000003-П2"}
    assert by_group["Запросы КП"]["params"] == {"tab": "rfq"}
    suppliers = client.get(URL, params={"q": "ромашка"})
    assert {"Поставщики", "Строки закупки"} <= groups(suppliers)
    assert titles(suppliers, "Поставщики") == ["Ромашка Электро"]
    commercial = client.get(URL, params={"q": "591"}).json()["results"]
    assert {"group": "Проекты", "title": "591К.3", "subtitle": "Проект", "section": "projects",
            "params": {"project": "591К.3"}} in commercial


def test_words_in_any_order_alias_and_inn(env):
    client, seed = env
    seed_all(seed)
    assert titles(client.get(URL, params={"q": "0603 10 резистор"}), "Строки закупки") == ["SM-0042 · Резистор 10 кОм 0603"]
    assert titles(client.get(URL, params={"q": "рез. 10к"}), "Номенклатура") == ["Резистор 10 кОм"]
    assert titles(client.get(URL, params={"q": "7701234567"}), "Поставщики") == ["Ромашка Электро"]
    assert client.get(URL, params={"q": "резистор виолончель"}).json()["results"] == []
    assert client.get(URL, params={"q": "   "}).json()["results"] == []


def test_lookalikes_case_and_yo_are_folded(env):
    client, seed = env
    seed(line("SM-0007", "Кабель КВВГ 4х1,5 Ёлка", supplier="ООО Ромашка"),
         lot("PPM.26.0000001", "Разъём"))
    # Latin look-alikes of Cyrillic letters, other case, е for ё
    assert titles(client.get(URL, params={"q": "KBBГ"}), "Строки закупки") != []
    assert titles(client.get(URL, params={"q": "кабель елка"}), "Строки закупки") != []
    assert titles(client.get(URL, params={"q": "КАБЕЛЬ"}), "Строки закупки") != []
    # The GRN typed in Cyrillic look-alikes finds the Latin lot
    assert titles(client.get(URL, params={"q": "РРМ.26.0000001"}), "Склад") == ["PPM.26.0000001"]
    assert titles(client.get(URL, params={"q": "ppm.26.0000001"}), "Склад") == ["PPM.26.0000001"]


def test_a_grn_puts_the_lot_first_and_the_exact_lot_leads(env):
    client, seed = env
    seed(line("SM-0001", "Заглушка PPM.26.0000001 для теста"),
         lot("PPM.26.0000001", "Разъём", package="П1"), lot("PPM.26.0000001", "Разъём", package="П2"),
         lot("PPM.26.00000010", "Другой"))
    results = client.get(URL, params={"q": "PPM.26.0000001-П2"}).json()["results"]
    assert results[0]["group"] == "Склад" and results[0]["title"] == "PPM.26.0000001-П2"
    plain = client.get(URL, params={"q": "PPM.26.0000001"}).json()["results"]
    assert plain[0]["group"] == "Склад"
    assert [item["title"] for item in plain if item["group"] == "Склад"][-1] == "PPM.26.00000010"


def test_at_most_eight_per_group(env):
    client, seed = env
    seed(*[line(f"SM-{n:04d}", f"Винт М3 {n}") for n in range(30)], *[lot(f"FRX.26.{n:07d}", "Винт М3") for n in range(1, 21)])
    body = client.get(URL, params={"q": "винт"}).json()["results"]
    assert sum(item["group"] == "Строки закупки" for item in body) == 8
    assert sum(item["group"] == "Склад" for item in body) == 8


def test_a_group_needs_the_users_section(env):
    client, seed = env
    seed_all(seed)
    _as({"procurement": "view"})
    assert groups(client.get(URL, params={"q": "резистор"})) == {
        "Строки закупки", "Номенклатура", "Запросы КП",
    }
    _as({"warehouse": "view"})
    assert groups(client.get(URL, params={"q": "резистор"})) == {"Склад"}
    _as({"projects": "view"})
    assert groups(client.get(URL, params={"q": "резистор"})) == {"Проекты"}
    _as({"newrequest": "edit"})
    assert client.get(URL, params={"q": "резистор"}).json()["results"] == []
    _as({"warehouse": "edit"})
    assert groups(client.get(URL, params={"q": "резистор"})) == {"Склад"}
    app.dependency_overrides.pop(authenticated_principal)
    assert client.get(URL, params={"q": "резистор"}).status_code == 401


def test_purchasing_groups_follow_their_options(env, monkeypatch, tmp_path):
    client, seed = env
    seed_all(seed)
    _options(monkeypatch, tmp_path)
    found = groups(client.get(URL, params={"q": "резистор"}))
    assert found == {"Строки закупки", "Склад"}


def test_option_off_is_404_after_auth(env, monkeypatch, tmp_path):
    client, _ = env
    path = tmp_path / "off.json"
    path.write_text(json.dumps({"ui.search": False}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))
    assert client.get(URL, params={"q": "x"}).status_code == 404
    monkeypatch.delenv(stage_options.ENV)
    assert client.get(URL, params={"q": "x"}).status_code == 404
    app.dependency_overrides.pop(authenticated_principal)
    assert client.get(URL, params={"q": "x"}).status_code == 401  # auth first


def test_a_few_hundred_rows_answer_fast(env):
    client, seed = env
    seed(*[line(f"SM-{n:05d}", f"Позиция {n} Резистор тип {n % 17}", supplier=f"Поставщик {n % 40}",
                order_number=f"З-{n}") for n in range(1500)],
         *[lot(f"FRX.26.{n:07d}", f"Деталь {n} Разъём", article=f"ART-{n}") for n in range(1, 1501)])
    client.get(URL, params={"q": "разъём"})  # warm-up
    for query in ("разъём 77", "FRX.26.0001200", "резистор тип 5", "нет такого"):
        started = time.perf_counter()
        response = client.get(URL, params={"q": query})
        elapsed = (time.perf_counter() - started) * 1000
        assert response.status_code == 200
        assert elapsed < 450, (query, elapsed)  # ×3 over the stage machine: CI runners jitter
    assert titles(client.get(URL, params={"q": "FRX.26.0001200"}), "Склад") == ["FRX.26.0001200"]


def test_mixed_case_cyrillic_is_found_on_sqlite_too():
    """«ЧипДип» in the data, «чипдип» typed: the SQLite LIKE prefilter must not drop it (ASCII-only case folding)."""
    from sqlalchemy import create_engine, text

    from app.shared import sqlite_casefold  # noqa: F401

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        hit = connection.execute(text("select erp_casefold('ООО «ЧипДип»') like '%чипдип%'")).scalar()
    assert hit == 1
