"""708К. без контроля КСВН (владелец 19.11.2026: нет переходников к ВАЦ): входные данные генератора — «не проверять»,
новая ревизия ЭТК без операции КСВН с причиной владельца, план/ожидание/откат, вне 708К. «Коакс» ничего не трогается."""

from copy import deepcopy
import json

import pytest
from sqlalchemy import select

from app.modules.techcards.infrastructure import ProjectTechcardRow, TechcardRevisionRow
from tests.workflows.test_connector_inputs import (  # noqa: F401 - `prod` is a fixture
    DATA, FRESH, LEGACY, NOW, _module, _refs, _row, _snapshot, _store, _uow, prod,
)

SCRIPT = _module("etk-708k-novswr-1119", "no_vswr_708k")


def _run(sessions, *, expect=None, commit=True):
    refs = _refs(sessions)
    with _uow(sessions) as uow:
        uow.serialise()
        report, plan, rollback = SCRIPT.no_vswr(uow, who=SCRIPT.actor(), now=NOW, refs=refs, expect=expect)
        if commit:
            uow.commit()
        return json.loads(json.dumps(report)), json.loads(json.dumps(plan)), json.loads(json.dumps(rollback))


def _with_form(config):
    """Saved inputs carry the generator form; «Контроль КСВН: все» as on production."""
    config = deepcopy(config)
    config["coax"]["vswrControl"] = {"mode": "all", "every": None}
    config["gen_state"] = {"S": {"coax": {"vswrMode": "all", "vswrEvery": "50"}}, "v": 1}
    return config


def _production(sessions):
    cards = {"КБ1": _store(sessions, "КБ1", _with_form(FRESH["КБ1"])),
             "КБ11": _store(sessions, "КБ11", _with_form(LEGACY["КБ11"])),
             "other": _store(sessions, "КБ1", _with_form(FRESH["КБ1"]), project="709К."),
             "harness": _store(sessions, "КБ2", FRESH["КБ2"])}
    with sessions() as session:  # the Orange Pi harness of 708К. is not a coax card
        session.get(ProjectTechcardRow, cards["harness"].id).inputs = {"mode": "harness", "ops": []}
        session.commit()
    return cards


def _codes(document):
    return [str(op.get("code") or op.get("template_id") or "") for op in document.get("operations") or []]


def _has_vswr(document):
    return any("VSWR" in code for code in _codes(document))


def test_inputs_are_what_the_generator_saves_for_no_check():
    inputs, changes = SCRIPT.without_vswr(_with_form(FRESH["КБ1"]))
    assert inputs["coax"]["vswrControl"]["mode"] == "none" and "переходников" in inputs["coax"]["vswrControl"]["reason"]
    assert inputs["gen_state"]["S"]["coax"]["vswrMode"] == "none"
    assert not any(op.get("type") == "coaxVswr" for op in inputs["ops"])
    assert len(inputs["ops"]) == len(FRESH["КБ1"]["ops"]) - 1 and len(changes) == 3
    assert SCRIPT.without_vswr(inputs) == (inputs, [])


def test_dry_run_writes_nothing_and_prints_every_change(prod, capsys):
    cards = _production(prod)
    before = _snapshot(prod)
    report, plan, _rollback = _run(prod, commit=False)
    printed = capsys.readouterr().out
    assert printed.count("изменения ЭТК:") == 2 and "Контроль КСВН" in printed and ": Операция: «есть» → «»" in printed
    assert _snapshot(prod) == before
    assert [item["item_index"] for item in plan["cards"]] == ["КБ1", "КБ11"]
    assert len(report["issued"]) == 2 and all("revised" in line for line in report["issued"])
    assert any(line.startswith(f"КБ2 [{cards['harness'].id}]") for line in report["kept"])


def test_apply_issues_a_revision_without_the_check_once(prod):
    cards = _production(prod)
    assert all(_has_vswr(_row(prod, cards[key]).document) for key in ("КБ1", "КБ11", "other"))
    _report, plan, _rollback = _run(prod, commit=False)
    report, _plan, _ = _run(prod, expect=plan)
    assert len(report["issued"]) == 2
    for key in ("КБ1", "КБ11"):
        row = _row(prod, cards[key])
        assert row.revision == 2 and not _has_vswr(row.document)
        # No coax references in this fixture: no norm, the control reads from build_config (coax_vswr_api).
        assert row.document["build_config"]["coax"]["vswrControl"]["mode"] == "none"
    with prod() as session:
        reasons = session.scalars(select(TechcardRevisionRow.reason).where(
            TechcardRevisionRow.project_card_id == cards["КБ11"].id, TechcardRevisionRow.revision == 2)).all()
    assert reasons == [f"{SCRIPT.REASON} (18.11.2026)"]
    assert _row(prod, cards["other"]).revision == 1 and _has_vswr(_row(prod, cards["other"]).document)
    assert _row(prod, cards["harness"]).revision == 1
    again, plan_again, _ = _run(prod, commit=False)
    assert plan_again["cards"] == [] and again["issued"] == []


def test_a_changed_card_refuses_the_plan(prod):
    cards = _production(prod)
    _report, plan, _rollback = _run(prod, commit=False)
    with prod() as session:
        session.get(ProjectTechcardRow, cards["КБ11"].id).version += 1
        session.commit()
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="изменилась после пробного прогона"):
        _run(prod, expect=plan)
    assert _snapshot(prod) == before


def test_rollback_restores_the_inputs_and_the_check(prod):
    cards = _production(prod)
    _report, plan, rollback = _run(prod, commit=False)
    _run(prod, expect=plan)
    refs = _refs(prod)
    with _uow(prod) as uow:
        uow.serialise()
        SCRIPT.rollback_inputs(rollback, uow, who=SCRIPT.actor(), now=NOW, refs=refs)
        uow.commit()
    row = _row(prod, cards["КБ11"])
    saved = next(item["inputs"] for item in rollback["cards"] if item["id"] == str(cards["КБ11"].id))
    assert row.revision == 3 and _has_vswr(row.document) and row.inputs == saved
    assert saved["coax"]["vswrControl"] == {"mode": "all", "every": None}
    assert (DATA / "etk-708k-novswr-1119/no_vswr_708k.py").read_bytes().count(b"\r\n") == 0


def test_any_other_change_refuses_the_run(prod, monkeypatch):
    _production(prod)
    plain = SCRIPT.without_vswr

    def also_longer(inputs):  # a run whose inputs would also move the cut length
        changed, changes = plain(inputs)
        if changes:
            changed["coax"]["cableLength"] = float(changed["coax"].get("cableLength") or 0) + 100
        return changed, changes

    monkeypatch.setattr(SCRIPT, "without_vswr", also_longer)
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="меняется не только «Контроль КСВН»"):
        _run(prod, commit=False)
    assert _snapshot(prod) == before


def test_only_the_vswr_operation_and_its_scheme_slot_are_expected():
    vswr = {"code": "CTRL_VSWR_coax", "position": 3, "result": [{"name": "Сборка 122664/AA", "norm": "15", "unit": "Шт."}]}
    heat = {"code": "HEAT_TYT_short", "position": 2, "result": [{"name": "Кабель с ТУТ", "norm": "15", "unit": "Шт."}]}
    heat_final = {**heat, "result": [{"name": "Сборка 122664/AA", "norm": "15", "unit": "Шт."}]}
    cut = {"code": "CUT_COAX", "position": 1}
    old = {"operations": [cut, heat, vswr], "route": {"blocks": [{"key": "final", "label": "1", "name": "Сборка", "ops": [1, 2, 3]}]}}
    new = {"operations": [cut, heat_final], "route": {"blocks": [{"key": "final", "label": "1", "name": "Сборка", "ops": [1, 2]}]}}
    gone = {"operation_key": "CTRL_VSWR_coax::::", "label": "Контроль КСВН", "field": "Операция", "old": "есть", "new": ""}
    scheme = {"operation_key": "", "label": "Схема ПФ", "field": "Схема ПФ", "old": "a", "new": "b"}
    result = {"operation_key": "HEAT_TYT_short::::", "label": "Усадка", "field": "Результат", "old": "a", "new": "b"}
    assert SCRIPT.unexpected_changes(old, new, [gone, scheme, result]) == []
    more = {"operations": [cut, {**heat, "result": [{"name": "Сборка 122664/AA", "norm": "16", "unit": "Шт."}]}],
            "route": new["route"]}
    assert SCRIPT.unexpected_changes(old, more, [gone, result]) == [result]
    other = {"operation_key": "HEAT_TYT_short::::", "label": "Усадка", "field": "Время", "old": "1", "new": "2"}
    assert SCRIPT.unexpected_changes(old, new, [gone, other]) == [other]
    moved = {**new, "route": {"blocks": [{"key": "final", "label": "1", "name": "Сборка", "ops": [2]}]}}
    assert SCRIPT.unexpected_changes(old, moved, [gone, scheme]) == [scheme]
