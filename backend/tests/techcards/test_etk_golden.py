"""Golden ETK output: every config the builder tests use builds exactly what it built before the block scheme.

ETK version 3 only adds `document.route`; everything else must stay byte-identical. The digests in
`fixtures/etk-golden.json` were taken from the version-2 builder and retaken from version 4 (18.10: time norms
without the card's setup — only the time rows changed, by exactly tPrep). When an ETK change is intended, regenerate them:
`.venv/Scripts/python.exe -m tests.techcards.test_etk_golden` (from `backend/`), and review the diff of the fixture.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from app.modules.techcards.etk_builder import ETK_VERSION, build_etk
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.references.technology import TerminalRecord
from app.workflows.techcard_build import enrich_terminal_norms


ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "fixtures" / "etk-golden.json"
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
ERP_LIBRARY = json.loads((ROOT / "data" / "techcard-library-erp.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
KITS = ("720К.-КБ1", "720К.1.2-КБ1", "708К.2-КБ1", "708К.5-КБ1", "591К.5-КБ2", "729К.2-КБ1", "345К.2-КБ1")
NEW_KEYS = {"route"}


def _kit(name: str) -> dict[str, Any]:
    return deepcopy(FIXTURES["files"][f"kit-{name}.json"]["payload"]["config"])


def _records() -> tuple[TerminalRecord, ...]:
    return tuple(
        TerminalRecord(
            article=item["article"], step=item["step"], strip=item["strip"],
            l_plus=item["lPlus"], l_minus=item["lMinus"], applicator=item["applicator"],
            crimp_height=item["crimpHeight"], pull_force_min=item["pullForceMin"],
            pull_force_max=item["pullForceMax"], awg_from=item["awgFrom"],
            awg_to=item["awgTo"], mm2_from=item["mm2From"], mm2_to=item["mm2To"],
        )
        for item in FIXTURES["files"]["db.json"]["payload"]["terRecords"]
    )


def _pins() -> dict[str, Any]:
    config = _kit("720К.-КБ1")
    config["ops"][2]["pins"] = ["1", "2"]
    return config


def _connections() -> dict[str, Any]:
    config = _kit("720К.-КБ1")
    mounts = [index for index, op in enumerate(config["ops"]) if op.get("type") == "insTerm"]
    config["ops"][mounts[0]]["epKey"], config["ops"][mounts[1]]["epKey"] = "А1", "В1"
    config["connections"] = [
        {"wire": "черный", "from": {"end": "А1", "conn": "XHP-2", "pin": 1}, "to": {"end": "В1", "conn": "XHP-2", "pin": "3"}},
        {"wire": "черный", "from": {"end": "А1", "pin": 2}, "to": {"end": "", "pin": 1}},
        "мусор",
    ]
    return config


def _missing_template() -> dict[str, Any]:
    config = _kit("720К.-КБ1")
    config["ops"][0]["templateId"] = "missing"
    return config


def _no_length() -> dict[str, Any]:
    config = _kit("708К.2-КБ1")
    return {**config, "wires": [{**wire, "length": 0} for wire in config["wires"]]}


def _cut_crimp(quantity: int, wire_qty: int) -> dict[str, Any]:
    wire = {"name": "Провод", "art": "ПВ3", "qty": wire_qty, "length": 250}
    return {
        "assemblyName": "Жгут", "partQty": quantity, "wires": [dict(wire)],
        "ops": [{
            "type": "cutWireCrimp", "templateId": "cut-wire-1crimp-автомат-резка-опрессовка",
            "opNum": "CUT_WIRE+1crimp", "side": "A", "tPrep": 60, "tOp": 2, "wires": [dict(wire)],
            "strip": {"a": True, "lenA": "3,5", "b": False, "lenB": ""},
            "ep": {"connArt": "XHP-2", "termArt": "SXH-001T-P0.6", "termName": "Терминал"},
            "terminalNorm": {"applicator": "A16"},
        }, {"type": "insTerm", "opNum": "INS_terminal", "side": "A", "ep": {"connArt": "XHP-2"}}],
    }


def _splice_strip() -> dict[str, Any]:
    wires = [{"name": "Провод", "art": "ПВ3", "qty": 2, "length": 300}]
    return {
        "assemblyName": "Жгут", "partQty": 1, "wires": deepcopy(wires),
        "ops": [
            {"type": "cutWire", "opNum": "CUT_WIRE_auto", "wires": deepcopy(wires)},
            {"type": "stripCable", "opNum": "STRIP_WIRE", "tutCount": 2},
            {"type": "splice", "opNum": "US_WELD", "units": 1},
            {"type": "insTerm", "opNum": "INS_terminal_2.00", "side": "A", "ep": {"connArt": "XHP-2"}, "wires": deepcopy(wires), "wireSources": [""]},
            {"type": "insTerm", "opNum": "INS_terminal_2.00", "side": "B", "ep": {"connArt": "PHR-2"}, "wires": deepcopy(wires), "wireSources": [""]},
        ],
    }


def _coax() -> dict[str, Any]:
    return {"mode": "coax", "coax": {
        "cableArt": "RG-174", "sideA": {"article": "SMA-M", "l1": "3", "l2": "5", "l3": "9", "d1": "0,5"},
        "sideB": {"article": "BNC-F", "l1": "4"},
    }, "ops": [{"type": "coaxStrip", "side": "A"}, {"type": "coaxStrip", "side": "B"}]}


def _cut_crimp_tin() -> dict[str, Any]:
    wire = {"name": "Силиконовый 28AWG черный", "art": "ПВ28", "qty": 2, "length": 100}
    return {
        "assemblyName": "Кабельная сборка", "partQty": 50, "wires": [dict(wire)],
        "ops": [{
            "type": "cutWireCrimp", "templateId": "cut-wire-1crimp-tin-автомат-резка-опрессовка-лужение",
            "opNum": "CUT_WIRE+1crimp+tin", "side": "A", "tin": True, "tPrep": 60, "tOp": 4, "wires": [dict(wire)],
            "strip": {"a": True, "lenA": "1,2", "b": True, "lenB": "3"},
            "ep": {"connArt": "SHR-02V-S-B", "termArt": "SSH-003T-P0.2-H", "termName": "Терминал"},
            "terminalNorm": {"applicator": "S9/M1/A19"},
        }, {"type": "insTerm", "opNum": "INS_terminal", "side": "A", "ep": {"connArt": "SHR-02V-S-B"}}],
    }


def _cases() -> dict[str, Callable[[], dict[str, Any]]]:
    """Case → builder of the document; configs are the ones test_etk_builder.py and test_techcards_all.py build."""
    def plain(config_factory, cards=None, **kwargs):
        return lambda: build_etk(config_factory(), cards or CARDS, operation_library_commit=LIBRARY["sourceCommit"], **kwargs)

    cases: dict[str, Callable[[], dict[str, Any]]] = {name: plain(lambda name=name: _kit(name)) for name in KITS}
    for name in ("720К.-КБ1", "720К.1.2-КБ1", "708К.5-КБ1"):
        cases[f"{name} нормы терминалов"] = plain(
            lambda name=name: enrich_terminal_norms(_kit(name), _records()), technology_source={"schema_version": 14},
        )
    cases.update({
        "720К.-КБ1 распиновка": plain(_pins),
        "720К.-КБ1 соединения": plain(_connections),
        "720К.-КБ1 нет шаблона": plain(_missing_template),
        "708К.2-КБ1 без длины": plain(_no_length),
        "резка с обжимом": plain(lambda: _cut_crimp(100, 2)),
        "резка с обжимом, мерительный инструмент": plain(lambda: _cut_crimp(10, 1)),
        "сростки и разделка": plain(_splice_strip),
        "резка без длины": plain(lambda: {"wires": [{"art": "ПВ3", "length": 0}], "ops": [{"type": "cutWire", "opNum": "CUT_WIRE"}]}),
        "коаксиал": plain(_coax),
        "резка, обжим, лужение": plain(_cut_crimp_tin, CARDS + parse_operation_library(ERP_LIBRARY["templates"])),
    })
    return cases


def _digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def _fingerprint(document: dict[str, Any]) -> dict[str, Any]:
    """Key order, every top-level value and every operation, each as a digest of its exact JSON text."""
    rest = {key: value for key, value in document.items() if key not in NEW_KEYS | {"schema_version"}}
    return {
        "keys": [key for key in document if key not in NEW_KEYS],
        "top": {key: _digest(value) for key, value in rest.items() if key != "operations"},
        "operations": [_digest(item) for item in rest["operations"]],
    }


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_cases_are_pinned_to_the_library_commit():
    golden = _golden()
    assert golden["library_commit"] == LIBRARY["sourceCommit"], (
        "Библиотека операций обновлена — перегенерируйте эталон: .venv/Scripts/python.exe -m tests.techcards.test_etk_golden"
    )
    assert set(golden["cases"]) == set(_cases())


@pytest.mark.parametrize("case", sorted(_cases()))
def test_etk_is_byte_identical_to_the_pinned_builder_except_the_route(case: str):
    document = _cases()[case]()
    assert document["schema_version"] == ETK_VERSION
    assert _fingerprint(document) == _golden()["cases"][case]


def main() -> None:
    payload = {
        "library_commit": LIBRARY["sourceCommit"],
        "cases": {name: _fingerprint(factory()) for name, factory in sorted(_cases().items())},
    }
    with GOLDEN.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    print(f"{GOLDEN}: {len(payload['cases'])} cases")


if __name__ == "__main__":
    main()
