"""Запуск ПЗ по нормам ЭТК (G2-T1): план задания берёт ту же норму операции, что показывает ЭТК (провода × жилы × партия,
«на изделие» ручной сборки), и идёт по нарисованной схеме блоков, даже когда операции несут свои ключи (opKey)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from app.modules.production.launchable import launch_operations, launch_route, route_from_document
from app.modules.techcards.assembly_schema import generator_to_assembly
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import parse_operation_library


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
FIXTURES = json.loads((ROOT / "tests" / "techcards" / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
KITS = ("720К.-КБ1", "720К.1.2-КБ1", "708К.2-КБ1", "708К.5-КБ1", "591К.5-КБ2", "729К.2-КБ1", "345К.2-КБ1")


def _config(name: str, part_quantity: int = 100) -> dict:
    config = deepcopy(FIXTURES["files"][f"kit-{name}.json"]["payload"]["config"])
    config["partQty"] = part_quantity
    return config


def _build(config: dict) -> dict:
    return build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])


def _etk_minutes(document: dict) -> Decimal:
    """What the ETK shows: Σ block time of its scheme (= Σ time rows of the operations), minutes per batch."""
    return sum((Decimal(str(block["minutes"])) for block in document["route"]["blocks"]), Decimal(0))


def _plan_minutes(steps, quantity: int) -> Decimal:
    return sum((step.setup_seconds + step.seconds_per_unit * quantity for step in steps), Decimal(0)) / 60


@pytest.mark.parametrize("name", KITS)
def test_plan_of_a_kit_at_its_etk_batch_equals_the_etk_time(name: str):
    document = _build(_config(name))
    steps = route_from_document(document)
    etk = _etk_minutes(document)

    assert _plan_minutes(steps, 100) == pytest.approx(etk, abs=Decimal("0.01"))
    # Per operation too: setup is the card's tPrep, the rest of the ETK norm is spread over the batch.
    config_ops = document["build_config"]["ops"]
    for step, operation, op in zip(steps, document["operations"], config_ops):
        norm = sum((Decimal(row["norm"].replace(",", ".")) for row in operation["time"]), Decimal(0))
        assert (step.setup_seconds + step.seconds_per_unit * 100) / 60 == pytest.approx(norm, abs=Decimal("0.0001"))
        assert step.setup_seconds == min(Decimal(str(op.get("tPrep") or 0)), norm * 60)


def test_plan_was_4_to_6_times_short_on_wire_kits_and_is_the_etk_now():
    """The bug: the plan counted the card's tOp once per item; the ETK multiplies it by wires × cores × batch."""
    for name, etk_minutes, old_minutes in (("591К.5-КБ2", "1560.26", "239.5"), ("708К.2-КБ1", "1954.7", "448.8"),
                                           ("720К.-КБ1", "895.32", "126.2")):
        config = _config(name)
        document = _build(config)
        old = sum((Decimal(str(op.get("tOp") or 0)) * 100 + Decimal(str(op.get("tPrep") or 0)) for op in config["ops"]),
                  Decimal(0)) / 60
        assert _etk_minutes(document) == Decimal(etk_minutes)
        assert round(old, 1) == Decimal(old_minutes)
        assert _plan_minutes(route_from_document(document), 100) == pytest.approx(Decimal(etk_minutes), abs=Decimal("0.01"))


def test_a_launched_task_carries_the_etk_norms_and_scales_with_its_quantity():
    document = _build(_config("720К.-КБ1"))
    steps = route_from_document(document)
    operations = launch_operations(steps, 250)
    assert [(item.seconds_per_unit, item.setup_seconds) for item in operations] == [
        (step.seconds_per_unit, step.setup_seconds) for step in steps
    ]
    total = sum((item.setup_seconds + item.seconds_per_unit * 250 for item in operations), Decimal(0)) / 60
    setup = sum((step.setup_seconds for step in steps), Decimal(0)) / 60
    # 250 pieces: setup once per operation, the rest 2.5 × that of the ETK's batch of 100.
    assert total == pytest.approx(setup + (_etk_minutes(document) - setup) * Decimal("2.5"), abs=Decimal("0.01"))


def _manual_with_generator_ops_and_two_branches() -> dict:
    """591К.5 КБ2 as «Ручная сборка»: its generator operations with their own keys (as a draft or a converted card
    carries them), a hand-drawn scheme of two wire branches that join the assembly block, and one own operation
    with «на изделие» 2 at the end."""
    config = generator_to_assembly(_config("591К.5-КБ2"))
    for op in config["ops"]:
        op["opKey"] = f"{op['type']}-{op['seq']}"
    config["ops"].append({
        "type": "customOp", "seq": 11, "label": "Контроль прозвонкой", "qty": 2, "tPrep": "60", "tOp": "15",
        "steps": [{"kind": "Контроль", "text": "Прозвонить каждую цепь", "tolerance": "обрыв недопустим"}],
    })
    config["route"] = {
        "blocks": [
            {"key": "w1", "name": "Провод 1 разделан", "ops": [1], "final": False},
            {"key": "w2", "name": "Провод 2 разделан, ТУТ нарезана", "ops": [2, 3], "final": False},
            {"key": "harness", "name": "", "ops": [4, 5, 6, 7, 8, 9, 10, 11], "final": True},
        ],
        "edges": [{"from": "w1", "to": "harness", "per_unit": 1}, {"from": "w2", "to": "harness", "per_unit": 1}],
    }
    return config


def test_manual_card_with_generator_ops_launches_both_drawn_branches():
    document = _build(_manual_with_generator_ops_and_two_branches())
    assert document["route"]["source"] == "manual"

    planned = launch_route(document)
    assert planned.warnings == ()
    steps = planned.steps
    assert [step.block for step in steps] == ["w1", "w2", "w2"] + ["harness"] * 8
    assert [step.final for step in steps] == [False] * 3 + [True] * 8

    operations = launch_operations(steps, 100)
    position_of = {item.id: item.position for item in operations}
    inputs = {item.position: [(position_of[entry.input_operation_id], entry.per_unit) for entry in item.inputs]
              for item in operations}
    # Both branches start on their own and both feed the first operation of the assembly block.
    assert inputs[1] == [] and inputs[2] == []
    assert inputs[3] == [(2, Decimal(1))]
    assert inputs[4] == [(1, Decimal(1)), (3, Decimal(1))]
    assert all(inputs[position] == [(position - 1, Decimal(1))] for position in range(5, 12))
    assert [item.stage_key for item in operations] == ["w1", "w2", "w2"] + ["harness"] * 8

    # Norms as the ETK shows them, the own operation with its «на изделие» 2: 15 s × 2 per item + 60 s setup.
    assert _plan_minutes(steps, 100) == pytest.approx(_etk_minutes(document), abs=Decimal("0.01"))
    assert (steps[10].seconds_per_unit, steps[10].setup_seconds) == (Decimal(30), Decimal(60))


def test_a_drawn_scheme_that_no_longer_fits_launches_as_a_chain_with_a_warning():
    config = _manual_with_generator_ops_and_two_branches()
    document = _build(config)
    # The stored scheme lost operation 11 (a hand edit of the document): the launch must not fail and must say so.
    document["route"]["blocks"][2]["ops"] = [4, 5, 6, 7, 8, 9, 10]

    planned = launch_route(document)

    assert [step.block for step in planned.steps] == [f"b{position}" for position in range(1, 12)]
    assert planned.warnings == (
        "Схема полуфабрикатов техкарты не подошла к её операциям (операция 11 не входит ни в один блок) — этапы "
        "задания идут цепочкой по порядку операций. Поправьте схему в техкарте и выпустите её заново.",
    )
    operations = launch_operations(planned.steps, 10)
    assert [len(item.inputs) for item in operations] == [0] + [1] * 10
