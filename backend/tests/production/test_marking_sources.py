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


@pytest.mark.parametrize("quantity", ["-1", "1.5", "NaN", "Infinity", "bad", "501"])
def test_bad_or_excessive_quantities_are_not_guessed(quantity):
    with pytest.raises(ValueError):
        labels({"operations": [{"fields": {"marks": [{"text": "X", "qty": quantity}]}}]},
               SimpleNamespace(id=uuid4(), quantity=3), (1, 3))


def test_mixed_media_and_tube_marks_are_refused():
    for materials in (["A", "B"], ["Трубка ТУТ"], ["sleeve"]):
        with pytest.raises(ValueError):
            check_materials([{"material": material} for material in materials])
