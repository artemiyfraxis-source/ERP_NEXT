"""Own centre-contact crimp card PRS_PIN_coax for 708К. ordinary coax (свод 4/5): card + БД.ОП row and the saved-route
correction in one transaction, plan/expect, in-transaction reissue, rollback, nothing outside the project."""

from copy import deepcopy
import json

import pytest

from app.modules.techcards.application import SeedErpTemplatesCommand, seed_erp_templates
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.workflows.etk_refresh import run_refresh
from tests.workflows.test_connector_inputs import (  # noqa: F401 - `prod` is a fixture
    DATA, FRESH, LATER, LEGACY, NOW, ROOT, _json, _module, _row, _snapshot, _store, _uow, _refs, prod,
)

SCRIPT = _module("etk-pin-coax-1119", "apply_pin_coax")
PAYLOAD = _json(DATA / "etk-pin-coax-1119/payload.json")
ERP_PIN = [item for item in _json(ROOT / "backend/data/techcard-library-erp.json")["templates"]
           if item["title"].startswith("PRS_PIN_coax |")]


def _store_production(sessions):
    """КБ11 as issued (crimped pin on the shield card), a straight FAKRA КБ1, the same КБ11 in another project."""
    return {"КБ11": _store(sessions, "КБ11", LEGACY["КБ11"]), "КБ1": _store(sessions, "КБ1", FRESH["КБ1"]),
            "other": _store(sessions, "КБ11", LEGACY["КБ11"], project="709К.")}


def _run(sessions, *, expect=None, commit=True, reissue=False):
    refs = _refs(sessions)
    with _uow(sessions) as uow:
        uow.serialise()
        out = SCRIPT.apply_pin_coax(uow, who=SCRIPT.actor(), now=NOW, payload=deepcopy(PAYLOAD), expect=expect,
                                    refs=refs, reissue=reissue)
        if commit:
            uow.commit()
        return out


def _ops(document, code=None, kind=None):
    return [op for op in document["operations"] if (code is None or op["code"] == code) and (kind is None or op["type"] == kind)]


def _text(op):
    card = op["card"]
    return " ".join([*(step["text"] + " " + step["tolerance"] for step in card["steps"]), *(tool["name"] for tool in card["tooling"])])


def test_dry_run_creates_nothing_and_plans_only_the_708_ordinary_coax(prod):
    _store_production(prod)
    before = _snapshot(prod)
    cards, result, plan, _ = _run(prod, commit=False, reissue=True)
    assert _snapshot(prod) == before  # the card, its row, the revisions — all rolled back
    assert cards["created"] == ["PRS_PIN_coax"] and cards["rows_created"] == ["PRS_PIN_coax"]
    assert [item["item_index"] for item in plan["cards"]] == ["КБ11"]
    assert plan["cards"][0]["changes"] == ["coaxPinCrimpA: PRS_SLEEVE_coax → PRS_PIN_coax"]
    assert len(result["revised"]) == 1 and not result["inputs_only"] and not result["protected"]
    assert result["other_projects"] == ["709К. КБ11"]


def test_apply_moves_the_pin_to_its_own_card_and_keeps_the_shield_crimp(prod):
    cards = _store_production(prod)
    _, _, plan, _ = _run(prod, commit=False)
    _, result, _, rollback = _run(prod, expect=plan, reissue=True)
    assert len(result["revised"]) == 1 and [item["item_index"] for item in rollback["cards"]] == ["КБ11"]
    row, old = _row(prod, cards["КБ11"]), cards["КБ11"]
    assert row.revision == 2
    pin = [op for op in row.inputs["ops"] if op["type"] == "coaxPinCrimp"]
    assert [(op["opNum"], op["templateId"], op["tPrep"], op["tOp"], op["tMachine"], "equipmentTools" in op) for op in pin] == [
        ("PRS_PIN_coax", "prs-pin-coax-обжим-центрального-контакта", "", "", "", False)]
    # Nothing else in the saved inputs moved.
    strip = lambda config: [op for op in config["ops"] if op["type"] != "coaxPinCrimp"]  # noqa: E731
    assert strip(row.inputs) == strip(old.inputs) and row.inputs["coax"] == old.inputs["coax"]
    # The pin operation: own card, no shield HEX, no press or hex dies; its input is still the side-A connector.
    (crimp,) = _ops(row.document, kind="coaxPinCrimp")
    assert crimp["code"] == "PRS_PIN_coax" and crimp["side"] == "A"
    assert "HEX 3" not in _text(crimp) and "EW-2550" in _text(crimp) and "SR-16M" not in _text(crimp) and "Шестигранные" not in _text(crimp)
    assert "Сдвинуть обжимную гильзу" not in _text(crimp) and "не задано" in _text(crimp)
    assert [step["kind"] for step in crimp["card"]["steps"]].count("Контроль") == 1
    (old_crimp,) = _ops(old.document, kind="coaxPinCrimp")
    assert old_crimp["code"] == "PRS_SLEEVE_coax" and crimp["components"] == old_crimp["components"]
    # The shield crimp stays PRS_SLEEVE_coax for both ends with its HEX.
    (shield,) = _ops(row.document, kind="coaxShield")
    assert shield["code"] == "PRS_SLEEVE_coax" and "HEX 3,25 мм" in _text(shield)
    assert shield["card"] == _ops(old.document, kind="coaxShield")[0]["card"]
    # Housings: the crimped side A no longer says «soldered»; side B (soldered SMA) keeps its text and depth.
    housing = {op["side"]: _text(op) for op in _ops(row.document, kind="coaxHousing")}
    assert "обжатый центральный контакт" in housing["A"] and "припаянный" not in housing["A"]
    assert "припаянный центральный контакт" in housing["B"] and "1,88…2,23 мм" in housing["B"]
    assert [op["code"] for op in row.document["operations"]] == [
        "PRS_PIN_coax" if op["type"] == "coaxPinCrimp" else op["code"] for op in old.document["operations"]]
    # Other cards and other projects are untouched; a second pass and a later refresh change nothing.
    for key in ("КБ1", "other"):
        assert _row(prod, cards[key]).revision == 1 and _row(prod, cards[key]).inputs == cards[key].inputs
    assert run_refresh(prod, now=LATER, everything=True).count("revised", "refreshed", "failed", "conflict") == 0
    again_cards, again, plan_again, _ = _run(prod, commit=False, reissue=True)
    assert again_cards["unchanged"] == ["PRS_PIN_coax"] and again_cards["rows_kept"] == ["PRS_PIN_coax"]
    assert not again["changed"] and plan_again["cards"] == []


def test_the_seeded_card_is_the_same_identity_and_the_row_is_still_created(prod):
    seed_erp_templates(SeedErpTemplatesCommand(templates=tuple(ERP_PIN), now=NOW, actor=SCRIPT.actor()),
                       SqlAlchemyTechcardsUnitOfWork(prod))
    _store_production(prod)
    cards, result, _, _ = _run(prod, commit=False, reissue=True)
    assert cards["unchanged"] == ["PRS_PIN_coax"] and cards["rows_created"] == ["PRS_PIN_coax"]
    assert len(result["revised"]) == 1


def test_hand_edited_cards_are_protected(prod):
    cards = _store_production(prod)
    with prod() as session:
        from app.modules.techcards.infrastructure import ProjectTechcardRow
        row = session.get(ProjectTechcardRow, cards["КБ11"].id)
        row.manual_operation_keys = ["coaxPinCrimpA"]
        session.commit()
    _, result, plan, _ = _run(prod, commit=False)
    assert plan["cards"] == [] and [line.split(" ")[0] for line in result["protected"]] == ["КБ11"]


def test_apply_follows_the_reviewed_plan_or_writes_nothing(prod):
    cards = _store_production(prod)
    _, _, plan, _ = _run(prod, commit=False)
    with prod() as session:
        from app.modules.techcards.infrastructure import ProjectTechcardRow
        row = session.get(ProjectTechcardRow, cards["КБ11"].id)
        row.inputs, row.version = {**row.inputs, "partQty": 16}, row.version + 1
        session.commit()
    before = _snapshot(prod)
    with pytest.raises(ValueError, match="изменилась после пробного прогона"):
        _run(prod, expect=plan, reissue=True)
    with pytest.raises(ValueError, match="Состав карт изменился"):
        _run(prod, expect={**plan, "cards": []}, reissue=True)
    assert _snapshot(prod) == before


def test_rollback_restores_the_routes_and_refuses_a_second_time(prod):
    cards = _store_production(prod)
    _, _, plan, _ = _run(prod, commit=False)
    _, _, _, rollback = _run(prod, expect=plan, reissue=True)
    refs = _refs(prod)
    with _uow(prod) as uow:
        uow.serialise()
        result = SCRIPT.rollback_pin_coax(json.loads(json.dumps(rollback)), uow, who=SCRIPT.actor(), now=NOW, refs=refs,
                                          reissue=True)
        uow.commit()
    assert len(result["changed"]) == 1 and len(result["revised"]) == 1
    row = _row(prod, cards["КБ11"])
    assert row.inputs == cards["КБ11"].inputs and row.revision == 3
    assert [op["code"] for op in row.document["operations"]] == [op["code"] for op in cards["КБ11"].document["operations"]]
    with _uow(prod) as uow, pytest.raises(ValueError, match="откат не выполнен"):
        uow.serialise()
        SCRIPT.rollback_pin_coax(rollback, uow, who=SCRIPT.actor(), now=NOW)


def test_only_crimped_pins_on_the_shield_card_move():
    code_of = lambda op: op.get("opNum", "")  # noqa: E731
    harness = {"mode": "harness", "ops": [{"type": "cutWire"}]}
    assert SCRIPT.migrate_config(harness, code_of, "pin", {})[2] == "не коакс-сборка"
    fakra = deepcopy(FRESH["КБ1"])
    assert SCRIPT.migrate_config(fakra, code_of, "pin", {}) == (fakra, [], "нет обжима пина картой экрана")
    migrated, changes, skipped = SCRIPT.migrate_config(LEGACY["КБ11"], code_of, "pin", {"tPrep": "", "tOp": "", "tMachine": ""})
    assert not skipped and changes == ["coaxPinCrimpA: PRS_SLEEVE_coax → PRS_PIN_coax"]
    assert [op["opNum"] for op in migrated["ops"] if op["type"] == "coaxShield"] == ["PRS_SLEEVE_coax"]


def test_payload_matches_the_library_seed_and_keeps_unknowns_visible():
    from app.modules.techcards.operation_cards import normalize_record_edit, parse_operation_library
    from app.modules.techcards.template_sheets import make_template_id

    (item,) = PAYLOAD
    (card,) = parse_operation_library(ERP_PIN)
    edit = normalize_record_edit(item["record"])
    assert card.source_id == make_template_id(f"{item['code']} | {edit.name}", ())
    assert (card.name, card.safety, card.defects, card.revision, card.steps, card.tooling) == (
        edit.name, edit.safety, edit.defects, edit.revision, edit.steps, edit.tooling)
    assert not any(tool.inventory_number for tool in card.tooling) and set(item["create"]["row"]["norms"].values()) == {""}
    assert "не задано" in card.tooling[0].name and "HEX" not in card.steps[1].text.replace("(HEX) не применять", "")
    folder = DATA / "etk-pin-coax-1119"
    assert all(b"\r\n" not in path.read_bytes() for path in folder.iterdir() if path.is_file())
