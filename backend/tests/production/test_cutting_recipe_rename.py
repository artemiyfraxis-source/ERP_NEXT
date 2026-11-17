"""Переименование станка в справочнике не меняет рецепт резки; чужой станок и любое другое отличие — меняют."""

from copy import deepcopy
from uuid import UUID

import pytest

from app.main import app
from app.modules.production.cutting import RECIPE_VERSION, canonical_snapshot, comparable_key, digest
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_cutting_groups import NOW, batch, document, groups, issue, recipe
from tests.production.test_cutting_group_refresh_etk import pinned, refresh, reissue

OLD = "Автомат резки и зачистки коаксиального кабеля"
NEW = "Автомат для резки и роторной разделки кабеля"
LABEL_OLD = "Автомат печати и намотки этикеток на провод"
LABEL_NEW = "Полуавтомат для печати и намотки этикеток на провода"


def named(name=OLD, label_name=LABEL_OLD, inventory="ОБ.094", model_id="m-1", **kwargs):
    """document() with machines that carry display names beside their identity."""
    doc = document(**kwargs)
    cut, label = doc["operations"][0], doc["operations"][1]
    cut["card"]["tooling"][0].update(name=f"BZW-886+Q2 | {name}", inventory_number=inventory,
                                     equipment_model_id=model_id, parameters="кабель Ø до 5,5 мм")
    label["card"]["tooling"][0].update(name=label_name, inventory_number="ОБ.102", equipment_model_id="m-2")
    machine = {"model": "BZW-886+Q2", "inventory": inventory, "name": name, "equipment_model_id": model_id}
    doc["build_config"]["ops"][0]["machine"] = dict(machine)
    cut["fields"]["machine"] = dict(machine)
    return doc


def key(doc):
    result, why = recipe(doc)
    assert result is not None, why
    return result.key


def test_display_name_of_a_machine_does_not_change_the_key():
    first, second = named(), named(name=NEW, label_name=LABEL_NEW)
    assert key(first) == key(second)
    snapshot = recipe(first)[0].snapshot
    assert snapshot["version"] == RECIPE_VERSION == 2
    tooling = snapshot["operations"][0]["card"]["tooling"][0]
    assert "name" not in tooling and tooling["inventory_number"] == "ОБ.094" and tooling["equipment_model_id"] == "m-1"
    assert tooling["parameters"] == "кабель Ø до 5,5 мм"
    assert snapshot["operations"][0]["parameters"]["machine"] == {
        "model": "BZW-886+Q2", "inventory": "ОБ.094", "equipment_model_id": "m-1"}
    assert snapshot["operations"][0]["fields"]["machine"] == snapshot["operations"][0]["parameters"]["machine"]
    assert digest(snapshot) == key(first)


@pytest.mark.parametrize("change", ["inventory", "model_id", "model", "strip", "label_width", "tool_range",
                                    "step_text"])
def test_everything_else_still_differs(change):
    altered = named()
    cut = altered["operations"][0]
    if change == "inventory": altered = named(inventory="ОБ.095")
    if change == "model_id": altered = named(model_id="m-9")
    if change == "model": altered["build_config"]["ops"][0]["machine"]["model"] = "OTHER"
    if change == "strip": altered = named(strip=4)
    if change == "label_width": altered["build_config"]["ops"][1]["marks"][0]["width"] = 20
    if change == "tool_range": cut["card"]["tooling"][0]["parameters"] = "кабель Ø до 7 мм"
    if change == "step_text": cut["card"]["steps"][0]["text"] += " Проверить."
    assert key(named()) != key(altered)


def test_rows_without_machine_identity_keep_their_full_text():
    one, two = named(), named()
    for doc, name in ((one, OLD), (two, NEW)):
        doc["operations"][0]["card"]["tooling"].append({"section": "Оснастка", "name": name, "inventory_number": "X1"})
    assert key(one) != key(two)
    three, four = named(), named()
    for doc, name in ((three, OLD), (four, NEW)):
        doc["operations"][0]["card"]["tooling"][0].pop("equipment_model_id")
        doc["operations"][0]["card"]["tooling"][0].pop("inventory_number")
        doc["operations"][0]["card"]["tooling"][0]["name"] = name
    # No identity on the row at all: gate in cutting_recipe refuses it, the name is never the identity.
    assert recipe(three)[0] is None and recipe(four)[0] is None


def test_canonical_is_idempotent_and_old_snapshot_upgrades_to_the_new_key():
    snapshot = recipe(named())[0].snapshot
    assert canonical_snapshot(snapshot) == snapshot
    old = deepcopy(snapshot)
    old["version"] = 1
    old["operations"][0]["card"]["tooling"][0]["name"] = OLD
    old["operations"][0]["parameters"]["machine"]["name"] = OLD
    assert digest(old) != digest(snapshot)
    assert comparable_key(digest(old), 1, old) == digest(snapshot)
    assert comparable_key("stored", 2, old) == "stored"


def test_card_body_hash_revision_ignores_names():
    one, two = named(), named(name=NEW)
    for doc in (one, two):
        for op in doc["operations"]:
            del op["card_changed_at"]
    assert key(one) == key(two)


def test_refresh_after_rename_only_revision_repins_and_is_idempotent(production_api):
    client, _ = production_api
    cards = [issue("КБ1", named()), issue("КБ2", named())]
    batch(client, cards)
    group = groups(client)[0]
    for card in cards:
        reissue(card, named(name=NEW, label_name=LABEL_NEW))
    assert groups(client)[0]["etk_stale"] is True
    response = refresh(client, group)
    assert response.status_code == 200, response.text
    fresh = response.json()["group"]
    assert fresh["recipe_version"] == RECIPE_VERSION and fresh["id"] == group["id"]
    assert pinned() == [("КБ1", 2), ("КБ2", 2)]
    assert groups(client)[0]["etk_stale"] is False
    again = refresh(client, fresh)
    assert again.status_code == 200, again.text
    assert again.json()["group"]["version"] == fresh["version"]


@pytest.mark.parametrize("change", ["inventory", "strip", "label_width"])
def test_refresh_is_still_refused_for_a_real_difference(production_api, change):
    client, _ = production_api
    cards = [issue("КБ1", named())]
    batch(client, cards)
    group = groups(client)[0]
    if change == "label_width":
        altered = named(name=NEW)
        altered["build_config"]["ops"][1]["marks"][0]["width"] = 20
    else:
        altered = named(name=NEW, **{"inventory": {"inventory": "ОБ.095"}, "strip": {"strip": 4}}[change])
    reissue(cards[0], altered)
    response = refresh(client, group)
    assert response.status_code == 422, response.text
    assert "рецепт резки" in response.json()["detail"]
    assert pinned() == [("КБ1", 1)]


def test_open_group_with_version_1_key_refreshes_and_is_upgraded(production_api):
    client, _ = production_api
    cards = [issue("КБ1", named()), issue("КБ2", named())]
    batch(client, cards)
    group = groups(client)[0]
    with app.state.production_uow_factory() as uow:
        row = uow.cutting.get(UUID(group["id"]), lock=True)
        old = deepcopy(row.recipe)
        old["version"] = 1
        old["operations"][0]["card"]["tooling"][0]["name"] = OLD
        old["operations"][0]["parameters"]["machine"]["name"] = OLD
        row.recipe, row.recipe_key, row.recipe_version = old, digest(old), 1
        uow.commit()
    for card in cards:
        reissue(card, named(name=NEW))
    response = refresh(client, groups(client)[0])
    assert response.status_code == 200, response.text
    fresh = response.json()["group"]
    assert fresh["recipe_version"] == RECIPE_VERSION
    assert fresh["recipe_key"] == key(named(name=NEW))
    assert pinned() == [("КБ1", 2), ("КБ2", 2)]


def test_started_group_is_not_touched(production_api):
    client, _ = production_api
    cards = [issue("КБ1", named())]
    batch(client, cards)
    group = groups(client)[0]
    with app.state.production_uow_factory() as uow:
        uow.cutting.freeze(uow.cutting.members(UUID(group["id"]))[0].operation_id, NOW)
        uow.commit()
    reissue(cards[0], named(name=NEW))
    current = groups(client)[0]
    assert current["state"] == "frozen"
    assert refresh(client, current).status_code == 422
    assert current["recipe_key"] == group["recipe_key"]


def test_new_task_joins_an_open_version_1_group_of_the_same_document(production_api):
    client, _ = production_api
    batch(client, [issue("КБ1", named())])
    group = groups(client)[0]
    with app.state.production_uow_factory() as uow:
        row = uow.cutting.get(UUID(group["id"]), lock=True)
        row.recipe_key, row.recipe_version = recipe(named())[0].legacy_key, 1
        uow.commit()
    batch(client, [issue("КБ2", named())])
    current = groups(client)
    assert len(current) == 1 and current[0]["id"] == group["id"]
    assert {row["item"] for row in current[0]["rows"]} == {"КБ1", "КБ2"}
