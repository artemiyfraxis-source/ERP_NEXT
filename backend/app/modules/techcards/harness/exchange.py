"""`.techmap-project.zip` of one harness (pure): `project/snapshot.json` as TECHMAP-GRAPHER writes it (snapshotFormat
4, harness design schemaVersion 1; SqliteProjectExportService.cs of e63befb, © Василий, SaveOurSouls, used with the
author's permission). One-way: after an import the ERP copy is the truth, nothing is synced back.

Library components placed on the drawing live inside the ERP document under `componentSnapshots` /
`componentPlacements` (only this harness's rows); the export moves them to the project level, the import back in."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from io import BytesIO
import json
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from .schema import COMPONENT_GRAPH_KEYS, COMPONENT_PLACEMENTS, COMPONENT_SNAPSHOTS, HarnessError, plain
from .snapshot import SCHEMA_VERSION

SNAPSHOT_PATH = "project/snapshot.json"
SNAPSHOT_FORMAT = 4
MAX_EXPORT_BYTES = 30 * 1024 * 1024


def _rows(value: object) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _utc(value: datetime) -> str:
    return value.isoformat()


def export_zip(design: Mapping[str, Any], *, project: str, index: str, product: str, name: str, quantity: object,
               harness_id: str, created: datetime, updated: datetime) -> bytes:
    """The archive of one harness: the ERP project number is the project designation, the position index is the
    harness designation (`product` — the full product id, the project name when the position has none), ids are
    stable per project / КД row."""
    run = plain(quantity) if isinstance(quantity, Decimal) else quantity
    content = {key: value for key, value in design.items() if key not in COMPONENT_GRAPH_KEYS}
    placements = [{**row, "harnessId": harness_id} for row in _rows(design.get(COMPONENT_PLACEMENTS))]
    snapshot = {
        "snapshotFormat": SNAPSHOT_FORMAT,
        "project": {
            "projectId": str(uuid5(NAMESPACE_URL, f"fraxis-erp:project:{project}")), "designation": project,
            "increment": 1, "name": name or product, "batchQuantity": run, "status": "active",
            "revision": 1, "createdUtc": _utc(created), "updatedUtc": _utc(updated),
        },
        "harnesses": [{
            "harnessId": harness_id, "designation": index, "quantity": run, "sortOrder": 0,
            "createdUtc": _utc(created), "updatedUtc": _utc(updated), "documents": [],
            "design": {"schemaVersion": SCHEMA_VERSION, "content": content},
        }],
        "attachments": [], "pinnedCharacteristics": [],
        COMPONENT_SNAPSHOTS: _rows(design.get(COMPONENT_SNAPSHOTS)), COMPONENT_PLACEMENTS: placements,
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(SNAPSHOT_PATH, json.dumps(snapshot, ensure_ascii=False, indent=2))
    return buffer.getvalue()


def design_from_export(content: bytes, file_name: str, *, index: str, product: str) -> dict[str, Any]:
    """The design document of this position from a TECHMAP-GRAPHER export (`.techmap-project.zip`, or its
    snapshot.json): the harness designated as the position (index or full product id), else the only one."""
    if not content:
        raise HarnessError("Файл пустой")
    if len(content) > MAX_EXPORT_BYTES:
        raise HarnessError("Файл больше 30 МБ")
    try:
        if file_name.lower().endswith(".json"):
            payload = content
        else:
            with ZipFile(BytesIO(content)) as archive:
                try:
                    payload = archive.read(SNAPSHOT_PATH)
                except KeyError as error:
                    raise HarnessError(f"В архиве нет {SNAPSHOT_PATH} — это не экспорт TECHMAP-GRAPHER") from error
        snapshot = json.loads(payload.decode("utf-8"))
    except (BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarnessError("Не удалось разобрать экспорт TECHMAP-GRAPHER") from error
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("harnesses"), list):
        raise HarnessError("Это не экспорт TECHMAP-GRAPHER: нет списка жгутов")
    harnesses = [item for item in snapshot["harnesses"] if isinstance(item, Mapping)]
    if not harnesses:
        raise HarnessError("В экспорте нет ни одного жгута")
    wanted = {index.strip().upper(), product.upper()}
    named = [item for item in harnesses if str(item.get("designation") or "").strip().upper() in wanted]
    if len(named) == 1:
        harness = named[0]
    elif len(harnesses) == 1:
        harness = harnesses[0]
    else:
        listed = ", ".join(str(item.get("designation") or "без обозначения") for item in harnesses)
        raise HarnessError(f"В экспорте {len(harnesses)} жгута(ов) ({listed}), жгута «{index}» среди них нет")
    design = harness.get("design")
    if not isinstance(design, Mapping):
        raise HarnessError(f"У жгута «{harness.get('designation') or index}» в экспорте нет чертежа")
    if design.get("schemaVersion") is not None and design.get("schemaVersion") != SCHEMA_VERSION:
        raise HarnessError(f"Поддерживается только схема чертежа {SCHEMA_VERSION}, в экспорте — {design.get('schemaVersion')}")
    inner = design.get("content")
    result = dict(inner) if isinstance(inner, Mapping) else dict(design)
    # The library placements of this harness and the snapshots they pin (project-level in the export) go inside.
    if not any(key in result for key in COMPONENT_GRAPH_KEYS):
        harness_id = str(harness.get("harnessId") or "")
        placements = [row for row in _rows(snapshot.get(COMPONENT_PLACEMENTS))
                      if harness_id and str(row.get("harnessId") or "") == harness_id]
        used = {str(row.get("snapshotId") or "") for row in placements}
        if placements:
            result[COMPONENT_PLACEMENTS] = placements
            result[COMPONENT_SNAPSHOTS] = [row for row in _rows(snapshot.get(COMPONENT_SNAPSHOTS))
                                           if str(row.get("snapshotId") or "") in used]
    return result
