"""«Построить чертёж» в генераторе ТК: чертёж жгута, собранный из формы генератора (frontend/src/generatorToHarness.ts),
проходит серверную проверку чертежа и сохраняется как КД позиции; «Обновить чертёж» оставляет прежнюю версию в истории.

`harness_generated_sample.json` — то, что строит генератор для трёхконцевого жгута (А → узел → В1, В2: свитая пара,
ТУТ на пайке стороны А); фронтенд-тест `generatorToHarness.test.ts` сверяет с ним свой результат, так что образец не
отстаёт от сборщика."""

import json
from copy import deepcopy
from pathlib import Path

from app.modules.techcards.harness import parse_design
from app.modules.techcards.harness.schema import normalized
from app.modules.techcards.harness.validate import validate_design

from .test_harness_api import BASE, harness_api  # noqa: F401 — фикстура API чертежа

SAMPLE = json.loads((Path(__file__).parent / "harness_generated_sample.json").read_text(encoding="utf-8"))


def test_the_generated_drawing_passes_the_server_validator():
    assert validate_design(deepcopy(SAMPLE)) == []
    design, warnings = parse_design(deepcopy(SAMPLE))
    assert warnings == []  # и версия контракта записи есть — не «экспорт старого TECHMAP-GRAPHER»
    assert [connector.designation for connector in design.connectors] == ["XS1", "XS2", "XS3"]
    stored, _ = normalized(deepcopy(SAMPLE))
    assert stored["physicalTopology"]["segments"] == SAMPLE["physicalTopology"]["segments"]
    assert stored["diffPairs"] == SAMPLE["diffPairs"]


def test_the_sample_is_a_y_harness_with_every_wire_routed():
    topology = SAMPLE["physicalTopology"]
    # У паяного XS1 каждый контакт выходит своим отрезком — ТУТ на пайку надевается на каждый провод.
    assert [(s["from"], s["to"]) for s in topology["segments"]] == [
        *[(f"gen-n1-{pin}", "gen-n1") for pin in range(1, 5)],
        ("gen-n1", "gen-j1"), ("gen-j1", "gen-n2"), ("gen-j1", "gen-n3"),
    ]
    assert {route["wireId"] for route in topology["routes"]} == {wire["id"] for wire in SAMPLE["wires"]}
    assert [c["name"] for c in topology["coverings"]] == [f"ТУТ на пайку XS1 · конт. {pin}" for pin in range(1, 5)]


def test_build_saves_the_drawing_and_update_keeps_the_previous_version_in_history(harness_api):  # noqa: F811
    client, _factory, clock = harness_api
    built = client.put(f"{BASE}/КБ1", json={"design": SAMPLE, "revision": None, "checkpoint": True})
    assert built.status_code == 200, built.text
    assert built.json()["created"] is True
    first = built.json()["revision"]

    # Правка в редакторе после построения — автосохранение новой версией.
    edited = deepcopy(SAMPLE)
    edited["connectors"][0]["designation"] = "XS1-правка"
    hand = client.put(f"{BASE}/КБ1", json={"design": edited, "revision": first})
    assert hand.status_code == 200, hand.text
    second = hand.json()["revision"]

    # «Обновить чертёж»: закрыть текущую строку истории и записать построенный чертёж закрытой строкой.
    closed = client.post(f"{BASE}/КБ1/checkpoint", json={"revision": second})
    assert closed.status_code == 200, closed.text
    rebuilt = client.put(f"{BASE}/КБ1", json={"design": SAMPLE, "revision": second, "checkpoint": True})
    assert rebuilt.status_code == 200, rebuilt.text
    third = rebuilt.json()["revision"]
    assert rebuilt.json()["design"]["connectors"][0]["designation"] == "XS1"

    history = client.get(f"{BASE}/КБ1/revisions").json()
    assert [row["revision"] for row in history["revisions"]] == [third, second, first]
    assert all(row["closed"] for row in history["revisions"])
    hand_version = client.get(f"{BASE}/КБ1/revisions/{second}").json()
    assert hand_version["design"]["connectors"][0]["designation"] == "XS1-правка"
