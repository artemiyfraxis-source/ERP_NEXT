# Hand-written `project/snapshot.json` of a `.techmap-project.zip`, shaped like the TECHMAP-GRAPHER export at
# commit e63befb85edb79f0c1635ac8314539ac24d2486a (branch codex/integration-m5-22): snapshotFormat 4
# (src/Techmap.Infrastructure.Sqlite/SqliteProjectExportService.cs), harness design schemaVersion 1 with
# requiredWriterContractVersion 2 (src/Techmap.Client/src/editor/model.ts), manufacturingRoute contractVersion 1
# (src/Techmap.Client/src/manufacturing/route-model.ts). Not production data.
#
# КБ1: X1 (template DS1069, 4 contacts) and X2 (BL2018P-04, 6 contacts), splice J1 of three wires, diff pair
# D+/D− under screen Э1 with a drain wire ending on the screen, a one-ended coax with strip layers L1..L3 (+ an
# «L4 = 0» that means «no layer»), a 2-core cable with sheath strip, a heat-shrink and a braid, a 2-row route.

from __future__ import annotations

from copy import deepcopy

SOURCE_COMMIT = "e63befb85edb79f0c1635ac8314539ac24d2486a"
SHA = "a" * 64
UUID = "11111111-2222-3333-8444-555555555555"
TEMPLATE = "22222222-3333-4444-8555-666666666666"
SHF_KEY = "3:JST|14:SHF-001T-0.8BS|8:BL2018TS|3:SHF"


def _pin(entity_type: str, key: str, name: str) -> dict:
    return {"sourceId": "technology", "snapshotId": UUID, "snapshotSha256": SHA, "recordId": SHA,
            "entityType": entity_type, "sourceKey": key, "displayName": name}


def _contact(cid: str, number: int, circuit: str, terminal: str, details: dict | None = None) -> dict:
    contact = {"id": cid, "number": number, "contactType": "сигнал", "circuit": circuit, "terminalArticle": terminal,
               "wire": "ПВАМ", "wireSection": "0,35", "color": "", "connectionStatus": "available", "customValues": {}}
    if details is not None:
        contact["terminalDetails"] = details
    return contact


DS_TERMINAL = {"manufacturer": "DS", "series": "1069", "description": "Терминал DS1069", "article": "T-DS1069-SC600",
               "lengthPlusMm": 1.5, "lengthMinusMm": 0.8}


def _wire(wid: str, source: dict, target: dict, circuit: str, length, corr_from=0, corr_to=0, **extra) -> dict:
    return {"id": wid, "from": source, "to": target, "circuit": circuit, "color": "красный",
            "materialBinding": _pin("wire", "ПВАМ 0,35", "ПВАМ 0,35 красный"), "lengthMm": length,
            "endCorrectionFromMm": corr_from, "endCorrectionToMm": corr_to, "cutRoundingStepMm": 1,
            "e4Route": [{"x": 1, "y": 2}], "drawingRoute": [], "layerIds": {"e4": "wires", "drawing": "wires"}, **extra}


def at(connector: str, contact: str) -> dict:
    return {"connectorId": connector, "contactId": contact}


def splice(junction: str) -> dict:
    return {"junctionId": junction, "connectorId": "", "contactId": ""}


DESIGN = {
    "schemaVersion": 1,
    "requiredWriterContractVersion": 2,
    "customWireColors": [],
    "connectors": [
        {"id": "x1", "designation": "X1", "partNumber": "I-DS1069-SCW004",
         "libraryBinding": {"mode": "template", "templateId": TEMPLATE, "templateVersion": 3, "versionSha256": SHA,
                            "articleVariantId": "v1",
                            "article": {"sourceId": "technology", "entityType": "connector", "articleKey": "I-DS1069-SCW004"},
                            "snapshot": {"code": "DS1069", "name": "Разъём DS1069", "contacts": [
                                {"logicalContactId": "L1", "name": "Питание"}]}},
         "contacts": [
             {**_contact("x1c1", 1, "+5V", "T-DS1069-SC600", DS_TERMINAL), "logicalContactId": "L1"},
             _contact("x1c2", 2, "GND", "T-DS1069-SC600", DS_TERMINAL),
             _contact("x1c3", 3, "D+", "T-DS1069-SC600", DS_TERMINAL),
             _contact("x1c4", 4, "D-", "T-DS1069-SC600", DS_TERMINAL),
         ],
         "schematic": {"orientation": "contacts-left", "baseColumns": [], "customFields": []},
         "positions": {"e4": {"x": 0, "y": 0}, "drawing": {"x": 0, "y": 0}}},
        {"id": "x2", "designation": "X2", "partNumber": "BL2018P-04",
         "contacts": [_contact(f"x2c{n}", n, "", SHF_KEY) for n in range(1, 7)]},
        {"id": "x3", "designation": "X3", "partNumber": "SMA-J", "contacts": [_contact("x3c1", 1, "RF", "")]},
        {"id": "x4", "designation": "X4", "partNumber": "PLS-2",
         "contacts": [_contact("x4c1", 1, "A", "PLS-T"), _contact("x4c2", 2, "B", "PLS-T")]},
        {"id": "x5", "designation": "X5", "partNumber": "PLS-2",
         "contacts": [_contact("x5c1", 1, "A", "PLS-T"), _contact("x5c2", 2, "B", "PLS-T")]},
    ],
    "wires": [
        _wire("w1", at("x1", "x1c1"), at("x2", "x2c1"), "+5V", 300, 8, 4),
        _wire("w2", at("x1", "x1c2"), splice("j1"), "GND", 150.2, 0.4, 0),
        _wire("w3", splice("j1"), at("x2", "x2c2"), "GND", 100, 0, -2),
        _wire("w4", splice("j1"), at("x2", "x2c3"), "GND", 120),
        _wire("w5", at("x1", "x1c3"), at("x2", "x2c5"), "D+", 400),
        _wire("w6", at("x1", "x1c4"), at("x2", "x2c6"), "D-", 400),
        _wire("w7", {"screenId": "s1", "screenTerminalSide": "below", "connectorId": "", "contactId": ""},
              at("x2", "x2c4"), "Экран", 90),
        _wire("w8", at("x3", "x3c1"), {"connectorId": "", "contactId": ""}, "RF", 500, stripProfiles={"from": {
            **_pin("coax-termination", "RG-316/SMA", "Разделка RG-316 под SMA"),
            "layers": [
                {"index": 1, "diameterMm": 0.5, "stripLengthMm": 2},
                {"index": 2, "diameterMm": 1.6, "stripLengthMm": 5},
                {"index": 3, "diameterMm": 2.6, "stripLengthMm": 9},
                {"index": 4, "diameterMm": 3.0, "stripLengthMm": 0},
            ]}}),
        _wire("w9", at("x4", "x4c1"), at("x5", "x5c1"), "A", 1000),
        _wire("w10", at("x4", "x4c2"), at("x5", "x5c2"), "B", 1000),
    ],
    "cables": [{
        "id": "c1", "memberWireIds": ["w9", "w10"], "materialBinding": _pin("cable", "ПВВС 2х0,35", "ПВВС 2х0,35"),
        "lengthMm": 1000, "endCorrectionFromMm": 8, "endCorrectionToMm": 23, "cutRoundingStepMm": 1,
        "sheathStrip": {"fromMm": 30, "toMm": 20},
    }],
    "junctions": [{"id": "j1", "position": {"x": 5, "y": 5}, "wireIds": ["w2", "w3", "w4"]}],
    "diffPairs": [{"id": "p1", "wireIds": ["w5", "w6"], "step": 12, "amplitude": 3, "variant": 1}],
    "screens": [{"id": "s1", "wireIds": ["w5", "w6"], "position": 0.5, "label": "Э1", "width": 10,
                 "terminalSide": "below"}],
    "views": {"e4": {"layers": [], "wireCrossingStyle": "none"}, "drawing": {"layers": [], "wireCrossingStyle": "none"}},
    "drawingDocuments": {"dimensions": [{"segmentId": "s2", "from": 0, "to": 1, "lengthMm": 80}]},
    "physicalTopology": {
        "snap": True,
        "nodes": [{"id": "n1", "position": {"x": 0, "y": 0}, "connectorId": "x1"},
                  {"id": "n2", "position": {"x": 9, "y": 0}, "connectorId": "x2"},
                  {"id": "n3", "position": {"x": 5, "y": 0}}],
        "segments": [{"id": "s1", "from": "n1", "to": "n3", "path": {"kind": "routed", "points": []}},
                     {"id": "s2", "from": "n3", "to": "n2", "path": {"kind": "routed", "points": []}}],
        "routes": [{"wireId": "w1", "steps": [{"segmentId": "s1", "reverse": False}, {"segmentId": "s2", "reverse": False}]},
                   {"wireId": "w5", "steps": [{"segmentId": "s1", "reverse": False}, {"segmentId": "s2", "reverse": False}]},
                   {"wireId": "w6", "steps": [{"segmentId": "s1", "reverse": False}, {"segmentId": "s2", "reverse": False}]},
                   {"wireId": "w2", "steps": [{"segmentId": "s1", "reverse": False}]}],
        "coverings": [
            {"id": "cv1", "name": "Термоусадка 6/3", "kind": "heat-shrink", "lengthMode": "auto", "width": 0,
             "color": "#424c53", "lengthMm": None, "material": _pin("protective-covering", "ТУТ 6/3мм", "ТУТ 6/3мм"),
             "spans": [{"segmentId": "s2", "from": 0.2, "to": 0.8, "fromAnchor": 0, "toAnchor": 1}]},
            {"id": "cv2", "name": "Оплётка", "kind": "braid", "lengthMode": "manual", "width": 0, "color": "#b19c77",
             "lengthMm": 250, "spans": [{"segmentId": "s1", "from": 0, "to": 1}]},
        ],
    },
    "manufacturingRoute": {
        "contractVersion": 1, "source": {"fingerprintVersion": 1, "sha256": SHA}, "status": "draft",
        "rows": [
            {"id": "r1", "kind": "semiFinished", "index": "ПФ-1", "title": "Провода нарезанные", "quantity": 1,
             "reserve": 0, "operationTimeMinutes": 2.5, "comment": "",
             "sourceObjects": [{"kind": "wire", "id": "w1"}, {"kind": "wire", "id": "w2"}, {"kind": "cable", "id": "c1"}],
             "dependsOn": [], "prepared": False,
             "operations": [
                 {"id": "op1", "mode": "cut-crimp", "note": "",
                  "binding": {**_pin("operation", "CUT_WIRE_auto", "Резка провода"), "sourceId": "БД.ОП"}},
                 {"id": "op2", "mode": "tin", "note": "кончики", "binding": None},
             ],
             "terminalRequirements": [{"wireId": "w1", "end": "from", "terminalArticle": "T-DS1069-SC600",
                                       "stripLengthMm": 3.5, "binding": None}],
             "presentation": {"backgroundOpacity": 1, "objects": []}},
            {"id": "r2", "kind": "assembly", "index": "СБ-1", "title": "Жгут КБ1", "comment": "",
             "sourceObjects": [{"kind": "connector", "id": "x1"}, {"kind": "connector", "id": "x2"},
                               {"kind": "covering", "id": "cv1"}],
             "dependsOn": ["r1"], "prepared": False,
             "operations": [{"id": "op3", "mode": "assembly", "note": "",
                             "binding": {**_pin("operation", "ASM_HARNESS", "Сборка жгута"), "sourceId": "БД.ОП"}}],
             "presentation": {"backgroundOpacity": 1, "objects": []}},
        ],
    },
}

SHEET = [
    {"id": "s-wire", "type": "Провод", "name": "ПВВС 4х0,35мм", "article": "ПВВС 4х0,35мм", "kd": "", "qty": 0.25},
    {"id": "s-x1", "type": "Соед", "name": "Розетка DS1069", "article": "I-DS1069-SCW004", "kd": "", "qty": 1},
    {"id": "s-t1", "type": "Терминал", "name": "Терминал DS1069", "article": "T-DS1069-SC600", "kd": "", "qty": 4},
    {"id": "s-x2", "type": "Соед", "name": "Колодка SMP", "article": "SMP-04V-BC", "kd": "BL2018P-04", "qty": 1},
    {"id": "s-t2", "type": "Терминал", "name": "Терминал SHF", "article": "SHF-001T-0.8BS", "kd": "BL2018TS", "qty": 4},
    {"id": "s-tut", "type": "ТУТ", "name": "ТУТ 6/3мм", "article": "ТУТ 6/3мм", "kd": "", "qty": 0.08},
]


def design() -> dict:
    return deepcopy(DESIGN)


def snapshot() -> dict:
    return {
        "snapshotFormat": 4,
        "project": {"projectId": UUID, "designation": "708К.2", "increment": 1, "name": "Контроллер", "batchQuantity": 1000,
                    "status": "active", "revision": 7, "createdUtc": "2026-10-01T00:00:00Z", "updatedUtc": "2026-10-05T00:00:00Z"},
        "harnesses": [{
            "harnessId": "h-kb1", "designation": "КБ1", "quantity": 1000, "sortOrder": 0,
            "createdUtc": "2026-10-01T00:00:00Z", "updatedUtc": "2026-10-05T00:00:00Z", "documents": [],
            "design": {"schemaVersion": 1, "content": design()},
        }],
        "attachments": [],
        "pinnedCharacteristics": [{
            "snapshotId": UUID, "sourceKind": "БД.ТЕР", "sourceRecordKey": "T-DS1069-SC600", "sourceVersion": "14",
            "characteristicName": "L+", "characteristicValue": "1,5", "unit": "мм", "canonicalPayload": "{}",
            "payloadSha256": SHA, "capturedUtc": "2026-10-01T00:00:00Z",
        }],
        "componentSnapshots": [{
            "snapshotId": UUID, "sourceTemplateId": TEMPLATE, "sourceVersion": 3, "sourceVersionSha256": SHA, "schemaVersion": 5,
            "code": "DS1069", "name": "Разъём DS1069",
            "articleBindings": [{"sourceId": "technology", "entityType": "connector", "articleKey": "I-DS1069-SCW004"}],
            "assets": [], "content": {"schemaVersion": 5}, "createdUtc": "2026-10-01T00:00:00Z", "updatedUtc": "2026-10-01T00:00:00Z",
        }],
        "componentPlacements": [{
            "placementId": "pl1", "harnessId": "h-kb1", "snapshotId": UUID, "sourceId": "technology",
            "entityType": "connector", "articleKey": "I-DS1069-SCW004", "instance": {},
            "createdUtc": "2026-10-01T00:00:00Z", "updatedUtc": "2026-10-01T00:00:00Z",
        }],
    }


def library(factory) -> None:
    """Puts the template X1 pins (DS1069 v3, the export's component snapshot) into the component library — a save
    of a drawing with that connector needs it there (migration 0091)."""
    from datetime import datetime

    from app.modules.techcards.harness.application import absorb
    from app.modules.techcards.harness.components import candidate_from_snapshot

    with factory() as uow:
        absorb(uow.harness_components, candidate_from_snapshot(snapshot()["componentSnapshots"][0]),
               by="Василий", at=datetime(2026, 10, 1))
        uow.commit()
