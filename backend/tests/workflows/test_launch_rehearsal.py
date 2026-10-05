"""Репетиция дня запуска 05.10.2026 (docs/launch/CUTOVER.md): весь день цеха через HTTP API, каждый шаг — под ролью,
которая его делает в цеху, и на каждом шаге чужая роль получает 403.

База собрана как на сервере: Alembic до головы (матрица доступов — из миграций), сотрудники входят по паролю или PIN,
cookie сессии и CSRF настоящие. Склад грузится выгрузкой SUPPLYMAP через `import_stock.run_import`: синтетической
(tests/workflows/fixtures/launch) — всегда, настоящей — когда задан ERP_REAL_EXPORT_DIR (папка с ЦАР.csv, ЖУК.csv,
СПИСАНИЕ.csv и _manifest.json). Google не трогается: расчёт читают подделки, адаптер из env падает.

День: склад загружен → владелец (admin) заводит проект из расчёта → технолог собирает и выдаёт ЭТК сборки →
кладовщик оформляет запрос на комплектацию «по расчёту» (партии FIFO) и выдаёт комплект со списанием →
мастер запускает задание и расписывает операции → директор производства отдаёт задание в ЖУК и возвращает →
монтажник проходит все операции → мастер подтверждает коробки → ОТК принимает → кладовщик сдаёт на склад ГП →
менеджер проекта оформляет отгрузку → кладовщик упаковывает и отгружает.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import build_warehouse_uow_factory, configure_database
from app.main import app
from app.modules.identity.domain import IdentityUser, hash_password, hash_pin
from app.modules.identity.infrastructure import provision_user
from app.modules.production.launchable import OPERATION_TYPE_NAMES
from app.modules.requests import google_sheets
from app.modules.requests.application import CalcSpreadsheet
from app.modules.techcards.application import ReplaceOperationLibraryCommand, replace_operation_library
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.warehouse.import_stock import format_report, reconcile, run_import
from app.modules.warehouse.infrastructure import StockLotRow, WriteOffEntryRow
from app.modules.warehouse.stock_export import read_stock_export
from app.shared import warehouse_location
from app.shared.clock import MOSCOW, moscow_now
from app.workflows.issue_calc import fifo_key
from tests.factories import Actor
from tests.techcards.calc_sheets import FakeSheets, grid

BACKEND = Path(__file__).resolve().parents[2]
SYNTHETIC_EXPORT = Path(__file__).resolve().parent / "fixtures" / "launch"
REAL_EXPORT_ENV = "ERP_REAL_EXPORT_DIR"
LIBRARY = json.loads((BACKEND / "data" / "techcard-library.json").read_text(encoding="utf-8"))
# Конфиг генератора настоящей сборки (708К.2 КБ1): 15 операций — резка, опрессовка, монтаж, обмотка, маркировка.
GENERATOR_CONFIG = json.loads((BACKEND / "tests" / "techcards" / "fixtures" / "current-etk.json").read_text(
    encoding="utf-8"))["files"]["kit-708К.2-КБ1.json"]["payload"]["config"]

PROJECT = "999К.5"
ASSEMBLY = "КБ1"
ASSEMBLY_NAME = "Жгут репетиции запуска"
KITS = 5
CALC_URL = "https://docs.google.com/spreadsheets/d/1rehearsal-calc-999K5/edit#gid=0"
PURPOSE = f"{PROJECT} · {ASSEMBLY} · {ASSEMBLY_NAME}"
MONDAY = datetime(2026, 10, 5, 8, 30, tzinfo=MOSCOW)
PASSWORD = "Rehearsal-2026!"
FITTER_PIN = "482913"


@dataclass(frozen=True)
class Person:
    key: str
    login: str
    name: str
    department: str
    role: str
    site: str | None = None

    @property
    def id(self) -> UUID:
        return Actor(name=self.name, department=self.department).id


PEOPLE = (
    Person("owner", "owner", "Владелец Репетиция", "Руководство", "admin"),
    Person("engineer", "engineer", "Технолог Репетиция", "Технология", "engineer"),
    Person("storekeeper", "storekeeper", "Кладовщик Репетиция", "Склад", "receiver", "ЦАР"),
    Person("master", "master", "Мастер Репетиция", "Производство", "master", "ЦАР"),
    Person("prodhead", "prodhead", "Директор производства Репетиция", "Производство", "prodhead"),
    Person("fitter", "fitter", "Монтажник Репетиция", "Монтаж", "staff", "ЦАР"),
    Person("qc", "qc", "Контролёр ОТК Репетиция", "ОТК", "qc"),
    Person("pm", "pm", "Менеджер проекта Репетиция", "Проекты", "pm"),
)


class Clock:
    """Время цеха: одно на сервер и на проверку сессий, двигается шагами дня."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def tick(self, minutes: int = 5) -> datetime:
        self.now += timedelta(minutes=minutes)
        return self.now


class Seat:
    """Рабочее место сотрудника: свой браузер (cookie), свой CSRF-токен."""

    def __init__(self, person: Person, client: TestClient):
        self.person, self.client = person, client

    def call(self, method: str, path: str, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            headers["X-CSRF-Token"] = self.client.cookies.get("erp_csrf") or ""
        return self.client.request(method, path, headers=headers, **kwargs)

    def ok(self, method: str, path: str, **kwargs) -> dict:
        response = self.call(method, path, **kwargs)
        assert response.status_code in (200, 201), (
            f"{self.person.role} «{self.person.name}»: {method} {path} → {response.status_code} {response.text}"
        )
        return response.json()

    def refused(self, method: str, path: str, **kwargs) -> None:
        response = self.call(method, path, **kwargs)
        assert response.status_code == 403, (
            f"{self.person.role} не должен проходить: {method} {path} → {response.status_code} {response.text}"
        )


@dataclass
class Shop:
    engine: Any
    sessions: sessionmaker
    clock: Clock
    seats: dict[str, Seat] = field(default_factory=dict)

    def __getattr__(self, key: str) -> Seat:
        seats = self.__dict__.get("seats", {})
        if key in seats:
            return seats[key]
        raise AttributeError(key)


class FakeCalcReader:
    """ЗКП расчёта для «Заявки по расчёту» (Issue.gs issueCalcPreview)."""

    def __init__(self, sheet: CalcSpreadsheet):
        self.sheet = sheet

    def read_calc(self, spreadsheet_id: str) -> CalcSpreadsheet:
        return self.sheet


def _no_google(*_args, **_kwargs):
    raise AssertionError("репетиция не ходит в Google: читатель расчёта должен быть подделкой")


@pytest.fixture()
def shop(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    monkeypatch.setattr(google_sheets, "reader_from_env", _no_google)
    url = f"sqlite+pysqlite:///{(tmp_path / 'launch.db').as_posix()}"
    config = Config(BACKEND / "alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    saved_state = dict(app.state._state)
    saved_loader = warehouse_location._layout_loader
    engine = configure_database(app, url)
    clock = Clock(MONDAY)
    app.state.cookie_secure = False
    app.state.identity_clock = clock
    app.dependency_overrides[moscow_now] = clock
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        for person in PEOPLE:
            provision_user(
                session,
                IdentityUser(id=person.id, login=person.login, email=f"{person.login}@rehearsal.test",
                             name=person.name, department=person.department, role=person.role, site=person.site,
                             active=True),
                hash_password(PASSWORD),
                pin_hash=hash_pin(FITTER_PIN) if person.key == "fitter" else None,
            )
        session.commit()
    # At the cutover the people exist before migration 0086: their levels, the level rows and the compensating
    # exceptions come from their roles (the transfer), not from the plan's defaults of an empty database.
    spec = importlib.util.spec_from_file_location(
        "migration_0086", BACKEND / "migrations" / "versions" / "0086_identity_access_levels.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        assert migration.transfer(connection, clock.now) > 0
    # «load-real-data.sh base» кладёт библиотеку операций (seed_library); без неё генератор ЭТК не собирает.
    replace_operation_library(ReplaceOperationLibraryCommand(
        source_commit=LIBRARY["sourceCommit"], templates=tuple(LIBRARY["templates"]),
        actor=Actor(name="ERP migration", department="System"), now=clock.now,
    ), SqlAlchemyTechcardsUnitOfWork(sessions))

    state = Shop(engine=engine, sessions=sessions, clock=clock)
    try:
        for person in PEOPLE:
            client = TestClient(app)
            if person.key == "fitter":
                # Монтажник входит по имени из списка и PIN (Portal.gs portalLogin).
                staff = client.get("/api/auth/login-staff").json()["staff"]
                assert person.name in [item["name"] for item in staff]
                login = client.post("/api/auth/pin-login", json={"who": str(person.id), "pin": FITTER_PIN})
            else:
                login = client.post("/api/auth/login", json={"login": person.login, "password": PASSWORD})
            assert login.status_code == 200, (person.key, login.text)
            state.seats[person.key] = Seat(person, client)
        yield state
    finally:
        app.dependency_overrides.pop(moscow_now, None)
        app.state._state.clear()
        app.state._state.update(saved_state)
        warehouse_location.set_rack_layout_loader(saved_loader)
        engine.dispose()


# ── План комплекта: какие позиции расчёта и что ждём от FIFO ─────────────────────────────────────────


@dataclass(frozen=True)
class KitPosition:
    item_type: str
    name: str  # «Обозначение» в ЗКП — артикул, как его пишет расчёт
    quantity: Decimal  # на весь запуск
    unit: str
    per_kit: Decimal


SYNTHETIC_KIT = (
    KitPosition("Разъём", "REH-43025-04", Decimal("10"), "шт", Decimal("2")),
    KitPosition("Провод", "REH-UL1007-22", Decimal("7.5"), "м", Decimal("1.5")),
    KitPosition("Контакт", "REH-43030-07", Decimal("40"), "шт", Decimal("8")),
    KitPosition("Разъём", "REH-SEAL-01", Decimal("5"), "шт", Decimal("1")),  # на складе нет — уходит в НЕДОБОР
)
# ЖУК держит самую старую партию того же корпуса (OLD.24.0000001): заявка по складу ЦАР её брать не должна.
SYNTHETIC_PICKS = {
    "REH-43025-04": [("OLD.25.0000101", Decimal("4")), ("OLD.26.0000102", Decimal("6"))],
    "REH-UL1007-22": [("OLD.26.0000103", Decimal("7.5"))],
    "REH-43030-07": [("OLD.26.0000104", Decimal("40"))],
}


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _real_kit(shop: Shop) -> tuple[KitPosition, ...]:
    """Комплект из настоящего склада: позиция, которая лежит в ЦАР двумя и более партиями (FIFO пройдёт через две),
    и ещё до двух позиций по одной штуке."""
    with shop.sessions() as session:
        rows = session.scalars(select(StockLotRow).where(StockLotRow.warehouse == "ЦАР")).all()
    by_article: dict[str, list[StockLotRow]] = defaultdict(list)
    for row in rows:
        available = row.inbound - row.written_off - row.reserved
        article = (row.article or "").strip()
        # Простые артикулы без пробелов. isSameItem_ сравнивает по словам и может добавить партии, где артикул
        # записан длиннее («SMA-Female RG316, SMA-C316F@RUICHI») — FIFO проверяется общим правилом, не списком ГРН.
        if available > 0 and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z\-@./]{3,}", article):
            by_article[article].append(row)
    assert by_article, "в настоящей выгрузке нет партий ЦАР с остатком"
    multi = sorted((a for a, lots in by_article.items() if len(lots) >= 2), key=lambda a: -len(by_article[a]))
    single = sorted(a for a, lots in by_article.items() if len(lots) == 1)
    kit: list[KitPosition] = []
    for article in multi[:1]:
        lots = sorted(by_article[article], key=lambda row: fifo_key(_lot_view(row)))
        first = lots[0].inbound - lots[0].written_off - lots[0].reserved
        need = first + min(Decimal("1"), lots[1].inbound - lots[1].written_off - lots[1].reserved)
        kit.append(KitPosition(lots[0].item_type or "Комплектующие", article, need, lots[0].unit, need / KITS))
    for article in single[: 3 - len(kit)]:
        row = by_article[article][0]
        need = min(Decimal("1"), row.inbound - row.written_off - row.reserved)
        kit.append(KitPosition(row.item_type or "Комплектующие", article, need, row.unit, need / KITS))
    return tuple(kit)


@dataclass(frozen=True)
class _LotView:
    grn: str
    package: str


def _lot_view(row: StockLotRow) -> _LotView:
    return _LotView(row.grn, row.package)


def _calc_sheets(kit: tuple[KitPosition, ...]) -> dict[str, list[list[str]]]:
    """Расчёт проекта, как его отдаёт Google: ЗКЗ (сборки) и СПЯ (состав) для «Создать проект из расчёта»."""
    return {
        "ЗКЗ": grid([
            ["Название проекта", "Репетиция запуска"],
            ["Номер", PROJECT],
            ["Заказчик", "ООО Репетиция"],
            [],
            ["№", "Индекс сборки", "Наименование сборки по чертежу", "Кол-во 1", "Вкл. в расчет?"],
            [1, ASSEMBLY, ASSEMBLY_NAME, KITS, 1],
        ]),
        "СПЯ": grid([
            ["Сборка", "Артикул КД", "Тип", "Наименование", "Кол-во на комплект", "Ссылка"],
            *[[ASSEMBLY, item.name, item.item_type, "", str(item.per_kit).replace(".", ","), "Склад"] for item in kit],
        ]),
    }


def _zpk(kit: tuple[KitPosition, ...]) -> CalcSpreadsheet:
    rows = [
        [f"Расчёт {PROJECT}", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],
        ["№", "Тип", "Обозначение", "На проект", "Цена  без НДС", "Цена с НДС", "Ссылка", "Место поставки"],
    ]
    rows += [[str(index), item.item_type, item.name, str(item.quantity).replace(".", ","), "", "", "", ""]
             for index, item in enumerate(kit, start=1)]
    return CalcSpreadsheet(title=f"Расчёт {PROJECT}", zpk_grid=rows, project_cell=PROJECT)


# ── Склад: проверки остатков и журнала СПИСАНИЕ ─────────────────────────────────────────────────────


def _journal_net(shop: Shop) -> dict[UUID, Decimal]:
    """Проведённые строки журнала по партии: списания плюсом, возвраты минусом (writeoffIsReturn_)."""
    net: dict[UUID, Decimal] = defaultdict(Decimal)
    with shop.sessions() as session:
        for row in session.scalars(select(WriteOffEntryRow).where(WriteOffEntryRow.posted.is_(True))):
            if row.lot_id is None:
                continue
            back = row.kind == "return" or (row.kind == "legacy" and row.reason.strip().lower().startswith("возврат"))
            net[row.lot_id] += -row.quantity if back else row.quantity
    return net


def _stock(seat: Seat, query: str) -> dict[str, dict]:
    """Весь склад экраном «Склад», страница за страницей (по 200 партий)."""
    lots: dict[str, dict] = {}
    params: dict[str, Any] = {"q": query, "zero": "true", "limit": 200}
    while True:
        page = seat.ok("GET", "/api/stock", params=params)
        lots.update((lot["id"], lot) for lot in page["lots"])
        if not page["next_cursor"]:
            return lots
        params["after"] = page["next_cursor"]


# ── Сам день ────────────────────────────────────────────────────────────────────────────────────────


def _rehearse(shop: Shop, export_dir: Path, *, synthetic: bool) -> None:
    clock = shop.clock

    # Пт–вс: склад заморожен и загружен выгрузкой SUPPLYMAP (CUTOVER, шаг 3: сверка → запись).
    export = read_stock_export(export_dir)
    dry = run_import(export, build_warehouse_uow_factory(shop.engine)(), dry_run=True, now=clock.now)
    assert reconcile(export, dry).ok, format_report(export, dry, reconcile(export, dry))
    result = run_import(export, build_warehouse_uow_factory(shop.engine)(), dry_run=False, now=clock.now)
    check = reconcile(export, result)
    assert check.ok, format_report(export, result, check)
    shop.pm.refused("GET", "/api/stock")  # менеджеру проекта склад не открыт
    if synthetic:
        # Синтетика сходится и по журналу: проведённые строки СПИСАНИЕ = графа СПИСАНО каждой партии.
        net = _journal_net(shop)
        with shop.sessions() as session:
            for row in session.scalars(select(StockLotRow)):
                assert net.get(row.id, Decimal("0")) == row.written_off, row.grn
    kit = SYNTHETIC_KIT if synthetic else _real_kit(shop)

    # Пн 08:30: владелец заводит проект из расчёта (Google — подделка).
    clock.tick()
    app.state.kitset_sheets_reader = FakeSheets(_calc_sheets(kit), title=f"{PROJECT} Расчет")
    load = {"url": CALC_URL, "deadline": "2026-10-20",
            "ship": {"shipping_point": "Москва, склад заказчика", "contact": "Иван Репетиция"}}
    shop.storekeeper.refused("POST", "/api/techcards/kitsets/load", json=load)
    created = shop.owner.ok("POST", "/api/techcards/kitsets/load", json=load)["res"]
    assert (created["project"], created["assemblies"], created["lines"]) == (PROJECT, 1, len(kit))
    assert created["erp"]["project"] is True and created["ship"]["added"] == 1
    assert not (created["erp_error"] or created["ship_error"] or created["deadline_error"])
    tiles = shop.master.ok("GET", "/api/techcards/kitsets")["items"]
    assert [tile["project"] for tile in tiles] == [PROJECT]

    # Технолог: карточка ТК сборки из проекта → генератор собирает ЭТК → выдаёт.
    clock.tick()
    shop.storekeeper.refused("POST", f"/api/techcards/kitsets/{PROJECT}/techcards/{ASSEMBLY}", json={})
    card_id = shop.engineer.ok("POST", f"/api/techcards/kitsets/{PROJECT}/techcards/{ASSEMBLY}",
                               json={"deadline": "2026-10-15"})["card_id"]
    card = shop.engineer.ok("GET", f"/api/techcards/projects/{card_id}")
    config = {**copy.deepcopy(GENERATOR_CONFIG), "assemblyIndex": ASSEMBLY, "assemblyName": ASSEMBLY_NAME}
    shop.storekeeper.refused("POST", f"/api/techcards/projects/{card_id}/build",
                             json={"version": card["version"], "config": config})
    built = shop.engineer.ok("POST", f"/api/techcards/projects/{card_id}/build",
                             json={"version": card["version"], "config": config})
    shop.master.refused("POST", f"/api/techcards/projects/{card_id}/issue", json={"version": built["version"]})
    issued_card = shop.engineer.ok("POST", f"/api/techcards/projects/{card_id}/issue",
                                   json={"version": built["version"]})
    assert issued_card["status"] == "Выдана", issued_card["status"]

    # Кладовщик: «Заявка по расчёту» — позиции ЗКП против склада ЦАР, партии FIFO.
    clock.tick()
    app.state.calc_spreadsheet_reader = FakeCalcReader(_zpk(kit))
    preview_body = {"url": CALC_URL, "warehouse": "ЦАР"}
    shop.fitter.refused("POST", "/api/issues/calc-preview", json=preview_body)
    shop.master.refused("POST", "/api/issues/calc-preview", json=preview_body)
    preview = shop.storekeeper.ok("POST", "/api/issues/calc-preview", json=preview_body)
    assert preview["project"] == PROJECT
    lines = preview["lines"]
    assert [line["name"] for line in lines] == [item.name for item in kit]
    picks = {line["name"]: [(pick["grn"], Decimal(pick["qty"])) for pick in line["picks"]] for line in lines}
    if synthetic:
        assert {name: got for name, got in picks.items() if got} == SYNTHETIC_PICKS
        missing = next(line for line in lines if line["name"] == "REH-SEAL-01")
        assert (missing["found"], missing["short"]) == (False, "5")
    stock_before = _stock(shop.storekeeper, "")
    by_grn = {(lot["warehouse"], lot["grn"], lot["package"]): lot for lot in stock_before.values()}
    for line, item in zip(lines, kit):
        if not line["picks"]:
            continue
        # FIFO: партии по порядку прихода, каждая кроме последней — целиком, всего — сколько нужно (или всё, что есть).
        chosen = [by_grn[("ЦАР", pick["grn"], pick["package"])] for pick in line["picks"]]
        assert all(lot["warehouse"] == "ЦАР" for lot in chosen)
        order = [fifo_key(_LotView(lot["grn"], lot["package"])) for lot in chosen]
        assert order == sorted(order), line["name"]
        for pick, lot in zip(line["picks"][:-1], chosen[:-1]):
            assert Decimal(pick["qty"]) == Decimal(lot["available"]), (line["name"], lot["grn"])
        assert sum(Decimal(pick["qty"]) for pick in line["picks"]) + Decimal(line["short"]) == item.quantity

    request_body = {
        "purpose": PURPOSE,
        "deadline": "2026-10-07",
        # Как экран «Заявка по расчёту» (FulfillmentPage.tsx send): у партии из Google НАИМЕНОВАНИЕ бывает пустым.
        "lines": [{"article": pick["article"], "name": pick["name"] or pick["article"] or line["name"],
                   "unit": pick["unit"] or line["unit"] or "шт", "requirement": pick["qty"], "lot_id": pick["lot_id"]}
                  for line in lines for pick in line["picks"]],
        "short_note": [f"{line['name']}: нет на складе (−{line['qty']})" for line in lines if not line["found"]],
    }
    shop.fitter.refused("POST", "/api/issues", json=request_body, headers={"Idempotency-Key": "launch-kit"})
    shop.pm.refused("POST", "/api/issues", json=request_body, headers={"Idempotency-Key": "launch-kit"})
    request = shop.storekeeper.ok("POST", "/api/issues", json=request_body,
                                  headers={"Idempotency-Key": "launch-kit"})["request"]
    assert request["project"] == PROJECT
    assert all(line["lot"] is not None for line in request["lines"])
    picked: dict[str, Decimal] = defaultdict(Decimal)
    for line in request["lines"]:
        picked[line["lot"]["id"]] += Decimal(line["requirement"])
    reserved = _stock(shop.storekeeper, "")
    for lot_id, quantity in picked.items():
        assert Decimal(reserved[lot_id]["reserved"]) == Decimal(stock_before[lot_id]["reserved"]) + quantity

    clock.tick(30)
    request = shop.storekeeper.ok("POST", f"/api/issues/{request['id']}/complete",
                                  json={"version": request["version"]})["request"]
    assert request["status"] == "Укомплектован"
    request = shop.storekeeper.ok("POST", f"/api/issues/{request['id']}/ready",
                                  json={"version": request["version"], "handout_location": "Полка выдачи 1"})["request"]
    assert request["status"] == "Готов к выдаче"
    # Выдача со списанием: технолог собирает запросы, но выдавать (списывать) — только складу.
    shop.engineer.refused("POST", f"/api/issues/{request['id']}/issue", json={"version": request["version"]})
    shop.master.refused("POST", f"/api/issues/{request['id']}/issue", json={"version": request["version"]})
    journal_before = _journal_net(shop)
    clock.tick()
    request = shop.storekeeper.ok("POST", f"/api/issues/{request['id']}/issue",
                                  json={"version": request["version"]})["request"]
    assert request["status"] == "КБ Выдана"

    # После выдачи: остатки и журнал СПИСАНИЕ сходятся.
    stock_after = _stock(shop.storekeeper, "")
    journal_after = _journal_net(shop)
    for lot_id, lot in stock_after.items():
        before = stock_before[lot_id]
        quantity = picked.get(lot_id, Decimal("0"))
        assert Decimal(lot["balance"]) == Decimal(before["balance"]) - quantity, lot["grn"]
        assert Decimal(lot["written_off"]) == Decimal(before["written_off"]) + quantity, lot["grn"]
        assert Decimal(lot["reserved"]) == Decimal(before["reserved"]), lot["grn"]
        assert journal_after.get(UUID(lot_id), Decimal("0")) - journal_before.get(UUID(lot_id), Decimal("0")) == quantity
        assert Decimal(lot["balance"]) == Decimal(lot["inbound"]) - Decimal(lot["written_off"])
    with shop.sessions() as session:
        rows = session.scalars(select(WriteOffEntryRow).where(WriteOffEntryRow.kind == "issue")).all()
    assert len(rows) == len(request["lines"])
    assert {row.source_key for row in rows} == {f"fulfillment-issue:{line['id']}" for line in request["lines"]}
    for row in rows:
        assert row.reason == f"Выдача {request['number']}"
        assert (row.actor_id, row.actor_name) == (shop.storekeeper.person.id, shop.storekeeper.person.name)
        assert row.remaining_after == Decimal(stock_after[str(row.lot_id)]["balance"])
    if synthetic:
        balances = {lot["grn"]: (lot["warehouse"], Decimal(lot["balance"])) for lot in stock_after.values()}
        assert balances == {
            "OLD.25.0000101": ("ЦАР", Decimal("0")), "OLD.26.0000102": ("ЦАР", Decimal("44")),
            "OLD.26.0000103": ("ЦАР", Decimal("80")), "OLD.26.0000104": ("ЦАР", Decimal("460")),
            "OLD.24.0000001": ("ЖУК", Decimal("20")),
        }
        net = _journal_net(shop)
        with shop.sessions() as session:
            for row in session.scalars(select(StockLotRow)):
                assert net.get(row.id, Decimal("0")) == row.written_off, row.grn
    shop.storekeeper.refused("GET", "/api/stock/journal")  # журнал склада — только администратору
    journal = shop.owner.ok("GET", "/api/stock/journal", params={"q": request["number"]})["rows"]
    assert len(journal) >= len(request["lines"])

    # Мастер запускает задание по выданной ЭТК и комплекту.
    clock.tick()
    launch = {"techcard_id": card_id, "quantity": KITS, "fulfillment_request_id": request["id"],
              "deadline": "2026-10-09"}
    shop.fitter.refused("POST", "/api/production/tasks", json=launch, headers={"Idempotency-Key": "launch-task"})
    shop.qc.refused("POST", "/api/production/tasks", json=launch, headers={"Idempotency-Key": "launch-task"})
    task = shop.master.ok("POST", "/api/production/tasks", json=launch,
                          headers={"Idempotency-Key": "launch-task"})["task"]
    assert (task["project"], task["item"], task["quantity"], task["status"]) == (PROJECT, ASSEMBLY, KITS, "Запущено")
    assert len(task["operations"]) == len(GENERATOR_CONFIG["ops"])
    # Мастеру — русские названия этапов, не служебные типы генератора (владелец 30.09).
    assert not {op["stage"] for op in task["operations"]} & set(OPERATION_TYPE_NAMES)

    # Директор производства: отдать задание в ЖУК целиком и вернуть на распределение по людям.
    clock.tick()
    shop.qc.refused("POST", f"/api/production/tasks/{task['id']}/handover",
                    json={"version": task["version"], "site": "ЖУК"})
    task = shop.prodhead.ok("POST", f"/api/production/tasks/{task['id']}/handover",
                            json={"version": task["version"], "site": "ЖУК"})["task"]
    assert task["handed_to"] == "ЖУК"
    task = shop.prodhead.ok("POST", f"/api/production/tasks/{task['id']}/handover",
                            json={"version": task["version"], "site": ""})["task"]
    assert task["handed_to"] == ""

    # Мастер расписывает операции на монтажника своей площадки.
    clock.tick()
    fitter_id = str(shop.fitter.person.id)
    operators = shop.master.ok("GET", "/api/production/operators")
    assert fitter_id in json.dumps(operators, ensure_ascii=False)
    first = task["operations"][0]
    shop.fitter.refused("POST", f"/api/production/tasks/{task['id']}/operations/{first['id']}/assign",
                        json={"version": task["version"], "operator_ids": [fitter_id]})
    for operation in task["operations"]:
        task = shop.master.ok("POST", f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
                              json={"version": task["version"], "operator_ids": [fitter_id]})["task"]
    assert task["assigned"] == len(task["operations"])

    # Монтажник проходит маршрут: первая операция берёт выданные партии, следующие — коробку прошлого этапа.
    mine = shop.fitter.ok("GET", "/api/production/my")["tasks"]
    assert len(mine) == len(task["operations"]) and {item["id"] for item in mine} == {task["id"]}
    materials = [{"fulfillment_line_id": line["id"], "quantity": line["issued"]} for line in request["lines"]]
    previous_box: dict | None = None
    for operation in task["operations"]:
        clock.tick(20)
        start = {"inputs": [previous_box["number"]]} if previous_box else {"materials": materials}
        session = shop.fitter.ok(
            "POST", f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start", json=start,
        )["session"]
        clock.tick(40)
        submitted = shop.fitter.ok("POST", f"/api/production/work/{session['id']}/submit",
                                   json={"good_quantity": str(KITS)})
        box = submitted["box"]
        assert Decimal(box["quantity"]) == KITS and submitted["defect"] is None
        shop.fitter.refused("POST", f"/api/production/boxes/{box['id']}/confirm", json={"version": box["version"]})
        box = shop.master.ok("POST", f"/api/production/boxes/{box['id']}/confirm",
                             json={"version": box["version"]})["box"]
        previous_box = box
    last_box = previous_box

    # ОТК принимает выход последней операции.
    clock.tick()
    inspect = {"version": last_box["version"], "accepted": KITS, "rejected": 0}
    shop.master.refused("POST", f"/api/production/boxes/{last_box['id']}/qc", json=inspect)
    inspected = shop.qc.ok("POST", f"/api/production/boxes/{last_box['id']}/qc", json=inspect)
    last_box = inspected["box"]
    assert len(inspected["serials"]) in (0, KITS)

    # Кладовщик ГП: принятая ОТК коробка — на склад готовой продукции, в позицию проекта.
    clock.tick()
    ready = shop.storekeeper.ok("GET", "/api/production/finished-goods-ready")["boxes"]
    assert [box["id"] for box in ready] == [last_box["id"]]
    goods = shop.storekeeper.ok("GET", "/api/shipping/goods")["goods"]
    good = next(item for item in goods if item["project"] == PROJECT and item["item_index"] == ASSEMBLY)
    handoff = {"version": last_box["version"], "finished_good_id": good["id"]}
    shop.fitter.refused("POST", f"/api/production/boxes/{last_box['id']}/handoff", json=handoff)
    shop.master.refused("POST", f"/api/production/boxes/{last_box['id']}/handoff", json=handoff)
    handed = shop.storekeeper.ok("POST", f"/api/production/boxes/{last_box['id']}/handoff", json=handoff)
    assert Decimal(handed["finished_good"]["accepted"]) == KITS
    assert handed["finished_good"]["grn"].startswith("ГП.26.")

    # Менеджер проекта оформляет отгрузку, склад упаковывает и отгружает.
    clock.tick(30)
    shipment_body = {"receiver": "ООО Репетиция", "shipping_point": "Москва, склад заказчика", "terms": "САМОВЫВОЗ",
                     "upd_type": "ЭДО", "no_docs": True, "lines": [{"good_id": good["id"], "quantity": str(KITS)}]}
    shop.fitter.refused("POST", "/api/shipping/shipments", json=shipment_body)
    shop.storekeeper.refused("POST", "/api/shipping/shipments", json=shipment_body)
    shipment = shop.pm.ok("POST", "/api/shipping/shipments", json=shipment_body)["shipment"]
    assert shipment["status"] == "Собрана", shipment["status"]
    assert Decimal(shipment["lines"][0]["picked"]) == KITS
    shop.pm.refused("POST", f"/api/shipping/shipments/{shipment['id']}/pack", json={"version": shipment["version"]})
    shipment = shop.storekeeper.ok("POST", f"/api/shipping/shipments/{shipment['id']}/pack",
                                   json={"version": shipment["version"]})["shipment"]
    assert shipment["status"] == "Упакована"
    clock.tick(60)
    shop.pm.refused("POST", f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]})
    shipped = shop.storekeeper.ok("POST", f"/api/shipping/shipments/{shipment['id']}/ship",
                                  json={"version": shipment["version"]})
    assert shipped["shipment"]["status"] in ("Отгружена", "Закрыта"), shipped
    goods = {item["id"]: item for item in shop.storekeeper.ok("GET", "/api/shipping/goods")["goods"]}
    assert (Decimal(goods[good["id"]]["sent"]), Decimal(goods[good["id"]]["remaining"])) == (KITS, 0)

    # Вечер: карточка проекта видит весь путь сборки до отгрузки.
    tile = next(item for item in shop.owner.ok("GET", "/api/techcards/kitsets")["items"] if item["project"] == PROJECT)
    assert (tile["kbs"], tile["status"]) == ([ASSEMBLY], "Отгружено")
    assert [(stage["status"], stage["tc"], stage["kit"]) for stage in tile["kb_stages"]] == [("Отгружено", True, True)]
    # Экраны, которые каждый откроет в течение дня, читаются без сбоев на тех же данных.
    shop.owner.ok("GET", "/api/production/plan")
    for path in ("/api/production/tasks", "/api/production/launchable", "/api/production/calendar",
                 "/api/production/boxes", "/api/production/defects", "/api/production/qc/rejections"):
        shop.master.ok("GET", path)
    shop.fitter.ok("GET", "/api/production/my/execution")
    shop.fitter.refused("GET", "/api/production/tasks")  # монтажнику — только свои операции
    assert [item["status"] for item in shop.storekeeper.ok("GET", "/api/issues")["requests"]] == ["КБ Выдана"]
    shop.storekeeper.ok("GET", f"/api/issues/{request['id']}/print")
    assert len(shop.pm.ok("GET", "/api/shipping/shipments")["shipments"]) == 1
    for document in ("pl", "dn"):
        shop.pm.ok("GET", f"/api/shipping/shipments/{shipment['id']}/print", params={"document": document})


def test_launch_day_on_the_synthetic_export(shop):
    _rehearse(shop, SYNTHETIC_EXPORT, synthetic=True)


@pytest.mark.skipif(not os.getenv(REAL_EXPORT_ENV, "").strip(),
                    reason=f"{REAL_EXPORT_ENV} не задан — настоящая выгрузка склада только локально")
def test_launch_day_on_the_real_export(shop):
    directory = Path(os.environ[REAL_EXPORT_ENV].strip())
    assert (directory / "ЦАР.csv").is_file(), f"{directory}: нет ЦАР.csv"
    _rehearse(shop, directory, synthetic=False)
