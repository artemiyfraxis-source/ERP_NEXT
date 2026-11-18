from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.production.marking_sources import check_materials, expand_serial, labels


def test_serial_tokens_match_browser_semantics():
    assert expand_serial("{№}/{№:1}/{№:2}/{№:3}/{№:4}/{№:5}/{№:6}", 12) == "12/12/12/012/0012/00012/000012"
    assert expand_serial("{№:0}/{№:7}/{N}/№ 1/{№:x}", 12) == "{№:0}/{№:7}/{N}/№ 1/{№:x}"


def test_quantities_order_and_explicit_override_text():
    task = SimpleNamespace(id=uuid4(), quantity=3)
    doc = {"operations": [{"fields": {"marks": [{"text": "КБ1", "qty": "2"}, {"text": "КБ-{№:3}", "qty": "1"}]}}]}
    rows = labels(doc, task, (6, 8))
    assert [row["text"] for row in rows] == ["КБ-006", "КБ-007", "КБ-008", "КБ1"]
    assert [row["quantity"] for row in rows] == [1, 1, 1, 6]
    assert labels(doc, task, (6, 8), [rows[-1]["key"]]) == rows[-1:]


def test_interleave_gives_each_item_its_set_fixed_first():
    task = SimpleNamespace(id=uuid4(), quantity=3)
    doc = {"operations": [{"fields": {"marks": [{"text": "A1 Лидар\nЗав. № FRX.{№}", "qty": "1"},
                                                {"text": "БАРСУК TELEM3", "qty": "1"}]}}]}
    rows = labels(doc, task, (1, 3), interleave=True)
    assert [row["text"] for row in rows] == ["БАРСУК TELEM3", "A1 Лидар\nЗав. № FRX.1", "БАРСУК TELEM3",
                                             "A1 Лидар\nЗав. № FRX.2", "БАРСУК TELEM3", "A1 Лидар\nЗав. № FRX.3"]
    assert [row["serial"] for row in rows] == [1, 1, 2, 2, 3, 3]
    assert all(row["quantity"] == 1 for row in rows)
    # Same labels and counts as the grouped order — only the sequence changes.
    grouped = labels(doc, task, (1, 3))
    assert sorted(row["text"] for row in rows) == sorted(row["text"] for row in grouped for _ in range(row["quantity"]))


def test_interleave_without_numbered_marks_keeps_the_grouped_order():
    task = SimpleNamespace(id=uuid4(), quantity=2)
    doc = {"operations": [{"fields": {"marks": [{"text": "XS1", "qty": "2"}]}}]}
    assert labels(doc, task, (1, 2), interleave=True) == labels(doc, task, (1, 2))


def test_interleave_respects_the_batch_limit():
    doc = {"operations": [{"fields": {"marks": [{"text": "XS1", "qty": "1"}, {"text": "№{№}", "qty": "1"}]}}]}
    with pytest.raises(ValueError, match="не более"):
        labels(doc, SimpleNamespace(id=uuid4(), quantity=251), (1, 251), interleave=True)


@pytest.mark.parametrize("quantity", ["-1", "1.5", "NaN", "Infinity", "bad", "501"])
def test_bad_or_excessive_quantities_are_not_guessed(quantity):
    with pytest.raises(ValueError):
        labels({"operations": [{"fields": {"marks": [{"text": "X", "qty": quantity}]}}]},
               SimpleNamespace(id=uuid4(), quantity=3), (1, 3))


def test_mixed_media_and_tube_marks_are_refused():
    for materials in (["A", "B"], ["Трубка ТУТ"], ["sleeve"]):
        with pytest.raises(ValueError):
            check_materials([{"material": material} for material in materials])
