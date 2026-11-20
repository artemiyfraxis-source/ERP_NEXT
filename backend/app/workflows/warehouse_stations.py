"""«Принтеры склада» tiles of «Оборудование» as device cards (owner 20.11.2026: «по принтерам тоже функционала нет»).

`stations(session, rows)` takes the rows `machines_api` built and, for the warehouse devices:
- names a device without an ОБ number by what it is and its model — «Принтер этикеток TSC MB341», «МФУ Pantum BM2300»
  — instead of the bare kind (the tile showed «Без номера»);
- gives the label printer `station = {kind: "labels"}` (its card: queue, recent jobs, «Пробная этикетка», «Перепечатать»);
- folds a PC's document printer (agent 2.12.0, kind 'document') into the tile of that PC's scanner — one МФУ, one tile —
  with `station = {kind: "mfp", machine_id, scanners, printers}`; a document printer without a scanner on its PC keeps
  its own tile («Принтер документов …») with the same card, print only."""

from __future__ import annotations

import re

from sqlalchemy import select

from app.modules.warehouse.devices import DeviceRow, PrinterRow

LABELS_NAME = "Принтер этикеток"
_NOISE = re.compile(r"\b(?:scanner|series|wia|сканер|printer|принтер)\b", re.IGNORECASE)


def model_of(name: str) -> str:
    """«Pantum BM2300 Series Scanner» → «Pantum BM2300»; «TSC MB341» stays."""
    text = " ".join(_NOISE.sub(" ", name or "").split())
    return text.strip(" -·,") or (name or "").strip()


def stations(session, rows: list[dict]) -> list[dict]:
    printers = {(machine, name): kind for machine, name, kind in session.execute(
        select(PrinterRow.machine_id, PrinterRow.name, PrinterRow.kind))}
    documents: dict[str, list[str]] = {}
    for (machine, name), kind in sorted(printers.items()):
        if kind == "document":
            documents.setdefault(machine, []).append(name)
    scanners = {f"device:{machine}:scan:{device}": (machine, device) for machine, device in session.execute(
        select(DeviceRow.machine_id, DeviceRow.device_id).where(DeviceRow.kind == "scan"))}
    with_scanner = {machine for machine, _device in scanners.values()}
    result = []
    for row in rows:
        key = row["key"]
        if key in scanners:
            machine, device = scanners[key]
            row = {**row, "station": {"kind": "mfp", "machine_id": machine, "scanners": [device],
                                      "printers": documents.get(machine, [])}}
            if not row["inventory"]:
                row["name"] = f"{'МФУ' if documents.get(machine) else 'Сканер'} {model_of(row['device'])}".strip()
        elif key == "printer:label-agent":
            row = {**row, "station": {"kind": "labels"}}
            if not row["inventory"] and row["device"]:
                row["name"] = f"{LABELS_NAME} {model_of(row['device'])}"
        elif row["kind"] == "printer" and key.startswith("printer:"):
            machine, _, name = key[len("printer:"):].partition(":")
            kind = printers.get((machine, name))
            if kind == "document":
                if machine in with_scanner:
                    continue  # the МФУ: one tile, its scanner's
                row = {**row, "station": {"kind": "mfp", "machine_id": machine, "scanners": [], "printers": [name]}}
                if not row["inventory"]:
                    row["name"] = f"Принтер документов {model_of(name)}"
            elif kind == "tspl":
                row = {**row, "station": {"kind": "labels"}}
                if not row["inventory"]:
                    row["name"] = f"{LABELS_NAME} {model_of(name)}"
        result.append(row)
    return result
