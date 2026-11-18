"""Saved 708К. coax inputs switch to the short common cards (свод 3/5): plan/expect, in-transaction reissue, rollback,
nothing outside the project's coax cards."""

from copy import deepcopy
import json

import pytest

from app.workflows.etk_refresh import run_refresh
from tests.workflows.test_connector_inputs import (  # noqa: F401 - `prod` is a fixture
    BATCH, FRESH, LEGACY, NOW, LATER, _module, _quantities, _refs, _row, _snapshot, _store, _uow, prod,
)

SCRIPT = _module("etk-short-common-1118", "apply_short_cards")


def _without_flag(config):
    config = deepcopy(config)
    config["coax"].pop("shortCards", None)
    return config


def _store_production(sessions):
    """Production before this delivery: the 708К. cards without the flag, another project's identical card."""
    cards = {kb: _store(sessions, kb, _without_flag(config)) for kb, config in
             (("КБ1", FRESH["КБ1"]), ("КБ3", BATCH["КБ3"]), ("КБ28", BATCH["КБ28"]), ("КБ11", LEGACY["КБ11"]))}
    cards["other"] = _store(sessions, "КБ1", _without_flag(FRESH["КБ1"]), project="709К.")
    return cards


def _run(sessions, *, expect=None, commit=True, reissue=False):
    refs = _refs(sessions)
    with _uow(sessions) as uow:
        uow.serialise()
        out = SCRIPT.apply_short_cards(uow, who=SCRIPT.actor(), now=NOW, expect=expect, refs=refs, reissue=reissue)
        if commit:
            uow.commit()
        return out


def _rollback(sessions, data, reissue=True):
    refs = _refs(sessions)
    with _uow(sessions) as uow:
        uow.serialise()
        out = SCRIPT.rollback_short_cards(json.loads(json.dumps(data)), uow, who=SCRIPT.actor(), now=NOW, refs=refs,
                                          reissue=reissue)
        uow.commit()
        return out


def _common_steps(document):
    return [op["card"]["steps"] for op in document["operations"]
            if op["type"] in {"coaxCut", "coaxVswr"} and not op["side"]]


def test_dry_run_reports_every_708_coax_card_and_writes_nothing(prod):
    _store_production(prod)
    before = _snapshot(prod)
    result, plan, _ = _run(prod, commit=False, reissue=True)
    assert _snapshot(prod) == before  # revisions issued inside the transaction roll back too
    assert sorted(item["item_index"] for item in plan["cards"]) == ["КБ1", "КБ11", "КБ28", "КБ3"]
    assert len(result["revised"]) == 4 and not result["inputs_only"]


def test_apply_reissues_once_and_touches_nothing_else(prod):
    cards = _store_production(prod)
    _, plan, _ = _run(prod, commit=False)
    result, _, rollback = _run(prod, expect=plan, reissue=True)
    assert len(result["revised"]) == 4 and {item["item_index"] for item in rollback["cards"]} == {"КБ1", "КБ3", "КБ28", "КБ11"}
    for kb in ("КБ1", "КБ3", "КБ28", "КБ11"):
        row = _row(prod, cards[kb])
        assert row.revision == 2 and row.inputs["coax"]["shortCards"] is True
        assert {key: value for key, value in row.inputs.items() if key != "coax"} == \
            {key: value for key, value in cards[kb].inputs.items() if key != "coax"}
        assert _quantities(row.document) == _quantities(cards[kb].document)
        for steps in _common_steps(row.document):
            assert [step["kind"] for step in steps].count("Контроль") == 1
        assert _common_steps(row.document) != _common_steps(cards[kb].document)
    other = _row(prod, cards["other"])
    assert other.revision == 1 and other.inputs == cards["other"].inputs
    assert run_refresh(prod, now=LATER, everything=True).count("revised", "refreshed", "failed", "conflict") == 0
    again, plan_again, _ = _run(prod, commit=False, reissue=True)
    assert not again["changed"] and plan_again["cards"] == []


def test_apply_follows_the_reviewed_plan_or_writes_nothing(prod):
    cards = _store_production(prod)
    _, plan, _ = _run(prod, commit=False)
    with prod() as session:  # a technologist saves КБ3 after the review
        from app.modules.techcards.infrastructure import ProjectTechcardRow
        row = session.get(ProjectTechcardRow, cards["КБ3"].id)
        row.inputs, row.version = {**row.inputs, "partQty": 16}, row.version + 1
        session.commit()
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="изменилась после пробного прогона"):
        _run(prod, expect=plan, reissue=True)
    _, fresh, _ = _run(prod, commit=False)
    with pytest.raises(ValueError, match="Состав карт изменился"):
        _run(prod, expect={**fresh, "cards": fresh["cards"][1:]}, reissue=True)
    assert _snapshot(prod) == before


def test_rollback_restores_the_previous_cards_and_refuses_a_second_time(prod):
    cards = _store_production(prod)
    _, plan, _ = _run(prod, commit=False)
    _, _, rollback = _run(prod, expect=plan, reissue=True)
    result = _rollback(prod, rollback)
    assert len(result["changed"]) == 4 and len(result["revised"]) == 4
    for kb in ("КБ1", "КБ3", "КБ28", "КБ11"):
        row = _row(prod, cards[kb])
        assert row.inputs == cards[kb].inputs and row.revision == 3
        assert _common_steps(row.document) == _common_steps(cards[kb].document)
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="откат не выполнен"):
        _rollback(prod, rollback)
    assert _snapshot(prod) == before


def test_only_coax_configurations_get_the_flag():
    harness = {"mode": "harness", "ops": [{"type": "cutWire"}], "wires": []}
    assert SCRIPT.migrate_config(harness) == (harness, "не коакс-сборка")
    flagged = deepcopy(FRESH["КБ1"])
    assert SCRIPT.migrate_config(flagged)[1] == "уже короткие карты"
    migrated, skipped = SCRIPT.migrate_config(_without_flag(FRESH["КБ1"]))
    assert not skipped and migrated == FRESH["КБ1"]  # exactly what the generator writes today
