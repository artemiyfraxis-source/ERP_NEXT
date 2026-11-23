"""Device inventory from heartbeats. Queue polling never refreshes device liveness."""

from datetime import datetime, timedelta
import hashlib
import re
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.clock import as_utc

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
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_vna_control_version"),
        CheckConstraint("source IN ('s2vna','arinst')", name="ck_vna_control_source"),
        CheckConstraint("trim(serial) <> '' AND ((source = 'arinst' AND serial = 'arinst:' || hardware_uid AND hardware_uid <> '') OR (source = 's2vna' AND lower(serial) NOT LIKE 'arinst:%'))", name="ck_vna_control_serial"),
        CheckConstraint("license_state IN ('unknown','not_required','TRIAL','LOCKED','ACTIVATED')", name="ck_vna_control_license"),
        CheckConstraint("(license_state = 'TRIAL' AND trial_remaining_seconds IS NOT NULL AND trial_remaining_seconds BETWEEN 0 AND 600) OR (license_state <> 'TRIAL' AND trial_remaining_seconds IS NULL)", name="ck_vna_control_trial"),
        CheckConstraint("source <> 'arinst' OR NOT control_ready OR (identity_verified AND version = 3 AND (license_state IN ('not_required','ACTIVATED') OR (license_state = 'TRIAL' AND trial_remaining_seconds > 0)))", name="ck_vna_control_ready"),
    )
    machine_id: Mapped[str] = mapped_column(String(80), ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True)
    serial: Mapped[str] = mapped_column(String(300), primary_key=True)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="", server_default="")
    firmware: Mapped[str] = mapped_column(String(80), nullable=False, default="", server_default="")
    hardware_uid: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    identity_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    license_state: Mapped[str] = mapped_column(String(12), nullable=False, default="unknown", server_default="unknown")
    trial_remaining_seconds: Mapped[int | None] = mapped_column(Integer)
    control_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    methods: Mapped[list] = mapped_column(JSON, nullable=False)
    # Bench ops of protocol 2 the agent implements (0205_vna_bench_sessions); bench commands go only to them.
    ops: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VnaIdentityRow(Base):
    """An explicit operator association, never inferred from a USB candidate or printed serial."""

    __tablename__ = "warehouse_vna_identities"
    __table_args__ = (
        UniqueConstraint("source", "hardware_uid", name="uq_vna_identity_uid"),
        UniqueConstraint("serial", name="uq_vna_identity_serial"),
        CheckConstraint("source = 'arinst' AND hardware_uid <> '' AND serial = 'arinst:' || hardware_uid", name="ck_vna_identity_serial"),
        CheckConstraint("trim(firmware) <> ''", name="ck_vna_identity_firmware"),
    )
    instrument_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_instruments.id", ondelete="RESTRICT"), primary_key=True)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    serial: Mapped[str] = mapped_column(String(300), nullable=False)
    hardware_uid: Mapped[str] = mapped_column(String(100), nullable=False)
    firmware: Mapped[str] = mapped_column(String(80), nullable=False)
    verified_machine_id: Mapped[str] = mapped_column(String(80), ForeignKey("warehouse_scan_agents.machine_id", ondelete="RESTRICT"), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)


def arinst_serial(uid: str) -> str:
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})-(?:0|[1-9][0-9]{0,9})-(?:0|[1-9][0-9]{0,9})", uid) or any(
            int(word) > 4294967295 for word in uid.split("-")):
        raise ValueError("Некорректный аппаратный UID Arinst")
    return "arinst:" + uid


def instrument_identity(session, instrument_id: UUID) -> VnaIdentityRow | None:
    return session.get(VnaIdentityRow, instrument_id)


def _observed_utc(value: datetime, now: datetime) -> datetime:
    # SQLite strips offsets from the ERP clock's Moscow wall time.
    return as_utc(value if value.tzinfo else value.replace(tzinfo=now.tzinfo))


def associate_vna(uow, *, instrument_id: UUID, serial: str, machine_id: str, actor: Actor, now: datetime) -> None:
    with uow:
        session = _session(uow)
        control = session.get(VnaControlRow, (machine_id, serial))
        device = session.get(DeviceRow, (machine_id, "arinst", serial))
        if (not control or control.source != "arinst" or not control.identity_verified or not device or not device.present
                or _observed_utc(control.reported_at, now) < as_utc(now) - LIVE_FOR
                or _observed_utc(device.last_seen, now) < as_utc(now) - LIVE_FOR):
            raise ScanJobClosed("Сначала подтвердите UID подключённого Arinst через агент ERP")
        existing = instrument_identity(session, instrument_id)
        if existing:
            if existing.serial != serial:
                raise ScanJobClosed("СИ уже связано с другим аппаратным UID — проверьте привязку")
            return
        if session.scalar(select(VnaIdentityRow).where(VnaIdentityRow.serial == serial)):
            raise ScanJobClosed("Аппаратный UID уже связан с другим СИ")
        try:
            with session.begin_nested():
                session.add(VnaIdentityRow(instrument_id=instrument_id, source="arinst", serial=serial,
                    hardware_uid=control.hardware_uid, firmware=control.firmware, verified_machine_id=machine_id,
                    verified_at=now, verified_by=actor.id))
                session.flush()
        except IntegrityError:
            raise ScanJobClosed("Привязка СИ изменилась — проверьте прибор ещё раз") from None
        uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=instrument_id, event_type="warehouse.vna_identity_verified",
            actor=actor, occurred_at=now, details={"source": "arinst", "serial": serial, "machine_id": machine_id}))
        uow.commit()


def _claim_binding(session, serial, machine_id):
    if session.get(VnaBindingRow, serial) is None:
        try:
            with session.begin_nested():
                session.add(VnaBindingRow(serial=serial, machine_id=machine_id))
                session.flush()
        except IntegrityError:
            if session.get(VnaBindingRow, serial) is None:
                raise


def control_heartbeat(uow, machine_id: str, capability: dict | None, now: datetime,
                      *, inventory: dict | None = None, candidates: list[dict] | None = None) -> None:
    with uow:
        session = _session(uow)
        if inventory is not None:
            reports = inventory["devices"]
            session.execute(delete(VnaControlRow).where(VnaControlRow.machine_id == machine_id))
            session.execute(update(DeviceRow).where(DeviceRow.machine_id == machine_id,
                DeviceRow.kind.in_(("s2vna", "arinst")), DeviceRow.device_id != "arinst").values(present=False))
        else:
            session.execute(delete(VnaControlRow).where(VnaControlRow.machine_id == machine_id, VnaControlRow.source == "s2vna"))
            owned = list(session.scalars(select(DeviceRow).join(VnaBindingRow,
                (DeviceRow.device_id == VnaBindingRow.serial) & (DeviceRow.machine_id == VnaBindingRow.machine_id)).where(
                DeviceRow.machine_id == machine_id, DeviceRow.kind == "s2vna", DeviceRow.present.is_(True),
                DeviceRow.last_seen >= now - LIVE_FOR)))
            reports = [{**capability, "serial": owned[0].device_id, "name": owned[0].name, "source": "s2vna", "control_ready": True,
                        "identity_verified": True}] if capability and len(owned) == 1 else []
        for item in reports:
            reported = item.get("observed_at") or now
            if isinstance(reported, str):
                reported = datetime.fromisoformat(reported.replace("Z", "+00:00"))
            # Cached observations retain their age; reject clock skew before opening a route.
            if as_utc(reported) > as_utc(now) + timedelta(seconds=5):
                continue
            reported = min(as_utc(reported), as_utc(now)).astimezone(now.tzinfo)
            row = VnaControlRow(machine_id=machine_id, serial=item["serial"], source=item["source"],
                version=item["version"], methods=list(item.get("methods") or []),
                ops=list(item.get("ops") or []) if item["version"] in (2, 3) else [], reported_at=reported)
            for field in ("model", "firmware", "hardware_uid", "identity_verified", "license_state",
                          "trial_remaining_seconds", "control_ready", "reason"):
                if field in item:
                    setattr(row, field, item[field])
            session.add(row)
            if item.get("identity_verified") and item["serial"] != "arinst":
                _claim_binding(session, item["serial"], machine_id)
                device = session.get(DeviceRow, (machine_id, item["source"], item["serial"]))
                if device is None:
                    device = DeviceRow(machine_id=machine_id, kind=item["source"], device_id=item["serial"])
                    session.add(device)
                device.name = (f"Arinst,VNA-DL,{item['hardware_uid']},{item['firmware']}" if item["source"] == "arinst"
                               else item["name"])
                device.present, device.last_seen = True, reported
        if candidates is not None:
            session.execute(update(DeviceRow).where(DeviceRow.machine_id == machine_id,
                DeviceRow.kind == "arinst", DeviceRow.device_id.like("candidate:%")).values(present=False))
            for candidate in candidates:
                key = "candidate:" + hashlib.sha256(candidate["usb_path"].encode()).hexdigest()
                device = session.get(DeviceRow, (machine_id, "arinst", key))
                if device is None:
                    device = DeviceRow(machine_id=machine_id, kind="arinst", device_id=key)
                    session.add(device)
                device.name = f"USB {candidate['port']} — требуется подтверждение UID Arinst"
                device.present, device.last_seen = True, now
        uow.commit()


def _control(session, machine_id, serial, source):
    if serial:
        row = session.get(VnaControlRow, (machine_id, serial))
        return row if row and row.source == source else None
    # Compatibility for old Planar session callers, never select the first arbitrary device.
    rows = list(session.scalars(select(VnaControlRow).where(
        VnaControlRow.machine_id == machine_id, VnaControlRow.source == source)))
    return rows[0] if len(rows) == 1 and source == "s2vna" else None


def control_capability(session, machine_id: str, now: datetime, *, serial: str = "", source: str = "s2vna") -> list[str]:
    pc, control = session.get(ScanAgentRow, machine_id), _control(session, machine_id, serial, source)
    reported = control.reported_at if control else None
    if reported is not None and reported.tzinfo is None:
        reported = reported.replace(tzinfo=now.tzinfo)  # SQLite drops timezone information.
    try:
        installed = tuple(int(p) for p in pc.version.split(".")) if pc else ()
    except ValueError:
        installed = ()
    # Protocol 2 is a superset of 1: a v2 agent still serves v1 session commands.
    if (not pc or pc.legacy or len(installed) != 3 or installed <= (2, 7, 0) or not control
            or control.version not in ((3,) if source == "arinst" else (1, 2)) or reported < now - LIVE_FOR):
        raise ScanJobClosed("Агент не поддерживает актуальный протокол vna_control версии 1 или 2")
    if source == "arinst":
        remaining = (control.trial_remaining_seconds or 0) - (now - reported).total_seconds()
        if control.license_state == "LOCKED" or (control.license_state == "TRIAL" and remaining <= 0):
            raise ScanJobClosed("Время пробного режима вышло — переподключите прибор вручную или активируйте лицензию")
        if not control.control_ready or not control.identity_verified or control.license_state not in ("not_required", "TRIAL", "ACTIVATED"):
            raise ScanJobClosed(control.reason or "Arinst не готов к управлению через ERP")
    return control.methods


def control_ops(session, machine_id: str, now: datetime, *, serial: str = "", source: str = "s2vna") -> set[str] | None:
    """Bench ops of a live agent speaking exactly protocol 2; None for protocol 1 (bench sessions route by this)."""
    control_capability(session, machine_id, now, serial=serial, source=source)
    control = _control(session, machine_id, serial, source)
    return set(control.ops or ()) if control.version in (2, 3) else None


def idn_serial(name: str) -> str:
    parts = name.split(",")
    return parts[2].strip() if len(parts) == 4 and all(part.strip() for part in parts) else ""


def heartbeat(uow, machine_id: str, scanners: list[dict], vna: dict | None,
              sources: list[str] | None, now: datetime) -> None:
    reported = [("scan", item["id"].strip() or item["name"].strip(), item["name"].strip())
                for item in scanners if item["name"].strip()]
    if vna and vna["ready"] and idn_serial(vna["name"]) and not idn_serial(vna["name"]).lower().startswith("arinst:"):
        reported.append(("s2vna", idn_serial(vna["name"]), vna["name"]))
    if "arinst" in (sources or []):
        reported.append(("arinst", "arinst", "Arinst VNA-DL"))
    with uow:
        session = _session(uow)
        # Keep missing devices for an actionable offline message, but stop routing immediately.
        session.execute(update(DeviceRow).where(DeviceRow.machine_id == machine_id,
            or_(DeviceRow.kind.in_(("scan", "s2vna")), DeviceRow.device_id == "arinst")).values(present=False))
        for kind, device_id, name in set(reported):
            if kind == "s2vna":
                _claim_binding(session, device_id, machine_id)
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
                 actor: Actor, now: datetime, check_sessions: Callable[[Session], None], source: str = "s2vna") -> None:
    with uow:
        session = _session(uow)
        target_device = session.scalar(select(DeviceRow).where(
            DeviceRow.machine_id == target, DeviceRow.kind == source, DeviceRow.device_id == serial,
            DeviceRow.present.is_(True), DeviceRow.last_seen >= now - LIVE_FOR))
        if target == owner or target_device is None:
            raise ScanJobClosed("Выберите другой ПК, который сейчас сообщает этот серийный номер ВАЦ")
        moved = session.execute(update(VnaBindingRow).where(
            VnaBindingRow.serial == serial, VnaBindingRow.machine_id == owner).values(machine_id=target))
        if moved.rowcount != 1:
            raise ScanJobClosed("Привязка ВАЦ уже изменилась — проверьте прибор ещё раз")
        # The binding update serializes this cross-module guard against new sessions using control_station.
        check_sessions(session)
        session.execute(update(DeviceRow).where(DeviceRow.kind == source, DeviceRow.device_id == serial,
            DeviceRow.machine_id != target).values(present=False))
        # No queued or in-flight capture from the previous owner may survive a transfer.
        jobs = session.execute(select(ScanJobRow.id, ScanJobRow.vna_name, ScanJobRow.instrument_id).where(
            ScanJobRow.kind == "vna", ScanJobRow.source == source, ScanJobRow.target_machine_id == owner,
            ScanJobRow.status.in_(OPEN_STATUSES)))
        cancelled = [job.id for job in jobs if job.instrument_id == instrument_id or idn_serial(job.vna_name) == serial]
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
        if source == "s2vna" or serial.startswith("arinst:"):
            # A missing serial cannot identify a physical instrument.
            query = query.where(DeviceRow.device_id == serial)
        else:
            # The explicitly labelled legacy import route has no verified hardware identity.
            query = query.where(DeviceRow.device_id == "arinst")
        if online:
            query = query.where(DeviceRow.present.is_(True), DeviceRow.last_seen >= now - LIVE_FOR)
        rows = _session(uow).execute(query.order_by(DeviceRow.last_seen.desc(), ScanAgentRow.pc_name))
        return [{"id": pc.machine_id, "pc": pc.pc_name or pc.machine_id, "version": pc.version,
                 "vna": {"name": device.name, "ready": True, "reason": ""} if source == "s2vna" else None,
                 "vna_sources": [source], "last_seen": device.last_seen.isoformat()} for pc, device in rows]
