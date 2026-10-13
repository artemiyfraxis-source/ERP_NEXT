"""Задание по ПФ (владелец + Василий, 13.10.2026): задание монтажника = один полуфабрикат схемы ЭТК.

Нарисованная технологом схема — по операции задания на ПФ (номер ПФ, его название, код первой операции, сумма норм,
входы из предыдущих ПФ с per_unit, операции ЭТК в порядке блока). Без схемы — как прежде, по операции ЭТК."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.production.application import LaunchProductionCommand, launch_production
from app.modules.production.autoplan import operation_keys, plan, setup_key
from app.modules.production.dispatch import rate_of
from app.modules.production.domain import (
    DEFAULT_SHIFT,
    EtkOperation,
    OperatorSkill,
    ProductionOperation,
    ProductionTask,
    plan_operation,
    skill_of,
)
from app.modules.production.infrastructure import ProductionOperationRow, SqlAlchemyProductionUnitOfWork
from app.modules.production.launchable import launch_operations, launch_route, semifinished_steps
from app.modules.production.scheduling import _allowed, _rate, work_seconds_between
from app.modules.techcards.project_cards import ProjectTechcard
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.production.test_api import production_api  # noqa: F401 — фикстура API цеха
from tests.production.test_autoplan import ANNA, BORIS, data, task as autoplan_task
from tests.production.test_launchable_norms import KITS, _build, _config

NOW = datetime(2026, 10, 13, 9, 0)
SNAPSHOT = Path(__file__).parent / "fixtures" / "launch-by-operation-snapshot.json"

# Четыре операции ЭТК: (код, этап, сек на штуку, подготовка).
OPS = [("OP-1", "Резка", 10, 60), ("OP-2", "Зачистка", 5, 30), ("OP-3", "Опрессовка", 20, 120), ("OP-4", "Сборка", 40, 0)]
# ПФ-1 «Провод нарезан» — операции 2 и 1 (порядок блока: сначала зачистка), ПФ-2 — опрессовка, ПФ-3 — сборка из
# 2 шт ПФ-1 и 1 шт ПФ-2.
ROUTE = {
    "source": "manual",
    "blocks": [
        {"key": "pf1", "name": "Провод нарезан", "ops": [2, 1]},
        {"key": "pf2", "name": "Терминал опрессован", "ops": [3]},
        {"key": "pf3", "name": "Жгут", "ops": [4], "final": True},
    ],
    "edges": [{"from": "pf1", "to": "pf3", "per_unit": 2}, {"from": "pf2", "to": "pf3", "per_unit": 1}],
}


def _document(route: dict | None = ROUTE, ops=OPS) -> dict:
    return {
        "operations": [{"code": code, "connector": str(i), "side": ""} for i, (code, *_rest) in enumerate(ops, 1)],
        "build_config": {"ops": [
            {"type": "assembly", "stage": stage, "opNumber": code, "tOp": t_op, "tPrep": t_prep}
            for code, stage, t_op, t_prep in ops
        ]},
        "kit": {"part_quantity": "10"},
        **({"route": route} if route is not None else {}),
    }


def _task(operations, quantity: int = 10) -> ProductionTask:
    return ProductionTask.create(
        number="ПЗ-0001", source_idempotency_key="key", source_fingerprint="fp", project="900К.1", item="КБ1",
        name="Жгут", quantity=quantity, fulfillment_request_id=None, deadline=None, launched_by="Мастер",
        operations=tuple(operations), created_at=NOW,
    )


def _inputs(task: ProductionTask) -> dict[int, list[tuple[int, Decimal]]]:
    position = {item.id: item.position for item in task.operations}
    return {item.position: [(position[edge.input_operation_id], edge.per_unit) for edge in item.inputs]
            for item in task.operations}


# --- запуск -----------------------------------------------------------------------------------------------------


def test_a_drawn_scheme_launches_one_task_operation_per_semifinished():
    planned = launch_route(_document())
    assert planned.warnings == ()
    task = _task(launch_operations(planned.steps, 10))

    assert [item.position for item in task.operations] == [1, 2, 3]
    assert [item.stage for item in task.operations] == ["Провод нарезан", "Терминал опрессован", "Жгут"]
    # Код ПФ — код его первой операции в порядке ПФ (ПФ-1 начинается с зачистки OP-2).
    assert [item.code for item in task.operations] == ["OP-2", "OP-3", "OP-4"]
    assert [item.stage_key for item in task.operations] == ["pf1", "pf2", "pf3"]
    assert task.operations[0].etk_ops == (EtkOperation(2, "OP-2", "Зачистка"), EtkOperation(1, "OP-1", "Резка"))
    assert task.operations[1].etk_ops == (EtkOperation(3, "OP-3", "Опрессовка"),)
    assert task.operations[0].etk_positions == (2, 1)
    # Норма ПФ — сумма норм его операций; ПФ-1 идёт 2 шт на изделие — его штук вдвое больше тиража.
    assert [(item.seconds_per_unit, item.setup_seconds) for item in task.operations] == [
        (Decimal(15), Decimal(90)), (Decimal(20), Decimal(120)), (Decimal(40), Decimal(0)),
    ]
    assert [item.norm_seconds for item in task.operations] == [90 + 15 * 20, 120 + 20 * 10, 40 * 10]
    # Входы — предыдущие ПФ с per_unit схемы; финал — ПФ-3.
    assert _inputs(task) == {1: [], 2: [], 3: [(1, Decimal(2)), (2, Decimal(1))]}
    assert task.final_operation.position == 3


def test_semifinished_norms_equal_the_sum_of_the_etk_operations():
    steps = launch_route(_document()).steps
    semis = semifinished_steps(steps)

    assert sum((item.seconds_per_unit for item in semis), Decimal(0)) == sum((item.seconds_per_unit for item in steps), Decimal(0))
    assert sum((item.setup_seconds for item in semis), Decimal(0)) == sum((item.setup_seconds for item in steps), Decimal(0))
    assert [len(item.etk_ops) for item in semis] == [2, 1, 1]
    # Шаги маршрута ЭТК остаются по операциям: экраны, коды для компетенций и нормы задания без ПФ не меняются.
    assert [item.position for item in steps] == [1, 2, 3, 4]


def test_a_semifinished_number_is_its_place_in_the_scheme_not_in_the_operations():
    route = {**ROUTE, "blocks": [ROUTE["blocks"][2], ROUTE["blocks"][0], ROUTE["blocks"][1]]}
    task = _task(launch_operations(launch_route(_document(route)).steps, 10))

    assert [(item.position, item.stage_key) for item in task.operations] == [(1, "pf3"), (2, "pf1"), (3, "pf2")]
    assert _inputs(task)[1] == [(2, Decimal(2)), (3, Decimal(1))]
    assert task.final_operation.position == 1


@pytest.mark.parametrize("route", [
    None,
    {"source": "derived", "blocks": [{"key": "b1", "ops": [1, 2, 3, 4], "final": True}], "edges": []},
    {"source": "manual", "blocks": [{"key": "b1", "ops": [1, 2], "final": True}], "edges": []},  # не подходит
])
def test_without_a_drawn_scheme_the_task_goes_by_etk_operations(route):
    task = _task(launch_operations(launch_route(_document(route)).steps, 10))

    assert [item.position for item in task.operations] == [1, 2, 3, 4]
    assert [item.code for item in task.operations] == ["OP-1", "OP-2", "OP-3", "OP-4"]
    assert all(item.etk_ops == () for item in task.operations)
    assert _inputs(task) == {1: [], 2: [(1, Decimal(1))], 3: [(2, Decimal(1))], 4: [(3, Decimal(1))]}


@pytest.mark.parametrize("name", KITS)
def test_launch_without_a_scheme_matches_the_snapshot_taken_before_launch_by_semifinished(name: str):
    """Снимок запуска по операциям ЭТК, снятый кодом до заданий по ПФ (13.10): поле в поле."""
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))[name]
    operations = launch_operations(launch_route(_build(_config(name))).steps, 250)
    position = {item.id: item.position for item in operations}

    assert [{
        "position": item.position, "code": item.code, "stage": item.stage, "description": item.description,
        "seconds_per_unit": str(item.seconds_per_unit), "setup_seconds": str(item.setup_seconds),
        "norm_seconds": item.norm_seconds, "stage_key": item.stage_key,
        "inputs": [[position[edge.input_operation_id], str(edge.per_unit)] for edge in item.inputs],
    } for item in operations] == expected
    assert all(item.etk_ops == () for item in operations)


# --- хранение ---------------------------------------------------------------------------------------------------


def test_semifinished_operations_persist_and_an_operation_without_them_stays_null():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)  # noqa: E731
    actor = Actor(id=UUID("10000000-0000-0000-0000-000000000001"), name="Мастер", department="Производство")

    def launch(key: str, document: dict):
        return launch_production(LaunchProductionCommand(
            idempotency_key=key, project="900К.1", item=key, name="Жгут", quantity=10, fulfillment_request_id=None,
            deadline=date(2026, 10, 30), operations=launch_operations(launch_route(document).steps, 10),
            actor=actor, now=NOW,
        ), factory())

    by_semis = launch("КБ1", _document())
    by_operations = launch("КБ2", _document(None))
    with factory() as uow:
        stored, plain = uow.production.get(by_semis.id), uow.production.get(by_operations.id)
    with sessions() as session:
        raw = {row.position: row.etk_ops for row in session.scalars(
            select(ProductionOperationRow).where(ProductionOperationRow.task_id == by_operations.id))}

    assert stored == by_semis
    assert stored.operations[0].etk_ops == (EtkOperation(2, "OP-2", "Зачистка"), EtkOperation(1, "OP-1", "Резка"))
    # Операция задания без ПФ (и все задания до 0120): NULL в базе, пустой список в домене — работает как прежде.
    assert plain == by_operations and raw == {1: None, 2: None, 3: None, 4: None}
    assert all(item.etk_ops == () and item.skill_codes == (item.code,) and item.etk_positions == (item.position,)
               for item in plain.operations)
    engine.dispose()


# --- автоплан и компетенции ---------------------------------------------------------------------------------------


def _crimp_document() -> dict:
    """ПФ из резки и опрессовки на учётном прессе ОБ.027: ключ наладки ПФ — цепочка ключей его операций."""
    return {
        "operations": [
            {"code": "CUT", "type": "cutWire", "fields": {"wires": [{"art": "НВ-4 0,35"}]}},
            {"code": "PRS", "type": "prsTerm", "fields": {"applicator": "A19"},
             "card": {"tooling": [{"section": "Оборудование", "inventory_number": "ОБ.027.1", "name": "Пресс"}]}},
        ],
        "build_config": {"ops": [
            {"type": "cutWire", "opNumber": "CUT", "stage": "Резка", "tOp": 5, "tPrep": 60},
            {"type": "prsTerm", "opNumber": "PRS", "stage": "Опрессовка", "tOp": 10, "tPrep": 600, "ep": {"termArt": "T-1"}},
        ]},
        "route": {"source": "manual", "blocks": [{"key": "pf1", "name": "Провод с терминалом", "ops": [1, 2], "final": True}],
                  "edges": []},
    }


def test_autoplan_key_of_a_semifinished_chains_the_keys_of_its_etk_operations():
    document = _crimp_document()
    task = _task(launch_operations(launch_route(document).steps, 10))
    built, config_ops = document["operations"], document["build_config"]["ops"]
    cut = setup_key(code="CUT", stage="Резка", built=built[0], config_op=config_ops[0], config=document["build_config"])
    crimp = setup_key(code="PRS", stage="Опрессовка", built=built[1], config_op=config_ops[1], config=document["build_config"])

    keys, warnings = operation_keys(task, document)
    key = keys[task.operations[0].id]

    assert warnings == ()
    assert key.family == cut.family + crimp.family and key.exact == cut.exact + crimp.exact
    # Станок ПФ — его учётный пресс: занятость ограничена единицами ОБ.027.
    assert (key.machine, key.limited) == ("ОБ.027", True)
    assert key.label == f"{cut.label}; {crimp.label}"


def test_autoplan_key_of_a_semifinished_falls_back_when_an_etk_operation_changed_its_code():
    document = _crimp_document()
    task = _task(launch_operations(launch_route(document).steps, 10))
    document["build_config"]["ops"][1]["opNumber"] = "PRS-2"

    keys, warnings = operation_keys(task, document)

    assert keys[task.operations[0].id].family == ("CUT",)
    assert warnings == (
        "ПЗ-0001 · ПФ 1, операция ЭТК 2: код PRS не совпадает с техкартой (PRS-2) — наладка по коду ПФ",
    )


def _skills() -> dict[str, dict[UUID, OperatorSkill]]:
    return {
        "OP-1": {ANNA: OperatorSkill(ANNA, "OP-1", True, 5, Decimal("2")), BORIS: OperatorSkill(BORIS, "OP-1", True, 4, Decimal("1.5"))},
        "OP-2": {ANNA: OperatorSkill(ANNA, "OP-2", True, 3, Decimal("0.8")), BORIS: OperatorSkill(BORIS, "OP-2", False, 0, Decimal("1"))},
    }


def _semifinished_operation(codes=("OP-1", "OP-2", "OP-NEW")) -> ProductionOperation:
    return ProductionOperation.create(
        position=1, code=codes[0], stage="Провод нарезан", description="", seconds_per_unit=Decimal(36),
        setup_seconds=Decimal(0), quantity=100,
        etk_ops=tuple(EtkOperation(index, code, code) for index, code in enumerate(codes, 1)),
    )


def test_a_fitter_needs_every_code_of_the_semifinished_and_works_at_his_lowest_coefficient():
    operation = _semifinished_operation()
    skills = _skills()

    assert operation.skill_codes == ("OP-1", "OP-2", "OP-NEW")
    # OP-NEW ещё нет в матрице — не проверяется (коэффициент 1), как у одиночной операции.
    anna, boris = skill_of(skills, operation.skill_codes, ANNA), skill_of(skills, operation.skill_codes, BORIS)
    assert (anna.checked, anna.allowed, anna.stars, anna.coefficient) == (True, True, 3, Decimal("0.8"))
    assert (boris.allowed, boris.coefficient) == (False, Decimal(1))
    assert skill_of(skills, ("OP-1",), ANNA) == skill_of(skills, "OP-1", ANNA)
    assert _allowed(skills, operation.skill_codes, ANNA) and not _allowed(skills, operation.skill_codes, BORIS)
    assert _rate(skills, operation.skill_codes, (ANNA,)) == Decimal("0.8")
    assert _rate(skills, operation.skill_codes, (ANNA, BORIS)) == Decimal("1.8")  # строка Бориса есть: его 1
    assert rate_of(skills, operation.skill_codes, (ANNA,)) == Decimal("0.8")
    result = plan_operation(
        operation=operation, quantity=100, operator_ids=(ANNA, BORIS), requested_start=None, earliest_start=NOW,
        busy={}, holidays=frozenset(), skills=skills, names={ANNA: "Анна", BORIS: "Борис"},
    )
    assert result.warnings == ("Борис: нет допуска к операции OP-1, OP-2, OP-NEW",)


def test_autoplan_gives_a_semifinished_only_to_a_fitter_admitted_to_all_its_codes():
    current = autoplan_task(1, [_semifinished_operation(("OP-1", "OP-2"))])
    operation = current.operations[0]
    keys = {operation.id: setup_key(code="OP-1", stage="Резка", built=None, config_op=None, config=None)}

    result = plan(data([current], keys, team=(BORIS, ANNA), skills=_skills()))

    assert [item.operator_id for item in result.placements] == [ANNA]
    placement = result.placements[0]
    # Анна по ПФ — на своём меньшем коэффициенте 0,8: 100 шт × 36 с / 0,8 = 4500 с (по OP-1 с её 2 было бы 1800).
    worked = work_seconds_between(placement.start, placement.finish, frozenset(), DEFAULT_SHIFT)
    assert 4400 < worked < 4600


# --- API --------------------------------------------------------------------------------------------------------


def _issue(document: dict, project: str = "900К.1") -> ProjectTechcard:
    card = ProjectTechcard.create(
        project=project, item_index="КБ1", product_name="Жгут", source_calculation="", deadline=None, now=NOW,
    ).rebuild(document, NOW).issue(NOW)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    return card


def test_api_launch_returns_etk_ops_and_the_etk_focus_shows_the_semifinished_operations(production_api):  # noqa: F811
    client, _operator_id = production_api
    card = _issue(_document())

    launched = client.post("/api/production/tasks", headers={"Idempotency-Key": "pf-1"},
                           json={"techcard_id": str(card.id), "quantity": 10})

    assert launched.status_code == 200, launched.text
    assert launched.json()["warnings"] == []
    task = launched.json()["task"]
    operations = task["operations"]
    assert [(item["position"], item["code"], item["stage"]) for item in operations] == [
        (1, "OP-2", "Провод нарезан"), (2, "OP-3", "Терминал опрессован"), (3, "OP-4", "Жгут"),
    ]
    assert operations[0]["etk_ops"] == [
        {"position": 2, "code": "OP-2", "stage": "Зачистка"}, {"position": 1, "code": "OP-1", "stage": "Резка"},
    ]
    assert [(item["position"], item["per_unit"]) for item in operations[2]["inputs"]] == [(1, "2"), (2, "1")]
    assert operations[0]["required_quantity"] == "20"

    focus = client.get(f"/api/production/my/tasks/{task['id']}/operations/{operations[0]['id']}/etk")
    assert focus.status_code == 200, focus.text
    assert focus.json()["focus"] == {"position": 2, "positions": [2, 1], "code": "OP-2"}

    plain = client.post("/api/production/tasks", headers={"Idempotency-Key": "pf-plain"},
                        json={"techcard_id": str(_issue(_document(None), "900К.2").id), "quantity": 10}).json()["task"]
    assert all(item["etk_ops"] == [] for item in plain["operations"])
    plain_focus = client.get(f"/api/production/my/tasks/{plain['id']}/operations/{plain['operations'][2]['id']}/etk")
    assert plain_focus.json()["focus"] == {"position": 3, "positions": [3], "code": "OP-3"}


def test_api_refreshes_semifinished_norms_and_refuses_when_the_semifinished_changed(production_api):  # noqa: F811
    client, _operator_id = production_api
    card = _issue(_document())
    task = client.post("/api/production/tasks", headers={"Idempotency-Key": "pf-norms"},
                       json={"techcard_id": str(card.id), "quantity": 10}).json()["task"]

    slower = [(code, stage, t_op * 2, t_prep) for code, stage, t_op, t_prep in OPS]
    with app.state.techcards_uow_factory() as uow:
        stored = uow.techcards.get(card.id)
        uow.techcards.save(stored.rebuild(_document(ROUTE, slower), NOW))
        uow.commit()
    refreshed = client.post(f"/api/production/tasks/{task['id']}/norms/refresh", json={"version": task["version"]})

    assert refreshed.status_code == 200, refreshed.text
    assert [item["seconds_per_unit"] for item in refreshed.json()["task"]["operations"]] == ["30", "40", "80"]

    moved = {**ROUTE, "blocks": [
        {"key": "pf1", "name": "Провод нарезан", "ops": [1]},
        {"key": "pf2", "name": "Терминал опрессован", "ops": [2, 3]},
        ROUTE["blocks"][2],
    ]}
    with app.state.techcards_uow_factory() as uow:
        stored = uow.techcards.get(card.id)
        uow.techcards.save(stored.rebuild(_document(moved, slower), NOW))
        uow.commit()
    after = refreshed.json()["task"]
    refused = client.post(f"/api/production/tasks/{task['id']}/norms/refresh", json={"version": after["version"]})

    assert refused.status_code == 422 and "Полуфабрикаты техкарты изменились" in refused.json()["detail"]
