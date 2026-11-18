"""Coax VSWR control by СТО ФРАК-002-2026 (docs/data/etk-vswr-ctrl-1118): plan/expect, in-transaction reissue,
guarded template replacement, rollback."""

from copy import deepcopy
import json

import pytest

from app.modules.techcards.application import ERP_TEMPLATE_AUTHOR
from app.modules.techcards.infrastructure import OperationCardTemplateRow
from app.modules.techcards.operation_cards import parse_operation_template
from app.workflows.etk_refresh import run_refresh
from tests.workflows.test_connector_inputs import (  # noqa: F401 - `prod` is a fixture
    BATCH, DATA, LEGACY, LATER, NOW, _json, _module, _refs, _row, _snapshot, _store, _uow, prod,
)

SCRIPT = _module("etk-vswr-ctrl-1118", "apply_vswr_ctrl")
PAYLOAD = SCRIPT.load_payload()
LIBRARY = _json(DATA.parent.parent / "backend/data/techcard-library-erp.json")["templates"]


def _old_template(sessions, *, saved_by=ERP_TEMPLATE_AUTHOR):
    """Production before this delivery: the 22.10 template (S11 with a load, 30 min) saved by the ERP seed."""
    card = parse_operation_template(source_id=PAYLOAD["id"], title=PAYLOAD["title"], category=PAYLOAD["category"],
                                    rows=PAYLOAD["before_rows"], images=None)
    with _uow(sessions) as uow:
        uow.operation_cards.save_sheet(card, description="", saved_at=NOW, saved_by=saved_by)
        uow.commit()


def _production(sessions):
    _old_template(sessions)
    cards = {"short": _store(sessions, "КБ3", deepcopy(BATCH["КБ3"])),
             "plain": _store(sessions, "КБ11", deepcopy(LEGACY["КБ11"]), project="447К.9")}
    run_refresh(sessions, now=NOW)  # the template write above is history
    return cards


def _run(sessions, *, expect=None, commit=True, reissue=False):
    refs = _refs(sessions)
    with _uow(sessions) as uow:
        uow.serialise()
        out = SCRIPT.apply_vswr_ctrl(uow, who=SCRIPT.actor(), now=NOW, refs=refs, expect=expect, reissue=reissue)
        if commit:
            uow.commit()
        return out


def _rollback(sessions, data, reissue=True):
    refs = _refs(sessions)
    with _uow(sessions) as uow:
        uow.serialise()
        out = SCRIPT.rollback_vswr_ctrl(json.loads(json.dumps(data, default=str)), uow, who=SCRIPT.actor(), now=NOW,
                                        refs=refs, reissue=reissue)
        uow.commit()
        return out


def _vswr_steps(document):
    [op] = [op for op in document["operations"] if op["type"] == "coaxVswr"]
    return op["card"]["steps"]


def test_payload_is_the_bundled_template():
    [bundled] = [item for item in LIBRARY if item["id"] == PAYLOAD["id"]]
    assert bundled["rows"] == PAYLOAD["rows"] and PAYLOAD["before"] != PAYLOAD["after"]
    text = json.dumps(PAYLOAD["before_rows"], ensure_ascii=False)
    assert "30 мин" in text and "(S11)" in text


def test_dry_run_plans_the_vswr_cards_and_writes_nothing(prod):
    _production(prod)
    before = _snapshot(prod)
    result, plan, _ = _run(prod, commit=False, reissue=True)
    assert _snapshot(prod) == before
    assert result["template"][0].startswith("CTRL_VSWR_coax: заменён") and not result["refused"]
    assert sorted((item["project"], item["item_index"]) for item in plan["cards"]) == [("447К.9", "КБ11"), ("708К.", "КБ3")]
    assert len(result["revised"]) == 2


def test_apply_reissues_both_ends_once(prod):
    cards = _production(prod)
    _, plan, _ = _run(prod, commit=False)
    result, _, rollback = _run(prod, expect=plan, reissue=True)
    assert len(result["revised"]) == 2 and rollback["template"]["id"] == PAYLOAD["id"]
    for key in ("short", "plain"):
        row = _row(prod, cards[key])
        assert row.revision == 2
        steps = _vswr_steps(row.document)
        text = " ".join(step["text"] for step in steps)
        assert [step["kind"] for step in steps].count("Контроль") == 1
        assert "не менее 40 мин" in text and "30 мин" not in text and "S22" in text
        assert any(step["tolerance"] == "N — 1,1–1,5 Н·м; 3,5 мм и SMA — 0,8–1,0 Н·м" for step in steps)
    plain = " ".join(step["text"] for step in _vswr_steps(_row(prod, cards["plain"]).document))
    assert "по СТО ФРАК-002-2026" in plain and "навернуть согласованную нагрузку" not in plain
    with prod() as session:
        assert session.get(OperationCardTemplateRow, PAYLOAD["id"]).saved_by == ERP_TEMPLATE_AUTHOR
    assert run_refresh(prod, now=LATER, everything=True).count("revised", "refreshed", "failed", "conflict") == 0
    again, plan_again, _ = _run(prod, commit=False, reissue=True)
    assert "без изменений" in again["template"][0] and plan_again["cards"] == []


def test_apply_follows_the_reviewed_plan_or_writes_nothing(prod):
    cards = _production(prod)
    _, plan, _ = _run(prod, commit=False)
    with prod() as session:  # a technologist saves КБ3 after the review
        from app.modules.techcards.infrastructure import ProjectTechcardRow
        row = session.get(ProjectTechcardRow, cards["short"].id)
        row.version += 1
        session.commit()
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="изменилась после пробного прогона"):
        _run(prod, expect=plan, reissue=True)
    _, fresh, _ = _run(prod, commit=False)
    with pytest.raises(ValueError, match="Состав карт изменился"):
        _run(prod, expect={**fresh, "cards": fresh["cards"][1:]}, reissue=True)
    assert _snapshot(prod) == before


def test_a_template_that_is_not_the_earlier_delivery_is_refused(prod):
    _production(prod)
    with prod() as session:
        row = session.get(OperationCardTemplateRow, PAYLOAD["id"])
        row.raw_rows = [*row.raw_rows[:-1], ["своя строка"]]
        session.commit()
    result, plan, rollback = _run(prod, commit=False)
    assert result["refused"] and rollback["template"] is None
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="не прежняя поставка"):
        _run(prod, expect=plan, reissue=True)
    assert _snapshot(prod) == before


def test_rollback_restores_the_template_and_refuses_a_second_time(prod):
    cards = _production(prod)
    _, plan, _ = _run(prod, commit=False)
    _, _, rollback = _run(prod, expect=plan, reissue=True)
    result = _rollback(prod, rollback)
    assert result["template"] and len(result["revised"]) == 2
    with prod() as session:
        row = session.get(OperationCardTemplateRow, PAYLOAD["id"])
        assert SCRIPT.rows_hash(row.raw_rows) == PAYLOAD["before"] and row.saved_by == ERP_TEMPLATE_AUTHOR
    plain = " ".join(step["text"] for step in _vswr_steps(_row(prod, cards["plain"]).document))
    assert "не менее 30 мин" in plain and _row(prod, cards["plain"]).revision == 3
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="откат не выполнен"):
        _rollback(prod, rollback)
    assert _snapshot(prod) == before
