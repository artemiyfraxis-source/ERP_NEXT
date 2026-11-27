"""Заводской номер изделия FRX.<проект>.<изделие>.<k> (владелец 27.11.2026): формат, изделие из ЭТК, счёт номеров."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.production.domain import MarkEvent, mark_serial_range
from app.modules.production.marking_sources import serial_designation
from app.modules.production.traceability import (clean_designation, default_designation, factory_number,
                                                 factory_number_key)

T0 = datetime(2026, 11, 27, 9, tzinfo=UTC)


def _task(number, item="КБ1", quantity=2, project="831К.", minutes=0):
    return SimpleNamespace(id=uuid4(), number=number, item=item, quantity=quantity, project=project,
                           created_at=T0 + timedelta(minutes=minutes))


@pytest.mark.parametrize(("project", "designation", "k", "expected"), [
    ("831К.", "КБ1", 3, "FRX.831К.КБ1.3"),
    ("831K.", "КБ1", 3, "FRX.831К.КБ1.3"),            # латинская K проекта — кириллицей
    ("831К.", "34.226", 7, "FRX.831К.34.226.7"),      # своё обозначение НПК РТС
    ("708К.4", "120907/AA", 12, "FRX.708К.4.120907/AA.12"),  # PN заказчика 708К.
    ("708К.4", " 120907/AA. ", 1, "FRX.708К.4.120907/AA.1"),
])
def test_factory_number_formats(project, designation, k, expected):
    assert factory_number(project, designation, k) == expected


@pytest.mark.parametrize(("project", "designation", "k"), [
    ("", "КБ1", 1), ("831К.", "", 1), ("831К.", "КБ1", 0), ("831К.", "34 226,1", 1), ("831К.", "a" * 65, 1),
])
def test_no_factory_number_without_project_designation_or_k(project, designation, k):
    assert factory_number(project, designation, k) is None


def test_designation_cleaning_and_default_kb():
    assert clean_designation(" 34.226 ") == "34.226" and clean_designation(".КБ1.") == "КБ1"
    assert clean_designation("A B") == "AB" and clean_designation("№1") is None and clean_designation("") is None
    assert default_designation("591K.3.КБ1") == "КБ1" and default_designation("KB-2") == "КБ2"
    assert default_designation("ПФ2") is None


@pytest.mark.parametrize(("scan", "stored"), [
    ("FRX.831К.КБ1.3", "FRX.831К.КБ1.3"),
    ("frx.831k.kb1.03", "FRX.831К.КБ1.3"),
    (" FRX.831K.КБ1.3 ", "FRX.831К.КБ1.3"),
    ("FRX.831К.34.226.7", "FRX.831К.34.226.7"),
    ("frx.708k.4.120907/aa.012", "FRX.708К.4.120907/AA.12"),
])
def test_scan_key_matches_the_stored_number(scan, stored):
    assert factory_number_key(scan) == factory_number_key(stored) is not None


def test_scan_key_keeps_dots_slashes_and_k_apart():
    key = factory_number_key("FRX.831К.34.226.7")
    assert key[1] == 7
    assert factory_number_key("FRX.831К.34.22.67") != key
    assert factory_number_key("FRX.708К.4.120907/AA.12") != factory_number_key("FRX.708К.4.120907.AA.12")
    assert factory_number_key("ПЗ-0001-0003") is None and factory_number_key("FRX.831К.КБ1.0") is None


def _doc(*fields):
    return {"operations": [{"fields": item} for item in fields]}


def test_designation_comes_from_the_marking_operation_then_the_template_then_kb():
    task = SimpleNamespace(project="708К.4", item="КБ5")
    assert serial_designation(_doc({"serial": {"product": "120907/AA", "template": "FRX.708К.4.КБ5.{№}"}}), task) == "120907/AA"
    assert serial_designation(_doc({"serial": {"designation": "34.226"}}), task) == "34.226"
    assert serial_designation(_doc({"serial": {"template": "FRX.708K.4.120907/AA.{№}"}}), task) == "120907/AA"
    assert serial_designation(_doc({}, {"marks": [{"text": "FRX.708К.4.34.226.{№}"}]}), task) == "34.226"
    assert serial_designation(_doc({"marks": [{"text": "КБ-{№:3}"}]}), task) == "КБ5"
    assert serial_designation(_doc({"marks": [{"text": "FRX.999К.X1.{№}"}]}), task) == "КБ5"  # чужой проект — не шаблон
    assert serial_designation(_doc(), SimpleNamespace(project="708К.4", item="ПФ2")) is None


def test_stored_numbers_are_held_forever_and_others_go_around_them():
    """Напечатанное задание держит свои номера и после «отменено без работы»; новое берёт следующие."""
    first, second = _task("ПЗ-0001"), _task("ПЗ-0002", minutes=5)
    released = [MarkEvent(first.id, T0 + timedelta(minutes=1), True, "ПЗ-0001")]
    assert mark_serial_range(second, [first, second], released) == (1, 2)       # прежнее правило: освобождены
    stored = {first.id: (1, 2, "КБ1")}
    assert mark_serial_range(first, [first, second], released, stored) == (1, 2)
    assert mark_serial_range(second, [first, second], released, stored) == (3, 4)


def test_count_runs_per_project_and_designation_across_items():
    """Два КБ с одним своим обозначением — один счёт: напечатанные номера одного обходит другой."""
    kb1, kb2 = _task("ПЗ-0001", item="КБ1"), _task("ПЗ-0002", item="КБ2", minutes=5)
    stored = {kb1.id: (1, 2, "34.226")}
    assert mark_serial_range(kb2, [kb1, kb2], (), stored, "34.226") == (3, 4)
    assert mark_serial_range(kb2, [kb1, kb2], (), stored, "КБ2") == (1, 2)      # своё изделие — свой счёт
    assert mark_serial_range(kb2, [kb1, kb2], (), stored) == (1, 2)
