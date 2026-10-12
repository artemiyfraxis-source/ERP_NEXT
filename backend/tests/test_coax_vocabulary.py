"""Единый словарь материалов коаксиальных кабелей: каждое «new» входит в словарь, слои карточек им написаны."""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "cables"
LAYER_KEYS = {"core": "material", "dielectric": "material", "shield": "type", "jacket": "material"}


def _load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def test_every_new_value_is_a_term():
    vocabulary = _load("coax-vocabulary.json")
    terms = set().union(*map(set, vocabulary["terms"].values()))
    assert all(entry["new"] in terms for entry in vocabulary["map"] if entry["new"])
    assert all(entry["new"] in terms and entry["source_ref"] for entry in vocabulary["changed"])


def test_card_layers_use_vocabulary_terms():
    terms = _load("coax-vocabulary.json")["terms"]
    for cable in _load("coax-cables.json")["cables"]:
        for layer, key in LAYER_KEYS.items():
            value = (cable["construction"].get(layer) or {}).get(key)
            if value:
                assert value in terms[layer], (cable["name"], layer, value)
