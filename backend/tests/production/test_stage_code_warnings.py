"""Предупреждения о кодах этапов (планирование, 26.11): понятный текст и план по ЭТК, с которой запущено задание."""

from uuid import uuid4

import pytest

from app.modules.production.autoplan import operation_keys
from app.modules.production.board import CardSnapshot
from app.modules.production.machine_load import task_steps
from tests.production.test_machine_load import ANNA, OperationShare, at, etk, make_task, placed, single

# Три кода из жалобы владельца: в задании — старый код, в выданной ЭТК на этом месте уже другой.
CASES = [
    ("INS_MARK_TUBE_AUTO", "Маркировка трубки", "INS_MARK_TUBE"),
    ("CUT_WIRE+2strip", "Резка с зачисткой", "CUT_WIRE+2strip+tin"),
    ("SDR_USB_A_2.0", "Пайка USB", "SDR_USB_A"),
]


@pytest.mark.parametrize(("code", "stage", "fresh"), CASES)
def test_mismatch_message_is_actionable_for_machine_load_and_autoplan(code, stage, fresh):
    task = placed(make_task(22, [single(1, code, stage)]), OperationShare(ANNA, None, at(9), at(12)))
    document = etk({"code": fresh, "stage": stage, "machine": "ОБ.027.1"})

    steps, load_warnings = task_steps(task, document)
    _, plan_warnings = operation_keys(task, document)

    assert steps == {}
    for text in (load_warnings[0], plan_warnings[0]):
        assert text.startswith("ПЗ-0022 · этап 1:")
        assert f"(код {code})" in text and f"(код {fresh})" in text
        assert "ТК изменилась после запуска" in text and "Перезапустите этап из актуальной ЭТК" in text


@pytest.mark.parametrize(("code", "stage", "fresh"), CASES)
def test_task_is_planned_by_the_pinned_revision_without_warnings(code, stage, fresh):
    task = placed(make_task(29, [single(1, code, stage)]), OperationShare(ANNA, None, at(9), at(12)))
    task = _pinned(task, 1)
    old = etk({"code": code, "stage": stage, "machine": "ОБ.027.1"})
    current = etk({"code": fresh, "stage": stage, "machine": "ОБ.027.1"})
    card = CardSnapshot(uuid4(), 3, current, 2, lambda revision: old if revision == 1 else None)

    document = card.document_for(task)
    steps, load_warnings = task_steps(task, document)
    _, plan_warnings = operation_keys(task, document)

    assert document is old and load_warnings == () and plan_warnings == () and steps


def test_document_for_falls_back_to_the_current_card_when_the_revision_is_gone():
    task = _pinned(placed(make_task(5, [single(1, "OLD", "Этап")]), OperationShare(ANNA, None, at(9), at(12))), 1)
    current = etk({"code": "NEW", "stage": "Этап"})

    assert CardSnapshot(uuid4(), 1, current, 2, lambda revision: None).document_for(task) is current
    assert CardSnapshot(uuid4(), 1, current, 2).document_for(task) is current
    assert CardSnapshot(uuid4(), 1, current, 1, lambda revision: etk({"code": "X", "stage": "Э"})).document_for(task) is current


def _pinned(task, revision):
    from dataclasses import replace

    return replace(task, techcard_revision=revision)
