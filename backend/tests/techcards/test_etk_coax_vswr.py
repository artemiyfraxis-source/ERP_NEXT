"""Операция «Контроль КСВН» в ЭТК коаксиальной сборки (владелец 22.10.2026): норма — по комплектующим (КСВ разъёмов,
затухание кабеля), в шаге контроля и в документе `coaxVswrNorm`; нет КСВ — норма не определена, решает ОТК."""
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from app.modules.references.cable_structure import card_from_seed
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import parse_operation_library
from app.workflows.techcard_build import Refs, enrich_coax_vswr_norm, with_technology

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = [*json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))["templates"],
             *json.loads((ROOT / "data" / "techcard-library-erp.json").read_text(encoding="utf-8"))["templates"]]
CARDS = {card.code: card for card in parse_operation_library(TEMPLATES)}
ROWS = (
    {"coaxArticle": "SMA-C174P", "coaxWire": "RG-174", "КСВ": "≤1,43", "Частота до, ГГц": "6"},
    {"coaxArticle": "SMA-C316F", "coaxWire": "RG-316", "КСВ": "≤1,41", "Частота до, ГГц": "18"},
)
CABLE = card_from_seed({"name": "RG-316", "max_operating_ghz": 3, "attenuation": [
    {"freq_mhz": 100, "value": "0,2", "unit": "dB/m"}, {"freq_mhz": 1000, "value": "0,6325", "unit": "dB/m"}]}, 0)


def _config() -> dict:
    ops = [{"type": "coaxPin", "side": "A", "units": 1, "templateId": CARDS["SDR_COAX_pin"].source_id},
           {"type": "coaxVswr", "opKey": "coaxVswr", "units": 1, "templateId": CARDS["CTRL_VSWR_coax"].source_id}]
    return {"mode": "coax", "partQty": 10, "assemblyName": "SMA-Male — SMA-Female RG316 285 мм", "coax": {
        "cableLength": 285, "cableArt": "RG316", "cableName": "RG316",
        "sideA": {"article": "SMA-C174P", "type": "SMA-male", "pinType": "пайка"},
        "sideB": {"article": "SMA-C316F", "type": "SMA-female", "pinType": "пайка"},
    }, "ops": ops}


def _control(document: dict) -> dict:
    [operation] = [item for item in document["operations"] if item["type"] == "coaxVswr"]
    [step] = [step for step in operation["card"]["steps"] if step["kind"] == "Контроль"]
    return step


def test_the_control_step_gives_the_band_and_the_norm_by_components():
    config = enrich_coax_vswr_norm(_config(), ROWS, (CABLE,))
    document = build_etk(config, list(CARDS.values()))
    step = _control(document)
    assert step["text"] == ("Измерить КСВН (S11) в полосе 1…3000 МГц, сторона Б нагружена согласованной нагрузкой 50 Ом. "
                            "Норма ≤ 2,06 (по комплектующим: SMA-C174P ≤1,43; SMA-C316F ≤1,41). "
                            "Справочно: затухание S21 на 3000 МГц ≈ 0,31 дБ.")
    assert step["tolerance"] == "≤ 2,06"
    norm = document["coaxVswrNorm"]
    assert norm == config["coax"]["vswrNorm"] and norm["status"] == "ok" and norm["vswrMax"] == 2.06
    assert norm["cable"]["name"] == "RG-316" and norm["lengthM"] == 0.285 and norm["bandMhz"] == [1.0, 3000.0]
    operation = document["operations"][-1]
    assert operation["title"].startswith("CTRL_VSWR_coax") and operation["fields"]["coaxVswrNorm"] == norm


def test_without_vswr_of_a_connector_the_step_hands_the_decision_to_otk():
    rows = (ROWS[0] | {"КСВ": ""}, ROWS[1])
    document = build_etk(enrich_coax_vswr_norm(_config(), rows, ()), list(CARDS.values()))
    step = _control(document)
    assert step["text"] == ("Измерить КСВН (S11) в полосе 1…6000 МГц, сторона Б нагружена согласованной нагрузкой 50 Ом; "
                            "норма не определена — у SMA-C174P нет КСВ в БД.КОАКС, решение принимает ОТК.")
    assert step["tolerance"] == "решение ОТК"
    assert document["coaxVswrNorm"]["status"] == "undefined" and document["coaxVswrNorm"]["vswrMax"] is None


def test_without_references_the_norm_is_not_computed_and_not_made_up():
    document = build_etk(_config(), list(CARDS.values()))
    assert "coaxVswrNorm" not in document
    step = _control(document)
    assert "норма не рассчитана" in step["text"] and "≤" not in step["text"] and step["tolerance"] == "решение ОТК"


def test_enrichment_twice_is_enrichment_once_and_a_stale_norm_goes():
    once = enrich_coax_vswr_norm(_config(), ROWS, (CABLE,))
    assert enrich_coax_vswr_norm(once, ROWS, (CABLE,)) == once
    one_end = deepcopy(once)
    del one_end["coax"]["sideB"]
    assert "vswrNorm" not in enrich_coax_vswr_norm(one_end, ROWS, (CABLE,))["coax"]
    assert enrich_coax_vswr_norm({"mode": "inter", "ops": []}, ROWS, ()) == {"mode": "inter", "ops": []}


def test_with_technology_adds_the_norm_and_the_stamp_follows_the_cable_loss():
    refs = Refs(MappingProxyType({"schema_version": 1}), coax_rows=tuple(MappingProxyType(row) for row in ROWS),
                coax_cards=(CABLE,))
    enriched, _source = with_technology(_config(), refs=refs)
    assert enriched["coax"]["vswrNorm"]["vswrMax"] == 2.06
    lossier = replace(CABLE, attenuation=tuple(replace(point, loss_db_m=point.loss_db_m * 2) for point in CABLE.attenuation))
    assert Refs(refs.snapshot, coax_rows=refs.coax_rows, coax_cards=(lossier,)).stamp != refs.stamp
    assert Refs(refs.snapshot, coax_rows=refs.coax_rows, coax_cards=(CABLE,)).stamp == refs.stamp
    assert Refs(refs.snapshot, coax_rows=refs.coax_rows, coax_cards=(replace(CABLE, max_operating_ghz=None),)).stamp != refs.stamp
