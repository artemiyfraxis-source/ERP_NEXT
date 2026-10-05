"""docs/assembly-corpus/build-config.schema.json: the JSON Schema of the manual build_config, checked against every
manual config of test_etk_manual (and still buildable — the schema must not describe what the builder refuses).

`jsonschema` is not a backend dependency, so a minimal validator for the keywords the schema uses sits here."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

import pytest

from .test_etk_manual import CONFIG, build, manual


SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs" / "assembly-corpus" / "build-config.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}


def errors(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Violations of `schema` by `value` (empty — valid). Keywords: $ref, type, const, enum, required, properties,
    additionalProperties (bool), items, minItems, minLength, pattern, minimum, exclusiveMinimum, allOf, oneOf, anyOf,
    not. Annotations (description, title, $schema) are ignored."""
    if "$ref" in schema:
        node: Any = SCHEMA
        for part in schema["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        found = errors(value, node, path)
        if found:
            return found
    found: list[str] = []
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[name](value) for name in names):
            return [f"{path}: тип {type(value).__name__}, нужен {names}"]
    if "const" in schema and value != schema["const"]:
        found.append(f"{path}: не {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{path}: {value!r} не из {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            found.append(f"{path}: короче {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            found.append(f"{path}: {value!r} не подходит под {schema['pattern']}")
    if _TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            found.append(f"{path}: {value} меньше {schema['minimum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            found.append(f"{path}: {value} не больше {schema['exclusiveMinimum']}")
    if isinstance(value, dict):
        found += [f"{path}: нет поля {key}" for key in schema.get("required", []) if key not in value]
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                found += errors(value[key], sub, f"{path}.{key}")
        if schema.get("additionalProperties") is False:
            found += [f"{path}: лишнее поле {key}" for key in value if key not in schema.get("properties", {})]
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            found.append(f"{path}: элементов меньше {schema['minItems']}")
        if "items" in schema:
            for index, item in enumerate(value):
                found += errors(item, schema["items"], f"{path}[{index}]")
    for sub in schema.get("allOf", []):
        found += errors(value, sub, path)
    if "anyOf" in schema and all(errors(value, sub, path) for sub in schema["anyOf"]):
        found.append(f"{path}: не подходит ни один вариант anyOf")
    if "oneOf" in schema:
        matched = sum(not errors(value, sub, path) for sub in schema["oneOf"])
        if matched != 1:
            found.append(f"{path}: подходит вариантов oneOf — {matched}, нужен один")
    if "not" in schema and not errors(value, schema["not"], path):
        found.append(f"{path}: подходит под запрещённое")
    return found


def configs() -> dict[str, dict]:
    """The manual configs test_etk_manual feeds the builder."""
    hand_drawn = manual(route={
        "blocks": [
            {"key": "cut", "name": "Мерный провод", "ops": [1], "final": False},
            {"key": "lug", "name": "Лепесток с проводом", "ops": [2], "final": False,
             "components": [{"id": "kit-2", "art": "ЛП-3", "name": "Лепесток", "qty": 1}]},
            {"key": "end", "ops": [3], "final": True},
        ],
        "edges": [{"from": "cut", "to": "end", "per_unit": 2}, {"from": "lug", "to": "end", "per_unit": 1}],
    })
    parallel = manual(route={
        "blocks": [{"key": "b1", "ops": [1], "final": False}, {"key": "b2", "ops": [2], "final": False},
                   {"key": "b3", "ops": [3], "final": True}],
        "edges": [{"from": "b1", "to": "b3", "per_unit": 1}, {"from": "b2", "to": "b3", "per_unit": 1}],
    })
    parallel["ops"][1]["link"] = "parallel"
    merged = manual(route={
        "blocks": [{"key": "b1", "ops": [1, 2], "final": False}, {"key": "b3", "ops": [3], "final": True}],
        "edges": [{"from": "b1", "to": "b3", "per_unit": 1}],
    })
    seqs = manual()
    for seq, op in zip((4, 7, 9), seqs["ops"]):
        op["seq"] = seq
    return {"base": deepcopy(CONFIG), "hand_drawn_route": hand_drawn, "parallel": parallel, "merged_block": merged,
            "seq": seqs}


@pytest.mark.parametrize("name", list(configs()))
def test_every_manual_config_of_test_etk_manual_fits_the_schema_and_builds(name):
    config = configs()[name]
    assert errors(config, SCHEMA) == []
    build(config)


def test_the_schema_file_is_a_2020_12_schema_with_an_extension_point_for_generator_operations():
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    variants = SCHEMA["$defs"]["operation"]["oneOf"]
    assert [item["$ref"].rsplit("/", 1)[1] for item in variants] == [
        "libraryOperation", "customOperation", "generatorOperation",
    ]
    assert "описание будет дополнено" in SCHEMA["$defs"]["generatorOperation"]["description"]
    # An operation of a generator type is accepted as a free-form object, whatever its other fields.
    assert errors({"mode": "manual", "ops": [{"type": "cutWireStrip", "strip": {"a": 1}, "anything": [1]}]}, SCHEMA) == []


@pytest.mark.parametrize(("change", "fragment"), [
    (lambda config: config.pop("ops"), "нет поля ops"),
    (lambda config: config.update(ops=[]), "элементов меньше 1"),
    (lambda config: config.update(mode="auto"), "не 'manual'"),
    (lambda config: config["ops"][0].pop("templateId"), "oneOf"),
    (lambda config: config["ops"][1].update(label=" "), "oneOf"),
    (lambda config: config["ops"][0].update(tOp="шесть"), "oneOf"),
    (lambda config: config["ops"][0].update(qty=0), "oneOf"),
    (lambda config: config["ops"][1].update(tPrep=-5), "oneOf"),
    (lambda config: config["ops"][0].update(link="together"), "oneOf"),
    (lambda config: config["ops"][1]["tooling"][0].update(section="Склад"), "oneOf"),
    (lambda config: config.update(route={"blocks": [], "edges": []}), "элементов меньше 1"),
    (lambda config: config.update(route={"blocks": [{"key": "b1", "ops": [0], "final": True}]}), "меньше 1"),
    (lambda config: config.update(route={"blocks": [{"key": "b1", "ops": [1], "final": True}],
                                         "edges": [{"from": "b1"}]}), "нет поля to"),
], ids=["no-ops", "empty-ops", "wrong-mode", "library-without-card", "custom-without-name", "word-as-time",
        "zero-qty", "negative-prep", "unknown-link", "unknown-tool-section", "empty-blocks", "position-zero",
        "edge-without-target"])
def test_the_schema_refuses_what_the_builder_refuses(change, fragment):
    config = manual()
    change(config)
    found = errors(config, SCHEMA)
    assert found and any(fragment in line for line in found), found
