"""Device inventory from heartbeats. Queue polling never refreshes device liveness."""

from datetime import datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base
from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .scan_agents import LIVE_FOR, ScanAgentRow, strict_scanner_version
from .scan_jobs import FAILED, OPEN_STATUSES, ScanJobClosed, ScanJobRow, _session


class DeviceRow(Base):
    __tablename__ = "warehouse_agent_devices"
    __table_args__ = (
        CheckConstraint("kind IN ('scan', 's2vna', 'arinst')", name="ck_warehouse_agent_devices_kind"),
        CheckConstraint("device_id <> ''", name="ck_warehouse_agent_devices_id"),
    )

    machine_id: Mapped[str] = mapped_column(String(80), ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VnaBindingRow(Base):
    """The first reporter owns a serial until an administrator explicitly transfers it."""

    __tablename__ = "warehouse_vna_bindings"
    __table_args__ = (CheckConstraint("trim(serial) <> ''", name="ck_warehouse_vna_bindings_serial"),)

    serial: Mapped[str] = mapped_column(String(300), primary_key=True)
    machine_id: Mapped[str] = mapped_column(String(80), ForeignKey("warehouse_scan_agents.machine_id"), nullable=False)


class PrinterRow(Base):
    """Exact Windows queue identity, independent of the singleton warehouse heartbeat."""
    __tablename__ = "warehouse_agent_printers"
    __table_args__ = (
        # 'document' (0214): an A4 printer for documents — the МФУ of the warehouse.
        CheckConstraint("kind IN ('tspl', 'gdi_png', 'document')", name="ck_agent_printer_kind"),
        CheckConstraint("trim(name) <> ''", name="ck_agent_printer_name"),
    )
    machine_id: Mapped[str] = mapped_column(String(80), ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    driver: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    queue_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Why the agent holds this marking queue (0203): the peel-off check failed. Empty — no hold.
    hold: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")


def printer_heartbeat(uow, machine_id, printers, now):
    with uow:
        session = _session(uow)
        session.execute(update(PrinterRow).where(PrinterRow.machine_id == machine_id).values(present=False))
        for item in printers:
            row = session.get(PrinterRow, (machine_id, item["name"]))
            if row is None:
                row = PrinterRow(machine_id=machine_id, name=item["name"])
                session.add(row)
            row.driver, row.port, row.kind = item["driver"], item["port"], item["kind"]
            row.present, row.last_seen = item["present"], now
            row.hold = " ".join(str(item.get("hold") or "").split())[:500]
        uow.commit()


def printer_online(session, machine_id, name, now, kind="gdi_png"):
    return session.scalar(select(PrinterRow).join(ScanAgentRow).where(
        PrinterRow.machine_id == machine_id, PrinterRow.name == name, PrinterRow.kind == kind,
        PrinterRow.present.is_(True), PrinterRow.last_seen >= now - LIVE_FOR,
        ScanAgentRow.legacy.is_(False)))


class VnaControlRow(Base):
    """Separate opt-in protocol; capture capability never implies permission to write."""

    __tablename__ = "warehouse_vna_control"
    __table_args__ = (CheckConstraint("version >= 1", name="ck_vna_control_version"),)
    machine_id: Mapped[str] = mapped_column(String(80), ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    methods: Mapped[list] = mapped_column(JSON, nullable=False)
    # Bench ops of protocol 2 the agent implements (0205_vna_bench_sessions); bench commands go only to them.
    ops: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def control_heartbeat(uow, machine_id: str, capability: dict | None, now: datetime) -> None:
    with uow:
        session = _session(uow)
        row = session.get(VnaControlRow, machine_id)
        if capability is None:
            if row is not None:
                session.delete(row)
        else:
            if row is None:
                row = VnaControlRow(machine_id=machine_id)
                session.add(row)
            row.version, row.methods, row.reported_at = capability["version"], capability["methods"], now
            row.ops = list(capability.get("ops") or []) if capability["version"] == 2 else []
        uow.commit()


def control_capability(session, machine_id: str, now: datetime) -> list[str]:
    pc, control = session.get(ScanAgentRow, machine_id), session.get(VnaControlRow, machine_id)
    reported = control.reported_at if control else None
    if reported is not None and reported.tzinfo is None:
        reported = reported.replace(tzinfo=now.tzinfo)  # SQLite drops timezone information.
    try:
        installed = tuple(int(p) for p in pc.version.split(".")) if pc else ()
    except ValueError:
        installed = ()
    # Protocol 2 is a superset of 1: a v2 agent still serves v1 session commands.
    if (not pc or pc.legacy or len(installed) != 3 or installed <= (2, 7, 0) or not control
            or control.version not in (1, 2) or reported < now - LIVE_FOR):
        raise ScanJobClosed("Агент не поддерживает актуальный протокол vna_control версии 1 или 2")
    return control.methods


def control_ops(session, machine_id: str, now: datetime) -> set[str] | None:
    """Bench ops of a live agent speaking exactly protocol 2; None for protocol 1 (bench sessions route by this)."""
    control_capability(session, machine_id, now)
    control = session.get(VnaControlRow, machine_id)
    return set(control.ops or ()) if control.version == 2 else None


def idn_serial(name: str) -> str:
    parts = name.split(",")
    return parts[2].strip() if len(parts) == 4 and all(part.strip() for part in parts) else ""


def heartbeat(uow, machine_id: str, scanners: list[dict], vna: dict | None,
              sources: list[str] | None, now: datetime) -> None:
    reported = [("scan", item["id"].strip() or item["name"].strip(), item["name"].strip())
                for item in scanners if item["name"].strip()]
    if vna and vna["ready"] and idn_serial(vna["name"]):
        reported.append(("s2vna", idn_serial(vna["name"]), vna["name"]))
    if "arinst" in (sources or []):
        reported.append(("arinst", "arinst", "Arinst VNA-DL"))
    with uow:
        session = _session(uow)
        # Keep missing devices for an actionable offline message, but stop routing immediately.
        session.execute(update(DeviceRow).where(DeviceRow.machine_id == machine_id).values(present=False))
        for kind, device_id, name in set(reported):
            if kind == "s2vna" and session.get(VnaBindingRow, device_id) is None:
                try:
                    # The unique serial arbitrates simultaneous first reports across API processes.
                    with session.begin_nested():
                        session.add(VnaBindingRow(serial=device_id, machine_id=machine_id))
                        session.flush()
                except IntegrityError:
                    if session.get(VnaBindingRow, device_id) is None:
                        raise
            row = session.get(DeviceRow, (machine_id, kind, device_id))
            if row is None:
                row = DeviceRow(machine_id=machine_id, kind=kind, device_id=device_id)
                session.add(row)
            row.name, row.present, row.last_seen = name, True, now
        uow.commit()


def scanner_agents(uow, now: datetime) -> list[dict]:
    with uow:
        session = _session(uow)
        rows = session.execute(select(ScanAgentRow, DeviceRow).join(DeviceRow).where(
            ScanAgentRow.legacy.is_(False), DeviceRow.kind == "scan", DeviceRow.present.is_(True),
            DeviceRow.last_seen >= now - LIVE_FOR,
        ).order_by(ScanAgentRow.pc_name, DeviceRow.name, DeviceRow.device_id))
        agents = {}
        for pc, device in rows:
            if not strict_scanner_version(pc.version):
                continue
            agent = agents.setdefault(pc.machine_id, {
                "id": pc.machine_id, "pc": pc.pc_name or pc.machine_id, "version": pc.version,
                "live": True, "last_seen": device.last_seen.isoformat(), "scanners": [],
            })
            agent["scanners"].append({"id": device.device_id, "name": device.name})
        return list(agents.values())


def offline_scanners(uow, now: datetime) -> list[dict]:
    with uow:
        rows = _session(uow).execute(select(ScanAgentRow, DeviceRow).join(DeviceRow).where(
            ScanAgentRow.legacy.is_(False), DeviceRow.kind == "scan",
            or_(DeviceRow.present.is_(False), DeviceRow.last_seen < now - LIVE_FOR)))
        return [{"agent_id": pc.machine_id, "scanner_id": device.device_id,
                 "name": device.name, "pc": pc.pc_name or pc.machine_id} for pc, device in rows]


def vna_binding(uow, serial: str) -> dict | None:
    with uow:
        row = _session(uow).execute(select(VnaBindingRow, ScanAgentRow).join(ScanAgentRow).where(
            VnaBindingRow.serial == serial)).first()
        return {"id": row[0].machine_id, "pc": row[1].pc_name or row[0].machine_id} if row else None


def transfer_vna(uow, *, serial: str, owner: str, target: str, instrument_id: UUID,
                 actor: Actor, now: datetime, check_sessions: Callable[[Session], None]) -> None:
    with uow:
        session = _session(uow)
        target_device = session.scalar(select(DeviceRow).where(
            DeviceRow.machine_id == target, DeviceRow.kind == "s2vna", DeviceRow.device_id == serial,
            DeviceRow.present.is_(True), DeviceRow.last_seen >= now - LIVE_FOR))
        if target == owner or target_device is None:
            raise ScanJobClosed("Выберите другой ПК, который сейчас сообщает этот серийный номер ВАЦ")
        moved = session.execute(update(VnaBindingRow).where(
            VnaBindingRow.serial == serial, VnaBindingRow.machine_id == owner).values(machine_id=target))
        if moved.rowcount != 1:
            raise ScanJobClosed("Привязка ВАЦ уже изменилась — проверьте прибор ещё раз")
        # The binding update serializes this cross-module guard against new sessions using control_station.
        check_sessions(session)
        session.execute(update(DeviceRow).where(DeviceRow.kind == "s2vna", DeviceRow.device_id == serial,
            DeviceRow.machine_id != target).values(present=False))
        # No queued or in-flight capture from the previous owner may survive a transfer.
        jobs = session.execute(select(ScanJobRow.id, ScanJobRow.vna_name).where(
            ScanJobRow.kind == "vna", ScanJobRow.source == "s2vna", ScanJobRow.target_machine_id == owner,
            ScanJobRow.status.in_(OPEN_STATUSES)))
        cancelled = [job.id for job in jobs if idn_serial(job.vna_name) == serial]
        if cancelled:
            session.execute(update(ScanJobRow).where(ScanJobRow.id.in_(cancelled), ScanJobRow.status.in_(OPEN_STATUSES))
                .values(status=FAILED, finished_at=now, error="ВАЦ перенесён на другой ПК — снимите трассу ещё раз"))
        uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=instrument_id, event_type="warehouse.vna_transferred",
            actor=actor, occurred_at=now, details={"serial": serial, "from_machine_id": owner, "to_machine_id": target}))
        uow.commit()


def vna_agents(uow, now: datetime, source: str, serial: str = "", *, online: bool = True) -> list[dict]:
    with uow:
        query = select(ScanAgentRow, DeviceRow).join(DeviceRow).where(
            ScanAgentRow.legacy.is_(False), DeviceRow.kind == source)
        if source == "s2vna":
            # A missing serial cannot identify a physical instrument.
            query = query.where(DeviceRow.device_id == serial)
        if online:
            query = query.where(DeviceRow.present.is_(True), DeviceRow.last_seen >= now - LIVE_FOR)
        rows = _session(uow).execute(query.order_by(DeviceRow.last_seen.desc(), ScanAgentRow.pc_name))
        return [{"id": pc.machine_id, "pc": pc.pc_name or pc.machine_id, "version": pc.version,
                 "vna": {"name": device.name, "ready": True, "reason": ""} if source == "s2vna" else None,
                 "vna_sources": [source], "last_seen": device.last_seen.isoformat()} for pc, device in rows]
