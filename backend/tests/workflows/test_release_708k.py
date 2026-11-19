"""Scoped 708К. preview/reissue (свод 5/5): identity by manifest, plan/expect, one revision wave, idempotent second
pass, nothing outside the reviewed cards."""

from copy import deepcopy
import json

import pytest

from app.modules.techcards.infrastructure import ProjectTechcardRow
from tests.workflows.test_connector_inputs import (  # noqa: F401 - `prod` is a fixture
    DATA, FRESH, LEGACY, NOW, _json, _module, _row, _snapshot, _store, _uow, _refs, prod,
)
from tests.workflows.test_pin_coax_708 import SCRIPT as PIN, PAYLOAD as PIN_PAYLOAD

SCRIPT = _module("etk-708k-release-1119", "release_708k")
MANIFEST = _json(DATA / "etk-708k-release-1119/manifest.json")
SMALL = {**MANIFEST, "expected": [item for item in MANIFEST["expected"] if item["kb"] in ("КБ1", "КБ11")]}


def _run(sessions, manifest=SMALL, *, expect=None, reissue=False, commit=True):
    refs = _refs(sessions)
    with _uow(sessions) as uow:
        uow.serialise()
        report = SCRIPT.release(uow, refs=refs, now=NOW, manifest=manifest, expect=expect, reissue=reissue)
        if commit:
            uow.commit()
        return json.loads(json.dumps(report))


def _pin_inputs_only(sessions):
    """The pin delivery committed without its own reissue: КБ11 inputs moved, the revision still pending."""
    refs = _refs(sessions)
    with _uow(sessions) as uow:
        uow.serialise()
        PIN.apply_pin_coax(uow, who=PIN.actor(), now=NOW, payload=deepcopy(PIN_PAYLOAD), refs=refs)
        uow.commit()


def _production(sessions):
    cards = {"КБ1": _store(sessions, "КБ1", FRESH["КБ1"]), "КБ11": _store(sessions, "КБ11", LEGACY["КБ11"]),
             "КБ41": _store(sessions, "КБ41", FRESH["КБ1"]),
             # One КБ per spelling of the project number; «708К» and «708К.» are the same project.
             "harness": _store(sessions, "КБ1", FRESH["КБ1"], project="708К"),
             "other": _store(sessions, "КБ11", LEGACY["КБ11"], project="709К.")}
    with sessions() as session:  # the Orange Pi harness shares «КБ1» but is not a coax card
        session.get(ProjectTechcardRow, cards["harness"].id).inputs = {"mode": "harness", "ops": []}
        session.commit()
    return cards


def test_manifest_is_the_reviewed_40_coax_assemblies():
    kbs = [item["kb"] for item in MANIFEST["expected"]]
    assert kbs == [f"КБ{n}" for n in range(1, 41)]
    families = {item["kb"]: (item["ends"]["A"], item["ends"]["B"]) for item in MANIFEST["expected"]}
    assert families["КБ11"] == families["КБ14"] == ("73403-6270", "901-9610-3")
    assert {item["kb"] for item in MANIFEST["excluded"]} == {"КБ1", "КБ41"}
    assert all(b"\r\n" not in path.read_bytes() for path in (DATA / "etk-708k-release-1119").iterdir() if path.is_file())


def test_preview_reads_ids_and_writes_nothing(prod):
    cards = _production(prod)
    _pin_inputs_only(prod)
    before = _snapshot(prod)
    report = _run(prod, commit=False)
    assert _snapshot(prod) == before
    assert [(entry["item_index"], entry["id"], entry["status"]) for entry in report["plan"]] == [
        ("КБ11", str(cards["КБ11"].id), "revised")]
    assert report["counts"] == {"revised": 1, "refreshed": 0, "unchanged": 1, "conflict": 0}
    assert [line.split(" ")[0] for line in report["excluded"]] == ["КБ1"]
    assert [line.split(" ")[0] for line in report["outside"]] == ["КБ41"]
    assert not report["problems"]


def test_reissue_issues_exactly_the_plan_once(prod):
    cards = _production(prod)
    _pin_inputs_only(prod)
    plan = _run(prod, commit=False)
    report = _run(prod, expect=plan, reissue=True)
    assert len(report["issued"]) == 1 and "ревизия 2" in report["issued"][0]
    assert _row(prod, cards["КБ11"]).revision == 2
    for key in ("КБ1", "КБ41", "harness", "other"):
        assert _row(prod, cards[key]).revision == 1
    again = _run(prod, commit=False)
    assert again["plan"] == [] and again["counts"]["unchanged"] == 2


def test_reissue_refuses_a_changed_card_an_identity_problem_or_no_plan(prod):
    cards = _production(prod)
    _pin_inputs_only(prod)
    plan = _run(prod, commit=False)
    with prod() as session:
        row = session.get(ProjectTechcardRow, cards["КБ11"].id)
        row.version += 1
        session.commit()
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="изменилась после пробного прогона"):
        _run(prod, expect=plan, reissue=True)
    with pytest.raises(ValueError, match="--expect"):
        _run(prod, reissue=True)
    assert _snapshot(prod) == before
    # A second coax card with the same КБ (another spelling of the project): never «the latest one».
    _store(prod, "КБ11", LEGACY["КБ11"], project="708К")
    report = _run(prod, commit=False)
    assert any(line.startswith("КБ11: ЭТК «Коакс» 2") for line in report["problems"])
    with pytest.raises(ValueError, match="Выпуск остановлен"):
        _run(prod, expect=report, reissue=True)


def test_wrong_end_articles_stop_the_release(prod):
    _production(prod)
    manifest = deepcopy(SMALL)
    manifest["expected"][1]["ends"]["B"] = "SMA-OTHER"
    report = _run(prod, manifest, commit=False)
    assert [line.split(" ")[0] for line in report["problems"]] == ["КБ11"]
    assert all(entry["item_index"] != "КБ11" for entry in report["cards"])
