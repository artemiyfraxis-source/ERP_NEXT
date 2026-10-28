"""Сканер с любого ПК (owner 28.10.2026: «везде… универсальность, если вдруг ПК сменять»): the registry of scan agents.

Every PC that scans runs the same agent (`tools/print-agent`). It names itself (the Windows MachineGuid + the PC name),
and every ~2 minutes reports its version, the WIA scanners it sees right now (none — an empty list) and its LAN address;
the server adds the address it saw the agent from (`request.client.host`, the real one through Caddy's X-Forwarded-For).
A button press is routed to ONE agent (`route`):
  1. the live agents with a scanner (heard within LIVE_FOR) whose address equals the browser's — exactly one wins; several
     — the browser's remembered choice among them, else «choose»;
  2. no address matches — the browser's remembered agent when it is live with a scanner, else «choose» from all live ones;
  3. none live with a scanner — an old agent that has no id (`legacy` row, kept by its claims) still takes an untargeted
     job (rollout: the warehouse PC works until it is reinstalled), else «no_agent».
An agent claims the jobs targeted to it and untargeted ones; an old agent only the untargeted ones.

Install tokens (`issue_install` / `redeem_install`): an administrator issues a one-time secret (only its SHA-256 is
stored, valid INSTALL_TTL); the install script on the new PC redeems it once — the conditional UPDATE lets exactly one
caller win — and gets the agent's API token from the caller (workflows/scan_agent_install.py)."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Text, Uuid, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

LIVE_FOR = timedelta(minutes=3)
TOUCH_EVERY = timedelta(seconds=30)  # a claim (every 3 s) rewrites last_seen at most this often
INSTALL_TTL = timedelta(hours=1)
INSTALL_PREFIX = "frxi_"
LEGACY_PREFIX = "legacy:"
MACHINE_ID_MAX = 80

AGENT, LEGACY, CHOOSE, NO_AGENT = "agent", "legacy", "choose", "no_agent"


class ScanAgentRow(Base):
    """One PC with the scan agent (migration 0157)."""

    __tablename__ = "warehouse_scan_agents"
    __table_args__ = (CheckConstraint("machine_id <> ''", name="ck_warehouse_scan_agents_machine_id"),)

    machine_id: Mapped[str] = mapped_column(String(MACHINE_ID_MAX), primary_key=True)
    pc_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    ip_seen: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    ip_lan: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    scanners: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScanAgentInstallRow(Base):
    """A one-time install token (migration 0157): the hash only; used once, then dead."""

    __tablename__ = "warehouse_scan_agent_installs"
    __table_args__ = (
        CheckConstraint(
            "(used_at IS NULL AND used_by_machine IS NULL) OR (used_at IS NOT NULL AND used_by_machine IS NOT NULL)",
            name="ck_warehouse_scan_agent_installs_used_pair",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by_machine: Mapped[str | None] = mapped_column(String(MACHINE_ID_MAX), nullable=True)


@dataclass(frozen=True)
class AgentInfo:
    id: str
    pc: str
    scanners: list[str]
    version: str
    ip: str

    def view(self) -> dict:
        return {"id": self.id, "pc": self.pc, "scanners": self.scanners, "version": self.version}


@dataclass(frozen=True)
class Routing:
    kind: str  # agent / legacy / choose / no_agent
    agent_id: str | None = None
    agents: list[AgentInfo] = field(default_factory=list)  # choose: who to pick from
    idle: list[str] = field(default_factory=list)  # no_agent: PCs with a live agent but no scanner


class InstallRefused(Exception):
    """The install token is unknown, used or expired (403)."""


def _session(uow) -> Session:
    session = getattr(uow, "session", None)
    if session is None:
        raise RuntimeError("Unit of Work has not been entered")
    return session


def _clean(text: object, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def scanner_names(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    names = []
    for item in data if isinstance(data, list) else []:
        name = item.get("name") if isinstance(item, dict) else item
        if str(name or "").strip():
            names.append(str(name).strip())
    return names


def _info(row: ScanAgentRow) -> AgentInfo:
    return AgentInfo(id=row.machine_id, pc=row.pc_name or row.machine_id, scanners=scanner_names(row.scanners),
                     version=row.version, ip=row.ip_seen)


def heartbeat(uow, *, machine_id: str, pc_name: str, version: str, ip_seen: str, ip_lan: str, scanners: list,
              now: datetime) -> None:
    """The agent's report (start, then every ~2 min): created on the first one, rewritten after."""
    machine_id = _clean(machine_id, MACHINE_ID_MAX)
    if not machine_id or machine_id.startswith(LEGACY_PREFIX):
        raise ValueError("Нужен machine_id агента")
    cleaned = []
    for item in scanners[:20]:
        name = _clean(item.get("name") if isinstance(item, dict) else item, 200)
        if name:
            cleaned.append({"id": _clean(item.get("id", "") if isinstance(item, dict) else "", 300), "name": name})
    values = dict(pc_name=_clean(pc_name, 128), version=_clean(version, 32), ip_seen=_clean(ip_seen, 64),
                  ip_lan=_clean(ip_lan, 64), scanners=json.dumps(cleaned, ensure_ascii=False), legacy=False,
                  last_seen=now)
    with uow:
        session = _session(uow)
        row = session.get(ScanAgentRow, machine_id)
        if row is None:
            session.add(ScanAgentRow(machine_id=machine_id, first_seen=now, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
        uow.commit()


def touch(uow, *, machine_id: str | None, host: str, ip_seen: str, now: datetime) -> str | None:
    """A claim: the agent is alive. An agent with an id keeps its row fresh (at most every TOUCH_EVERY; an unknown id —
    a minimal row, its heartbeat follows); one without an id (the old version) keeps its `legacy:<host>` row. Returns
    the id the claim is made under (None — a legacy claim: untargeted jobs only)."""
    machine_id = _clean(machine_id, MACHINE_ID_MAX)
    if machine_id.startswith(LEGACY_PREFIX):
        machine_id = ""
    legacy = not machine_id
    key = LEGACY_PREFIX + (_clean(host, 100) or "?") if legacy else machine_id
    with uow:
        session = _session(uow)
        row = session.get(ScanAgentRow, key, populate_existing=True)
        if row is None:
            session.add(ScanAgentRow(machine_id=key, pc_name=_clean(host, 128), ip_seen=_clean(ip_seen, 64),
                                     legacy=legacy, first_seen=now, last_seen=now))
        else:
            fresh = session.execute(select(ScanAgentRow.machine_id).where(
                ScanAgentRow.machine_id == key, ScanAgentRow.last_seen >= now - TOUCH_EVERY)).first()
            if fresh is None:
                row.last_seen = now
                row.ip_seen = _clean(ip_seen, 64)
        uow.commit()
    return None if legacy else key


def _live(session: Session, now: datetime, *, legacy: bool) -> list[ScanAgentRow]:
    return list(session.scalars(
        select(ScanAgentRow).where(ScanAgentRow.legacy.is_(legacy), ScanAgentRow.last_seen >= now - LIVE_FOR)
        .order_by(ScanAgentRow.pc_name, ScanAgentRow.machine_id)))


def route(uow, *, browser_ip: str, remembered: str, now: datetime) -> Routing:
    """Where a press of «Сканировать» goes (see the module doc)."""
    with uow:
        session = _session(uow)
        live = _live(session, now, legacy=False)
        old = bool(_live(session, now, legacy=True))
        pairs = [(row.ip_seen, row.ip_lan, _info(row)) for row in live if scanner_names(row.scanners)]
        idle = [row.pc_name or row.machine_id for row in live if not scanner_names(row.scanners)]
    with_scanner = [agent for _seen, _lan, agent in pairs]
    ip = _clean(browser_ip, 64)
    ids = {agent.id for agent in with_scanner}
    by_ip = [agent for seen, lan, agent in pairs if ip and ip in (seen, lan)]
    if len(by_ip) == 1:
        return Routing(AGENT, by_ip[0].id)
    if len(by_ip) > 1:
        if remembered in {agent.id for agent in by_ip}:
            return Routing(AGENT, remembered)
        return Routing(CHOOSE, agents=by_ip)
    if remembered and remembered in ids:
        return Routing(AGENT, remembered)
    if with_scanner:
        return Routing(CHOOSE, agents=with_scanner)
    if old:
        return Routing(LEGACY)
    return Routing(NO_AGENT, idle=idle)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_install(uow, *, created_by: UUID, created_by_name: str, now: datetime) -> tuple[str, datetime]:
    """A fresh one-time install secret and when it dies."""
    secret = INSTALL_PREFIX + secrets.token_urlsafe(24)
    expires = now + INSTALL_TTL
    with uow:
        _session(uow).add(ScanAgentInstallRow(
            id=uuid4(), token_hash=_hash(secret), created_by=created_by, created_by_name=created_by_name[:255] or "—",
            created_at=now, expires_at=expires))
        uow.commit()
    return secret, expires


def redeem_install(uow, secret: str, machine_id: str, now: datetime) -> tuple[UUID, str]:
    """Spends the secret once (the conditional UPDATE: a second caller finds it used) → who issued it. InstallRefused —
    unknown, used or expired."""
    machine_id = _clean(machine_id, MACHINE_ID_MAX)
    if not machine_id or not secret.startswith(INSTALL_PREFIX) or len(secret) > 200:
        raise InstallRefused("Токен установки недействителен")
    with uow:
        session = _session(uow)
        taken = session.execute(
            update(ScanAgentInstallRow).where(
                ScanAgentInstallRow.token_hash == _hash(secret), ScanAgentInstallRow.used_at.is_(None),
                ScanAgentInstallRow.expires_at > now)
            .values(used_at=now, used_by_machine=machine_id).execution_options(synchronize_session=False))
        if taken.rowcount != 1:
            raise InstallRefused("Токен установки недействителен, уже использован или истёк — попросите новый")
        row = session.execute(select(ScanAgentInstallRow.created_by, ScanAgentInstallRow.created_by_name).where(
            ScanAgentInstallRow.token_hash == _hash(secret))).one()
        uow.commit()
    return row.created_by, row.created_by_name
