"""«Норма заказчика (КД)» КСВН коаксиальной сборки (владелец 23.10.2026: «норма заказчика — на этапе ТК; я главный на
производстве по технологии, я задаю»): технолог задаёт её в генераторе (`build_config.coax.vswrCustomer`), она —
норма приёмки (`coaxVswrNorm.source = "customer"`), норма по комплектующим — справочно (`components`)."""

from __future__ import annotations

import pytest

from app.modules.references.coax_vswr import CustomerNormError, components_of, customer_norm, customer_of
from app.modules.techcards.etk_builder import build_etk, vswr_step
from app.workflows.techcard_build import coax_customer_problem, enrich_coax_vswr_norm
from tests.techcards.test_assembly_drafts_api import _card, _csrf, drafts_api  # noqa: F401
from tests.techcards.test_etk_coax_vswr import CABLE, CARDS, ROWS, _config, _control
from tests.techcards.test_scheme_corrections import GENERATOR

CUSTOMER = {"vswrMax": "1,5", "fromMhz": "700", "toMhz": "2700", "basis": "ТУ 16-505.123, п. 3.4"}


def _with_customer(customer: dict | None = CUSTOMER) -> dict:
    config = _config()
    if customer is not None:
        config["coax"]["vswrCustomer"] = customer
    return config


def test_without_a_customer_norm_the_contract_is_unchanged():
    norm = enrich_coax_vswr_norm(_with_customer(None), ROWS, (CABLE,))["coax"]["vswrNorm"]
    assert norm["vswrMax"] == 2.06 and "source" not in norm and "components" not in norm
    assert customer_of(norm) is None and components_of(norm)["vswrMax"] == 2.06


def test_the_customer_norm_is_the_acceptance_norm_and_the_components_stay_for_reference():
    plain = enrich_coax_vswr_norm(_with_customer(None), ROWS, (CABLE,))["coax"]["vswrNorm"]
    norm = enrich_coax_vswr_norm(_with_customer(), ROWS, (CABLE,))["coax"]["vswrNorm"]
    assert norm["status"] == "ok" and norm["reason"] == ""
    assert norm["vswrMax"] == 1.5 and norm["bandMhz"] == [700.0, 2700.0]
    assert norm["source"] == "customer" and norm["basis"] == "ТУ 16-505.123, п. 3.4"
    assert norm["customer"] == {"vswrMax": 1.5, "fromMhz": 700.0, "toMhz": 2700.0, "basis": "ТУ 16-505.123, п. 3.4"}
    assert norm["components"] == {key: plain[key] for key in ("status", "reason", "vswrMax", "bandMhz", "s21Db", "ends")}
    # Факты сборки остаются там, где их читает паспорт.
    assert norm["cable"] == plain["cable"] and norm["lengthM"] == plain["lengthM"] and norm["ends"] == plain["ends"]
    # Повторное обогащение — то же самое (норма заказчика не наслаивается).
    once = enrich_coax_vswr_norm(_with_customer(), ROWS, (CABLE,))
    assert enrich_coax_vswr_norm(once, ROWS, (CABLE,)) == once


def test_a_customer_norm_holds_even_when_the_components_give_none():
    rows = (ROWS[0] | {"КСВ": ""}, ROWS[1])
    norm = enrich_coax_vswr_norm(_with_customer(), rows, ())["coax"]["vswrNorm"]
    assert norm["status"] == "ok" and norm["vswrMax"] == 1.5
    assert norm["components"]["status"] == "undefined" and "нет КСВ" in norm["components"]["reason"]


def test_the_step_text_gives_the_customer_norm_and_the_components_for_reference():
    document = build_etk(enrich_coax_vswr_norm(_with_customer(), ROWS, (CABLE,)), list(CARDS.values()))
    step = _control(document)
    assert step["text"] == ("Измерить КСВН (S11) в полосе 700…2700 МГц, сторона Б нагружена согласованной нагрузкой 50 Ом. "
                            "Норма заказчика ≤ 1,5 в полосе 700…2700 МГц (ТУ 16-505.123, п. 3.4); "
                            "по комплектующим ≤ 2,06 — справочно.")
    assert step["tolerance"] == "≤ 1,5"
    assert document["coaxVswrNorm"]["source"] == "customer"


def test_the_step_says_when_the_components_norm_is_not_defined():
    norm = {"status": "ok", "vswrMax": 1.25, "bandMhz": [1000, 6000], "source": "customer", "basis": "заказ 77",
            "components": {"status": "undefined", "reason": "у SMA-X нет КСВ в БД.КОАКС", "vswrMax": None}}
    text, tolerance = vswr_step(norm)
    assert text.endswith("Норма заказчика ≤ 1,25 в полосе 1000…6000 МГц (заказ 77); "
                         "по комплектующим норма не определена (у SMA-X нет КСВ в БД.КОАКС) — справочно.")
    assert tolerance == "≤ 1,25"


@pytest.mark.parametrize(("raw", "problem"), [
    ({"vswrMax": "0,9", "fromMhz": 700, "toMhz": 2700, "basis": "ТУ"}, "меньше 1"),
    ({"vswrMax": "1,5", "fromMhz": 2700, "toMhz": 700, "basis": "ТУ"}, "меньше конца"),
    ({"vswrMax": "1,5", "fromMhz": "", "toMhz": 700, "basis": "ТУ"}, "полосу"),
    ({"vswrMax": "1,5", "fromMhz": 700, "toMhz": 2700, "basis": "  "}, "основание"),
    ({"vswrMax": "abc", "fromMhz": 700, "toMhz": 2700, "basis": "ТУ"}, "не число"),
])
def test_an_unreadable_customer_norm_is_refused_in_russian(raw, problem):
    with pytest.raises(CustomerNormError, match=problem):
        customer_norm(raw)
    assert problem in coax_customer_problem({"coax": {"vswrCustomer": raw}})


def test_an_empty_customer_norm_means_the_norm_by_components():
    assert customer_norm(None) is None
    assert customer_norm({"vswrMax": "", "fromMhz": "", "toMhz": "", "basis": ""}) is None
    assert coax_customer_problem({"mode": "inter"}) is None


def test_build_and_preview_refuse_an_unreadable_customer_norm(drafts_api):  # noqa: F811
    _sessions, _clock, admin = drafts_api
    card = _card(admin)
    config = {**GENERATOR, "coax": {"vswrCustomer": {"vswrMax": "1,5", "fromMhz": 700, "toMhz": 2700, "basis": ""}}}
    for path in ("build", "build/preview"):
        refused = admin.post(f"/api/techcards/projects/{card['id']}/{path}", headers=_csrf(admin),
                             json={"version": card["version"], "config": config})
        assert refused.status_code == 422 and "основание" in refused.json()["detail"]
