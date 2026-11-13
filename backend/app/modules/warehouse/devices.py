"""Device inventory from heartbeats. Queue polling never refreshes device liveness."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, select, update
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base

from .scan_agents import LIVE_FOR, ScanAgentRow, strict_scanner_version
from .scan_jobs import _session


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
