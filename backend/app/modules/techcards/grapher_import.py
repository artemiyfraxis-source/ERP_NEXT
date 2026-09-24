"""Pure TECHMAP-GRAPHER project export import for the native ETK builder."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from io import BytesIO
import json
import re
from typing import Any, Mapping, Sequence
from zipfile import BadZipFile, ZipFile


SNAPSHOT_PATH = "project/snapshot.json"
MAX_EXPORT_BYTES = 30 * 1024 * 1024


class GrapherImportError(ValueError):
    pass


@dataclass(frozen=True)
class GrapherComponent:
    id: str
    article: str
    name: str


def cut_length(
    length_mm: object,
    correction_from_mm: object = 0,
    correction_to_mm: object = 0,
    rounding_step_mm: object = 1,
) -> Decimal | None:
    if length_mm is None or length_mm == "":
        return None
    length = Decimal(str(length_mm))
    total = length + Decimal(str(correction_from_mm or 0)) + Decimal(str(correction_to_mm or 0))
    step = Decimal(str(rounding_step_mm or 1))
    if total < 0 or step <= 0:
        return None
    return (total / step).to_integral_value(rounding=ROUND_CEILING) * step


def material_tokens(value: object) -> tuple[str, ...]:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"(\d),(\d)", r"\1.\2", text).replace("×", "x").replace("*", "x")
    return tuple(
        token.strip(".-") for token in re.split(r"[^a-zа-я0-9.\-x]+", text)
        if token.strip(".-") and (len(token.strip(".-")) > 1 or any(char.isdigit() for char in token))
    )


def match_component(material: object, components: Sequence[GrapherComponent]) -> str:
    wanted = material_tokens(material)
    if not wanted:
        return ""
    best, score, tied = "", 0, False
    for component in components:
        available = set(material_tokens(f"{component.article} {component.name}"))
        hits = [token for token in wanted if token in available]
        if not any(any(char.isdigit() for char in token) for token in hits):
            continue
        if len(hits) > score:
            best, score, tied = component.id, len(hits), False
        elif len(hits) == score and score:
            tied = True
    return best if score >= min(2, len(wanted)) and not tied else ""


def _end(connectors: Mapping[str, Mapping[str, Any]], value: object) -> tuple[Mapping[str, Any], Mapping[str, Any] | None] | None:
    if not isinstance(value, Mapping) or not value.get("connectorId"):
        return None
    connector = connectors.get(str(value["connectorId"]))
    if connector is None:
        return None
    contact = next((
        item for item in connector.get("contacts", [])
        if isinstance(item, Mapping) and item.get("id") == value.get("contactId")
    ), None)
    return connector, contact


def _end_label(value) -> str:
    if value is None:
        return "сросток"
    connector, contact = value
    label = str(connector.get("designation") or connector.get("partNumber") or "?")
    return f"{label}:{contact.get('number')}" if contact else label


def _material(binding: object, ends: Sequence[object]) -> str:
    if isinstance(binding, Mapping) and binding.get("displayName"):
        return str(binding["displayName"]).strip()
    for value in ends:
        if value is None or value[1] is None:
            continue
        contact = value[1]
        if contact.get("wire") or contact.get("wireSection"):
            return f"{contact.get('wire', '')} {contact.get('wireSection', '')}".strip()
    return ""


def design_groups(design: Mapping[str, Any], components: Sequence[GrapherComponent] = ()) -> dict[str, Any]:
    connectors = {
        str(item.get("id")): item for item in design.get("connectors", [])
        if isinstance(item, Mapping) and item.get("id")
    }
    wires = {
        str(item.get("id")): item for item in design.get("wires", [])
        if isinstance(item, Mapping) and item.get("id")
    }
    cable_members = {
        str(wire_id) for cable in design.get("cables", []) if isinstance(cable, Mapping)
        for wire_id in cable.get("memberWireIds", [])
    }
    groups: dict[tuple, dict[str, Any]] = {}
    warnings: list[str] = []

    def add(item: dict[str, Any]) -> None:
        key = (item["material"], item["length_mm"], item["cores"], item["jacket_mm"])
        group = groups.setdefault(key, {
            "material": item["material"], "length_mm": item["length_mm"],
            "quantity": 0, "cores": item["cores"], "jacket_mm": item["jacket_mm"],
            "correction_from_mm": item["correction_from_mm"],
            "correction_to_mm": item["correction_to_mm"], "cut_mm": item["cut_mm"],
            "ends": [], "source_ids": [],
        })
        group["quantity"] += 1
        group["source_ids"].append(item["id"])
        if len(group["ends"]) < 6:
            group["ends"].append(item["ends"])
        if (
            group["correction_from_mm"] != item["correction_from_mm"]
            or group["correction_to_mm"] != item["correction_to_mm"]
        ):
            group["mixed_corrections"] = True

    for wire_id, wire in wires.items():
        if wire_id in cable_members:
            continue
        ends = (_end(connectors, wire.get("from")), _end(connectors, wire.get("to")))
        length = wire.get("lengthMm")
        if length is None:
            warnings.append(f"Провод {wire.get('circuit') or wire_id} без длины в чертеже")
        add({
            "id": wire_id, "material": _material(wire.get("materialBinding"), ends),
            "length_mm": None if length is None else Decimal(str(length)), "cores": 1,
            "jacket_mm": None,
            "correction_from_mm": Decimal(str(wire.get("endCorrectionFromMm") or 0)),
            "correction_to_mm": Decimal(str(wire.get("endCorrectionToMm") or 0)),
            "cut_mm": cut_length(length, wire.get("endCorrectionFromMm"), wire.get("endCorrectionToMm"), wire.get("cutRoundingStepMm")),
            "ends": f"{_end_label(ends[0])} → {_end_label(ends[1])}{f' {wire.get("color")}' if wire.get('color') else ''}",
        })

    for cable in design.get("cables", []):
        if not isinstance(cable, Mapping):
            continue
        members = [wires[str(wire_id)] for wire_id in cable.get("memberWireIds", []) if str(wire_id) in wires]
        ends = (
            _end(connectors, members[0].get("from")), _end(connectors, members[0].get("to")),
        ) if members else (None, None)
        strip = cable.get("sheathStrip") if isinstance(cable.get("sheathStrip"), Mapping) else {}
        strip_values = [Decimal(str(value)) for value in (strip.get("fromMm"), strip.get("toMm")) if value is not None]
        jacket = max(strip_values) if strip_values else None
        if len(set(strip_values)) > 1:
            warnings.append(f"Кабель {cable.get('id')}: разделка оболочки различается на концах")
        length = cable.get("lengthMm")
        if length is None:
            warnings.append(f"Кабель {cable.get('id')} без длины в чертеже")
        add({
            "id": str(cable.get("id") or ""), "material": _material(cable.get("materialBinding"), ()),
            "length_mm": None if length is None else Decimal(str(length)), "cores": len(members) or 1,
            "jacket_mm": jacket,
            "correction_from_mm": Decimal(str(cable.get("endCorrectionFromMm") or 0)),
            "correction_to_mm": Decimal(str(cable.get("endCorrectionToMm") or 0)),
            "cut_mm": cut_length(length, cable.get("endCorrectionFromMm"), cable.get("endCorrectionToMm"), cable.get("cutRoundingStepMm")),
            "ends": f"кабель {len(members)} жил: {_end_label(ends[0])} → {_end_label(ends[1])}",
        })

    result_groups = []
    for group in groups.values():
        if not group["material"]:
            warnings.append(f"У провода {group['source_ids'][0]} не указан материал")
        group["component_id"] = match_component(group["material"], components)
        result_groups.append(group)
    connector_rows = []
    for connector in connectors.values():
        terminals = sorted({
            str(contact["terminalArticle"]).strip()
            for contact in connector.get("contacts", [])
            if isinstance(contact, Mapping) and contact.get("terminalArticle")
        })
        connector_rows.append({
            "designation": str(connector.get("designation") or ""),
            "part_number": str(connector.get("partNumber") or ""), "terminals": terminals,
        })
    return {
        "groups": result_groups, "connectors": connector_rows,
        "pairs": len(design.get("diffPairs", [])), "warnings": warnings,
    }


def import_snapshot(snapshot: Mapping[str, Any], components: Sequence[GrapherComponent] = ()) -> dict[str, Any]:
    if not isinstance(snapshot.get("project"), Mapping) or not isinstance(snapshot.get("harnesses"), list):
        raise GrapherImportError("Это не экспорт TECHMAP-GRAPHER: нет project/harnesses")
    harnesses = []
    for harness in snapshot["harnesses"]:
        if not isinstance(harness, Mapping):
            continue
        design = harness.get("design") if isinstance(harness.get("design"), Mapping) else {}
        if design.get("schemaVersion") is not None and int(design["schemaVersion"]) != 1:
            raise GrapherImportError(
                f"Жгут {harness.get('designation')}: поддерживается только схема чертежа 1"
            )
        content = design.get("content") if isinstance(design.get("content"), Mapping) else design
        harnesses.append({
            "designation": str(harness.get("designation") or ""),
            "quantity": int(harness.get("quantity") or 0),
            **design_groups(content, components),
        })
    project = snapshot["project"]
    return {
        "project": {
            "designation": str(project.get("designation") or ""),
            "name": str(project.get("name") or ""),
        },
        "harnesses": harnesses,
    }


def read_export(content: bytes, filename: str, components: Sequence[GrapherComponent] = ()) -> dict[str, Any]:
    if not content:
        raise GrapherImportError("Файл пустой")
    if len(content) > MAX_EXPORT_BYTES:
        raise GrapherImportError("Файл больше 30 МБ")
    try:
        if filename.lower().endswith(".json"):
            payload = content
        else:
            with ZipFile(BytesIO(content)) as archive:
                try:
                    payload = archive.read(SNAPSHOT_PATH)
                except KeyError as error:
                    raise GrapherImportError(f"В архиве нет {SNAPSHOT_PATH}") from error
        snapshot = json.loads(payload.decode("utf-8"))
    except (BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GrapherImportError("Не удалось разобрать экспорт TECHMAP-GRAPHER") from error
    if not isinstance(snapshot, Mapping):
        raise GrapherImportError("Корень snapshot.json должен быть объектом")
    return import_snapshot(snapshot, components)
