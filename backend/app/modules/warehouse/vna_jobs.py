"""VNA transport on the shared PC-agent queue; VSWR interpretation stays in production.

The capability is an extension of the existing agent identity, not a second registry.
Like strict scanner routing, require an explicit live PC choice: NAT is not device identity.
"""

from datetime import datetime
import re
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, delete, select, update
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base

from .scan_agents import LIVE_FOR, ScanAgentRow
from .scan_jobs import CLAIMED, DONE, VNA_FILE_LIMIT, ScanJobClosed, ScanJobRow, _closed, _session, open_for_result


class AgentVnaRow(Base):
    __tablename__ = "warehouse_agent_vna"

    machine_id: Mapped[str] = mapped_column(String(80), ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    ready: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=lambda: ["s2vna"], server_default='["s2vna"]')


def heartbeat(uow, machine_id: str, vna: dict | None, now: datetime, sources: list[str] | None = None) -> None:
    if sources is None:
        sources = ["s2vna"] if vna else []  # Existing agents report S2VNA via `vna` alone.
    if any(source not in ("s2vna", "arinst") for source in sources) or (("s2vna" in sources) != (vna is not None)):
        raise ValueError("Некорректный список источников ВАЦ")
    with uow:
        session = _session(uow)
        if not sources:
            session.execute(delete(AgentVnaRow).where(AgentVnaRow.machine_id == machine_id))
        else:
            row = session.get(AgentVnaRow, machine_id)
            if row is None:
                row = AgentVnaRow(machine_id=machine_id)
                session.add(row)
            row.name, row.ready, row.reported_at = vna["name"] if vna else "", vna["ready"] if vna else False, now
            row.reason = vna.get("reason", "") if vna else ""
            row.sources = list(dict.fromkeys(sources))
        uow.commit()


def agents(uow, now: datetime) -> list[dict]:
    with uow:
        rows = _session(uow).execute(select(ScanAgentRow, AgentVnaRow).join(AgentVnaRow).where(
            ScanAgentRow.legacy.is_(False), ScanAgentRow.last_seen >= now - LIVE_FOR,
            AgentVnaRow.reported_at >= now - LIVE_FOR,
        ).order_by(ScanAgentRow.pc_name, ScanAgentRow.machine_id))
        return [{"id": pc.machine_id, "pc": pc.pc_name or pc.machine_id, "version": pc.version,
                 "vna": {"name": vna.name, "ready": vna.ready, "reason": vna.reason} if "s2vna" in vna.sources else None,
                 "vna_sources": vna.sources} for pc, vna in rows]


def complete(uow, job_id: UUID, content: bytes, file_name: str, now: datetime, machine_id: str) -> None:
    if not content or len(content) > VNA_FILE_LIMIT:
        raise ValueError("Файл ВАЦ пуст или больше 2 МиБ")
    file_name = re.split(r"[/\\]", file_name)[-1]
    if not file_name or len(file_name) > 255 or any(ord(char) < 32 for char in file_name):
        raise ValueError("Некорректное имя файла ВАЦ")
    with uow:
        session = _session(uow)
        job = open_for_result(session, job_id, now)
        if job.kind != "vna" or job.target_machine_id != machine_id:
            raise ScanJobClosed("Задание ВАЦ выдано другому ПК")
        if job.source == "arinst":
            if not file_name.lower().endswith((".s1p", ".s2p")):
                raise ValueError("Arinst должен передать файл .s1p или .s2p")
            name = "Arinst VNA-DL"
        else:
            if not file_name.lower().endswith(".s1p"):
                raise ValueError("ВАЦ должен передать файл .s1p")
            # S2VNA provenance only; the existing production upload parses and judges both sources.
            try:
                first = content.decode("utf-8-sig").splitlines()[0]
            except (UnicodeDecodeError, IndexError):
                raise ValueError("Файл ВАЦ не прочитан") from None
            match = re.fullmatch(r"! S2VNA (.{1,500}) \d{4}-\d{2}-\d{2}T\S+ (S11|S22)", first)
            if not match:
                raise ValueError("В файле ВАЦ нет сведений S2VNA о приборе и трассе S11/S22")
            name = match[1]
        done = session.execute(update(ScanJobRow).where(
            ScanJobRow.id == job_id, ScanJobRow.status == CLAIMED,
            ScanJobRow.kind == "vna", ScanJobRow.target_machine_id == machine_id,
        ).values(status=DONE, finished_at=now, capture_content=content, vna_name=name,
                 capture_file_name=file_name if job.source == "arinst" else "vna.s1p", error=""))
        if done.rowcount != 1:
            raise _closed(session, job_id)
        uow.commit()


def content(uow, job_id: UUID) -> bytes:
    with uow:
        return _session(uow).scalar(select(ScanJobRow.capture_content).where(
            ScanJobRow.id == job_id, ScanJobRow.kind == "vna", ScanJobRow.status == DONE,
        )) or b""
