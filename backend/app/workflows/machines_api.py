"""HTTP «Станки» (Производство), slice 1 — read only; rules and storage in production/machines.py.

- `GET /api/production/machines` (Производство or Задания, view) → `{now, rows}`: one row per connected thing —
  ОБ with an ERP bridge (and supported ОБ still waiting for one), marking and label printers of the print agents,
  scanners and network analysers the agents see. One value per column: ОБ, Название, ПК, IP, Служба, Версия, Связь,
  Последний сигнал, Устройство, Состояние.
- `GET /api/production/machines/{equipment_id}` (same gate) → the live card of a bridged ОБ (decoded PLC words).
- `POST /api/production/machines/agent/report` — the bridge only: `Authorization: Bearer <token with machine:report>`;
  a person's session is refused (403), so is a token or PC not bound to that ОБ (403). Body: `{proto, inventory,
  machine_id, pc_name, ip_lan, bridge_version, service_version, boot, seq, link, error, registers: {"766": 102, …}}`
  → `{ok, accepted, bound}`; `accepted: false` — an older snapshot of the same bridge run, ignored."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.modules.production import machines as rules
from app.modules.production.api import production_uow_factory
from app.modules.references.equipment import INV_KEY, NAME_KEYS, field
from app.modules.references.infrastructure import EquipmentMachineRow
from app.modules.warehouse.devices import DeviceRow, PrinterRow
from app.modules.warehouse.label_jobs import LabelAgentRow
from app.modules.warehouse.marking_profiles import MarkingBindingRow
from app.modules.warehouse.scan_agents import LIVE_FOR, ScanAgentRow
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import TOKEN_SCOPE_PREFIX, Principal, authenticated_principal, require_section

router = APIRouter(prefix="/api/production/machines", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
# A named gate of the access contract (tests/identity/test_level_gates.py DEPENDENCIES): the master and the fitter read.
machines_view_principal = require_section(("production", "myops"), "view", detail="Раздел «Станки» вам не открыт.")
Reader = Annotated[Principal, Depends(machines_view_principal)]
BRIDGE_ONLY = "Снимки станка присылает только мост на ПК станка (токен API «machine:report»)."

PRINTER_NAMES = {"tspl": "Принтер этикеток", "gdi_png": "Принтер маркировки"}
DEVICE_NAMES = {"scan": "Сканер", "s2vna": "Анализатор цепей", "arinst": "Анализатор цепей Arinst"}
AGENT_SERVICE = "Агент печати"


def bridge_principal(
    principal: Annotated[Principal, Security(authenticated_principal, scopes=[TOKEN_SCOPE_PREFIX + rules.SCOPE])],
) -> Principal:
    if not principal.service:
        raise HTTPException(status_code=403, detail=BRIDGE_ONLY)
    return principal


bridge_principal.token_scopes = frozenset({rules.SCOPE})  # type: ignore[attr-defined]  (the token contract test reads it)
Bridge = Annotated[Principal, Depends(bridge_principal)]


class ReportPayload(BaseModel):
    proto: Literal[1]
    inventory: str = Field(min_length=1, max_length=64)
    machine_id: str = Field(min_length=1, max_length=80)
    pc_name: str = Field(default="", max_length=128)
    ip_lan: str = Field(default="", max_length=64)
    bridge_version: str = Field(default="", max_length=32)
    service_version: str = Field(default="", max_length=64)
    boot: str = Field(min_length=1, max_length=64)
    seq: int = Field(ge=0, le=2**62)
    link: Literal["ok", "port_busy", "no_plc", "no_service"]
    error: str = Field(default="", max_length=2000)
    registers: dict[str, int] = Field(default_factory=dict, max_length=len(rules.OB019_ALLOWED))


def _inventory(value: object) -> str:
    return "".join(str(value or "").split()).upper()


def _equipment(session) -> dict[str, list[EquipmentMachineRow]]:
    found: dict[str, list[EquipmentMachineRow]] = {}
    for row in session.scalars(select(EquipmentMachineRow)):
        number = _inventory(field(row.fields or {}, INV_KEY, "inventoryNumber"))
        if number:
            found.setdefault(number, []).append(row)
    return found


def _name(row: EquipmentMachineRow | None) -> str:
    if row is None:
        return ""
    return field(row.fields or {}, *NAME_KEYS) or row.display_text


def _moment(value: datetime | None, now: datetime) -> str | None:
    if value is None:
        return None
    return (value.replace(tzinfo=now.tzinfo) if value.tzinfo is None else value).isoformat()


def _fresh(value: datetime | None, now: datetime) -> bool:
    return value is not None and rules.since(value, now) <= LIVE_FOR


def _tone(code: str) -> dict:
    text, tone = rules.LINK_TEXTS[code]
    return {"code": code, "text": text, "tone": tone}


def _machine_state(link_code: str, words: dict[int, int]) -> dict:
    if link_code in ("stale", "not_installed"):
        return {"text": "—", "tone": "neutral"}
    if link_code != "ok":
        return {"text": "Нет данных ПЛК", "tone": "neutral"}
    decoded = rules.decode_ob019(words)
    return {"text": decoded["state"]["text"], "tone": decoded["state"]["tone"]}


def _machine_rows(session, equipment, now) -> list[dict]:
    rows, linked = [], set()
    for link, snapshot in rules.links(session):
        linked.add(link.inventory_number)
        code = rules.link_status(snapshot, now)
        unit = next(iter(equipment.get(_inventory(link.inventory_number), [])), None)
        rows.append({
            "key": f"machine:{link.equipment_id}", "kind": "machine", "equipment_id": str(link.equipment_id),
            "inventory": link.inventory_number, "name": _name(unit), "pc": link.pc_name, "ip": link.ip_lan or link.ip_seen,
            "service": rules.KIND_NAMES[link.kind], "version": link.bridge_version, "link": _tone(code),
            "last_seen": _moment(snapshot.reported_at, now) if snapshot else None,
            "device": rules.DEVICE_NAMES[link.kind], "state": _machine_state(code, rules.snapshot_words(snapshot)),
            "card": True,
        })
    for inventory, kind in rules.SUPPORTED.items():
        units = equipment.get(_inventory(inventory), [])
        if inventory in linked or len(units) != 1:
            continue
        rows.append({
            "key": f"machine:{units[0].id}", "kind": "machine", "equipment_id": str(units[0].id), "inventory": inventory,
            "name": _name(units[0]), "pc": "", "ip": "", "service": rules.KIND_NAMES[kind], "version": "",
            "link": _tone("not_installed"), "last_seen": None, "device": rules.DEVICE_NAMES[kind],
            "state": {"text": "—", "tone": "neutral"}, "card": False,
        })
    return rows


def _agent_link(agent: ScanAgentRow, now: datetime) -> dict:
    return {"code": "ok", "text": "На связи", "tone": "ok"} if _fresh(agent.last_seen, now) \
        else {"code": "stale", "text": "Нет связи с ПК", "tone": "danger"}


def _present(present: bool, seen: datetime | None, now: datetime, live: str, gone: str) -> dict:
    return {"text": live, "tone": "ok"} if present and _fresh(seen, now) else {"text": gone, "tone": "warn"}


def _printer_rows(session, equipment, now) -> list[dict]:
    units = {row.id: row for rows in equipment.values() for row in rows}
    bound = {(row.machine_id, row.printer): row.equipment_id for row in session.scalars(select(MarkingBindingRow))}
    rows = []
    for printer, agent in session.execute(select(PrinterRow, ScanAgentRow).join(
            ScanAgentRow, ScanAgentRow.machine_id == PrinterRow.machine_id)
            .order_by(ScanAgentRow.pc_name, PrinterRow.name)):
        unit = units.get(bound.get((printer.machine_id, printer.name)))
        rows.append({
            "key": f"printer:{printer.machine_id}:{printer.name}", "kind": "printer",
            "equipment_id": str(unit.id) if unit else None,
            "inventory": field(unit.fields or {}, INV_KEY) if unit else "",
            "name": _name(unit) if unit else PRINTER_NAMES.get(printer.kind, "Принтер"),
            "pc": agent.pc_name, "ip": agent.ip_lan or agent.ip_seen, "service": AGENT_SERVICE, "version": agent.version,
            "link": _agent_link(agent, now), "last_seen": _moment(agent.last_seen, now), "device": printer.name,
            "state": _present(printer.present, printer.last_seen, now, "Принтер виден", "Принтер не найден"),
            "card": False,
        })
    # The TSC of the warehouse PC: an agent that reports no printer list is known only by its singleton heartbeat.
    if session.scalar(select(PrinterRow.name).where(PrinterRow.kind == "tspl").limit(1)) is None:
        label = session.get(LabelAgentRow, 1)
        if label is not None:
            fresh = _fresh(label.last_seen, now)
            rows.append({
                "key": "printer:label-agent", "kind": "printer", "equipment_id": None, "inventory": "",
                "name": PRINTER_NAMES["tspl"], "pc": label.host, "ip": "", "service": AGENT_SERVICE, "version": "",
                "link": {"code": "ok", "text": "На связи", "tone": "ok"} if fresh
                else {"code": "stale", "text": "Нет связи с ПК", "tone": "danger"},
                "last_seen": _moment(label.last_seen, now), "device": label.printer,
                "state": {"text": "Ошибка печати", "tone": "danger"} if label.last_error
                else {"text": "Принтер виден" if fresh else "—", "tone": "ok" if fresh else "neutral"},
                "card": False,
            })
    return rows


def _device_rows(session, now) -> list[dict]:
    rows = []
    for device, agent in session.execute(select(DeviceRow, ScanAgentRow).join(
            ScanAgentRow, ScanAgentRow.machine_id == DeviceRow.machine_id)
            .order_by(DeviceRow.kind, ScanAgentRow.pc_name, DeviceRow.name)):
        rows.append({
            "key": f"device:{device.machine_id}:{device.kind}:{device.device_id}", "kind": "device",
            "equipment_id": None, "inventory": "", "name": DEVICE_NAMES.get(device.kind, "Устройство"),
            "pc": agent.pc_name, "ip": agent.ip_lan or agent.ip_seen, "service": AGENT_SERVICE, "version": agent.version,
            "link": _agent_link(agent, now), "last_seen": _moment(agent.last_seen, now), "device": device.name,
            "state": _present(device.present, device.last_seen, now, "Подключён", "Не найден"), "card": False,
        })
    return rows


@router.get("")
def machines(_principal: Reader, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        equipment = _equipment(session)
        rows = [*_machine_rows(session, equipment, now), *_printer_rows(session, equipment, now),
                *_device_rows(session, now)]
    return {"now": now.isoformat(), "rows": rows}


@router.get("/{equipment_id}")
def machine_card(equipment_id: UUID, _principal: Reader, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        link = session.get(rules.MachineLinkRow, equipment_id)
        if link is None:
            raise HTTPException(status_code=404, detail="Станок ещё не подключён к ERP")
        snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
        unit = next(iter(_equipment(session).get(_inventory(link.inventory_number), [])), None)
        code = rules.link_status(snapshot, now)
        reported = snapshot.reported_at if snapshot else None
        words = rules.snapshot_words(snapshot)
        return {
            "equipment_id": str(link.equipment_id), "inventory": link.inventory_number, "name": _name(unit),
            "pc": link.pc_name, "ip": link.ip_lan or link.ip_seen, "service": rules.KIND_NAMES[link.kind],
            "service_version": link.service_version, "bridge_version": link.bridge_version,
            "link": _tone(code), "reported_at": _moment(reported, now),
            "age_seconds": None if reported is None else max(0, int(rules.since(reported, now).total_seconds())),
            "error": snapshot.error if snapshot else "",
            # The last words stay visible when the link is lost, marked by `link` and `reported_at` as old.
            "live": rules.decode_ob019(words) if words else None,
        }


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/agent/report")
def agent_report(payload: ReportPayload, request: Request, bridge: Bridge, factory: Factory, now: Now) -> dict:
    inventory = _inventory(payload.inventory)
    if inventory not in rules.SUPPORTED:
        raise HTTPException(status_code=403, detail=f"{payload.inventory} не подключается к ERP")
    info = rules.BridgeInfo(machine_id=payload.machine_id, pc_name=payload.pc_name, ip_lan=payload.ip_lan,
                            ip_seen=_client_ip(request), bridge_version=payload.bridge_version,
                            service_version=payload.service_version)
    with factory() as uow:
        session = uow.session
        units = _equipment(session).get(inventory, [])
        if len(units) != 1:
            raise HTTPException(status_code=409, detail=(
                f"{inventory} нет в справочнике оборудования" if not units
                else f"{inventory} указан у нескольких записей справочника оборудования"))
        try:
            link, bound, accepted = rules.report(
                session, equipment_id=units[0].id, inventory=inventory, token_id=bridge.actor.id, info=info,
                snapshot=rules.Snapshot(boot=payload.boot, seq=payload.seq, link_state=payload.link,
                                        registers=payload.registers, error=payload.error),
                now=now)
        except rules.MachineRefused as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except rules.MachineReportInvalid as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if bound:
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=link.equipment_id, event_type="production.machine_bound", actor=bridge.actor,
                occurred_at=now, details={"inventory": inventory, "machine_id": link.machine_id, "pc_name": link.pc_name}))
        uow.commit()
    return {"ok": True, "accepted": accepted, "bound": bound}
