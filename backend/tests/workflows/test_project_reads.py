"""Один проект — по индексу ключа № проекта (аудит U06): трек, «Снабжение» и автоплан не читают «первые N» всей ERP.

Старая запись проекта видна за границей прежних лимитов (10 000 / 100 000), «719К.2» не смешивается с «719К.21»,
а чтение трека ограничено строками одного проекта — проверяется по SQL, который уходит в базу, и плану SQLite.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import count
from uuid import uuid4

import pytest
from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, Numeric, Uuid, event, func, insert, select
from sqlalchemy.orm import Mapper

from app.modules.fulfillment.application import CreateFulfillmentCommand, create_fulfillment
from app.modules.fulfillment.domain import FulfillmentLine
from app.modules.fulfillment.infrastructure import FulfillmentRequestRow
from app.modules.procurement.domain import ProcurementStatus
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.production.domain import ProductionStatus
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.receiving.infrastructure import ReceivingLineRow
from app.modules.requests.infrastructure import RequestRow
from app.modules.shipping.infrastructure import FinishedGoodRow, ShipmentLineRow
from app.modules.techcards.infrastructure import ProjectTechcardRow
from app.modules.techcards.kitsets import KitAssembly, KitComponent, Kitset
from app.shared.project_number import digit_stem, normalize_project_number, project_from_purpose
from app.workflows.project_kitset import project_supply
from tests.workflows.test_project_supply import _line, _request
from tests.workflows.test_project_track import (  # noqa: F401 — фикстуры erp/seeded
    ACTOR, PROJECT, erp, seed_project, seed_shipping, seed_workshop, seeded, track,
)

TARGET, NEIGHBOUR = "719К.2", "719К.21"
# Таблицы с ключом № проекта и колонки, по которым трек ищет проект.
KEYED = (
    "production_tasks", "finished_goods", "shipment_lines", "techcard_projects", "procurement_lines", "requests",
    "receiving_lines",
)
# Заявка соседа, вставленная мимо ORM, с теми ключами, что поставил бы ORM.
NEIGHBOUR_REQUEST = dict(
    project_reference=NEIGHBOUR, project_key=NEIGHBOUR, purpose="Обеспечение производства",
    purpose_project_key=project_from_purpose("Обеспечение производства"), designation="", designation_project_key="",
    status="Новый",
)


_NUMBERS = count(1)


def new_request(name: str, quantity: str, *, project: str | None, article: str | None = None):
    """Заявка проекта со своим ID (помощник `_request` выдаёт всем один и тот же)."""
    return replace(_request(name, quantity, project=project, article=article), legacy_id=f"SM-{uuid4().hex[:10]}",
                   creation_key=uuid4().hex, designation=f"{project or 'РАЗНОЕ'}.{next(_NUMBERS):03d}")


def _engine(erp):
    return erp.sessions.kw["bind"]


def _filler(column, index: int):
    """Значение-заглушка NOT NULL колонки по её типу; строки уникальны по номеру."""
    kind = column.type
    if isinstance(kind, Uuid):
        return uuid4()
    if isinstance(kind, Boolean):
        return False
    if isinstance(kind, Integer):
        return 1
    if isinstance(kind, Numeric):
        return Decimal("1")
    if isinstance(kind, DateTime):
        return datetime(2026, 1, 1)
    if isinstance(kind, Date):
        return date(2026, 1, 1)
    if isinstance(kind, JSON):
        return []
    length = getattr(kind, "length", None) or 64
    return f"n{index}-{uuid4().hex}"[:length]


def bulk(erp, row, count: int, **values) -> None:
    """`count` строк мимо домена (быстро): NOT NULL колонки — заглушки, `values` — значение или функция от номера.
    Ключ № проекта ставится явно, как его поставил бы ORM."""
    table = row.__table__
    rows = []
    for index in range(count):
        item = {
            column.name: _filler(column, index) for column in table.columns
            if not column.nullable and column.default is None and column.server_default is None
        }
        item.update({name: value(index) if callable(value) else value for name, value in values.items()})
        rows.append(item)
    with erp.sessions() as session:
        session.execute(insert(table), rows)
        session.commit()


class _Loaded:
    def __init__(self, kind, values):
        self.kind, self.__dict__["values"] = kind, values

    def __getattr__(self, name):
        return self.values[name]


def loaded(recorder, kind) -> list:
    return [item for item in recorder.loaded if item.kind is kind]


class Recorder:
    """SQL, ушедший в базу, и строки ORM, поднятые из неё, — за время блока."""

    def __init__(self, erp):
        self.engine = _engine(erp)
        self.statements: list[tuple[str, tuple]] = []
        self.loaded: list[object] = []

    def _sql(self, _conn, _cursor, statement, parameters, _context, _many):
        self.statements.append((statement, tuple(parameters or ())))

    def _load(self, target, _context):
        # Снимок полей сразу при загрузке: после закрытия сессии строка отсоединена.
        values = {key: value for key, value in vars(target).items() if not key.startswith("_")}
        self.loaded.append(_Loaded(type(target), values))

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._sql)
        event.listen(Mapper, "load", self._load)
        return self

    def __exit__(self, *_exc):
        event.remove(self.engine, "before_cursor_execute", self._sql)
        event.remove(Mapper, "load", self._load)

    def selects_from(self, table: str) -> list[tuple[str, tuple]]:
        pattern = re.compile(rf"\bFROM {table}\b")
        return [(sql, params) for sql, params in self.statements if sql.lstrip().upper().startswith("SELECT")
                and pattern.search(sql)]

    def plan(self, sql: str, params: tuple) -> str:
        with self.engine.connect() as connection:
            return "\n".join(str(row[-1]) for row in connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {sql}", params))


# ── Ключ № проекта ───────────────────────────────────────────────────────


def test_the_key_is_exact_and_folds_spelling():
    assert normalize_project_number("719k.2") == normalize_project_number(" 719К.2 ") == TARGET
    assert normalize_project_number("719К") == "719К.1"
    assert normalize_project_number(NEIGHBOUR) != TARGET
    assert project_from_purpose("719К.21 · КБ1 · Жгут") == NEIGHBOUR
    assert project_from_purpose("719К.2.004") == TARGET
    # Цифры, что стоят в исходном тексте подряд: «.1» дописывается после них и в отбор не идёт.
    assert digit_stem("719К.1") == "719" and digit_stem("П2401-1") == "2401" and digit_stem("АБВ") == ""


def test_writing_the_project_text_keeps_the_key(erp):
    request = new_request("Корпус", "4", project=TARGET)
    with erp.requests() as uow:
        uow.requests.add(request)
        uow.commit()
    with erp.sessions() as session:
        row = session.get(RequestRow, request.id)
        assert (row.project_key, row.purpose_project_key, row.designation_project_key) == (
            TARGET, project_from_purpose(request.purpose), project_from_purpose(request.designation),
        )

    moved = replace(request, project_reference=NEIGHBOUR, purpose=f"{NEIGHBOUR} · КБ1", designation="",
                    version=request.version + 1)
    with erp.requests() as uow:
        uow.requests.add(moved)
        uow.commit()
    with erp.requests() as uow:
        assert uow.requests.for_project(TARGET) == []
        assert [item.id for item in uow.requests.for_project(NEIGHBOUR)] == [request.id]


def test_a_row_written_past_the_orm_without_a_key_is_still_found(erp):
    """NULL ключа (строка мимо ORM) не прячет запись: такие читаются и сверяются точным сравнением."""
    bulk(erp, ProductionTaskRow, 1, project="719k.2", status=ProductionStatus.RUNNING.value, project_key=None,
         number="ПЗ-NULL", created_at=datetime(2026, 9, 1))
    bulk(erp, ProductionTaskRow, 1, project=NEIGHBOUR, status=ProductionStatus.RUNNING.value, project_key=None,
         number="ПЗ-NULL-21", created_at=datetime(2026, 9, 1))

    with erp.production() as uow:
        assert [task.number for task in uow.production.for_project(TARGET)] == ["ПЗ-NULL"]


# ── 719К.2 ≠ 719К.21 ─────────────────────────────────────────────────────


def test_neighbour_numbers_never_mix_in_any_module(erp):
    for project in (TARGET, NEIGHBOUR):
        seed_project(erp, project)
        seed_workshop(erp, project)
        seed_shipping(erp, project)
        stored = new_request(f"Корпус {project}", "4", project=project)
        with erp.requests() as uow:
            uow.requests.add(stored)
            uow.commit()
        with erp.procurement() as uow:
            uow.procurement.add(_line(stored, "4", ProcurementStatus.IN_WORK))
            uow.commit()
    # Заявка 719К.21, где проект только в обозначении «проект.NNN»: тоже не 719К.2.
    designated = replace(new_request("Провод", "1", project=None), designation=f"{NEIGHBOUR}.004")
    with erp.requests() as uow:
        uow.requests.add(designated)
        uow.commit()

    mine, theirs = track(erp, TARGET), track(erp, NEIGHBOUR)

    assert {item.module for item in mine} == {"Проекты", "Техкарты", "Заявки", "Закупка", "Комплектация",
                                              "Производство", "Отгрузка"}
    assert not {item.ref for item in mine} & {item.ref for item in theirs}
    assert all(not item.ref.startswith(NEIGHBOUR) for item in mine)
    assert designated.legacy_id in {item.ref for item in theirs}
    with erp.requests() as uow:
        assert {item.project_reference for item in uow.requests.for_project(TARGET)} == {TARGET}
    with erp.fulfillment() as uow:
        assert {project_from_purpose(item.purpose) for item in uow.fulfillment.for_project(TARGET)} == {TARGET}
    with erp.shipping() as uow:
        assert {good.project for good in uow.goods.for_project(TARGET)} == {TARGET}
        assert all(line.project == TARGET for item in uow.shipments.for_project(TARGET) for line in item.lines)
    with erp.techcards() as uow:
        assert {card.project for card in uow.techcards.for_project(TARGET)} == {TARGET}


# ── Старое за границей прежних лимитов ───────────────────────────────────


def test_an_old_request_beyond_ten_thousand_newer_ones_reaches_supply_and_track(erp):
    old = replace(new_request("Корпус Molex", "4", article="43025-0400", project=TARGET),
                  created_at=datetime(2025, 1, 10, 9, 0))
    with erp.requests() as uow:
        uow.requests.add(old)
        uow.commit()
    newer = 10_001
    bulk(erp, RequestRow, newer, **NEIGHBOUR_REQUEST,
         created_at=lambda index: datetime(2026, 9, 1) + timedelta(seconds=index))
    with erp.sessions() as session:  # «первые 10 000» свежих (прежний `recent(10_000)`) старую заявку не брали
        assert session.scalar(select(func.count()).select_from(RequestRow).where(
            RequestRow.created_at > old.created_at)) == newer
    kitset = Kitset(
        project=TARGET, customer="ООО Заказчик", calc_source="",
        assemblies=(KitAssembly(index="КБ1", name="Жгут", run=Decimal("2"),
                                components=(KitComponent(article="43025-0400", type="Разъём", name="Корпус"),)),),
        updated_at=datetime(2026, 9, 1), updated_by="Технолог", created_at=datetime(2026, 9, 1),
    )

    lines, requests = project_supply(kitset, erp.requests, erp.procurement, erp.fulfillment)

    assert [item.id for item in requests] == [old.id]
    assert [(row.where, row.source_id) for line in lines for row in line.rows] == [("Запрос", old.legacy_id)]
    assert [item.ref for item in track(erp, TARGET) if item.module == "Заявки"] == [old.legacy_id]


def test_autoplan_sees_an_old_running_task_beyond_ten_thousand_newer_ones(erp):
    bulk(erp, ProductionTaskRow, 1, project=TARGET, project_key=TARGET, status=ProductionStatus.RUNNING.value,
         number="ПЗ-СТАРОЕ", created_at=datetime(2025, 1, 10))
    newer = 10_001
    bulk(erp, ProductionTaskRow, newer, project=NEIGHBOUR, project_key=NEIGHBOUR,
         status=ProductionStatus.CANCELLED.value,
         number=lambda index: f"ПЗ-{index:05d}", created_at=lambda index: datetime(2026, 9, 1) + timedelta(seconds=index))

    with Recorder(erp) as recorder, erp.production() as uow:
        running = uow.production.with_status(ProductionStatus.RUNNING)

    assert [task.number for task in running] == ["ПЗ-СТАРОЕ"]
    assert sum(item.kind is ProductionTaskRow for item in recorder.loaded) == 1
    (sql, params), = recorder.selects_from("production_tasks")
    assert "LIMIT" not in sql.upper() and "SCAN production_tasks" not in recorder.plan(sql, params)


# ── Трек читает один проект ──────────────────────────────────────────────


def test_the_track_reads_only_the_rows_of_its_project_through_indexes(erp, seeded):
    seed_project(erp, "708К.41")
    seed_workshop(erp, "708К.41")
    seed_shipping(erp, "708К.41")
    noise = 300
    bulk(erp, ProductionTaskRow, noise, project=NEIGHBOUR, project_key=NEIGHBOUR,
         status=ProductionStatus.RUNNING.value, created_at=datetime(2026, 9, 1))
    bulk(erp, RequestRow, noise, **NEIGHBOUR_REQUEST, created_at=datetime(2026, 9, 1))
    bulk(erp, ProjectTechcardRow, noise, project=NEIGHBOUR, project_key=NEIGHBOUR, status="Черновик",
         item_index=lambda index: f"КБ{index}", created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1))

    with Recorder(erp) as recorder:
        entries = track(erp)

    assert entries and all(not item.ref.startswith("708К.41") for item in entries)
    canon = normalize_project_number(PROJECT)
    for item in recorder.loaded:  # ни одной строки чужого проекта не поднято
        if item.kind in (ProductionTaskRow, FinishedGoodRow, ShipmentLineRow, ProjectTechcardRow):
            assert item.project_key == canon, item
        elif item.kind is RequestRow:
            assert canon in (item.project_key, item.purpose_project_key, item.designation_project_key), item
        elif item.kind is ProcurementLineRow:
            assert item.project_key == canon or item.id == seeded.line.id, item
        elif item.kind is ReceivingLineRow:
            assert item.procurement_line_id == seeded.line.id, item
        elif item.kind is FulfillmentRequestRow:  # без колонки ключа (#254): отбор по цифрам номера, точно — в Python
            assert digit_stem(canon) in item.purpose, item
    planned = set()
    for table in KEYED:
        statements = recorder.selects_from(table)
        assert statements, table
        for sql, params in statements:
            assert "WHERE" in sql.split() and "LIMIT" not in sql.upper(), sql
            if "project_key =" in sql:  # отбор проекта — поиском по индексу ключа, не проходом таблицы
                plan = recorder.plan(sql, params)
                assert f"SCAN {table}" not in plan and "_project_key" in plan, (table, plan)
                planned.add(table)
    assert planned == set(KEYED)
    # Число запросов — по строкам проекта, не по размеру базы: ещё шум — столько же запросов.
    bulk(erp, ProductionTaskRow, noise, project="1719К.2", project_key="1719К.2",
         status=ProductionStatus.RUNNING.value, created_at=datetime(2026, 9, 2))
    with Recorder(erp) as again:
        assert track(erp) == entries
    assert len(again.statements) == len(recorder.statements)


def test_fulfillment_picks_by_the_number_digits_and_compares_exactly(erp):
    for project in (TARGET, NEIGHBOUR, "1719К.2"):
        create_fulfillment(CreateFulfillmentCommand(
            idempotency_key=f"reads:{uuid4()}", purpose=f"{project} · КБ1 · Жгут", deadline=date(2026, 10, 20),
            lines=(FulfillmentLine.create(article="A", name="A", unit="шт", requirement=Decimal("1")),),
            actor=ACTOR, now=datetime(2026, 9, 22, 9, 0),
        ), erp.fulfillment())
    create_fulfillment(CreateFulfillmentCommand(
        idempotency_key=f"reads:{uuid4()}", purpose="Опытный образец", deadline=date(2026, 10, 20),
        lines=(FulfillmentLine.create(article="A", name="A", unit="шт", requirement=Decimal("1")),),
        actor=ACTOR, now=datetime(2026, 9, 22, 9, 0),
    ), erp.fulfillment())

    with Recorder(erp) as recorder, erp.fulfillment() as uow:
        found = uow.fulfillment.for_project("719k.2")

    assert [item.purpose for item in found] == [f"{TARGET} · КБ1 · Жгут"]
    # В базе отобраны только назначения с цифрами номера — «Опытный образец» не поднимается вовсе.
    assert {item.purpose for item in loaded(recorder, FulfillmentRequestRow)} == {
        f"{TARGET} · КБ1 · Жгут", f"{NEIGHBOUR} · КБ1 · Жгут", "1719К.2 · КБ1 · Жгут",
    }


@pytest.mark.parametrize("module", ["requests", "procurement", "receiving", "production", "techcards", "fulfillment"])
def test_an_empty_project_reads_nothing(erp, module):
    with getattr(erp, module)() as uow:
        assert list(getattr(uow, module).for_project("")) == []
