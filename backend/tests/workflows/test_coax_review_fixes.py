"""Batch A review: demand identity, reference-unit reserves and printer consumables."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from app.main import app
from app.modules.fulfillment.application import active_kit_reserves
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.kitsets import issue_tasks, supply_lines
from app.modules.warehouse.domain import StockLot
from app.shared.http_auth import authenticated_principal
from app.workflows import kitset_reserve
from app.workflows.project_kitset import material_units
from tests.techcards.test_kitset_api import _principal, kitset_api  # noqa: F401
from tests.workflows.test_kitset_units import card_with, component
from tests.workflows.test_kitset_issue_once import ACTOR, NOW


def store(card):
    with app.state.techcards_uow_factory() as uow:
        uow.kitsets.save(card)
        uow.commit()


@pytest.mark.parametrize("selection", ["all", "keys", "legacy", "one_then_other"])
@pytest.mark.parametrize("replacement", ["", "ALT-1"])
def test_same_article_different_units_orders_both_and_is_idempotent(kitset_api, selection, replacement):  # noqa: F811
    client, _ = kitset_api
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit", "newrequest": "edit"})
    store(card_with(component("REF-1", "ТУТ", "1"), replace(component("REF-1", "Разъём", "1"), name=replacement)))
    url = "/api/techcards/kitsets/708К."
    response = client.get(url + "/supply")
    assert response.status_code == 200, response.text
    lines = response.json()["lines"]
    keys = [line["key"] for line in lines]
    assert len(set(keys)) == 2
    assert {line["unit"] for line in lines} == {"м", "шт"}
    articles = keys if selection == "keys" else ["REF-1"] if selection == "legacy" else []
    if selection == "one_then_other":
        groups = [[keys[0]], [keys[1]]]
    else:
        groups = [articles]
    created = []
    for group in groups:
        body = {"articles": group, "deadline": "2026-10-20"}
        response = client.post(url + "/order-missing", json=body)
        assert response.status_code == 200, response.text
        created.extend(response.json()["created"])
        again = client.post(url + "/order-missing", json=body)
        assert again.status_code == 200 and again.json()["created"] == []
    assert {(row["art"], row["quantity"], row["unit"]) for row in created} == {
        ("REF-1", "15", "м"), ("REF-1", "15", "шт"),
    }
    assert len(created) == 2
    assert all(line["short"] == "0" for line in client.get(url + "/supply").json()["lines"])
    assert [line["key"] for line in client.get(url + "/supply").json()["lines"]] == keys


def test_single_unit_supply_preserves_article_key():
    (line,) = supply_lines(card_with(component("REF-1", "ТУТ", "1")), [])
    assert line.key == "REF-1"


def test_reference_metres_reach_saved_reserve_and_resync(kitset_api, monkeypatch):  # noqa: F811
    client, _ = kitset_api
    sessions = app.state.techcards_uow_factory().session_factory
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    monkeypatch.setattr(app.state, "references_uow_factory", factory, raising=False)
    record = TechOperationRecord(
        id=uuid4(), tab=TechOperationTab.MATERIAL, display_text="REF-1", normalized_search="ref-1",
        source_sheet="test", sort_key="REF-1", fields={"Артикул": "REF-1", "Ед. изм.": "m"},
    )
    with factory() as uow:
        uow.tech_operations.save(record)
        uow.commit()
    lot = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000020", package="", name="REF-1", item_type="Др", purpose="Склад",
        unit="м", quantity=Decimal("10"), storage_location="1F4", source_receiving_ids=("ref-one",), article="REF-1",
    )
    with app.state.fulfillment_uow_factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    card = card_with(replace(component("REF-1", "Др", "0.06"), manual=True))
    store(card)
    response = client.put("/api/techcards/kitsets/708К./lines", json={"edits": [{
        "index": "КБ1", "art": "REF-1", "per": "0.06", "scrap": "0",
        "lot": {"lot_id": str(lot.id), "grn": lot.grn, "warehouse": "ЦАР"},
    }]})
    assert response.status_code == 200, response.text

    def held():
        with app.state.fulfillment_uow_factory() as uow:
            (reserve,) = active_kit_reserves(uow, card.project)
            stock = uow.warehouse.get(lot.id)
        assert reserve.quantity == stock.reserved
        return reserve.quantity, reserve.unit

    assert held() == (Decimal("0.90"), "м")
    with app.state.techcards_uow_factory() as uow:
        saved = uow.kitsets.get(card.project)
    store(replace(saved, assemblies=(replace(saved.assemblies[0], run=Decimal("30")),)))
    assert kitset_reserve.resync_project(
        card.project, ACTOR, NOW, app.state.techcards_uow_factory, app.state.fulfillment_uow_factory,
        reference_units=material_units([record]),
    ) == ""
    assert held() == (Decimal("1.80"), "м")

    # A concurrent card save rejects removal after releasing stock; rollback must restore metres too.
    original = kitset_reserve.sync_rows

    def sync_then_save(*args, **kwargs):
        result = original(*args, **kwargs)
        with app.state.techcards_uow_factory() as uow:
            current = uow.kitsets.get(card.project)
            uow.kitsets.save(current.with_edits([], who="Другой", now=NOW)[0])
            uow.commit()
        return result

    monkeypatch.setattr(kitset_reserve, "sync_rows", sync_then_save)
    response = client.post("/api/techcards/kitsets/708К./bom/КБ1/components/remove", json={"art": "REF-1"})
    assert response.status_code == 409, response.text
    assert held() == (Decimal("1.80"), "м")


@pytest.mark.parametrize("article,kind,name", [
    ("Лента Vell VL-S641", "Др", ""),
    ("VL-S641", "Лента", ""),
    ("LABEL-18", "Др", "Лента для этикеток"),
])
def test_printer_tape_is_not_demand_but_mounting_tape_is(kitset_api, article, kind, name):  # noqa: F811
    client, _ = kitset_api
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit", "newrequest": "edit"})
    printer = replace(component(article, kind, "0.06"), name=name)
    mounting = component("Лента монтажная 3M", "Др", "0.06")
    card = card_with(printer, mounting)
    store(card)
    (task,) = issue_tasks(card, set())
    assert [(row.article, row.need, row.unit) for row in task.items] == [(mounting.article, Decimal("0.90"), "м")]
    assert all(want.article != article for want in kitset_reserve.row_wants(card).values())
    url = "/api/techcards/kitsets/708К."
    (line,) = client.get(url + "/supply").json()["lines"]
    assert (line["art"], line["need"]) == (mounting.article, "0.9")
    response = client.post(url + "/order-missing", json={"deadline": "2026-10-20"})
    assert response.status_code == 200, response.text
    assert [row["art"] for row in response.json()["created"]] == [mounting.article]
    response = client.post(url + "/issue", json={"deadline": "2026-10-20"})
    assert response.status_code == 200, response.text
    with app.state.fulfillment_uow_factory() as uow:
        (request,) = uow.fulfillment.for_project(card.project)
    assert [(row.name, row.requirement, row.unit) for row in request.lines] == [(mounting.article, Decimal("0.90"), "м")]
