"""HTTP «Станки» (Производство), slice 1 — read only; rules and storage in production/machines.py.

- `GET /api/production/machines` (Производство or Задания, view) → `{now, rows, families}`: one row per machine of
  «Оборудование» — a БД.ОБ unit with the checkbox «Показывать в «Оборудовании»» or any unit an ERP agent reaches
  (owner 19.11.2026), with its article, site, operations and family (`families` — their order), `controlled`, setup
  class and open setup tasks — then marking and label printers of the print agents,
  scanners and network analysers the agents see. One value per column: № (ОБ or СИ), Название, ПК, IP, Служба, Версия,
  Связь, Последний сигнал, Устройство, Состояние, Также на ПК. One row per physical device: an analyser several PCs
  report (by serial / the registry СИ) stands on the PC with the latest real presence, the others are named in «Также
  на ПК» (owner 18.11.2026). ОБ with a planned bridge (`rules.PLANNED`) show «Агент не установлен». Column «Агент»: one name «Агент ERP · <parts>»
  per PC (печать, приборы, станок — whatever that PC does), whichever program it is.
- `GET /api/production/machines/{equipment_id}` (same gate) → the live card of a bridged ОБ (decoded PLC words by
  `kind`); a press ОБ.099 adds `today` — its crimps OK/NG with NG reasons and its alarm journal for the shop day.
- `POST /api/production/machines/agent/report` — the bridge only: `Authorization: Bearer <token with machine:report>`;
  a person's session is refused (403), so is a token or PC not bound to that ОБ (403). Body: `{proto, inventory,
  machine_id, pc_name, ip_lan, bridge_version, service_version, boot, seq, link, error, registers: {"766": 102, …}}`
  → `{ok, accepted, bound}`; `accepted: false` — an older snapshot of the same bridge run, ignored. ОБ.099 sends FINS
  words keyed by area («D5006», «H60», «W80»).
- `POST /api/production/machines/agent/events` — the same bridge, after its first snapshot bound it (409 before):
  append-only records of an ОБ.099 press `{proto, inventory, machine_id, crimps: [{key, at, result, peak, …}],
  alarms: [{key, at, text}]}` → `{ok, crimps, alarms}` (how many were new; a key already stored is ignored)."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import func, select

from app.modules.equipment_setup.recipes import wire_key
from app.modules.production import machine_history as history
from app.modules.production import machines as rules
from app.modules.production import ob099
from app.modules.production.api import production_uow_factory
from app.modules.production.infrastructure import ProductionInstrumentRow
from app.modules.production.machine_load import normalized_site
from app.modules.references.equipment import ARTICLE_KEY, INV_KEY, NAME_KEYS, field
from app.modules.references.infrastructure import EquipmentMachineRow, EquipmentMeasuringRow, EquipmentUnitRow
from app.modules.warehouse.agent_release import version_tuple
from app.modules.warehouse.devices import DeviceRow, PrinterRow
from app.modules.warehouse.label_jobs import OPEN_STATUSES as LABEL_OPEN, LabelAgentRow, LabelJobRow
from app.modules.warehouse.marking_profiles import MarkingBindingRow, MarkingProfileRow
from app.modules.warehouse.scan_agents import LIVE_FOR, ScanAgentRow
from app.modules.warehouse.scan_jobs import DONE, ScanJobRow
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.persistence import AuditEventRow
from app.workflows.marking_labels import STATUS_LABELS
from app.workflows.ob019_setup import wire_label
from app.shared.http_auth import TOKEN_SCOPE_PREFIX, Principal, authenticated_principal, require_section

router = APIRouter(prefix="/api/production/machines", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
# A named gate of the access contract (tests/identity/test_level_gates.py DEPENDENCIES): the master and the fitter read.
machines_view_principal = require_section(("production", "myops"), "view", detail="Раздел «Станки» вам не открыт.")
Reader = Annotated[Principal, Depends(machines_view_principal)]
BRIDGE_ONLY = "Снимки станка присылает только агент ERP на ПК станка (токен API «machine:report»)."

PRINTER_NAMES = {"tspl": "Принтер этикеток", "gdi_png": "Принтер маркировки"}
DEVICE_NAMES = {"scan": "Сканер", "s2vna": "Анализатор цепей", "arinst": "Анализатор цепей Arinst"}
PRINT_SERVICE = f"{rules.AGENT_NAME} · печать"
DEVICE_SERVICE = f"{rules.AGENT_NAME} · приборы"
VNA_APPS = {"s2vna": "S2VNA", "arinst": "Arinst VL"}  # agent source → «Программа ВАЦ» of the СИ registry
ARINST_PRESENCE_VERSION = (2, 9, 7)  # from this agent «arinst» means the analyser is in use, not just installed


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
    registers: dict[str, int] = Field(default_factory=dict, max_length=rules.REGISTERS_MAX)


class CrimpPayload(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    at: AwareDatetime
    result: Literal["OK", "NG"]
    peak: float | None = Field(default=None, ge=0, le=1e7)
    equipment_no: str = Field(default="", max_length=ob099.TEXT_MAX)
    product: str = Field(default="", max_length=ob099.TEXT_MAX)
    wire: str = Field(default="", max_length=ob099.TEXT_MAX)
    terminal: str = Field(default="", max_length=ob099.TEXT_MAX)
    applicator: str = Field(default="", max_length=ob099.TEXT_MAX)
    scan_code: str = Field(default="", max_length=ob099.SCAN_MAX)


class AlarmPayload(BaseModel):
    key: str = Field(min_length=1, max_length=400)
    at: AwareDatetime
    text: str = Field(min_length=1, max_length=ob099.ALARM_TEXT_MAX)


class EventsPayload(BaseModel):
    proto: Literal[1]
    inventory: str = Field(min_length=1, max_length=64)
    machine_id: str = Field(min_length=1, max_length=80)
    crimps: list[CrimpPayload] = Field(default_factory=list, max_length=ob099.BATCH_MAX)
    alarms: list[AlarmPayload] = Field(default_factory=list, max_length=ob099.BATCH_MAX)


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


def _machine_state(kind: str, link_code: str, snapshot: rules.MachineSnapshotRow | None) -> dict:
    if link_code in ("stale", "not_installed"):
        return {"text": "—", "tone": "neutral"}
    decoded = rules.decode(kind, snapshot) if link_code == "ok" else None
    if decoded is None:
        return {"text": "Нет данных ПЛК", "tone": "neutral"}
    return {"text": decoded["state"]["text"], "tone": decoded["state"]["tone"]}


OPERATIONS_KEY = "Операции"
SITE_KEY = "Расположение"
NO_CATALOG = {"article": "", "site": "", "operations": [], "family": ""}


def _catalog(unit: EquipmentMachineRow | None) -> dict:
    """What БД.ОБ says about a machine for its tile: article (model), site ЦАР/ЖУК, operations and their family."""
    if unit is None:
        return dict(NO_CATALOG)
    fields = unit.fields or {}
    operations = [item.strip() for item in field(fields, OPERATIONS_KEY).split(",") if item.strip()]
    return {"article": field(fields, ARTICLE_KEY), "site": normalized_site(field(fields, SITE_KEY)),
            "operations": operations, "family": rules.machine_family(operations, _name(unit))}


SETUP_OPEN = ("New", "InProgress", "Blocked")
SETUP_CONFIGURED = "production.setup_configured"
NO_SETUP = {"code": None, "label": ""}


def _setup_classes(session) -> dict[UUID, dict]:
    """«Наладка: нет / простая / полная» of every unit with a setup model (equipment_setup_models, 0199); independent
    of the checkbox. A unit whose stored model breaks the rules gets no class rather than breaking the list."""
    from app.modules.equipment_setup.setup_model import REQUIRES_LABELS, SetupValidationError, effective
    from app.modules.equipment_setup.setup_model_infrastructure import SqlAlchemySetupModels
    store = SqlAlchemySetupModels(session)
    models, overrides = store.models(), store.overrides()
    found: dict[UUID, dict] = {}
    for unit_id, model_id in session.execute(select(EquipmentUnitRow.id, EquipmentUnitRow.model_id)):
        try:
            model = effective(models.get(model_id), overrides.get(unit_id))
        except SetupValidationError:
            model = None
        if model is not None:
            found[unit_id] = {"code": model.requires_setup, "label": REQUIRES_LABELS[model.requires_setup]}
    return found


def pending_setup(session) -> dict[str, int]:
    """«Ждёт наладки»: open setup tasks (Назначена / В работе / Заблокирована) per unit — a task leaves it when it is
    «Готова» («Подтвердить готовность наладки» or «Настроено по заданию») or «Отменена». Counted in SQL: the list polls."""
    from app.modules.equipment_setup.infrastructure import SetupTaskRow
    return {str(machine_id): count for machine_id, count in session.execute(
        select(SetupTaskRow.machine_id, func.count()).where(SetupTaskRow.status.in_(SETUP_OPEN))
        .group_by(SetupTaskRow.machine_id))}


def _machine_rows(session, equipment, printers: list[dict], now) -> list[dict]:
    """The machines of «Оборудование» (owner 19.11.2026: «просто галочку для вытягивания»): a unit of БД.ОБ
    «Оборудование» with the checkbox «Показывать в «Оборудовании»» (`show_in_equipment`, 0206) plus every unit an ERP
    agent reaches whatever the checkbox — a machine bridge (live state), a supported or planned bridge not installed
    yet («Агент не установлен»), the print agent of a marking machine's printer. `controlled` — ERP reaches it through
    an agent now (the tab «С управлением»); a ticked unit without one says «Нет подключения к ERP». Every row carries
    the БД.ОБ article, site, operations and family, its setup class and the open setup tasks waiting for it."""
    rows, shown = [], set()
    classes, pending = _setup_classes(session), pending_setup(session)

    def unit_of(inventory: str) -> EquipmentMachineRow | None:
        units = equipment.get(_inventory(inventory), [])
        return units[0] if len(units) == 1 else None

    def add(row: dict, unit: EquipmentMachineRow | None, controlled: bool) -> None:
        unit_id = row["equipment_id"]
        rows.append({**row, **_catalog(unit), "controlled": controlled,
                     "setup": classes.get(UUID(unit_id), NO_SETUP) if unit_id else NO_SETUP,
                     "pending_setup": pending.get(unit_id, 0) if unit_id else 0})

    for link, snapshot in rules.links(session):
        shown.add(_inventory(link.inventory_number))
        code = rules.link_status(snapshot, now)
        unit = next(iter(equipment.get(_inventory(link.inventory_number), [])), None)
        add({
            "key": f"machine:{link.equipment_id}", "kind": "machine", "equipment_id": str(link.equipment_id),
            "inventory": link.inventory_number, "name": _name(unit), "pc": link.pc_name,
            "ip": _address(link.ip_lan, link.ip_seen),
            "service": rules.KIND_NAMES[link.kind], "version": link.bridge_version, "link": _tone(code),
            "last_seen": _moment(snapshot.reported_at, now) if snapshot else None,
            "device": rules.DEVICE_NAMES[link.kind], "state": _machine_state(link.kind, code, snapshot),
            "card": True, "others": "",
        }, unit, True)
    for inventory, kind in rules.SUPPORTED.items():
        unit = unit_of(inventory)
        if _inventory(inventory) in shown or unit is None:
            continue
        shown.add(_inventory(inventory))
        add({
            "key": f"machine:{unit.id}", "kind": "machine", "equipment_id": str(unit.id), "inventory": inventory,
            "name": _name(unit), "pc": "", "ip": "", "service": rules.KIND_NAMES[kind], "version": "",
            "link": _tone("not_installed"), "last_seen": None, "device": rules.DEVICE_NAMES[kind],
            "state": {"text": "—", "tone": "neutral"}, "card": False, "others": "",
        }, unit, False)
    for inventory, planned in rules.PLANNED.items():
        if _inventory(inventory) in shown:
            continue
        shown.add(_inventory(inventory))
        unit = unit_of(inventory)
        add({
            "key": f"machine:planned:{inventory}", "kind": "machine", "equipment_id": str(unit.id) if unit else None,
            "inventory": inventory, "name": _planned_name(equipment, inventory), "pc": planned.pc, "ip": planned.ip,
            "service": rules.KIND_NAMES[rules.KIND_OB019], "version": "", "link": _tone("not_installed"),
            "last_seen": None, "device": planned.device, "state": {"text": "—", "tone": "neutral"}, "card": False,
            "others": "",
        }, unit, False)
    # A marking machine reaches ERP through the print agent of its bound printer (ОБ.101 and ОБ.102 share one Godex).
    by_key = {row["key"]: row for row in printers}
    by_unit = {str(binding.equipment_id): by_key[f"printer:{binding.machine_id}:{binding.printer}"]
               for binding in session.scalars(select(MarkingBindingRow))
               if f"printer:{binding.machine_id}:{binding.printer}" in by_key}
    for inventory, units in sorted(equipment.items()):
        if inventory in shown:
            continue
        for unit in units:
            printer = by_unit.get(str(unit.id))
            if printer is not None:  # a marking machine: its printer's agent is its connection to ERP
                add({**printer, "key": f"machine:{unit.id}", "kind": "machine", "equipment_id": str(unit.id),
                     "inventory": inventory, "name": _name(unit)}, unit, True)
            elif unit.show_in_equipment:
                add({
                    "key": f"machine:{unit.id}", "kind": "machine", "equipment_id": str(unit.id), "inventory": inventory,
                    "name": _name(unit), "pc": "", "ip": "", "service": "", "version": "",
                    "link": _tone("not_connected"), "last_seen": None, "device": "",
                    "state": {"text": "—", "tone": "neutral"}, "card": False, "others": "",
                }, unit, False)
    return rows


def _planned_name(equipment, inventory: str) -> str:
    """The catalogue name of the ОБ itself, or the one name its units (ОБ.099.1, ОБ.099.2) share."""
    key = _inventory(inventory)
    units = equipment.get(key) or [row for number, rows in equipment.items() if number.startswith(key + ".")
                                   for row in rows]
    names = {_name(unit) for unit in units} - {""}
    return names.pop() if len(names) == 1 else ""


# Address of a PC: the shop LAN first, then Tailscale; a virtual adapter's address (WSL/Hyper-V/Docker, a VPN tunnel —
# 172.16/12) or what a proxy shows the server only when nothing better is known (owner 18.11.2026: 172.19.0.1).
def _address_rank(text: str) -> int | None:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if address.version != 4 or address.is_loopback or address.is_link_local or address.is_unspecified:
        return None
    if address in ipaddress.ip_network("192.168.0.0/16"):
        return 0
    if address in ipaddress.ip_network("10.0.0.0/8"):
        return 1
    if address in ipaddress.ip_network("100.64.0.0/10"):
        return 2
    if address in ipaddress.ip_network("172.16.0.0/12"):
        return 4
    return 3


def _address(*candidates: str) -> str:
    ranked = [(rank, index, text) for index, text in enumerate(item.strip() for item in candidates if item)
              if (rank := _address_rank(text)) is not None]
    if ranked:
        return min(ranked)[2]
    return next((item.strip() for item in candidates if item and item.strip()), "")


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
            "pc": agent.pc_name, "ip": _address(agent.ip_lan, agent.ip_seen), "service": PRINT_SERVICE,
            "version": agent.version, "link": _agent_link(agent, now), "last_seen": _moment(agent.last_seen, now),
            "device": printer.name,
            "state": _present(printer.present, printer.last_seen, now, "Принтер виден", "Принтер не найден"),
            "card": False, "others": "", **_catalog(unit),
        })
    # The TSC of the warehouse PC: an agent that reports no printer list is known only by its singleton heartbeat.
    if session.scalar(select(PrinterRow.name).where(PrinterRow.kind == "tspl").limit(1)) is None:
        label = session.get(LabelAgentRow, 1)
        if label is not None:
            fresh = _fresh(label.last_seen, now)
            rows.append({
                "key": "printer:label-agent", "kind": "printer", "equipment_id": None, "inventory": "",
                "name": PRINTER_NAMES["tspl"], "pc": label.host, "ip": "", "service": PRINT_SERVICE, "version": "",
                "link": {"code": "ok", "text": "На связи", "tone": "ok"} if fresh
                else {"code": "stale", "text": "Нет связи с ПК", "tone": "danger"},
                "last_seen": _moment(label.last_seen, now), "device": label.printer,
                "state": {"text": "Ошибка печати", "tone": "danger"} if label.last_error
                else {"text": "Принтер виден" if fresh else "—", "tone": "ok" if fresh else "neutral"},
                "card": False, "others": "", **NO_CATALOG,
            })
    return rows


def _aware(moment: datetime | None, now: datetime) -> datetime:
    if moment is None:
        return datetime.min.replace(tzinfo=now.tzinfo)
    return moment.replace(tzinfo=now.tzinfo) if moment.tzinfo is None else moment


def _vna_registry(session) -> tuple[dict[str, ProductionInstrumentRow], ProductionInstrumentRow | None]:
    """СИ of the registry the agents' analysers are: S2VNA by serial; Arinst (no serial in its reports) only when the
    registry has exactly one Arinst VL instrument."""
    rows = list(session.scalars(select(ProductionInstrumentRow).where(ProductionInstrumentRow.vna_app != "")))
    by_serial = {row.serial_number.strip(): row for row in rows
                 if row.vna_app == VNA_APPS["s2vna"] and row.serial_number.strip()}
    arinst = [row for row in rows if row.vna_app == VNA_APPS["arinst"]]
    return by_serial, arinst[0] if len(arinst) == 1 else None


def _arinst_captures(session) -> dict[str, datetime]:
    """The last finished Arinst capture per PC — the file came from that PC, so the analyser was used there."""
    return {machine: finished for machine, finished in session.execute(
        select(ScanJobRow.target_machine_id, func.max(ScanJobRow.finished_at))
        .where(ScanJobRow.kind == "vna", ScanJobRow.source == "arinst", ScanJobRow.status == DONE,
               ScanJobRow.target_machine_id.is_not(None))
        .group_by(ScanJobRow.target_machine_id)) if machine and finished is not None}


@dataclass(frozen=True)
class _Presence:
    live: bool       # the PC reports the device now
    real: bool       # … and the report proves the device itself, not just its program
    captured: datetime | None


def _presence(device: DeviceRow, agent: ScanAgentRow, captures: dict[str, datetime], now: datetime) -> _Presence:
    """S2VNA reports a serial only with the Planar on USB (VID_2226) and its IDN read. Arinst before agent 2.9.7 meant
    «Arinst VL installed»; from 2.9.7 it needs Arinst VL running or a fresh trace — or a capture came from that PC."""
    live = bool(device.present) and _fresh(device.last_seen, now)
    captured = captures.get(device.machine_id) if device.kind == "arinst" else None
    real = live and (device.kind != "arinst" or captured is not None
                     or version_tuple(agent.version) >= ARINST_PRESENCE_VERSION)
    return _Presence(live, real, captured)


def _state(presence: _Presence) -> dict:
    if presence.real:
        return {"text": "Подключён", "tone": "ok"}
    if presence.live:
        return {"text": "Установлена программа", "tone": "warn"}
    return {"text": "Не найден", "tone": "warn"}


def _other_note(agent: ScanAgentRow, presence: _Presence) -> str:
    pc = agent.pc_name or agent.machine_id
    if presence.real:
        return f"{pc} — тоже сообщает"
    if presence.live:
        return f"{pc} — только программа, без прибора (агент {agent.version or 'старый'})"
    return f"{pc} — не найден"


def _device_rows(session, now) -> list[dict]:
    """One row per physical device. An analyser reported by several PCs (moved from one PC to another, or a PC with
    only its program installed) is one row on the PC with the latest real presence; the others go to «Также на ПК»."""
    by_serial, arinst = _vna_registry(session)
    captures = _arinst_captures(session)
    articles = {_inventory(field(row.fields or {}, INV_KEY, "inventoryNumber")): field(row.fields or {}, ARTICLE_KEY)
                for row in session.scalars(select(EquipmentMeasuringRow))}
    groups: dict[str, list[tuple[DeviceRow, ScanAgentRow]]] = {}
    instruments: dict[str, ProductionInstrumentRow | None] = {}
    for device, agent in session.execute(select(DeviceRow, ScanAgentRow).join(
            ScanAgentRow, ScanAgentRow.machine_id == DeviceRow.machine_id)):
        instrument = by_serial.get(device.device_id) if device.kind == "s2vna" else arinst if device.kind == "arinst" \
            else None
        if instrument is not None:
            key = f"instrument:{instrument.id}"
        elif device.kind in VNA_APPS:
            key = f"{device.kind}:{device.device_id}"  # same serial (or the one Arinst) from any PC — one analyser
        else:
            key = f"{device.machine_id}:{device.kind}:{device.device_id}"  # a scanner is seen by its own PC only
        groups.setdefault(key, []).append((device, agent))
        instruments[key] = instrument

    rows = []
    for key, pairs in groups.items():
        seen = [(device, agent, _presence(device, agent, captures, now)) for device, agent in pairs]
        # Latest real presence first: proven, then reported now, then the last capture, then the last report.
        seen.sort(key=lambda item: (item[2].real, item[2].live, _aware(item[2].captured, now),
                                    _aware(item[0].last_seen, now), item[1].pc_name or ""), reverse=True)
        (device, agent, presence), others = seen[0], seen[1:]
        instrument = instruments[key]
        rows.append({
            "key": f"device:{key}", "kind": "device", "equipment_id": None,
            "inventory": instrument.inventory_number if instrument else "",
            "name": instrument.name if instrument else DEVICE_NAMES.get(device.kind, "Устройство"),
            "pc": agent.pc_name, "ip": _address(agent.ip_lan, agent.ip_seen), "service": DEVICE_SERVICE,
            "version": agent.version, "link": _agent_link(agent, now),
            # The device's own last presence: for a device not found now it says since when.
            "last_seen": _moment(device.last_seen, now), "device": device.name,
            "state": _state(presence), "card": False,
            "others": "; ".join(_other_note(other_agent, other_presence) for _, other_agent, other_presence in others),
            **NO_CATALOG,
            "article": articles.get(_inventory(instrument.inventory_number), "") if instrument else "",
        })
    rows.sort(key=lambda row: (row["inventory"] == "", row["inventory"], row["name"], row["pc"]))
    return rows


# --- «Станки»: production machines and their cards (owner 18.11.2026: «я думал будут карточки станков, куда я могу
# зайти, посмотреть актуальные настройки, поработать с ними»). PCs, agents, instruments and scanners are the
# «ПК и агенты» tab (`rows`); `machines` holds production equipment only. ---

NOT_CONNECTED = {"text": "Не подключён", "tone": "neutral"}
NO_JOB = "Нет задания"
JOB_TONES = {"pending": "info", "taken": "info", "spooled": "ok", "printed": "ok", "failed": "danger", "unknown": "warn"}
EVENT_TEXTS = {"production.machine_bound": "Агент станка привязан к ПК {pc_name}",
               # Writes and corrections (slice 4, machine_writes_api.py) carry their own line.
               **{f"production.machine_{event}": "{text}" for event in (
                   "write_queued", "write_done", "write_failed", "offset_set", "etk_corrected", "run_verified", "run_closed")}}
JOURNAL_LIMIT = 30


def _unit_inventory(unit: EquipmentMachineRow) -> str:
    return _inventory(field(unit.fields or {}, INV_KEY, "inventoryNumber"))


def _ob019_summary(snapshot, now: datetime) -> dict:
    code = rules.link_status(snapshot, now)
    words = rules.snapshot_words(snapshot)
    if code != "ok" or not words:
        text, tone = rules.LINK_TEXTS[code]
        state, job, counter = {"text": text, "tone": tone}, "", {"value": None, "of": None, "today": False}
    else:
        decoded = rules.decode_ob019(words)
        state = {"text": decoded["state"]["text"], "tone": decoded["state"]["tone"]}
        running = decoded["job"]
        job = (running["order_number"] or running["product"] or "") if running["order_running"] else NO_JOB
        counter = {"value": running["total_finished"], "of": running["total_set"], "today": False}
    return {"state": state, "job": job, "counter": counter,
            "last_seen": _moment(snapshot.reported_at, now) if snapshot else None}


def _ob099_summary(snapshot, now: datetime) -> dict:
    """A BZW press ОБ.099.x in the list: its state, the product in the press and the total counter."""
    code = rules.link_status(snapshot, now)
    decoded = rules.decode(ob099.KIND, snapshot) if code == "ok" else None
    if decoded is None:
        text, tone = rules.LINK_TEXTS[code]
        state, job, counter = {"text": text, "tone": tone}, "", {"value": None, "of": None, "today": False}
    else:
        state = {"text": decoded["state"]["text"], "tone": decoded["state"]["tone"]}
        job = decoded["job"]["product"] or NO_JOB
        counter = {"value": decoded["counters"]["total_done"], "of": decoded["counters"]["total_set"], "today": False}
    return {"state": state, "job": job, "counter": counter,
            "last_seen": _moment(snapshot.reported_at, now) if snapshot else None}


def _start_of_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _marking_state(session, binding: MarkingBindingRow, now: datetime) -> dict:
    printer = session.get(PrinterRow, (binding.machine_id, binding.printer))
    agent = session.get(ScanAgentRow, binding.machine_id)
    target = (LabelJobRow.kind == "marking", LabelJobRow.target_machine_id == binding.machine_id,
              LabelJobRow.target_printer == binding.printer)
    waiting = dict(session.execute(select(LabelJobRow.status, func.count()).where(
        *target, LabelJobRow.status.in_(LABEL_OPEN)).group_by(LabelJobRow.status)).all())
    today = session.scalar(select(func.count()).select_from(LabelJobRow).where(
        *target, LabelJobRow.status.in_(("printed", "spooled")), LabelJobRow.finished_at >= _start_of_day(now))) or 0
    last = session.execute(select(LabelJobRow.status, LabelJobRow.finished_at, LabelJobRow.error).where(
        *target, LabelJobRow.finished_at.is_not(None)).order_by(LabelJobRow.finished_at.desc()).limit(1)).first()
    online = agent is not None and _fresh(agent.last_seen, now)
    present = printer is not None and bool(printer.present) and _fresh(printer.last_seen, now)
    queue = sum(waiting.values())
    if not online:
        state = {"text": "Нет связи с ПК", "tone": "danger"}
    elif not present:
        state = {"text": "Принтер не найден", "tone": "warn"}
    elif waiting.get("taken"):
        state = {"text": "Печатает", "tone": "ok"}
    else:
        state = {"text": "Готов", "tone": "ok"}
    seen = printer.last_seen if printer else agent.last_seen if agent else None
    return {
        "state": state, "job": f"В очереди: {queue}" if queue else NO_JOB,
        "counter": {"value": today, "of": None, "today": True}, "last_seen": _moment(seen, now),
        "printer": binding.printer, "present": present, "queue": queue, "agent": agent,
        "last_job": {"status": STATUS_LABELS.get(last.status, "Напечатано"), "tone": JOB_TONES.get(last.status, "ok"),
                     "finished_at": _moment(last.finished_at, now), "error": last.error or ""} if last else None,
    }


def _catalog_name(equipment, inventory: str) -> str:
    units = equipment.get(_inventory(inventory), [])
    return _name(units[0]) if len(units) == 1 else _planned_name(equipment, inventory)


def _machine_entries(session, equipment, now: datetime) -> dict[str, dict]:
    """Every production machine of «Станки» by inventory number: its summary and what its card needs."""
    found: dict[str, dict] = {}
    for link, snapshot in rules.links(session):
        press = link.kind == ob099.KIND
        found[link.inventory_number] = {"kind": "ob099" if press else "ob019", "equipment_id": str(link.equipment_id),
                                        "link": link, "snapshot": snapshot,
                                        **(_ob099_summary if press else _ob019_summary)(snapshot, now)}
    units = {row.id: row for rows in equipment.values() for row in rows}
    for binding in session.scalars(select(MarkingBindingRow)):
        unit = units.get(binding.equipment_id)
        inventory = _unit_inventory(unit) if unit else ""
        if inventory and inventory not in found:
            found[inventory] = {"kind": "marking", "equipment_id": str(binding.equipment_id), "binding": binding,
                                **_marking_state(session, binding, now)}
    for inventory in (*rules.SUPPORTED, *rules.PLANNED):
        if inventory not in found:
            own = equipment.get(_inventory(inventory), [])
            found[inventory] = {
                "kind": ("ob099" if rules.SUPPORTED[inventory] == ob099.KIND else "ob019")
                if inventory in rules.SUPPORTED else "planned",
                "equipment_id": str(own[0].id) if len(own) == 1 else None, "link": None, "snapshot": None,
                "state": NOT_CONNECTED, "job": "", "counter": {"value": None, "of": None, "today": False},
                "last_seen": None}
    for inventory, entry in found.items():
        entry["inventory"], entry["name"] = inventory, _catalog_name(equipment, inventory)
    return dict(sorted(found.items()))


def _machine_list(session, equipment, now: datetime) -> list[dict]:
    return [{"inventory": inventory, "name": entry["name"], "kind": entry["kind"], "equipment_id": entry["equipment_id"],
             "state": entry["state"], "job": entry["job"], "counter": entry["counter"], "last_seen": entry["last_seen"]}
            for inventory, entry in _machine_entries(session, equipment, now).items()]


def _journal_ob019(session, entry: dict, now: datetime) -> list[dict]:
    events = []
    snapshot = entry.get("snapshot")
    if snapshot is not None:
        words = rules.snapshot_words(snapshot) if entry["kind"] == "ob019" else {}
        alarm = rules.decode_ob019(words)["alarm"] if words else None
        if alarm and alarm["code"] not in (None, 0):
            events.append({"at": _moment(snapshot.reported_at, now), "text": "Сейчас: " + alarm["text"],
                           "tone": alarm["tone"]})
        if snapshot.error:
            events.append({"at": _moment(snapshot.reported_at, now), "text": rules.agent_error(snapshot.error), "tone": "warn"})
    if entry.get("equipment_id"):
        for event in session.scalars(select(AuditEventRow).where(
                AuditEventRow.aggregate_id == UUID(entry["equipment_id"]),
                AuditEventRow.event_type.like("production.machine%"))
                .order_by(AuditEventRow.occurred_at.desc()).limit(JOURNAL_LIMIT)):
            details = {"pc_name": "", **{key: str(value) for key, value in (event.details or {}).items()}}
            text = EVENT_TEXTS.get(event.event_type, "Событие станка").format_map(details)
            events.append({"at": _moment(event.occurred_at, now), "text": text.strip(), "tone": "neutral"})
    return events


def _journal_marking(session, binding: MarkingBindingRow, now: datetime) -> list[dict]:
    rows = session.execute(select(LabelJobRow.status, LabelJobRow.created_at, LabelJobRow.finished_at,
                                  LabelJobRow.sequence, LabelJobRow.error).where(
        LabelJobRow.kind == "marking", LabelJobRow.target_machine_id == binding.machine_id,
        LabelJobRow.target_printer == binding.printer)
        .order_by(func.coalesce(LabelJobRow.finished_at, LabelJobRow.created_at).desc()).limit(JOURNAL_LIMIT))
    journal = []
    for status, created, finished, sequence, error in rows:
        parts = (f"Этикетка {sequence}" if sequence else "Этикетка", STATUS_LABELS.get(status, "Напечатано"), error or "")
        journal.append({"at": _moment(finished or created, now), "text": " · ".join(part for part in parts if part),
                        "tone": JOB_TONES.get(status, "ok")})
    return journal


def _connection(entry: dict, now: datetime) -> dict:
    if entry["kind"] in ("ob019", "ob099") and entry.get("link") is not None:
        link = entry["link"]
        return {"pc": link.pc_name, "ip": _address(link.ip_lan, link.ip_seen), "agent": rules.KIND_NAMES[link.kind],
                "version": link.bridge_version, "link": _tone(rules.link_status(entry["snapshot"], now)),
                "last_seen": entry["last_seen"],
                "extra": [["Служба Modbus", link.service_version]] if entry["kind"] == "ob019" else [],
                "error": rules.agent_error(entry["snapshot"].error) if entry["snapshot"] else ""}
    if entry["kind"] == "marking":
        agent = entry["agent"]
        return {"pc": agent.pc_name if agent else "", "ip": _address(agent.ip_lan, agent.ip_seen) if agent else "",
                "agent": PRINT_SERVICE, "version": agent.version if agent else "",
                "link": _agent_link(agent, now) if agent else _tone("not_installed"), "last_seen": entry["last_seen"],
                "extra": [["Принтер", entry["printer"]]], "error": ""}
    planned = rules.PLANNED.get(entry["inventory"])
    return {"pc": planned.pc if planned else "", "ip": planned.ip if planned else "",
            "agent": rules.KIND_NAMES[rules.KIND_OB019], "version": "", "link": _tone("not_installed"),
            "last_seen": None, "extra": [], "error": ""}


@router.get("/card/{inventory}")
def machine_window(inventory: str, principal: Reader, factory: Factory, now: Now) -> dict:
    """The machine's card: «Состояние», «Настройки», «Наладка и рецепты», «Журнал», «Связь» — read only."""
    with factory() as uow:
        session = uow.session
        equipment = _equipment(session)
        entry = _machine_entries(session, equipment, now).get(_inventory(inventory))
        if entry is None:
            raise HTTPException(status_code=404, detail=f"{inventory} нет среди станков ERP")
        card = {key: entry[key] for key in ("inventory", "name", "kind", "equipment_id", "state", "job", "counter",
                                            "last_seen")}
        card.update({"live": None, "positions": None, "settings": [], "marking": None, "journal": [],
                     "connection": _connection(entry, now), "setup": entry["kind"] == "ob019"})
        if entry["kind"] == "ob019":
            words = rules.snapshot_words(entry["snapshot"])
            card["live"] = rules.decode_ob019(words) if words else None
            card["positions"] = rules.setting_group("Положение осей", rules.OB019_POSITIONS, words)
            card["settings"] = rules.ob019_settings(words)
            card["journal"] = _journal_ob019(session, entry, now)
        elif entry["kind"] == "ob099":
            card["live"] = rules.decode(ob099.KIND, entry["snapshot"])
            if entry["equipment_id"]:
                card["today"] = ob099.today(session, UUID(entry["equipment_id"]), now)
            card["journal"] = _journal_ob019(session, entry, now)
        elif entry["kind"] == "marking":
            binding = entry["binding"]
            profiles = session.scalars(select(MarkingProfileRow).where(
                MarkingProfileRow.equipment_id == binding.equipment_id).order_by(MarkingProfileRow.name))
            card["marking"] = {
                "printer": entry["printer"], "present": entry["present"], "queue": entry["queue"],
                "last_job": entry["last_job"],
                "profiles": [{"id": str(row.id), "name": row.name, "calibrated": row.calibrated, "version": row.version}
                             for row in profiles]}
            card["journal"] = _journal_marking(session, binding, now)
        from .equipment_setup_model import unit_setup  # lazy: that module reuses this one's gate

        card["setup_model"] = unit_setup(session, entry["inventory"], now, principal)
        return card


@router.get("")
def machines(_principal: Reader, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        equipment = _equipment(session)
        printers = _printer_rows(session, equipment, now)
        rows = [*_machine_rows(session, equipment, printers, now), *printers, *_device_rows(session, now)]
        machine_list = _machine_list(session, equipment, now)
    return {"now": now.isoformat(), "machines": machine_list, "rows": _agent_names(rows),
            "families": list(rules.FAMILIES)}


def _agent_names(rows: list[dict]) -> list[dict]:
    """«Агент ERP · печать, приборы» on every row of a PC that does several things (one name for the PC's agent)."""
    parts: dict[str, set[str]] = {}
    for row in rows:
        if row["pc"]:
            parts.setdefault(row["pc"].casefold(), set()).add(row["service"].split(" · ", 1)[-1])
    for row in rows:
        if row["pc"]:
            done = parts[row["pc"].casefold()]
            row["service"] = f"{rules.AGENT_NAME} · " + ", ".join(part for part in rules.AGENT_PARTS if part in done)
    return rows


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
        card = {
            "kind": link.kind, "equipment_id": str(link.equipment_id), "inventory": link.inventory_number,
            "name": _name(unit),
            "pc": link.pc_name, "ip": link.ip_lan or link.ip_seen, "service": rules.KIND_NAMES[link.kind],
            "service_version": link.service_version, "bridge_version": link.bridge_version,
            "link": _tone(code), "reported_at": _moment(reported, now),
            "age_seconds": None if reported is None else max(0, int(rules.since(reported, now).total_seconds())),
            "error": rules.agent_error(snapshot.error) if snapshot else "",
            # The last words stay visible when the link is lost, marked by `link` and `reported_at` as old.
            "live": rules.decode(link.kind, snapshot),
        }
        if link.kind == ob099.KIND:
            card["today"] = ob099.today(session, link.equipment_id, now)
        return card


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
        if accepted and link.kind == rules.KIND_OB019 and payload.link == "ok":
            # Speed history (machine_history): a row when the counter, state, run or setup changed.
            history.record(session, link, rules.checked_registers(link.kind, payload.registers), payload.boot,
                           payload.seq, now, wire_of=lambda text: wire_key(wire_label(text)))
        uow.commit()
    return {"ok": True, "accepted": accepted, "bound": bound}


@router.post("/agent/events")
def agent_events(payload: EventsPayload, bridge: Bridge, factory: Factory, now: Now) -> dict:
    inventory = _inventory(payload.inventory)
    if rules.SUPPORTED.get(inventory) != ob099.KIND:
        raise HTTPException(status_code=403, detail=f"{payload.inventory} не присылает поштучные записи")
    with factory() as uow:
        session = uow.session
        try:
            link = rules.bound_link(session, inventory=inventory, token_id=bridge.actor.id, machine_id=payload.machine_id)
        except rules.MachineRefused as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except rules.MachineNotBound as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        crimps, alarms = ob099.store(
            session, equipment_id=link.equipment_id,
            crimps=[ob099.Crimp(**item.model_dump()) for item in payload.crimps],
            alarms=[ob099.Alarm(**item.model_dump()) for item in payload.alarms], now=now)
        uow.commit()
    return {"ok": True, "crimps": crimps, "alarms": alarms}
