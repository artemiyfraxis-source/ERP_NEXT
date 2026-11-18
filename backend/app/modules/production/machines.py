"""«Станки» (owner 18.11.2026: «в разделе производство — станки: всё, что мы подключили, и управление ими»), slice 1:
read only. A bridge on the machine PC (`tools/machine-agent/ob019-bridge.ps1`) reads an allow-list of PLC registers
through the local Modbus service and reports them to ERP outbound (pull transport, API token scope `machine:report`).

Binding (migration 0193): the first report of a `machine:report` token for a supported ОБ binds that token and that
PC to the ОБ (trust on first use, like the print agent's legacy tokens; tokens are issued by an administrator only).
From then on only the same token from the same PC reports for it — anything else is refused (403). A token serves one
ОБ (UNIQUE), an ОБ has one link (PK). The latest snapshot per ОБ is kept (no history in this slice); an older or
repeated sequence number of the same bridge run is ignored, never written over a newer one.

Decoding is pure (`decode_ob019`; ОБ.099 — `ob099.decode`): raw 16-bit words → Russian states and values (register map:
docs/data/ob019-register-map-2026-11-18.md). Staleness is computed on read: no report for `STALE_AFTER` → «Нет связи
с ПК станка» (the E-stop of ОБ.019 also powers its PC off)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.modules.production import ob099
from app.shared.persistence import Base

SCOPE = "machine:report"
PROTO = 1
STALE_AFTER = timedelta(seconds=10)
KIND_OB019 = "ob019_modbus"
KINDS = (KIND_OB019, ob099.KIND)
# The ОБ an ERP bridge exists for (inventory number → kind). Others are refused until a bridge is written for them.
# ОБ.099 — the BZW crimp presses (production/ob099.py; agent tools/machine-agent/ob099-agent.ps1, FINS read only).
SUPPORTED = {"ОБ.019": KIND_OB019, **{inventory: ob099.KIND for inventory in ob099.INVENTORIES}}
LINK_STATES = ("ok", "port_busy", "no_plc", "no_service")
KIND_NAMES = {KIND_OB019: "Мост ERP", ob099.KIND: "Агент ERP · станок"}
DEVICE_NAMES = {KIND_OB019: "ПЛК линии резки", ob099.KIND: "ПЛК пресса"}

# Registers the ОБ.019 bridge may read and send (Dn = holding register n). Mirrors `$script:Allowed` in
# ob019-bridge.ps1; 32-bit values take two words (low word first), strings 15 words.
OB019_ALLOWED: frozenset[int] = frozenset(
    {20, 22, 23, 24, 25, 26, 27, *range(32, 44), 641, 642, 643, 644, 645, 646, 649, 650, 711, 766, 782,
     *range(940, 970), 1280}
)
WORD_MAX = 0xFFFF
REGISTERS_MAX = max(len(OB019_ALLOWED), len(ob099.ALLOWED))

OB019_STATES = {
    100: ("Не сброшен — нужен сброс", "warn"),
    101: ("Идёт сброс", "info"),
    102: ("Готов, ждёт пуска", "ok"),
    103: ("Работает", "ok"),
    104: ("Пауза", "warn"),
    105: ("Аварийный стоп", "danger"),
    106: ("Пучок готов", "info"),
    107: ("Тираж готов", "ok"),
}
OB019_ALARMS = {
    0: "Аварий нет", 1: "Нож не вышел в ноль", 2: "Передний прижим не вышел в ноль", 3: "Трубка не вышла в ноль",
    4: "Пучок готов", 5: "Тираж готов", 6: "Нет провода", 7: "Ошибка параметров", 8: "Ошибка параметров разрезки",
    12: "Открыта защитная дверь, только пошагово", 13: "Задний прижим не вышел в ноль", 15: "Авария двигателя намотки",
    16: "Нет ответа от маркиратора", 17: "Ошибка связи с маркиратором", 18: "Авария размотчика",
    19: "Авария двигателя ножа",
}
OB019_NORMAL_ALARMS = {0, 4, 5}
OB019_MODES = {0: "Пошаговый", 1: "Один цикл", 2: "Автомат"}

LINK_TEXTS = {
    "ok": ("На связи", "ok"),
    "port_busy": ("Пульт PXS_HMI открыт — порт занят", "warn"),
    "no_plc": ("ПЛК не отвечает", "danger"),
    "no_service": ("Служба mbd на ПК не отвечает", "danger"),
    "stale": ("Нет связи с ПК станка", "danger"),
    "not_installed": ("Мост не установлен", "neutral"),
}


class MachineRefused(Exception):
    """The token or the PC may not report for this ОБ (403)."""


class MachineReportInvalid(ValueError):
    """The snapshot breaks the protocol: an address outside the allow-list, a value outside 16 bits (422)."""


class MachineNotBound(Exception):
    """Records came from a bridge before its first snapshot bound it (409)."""


class MachineLinkRow(Base):
    """One ОБ connected to ERP through a bridge (migration 0193)."""

    __tablename__ = "production_machine_links"
    __table_args__ = (
        CheckConstraint(f"kind IN ({', '.join(repr(kind) for kind in KINDS)})", name="ck_production_machine_links_kind"),
        CheckConstraint("trim(inventory_number) <> ''", name="ck_production_machine_links_inventory"),
        CheckConstraint("trim(machine_id) <> ''", name="ck_production_machine_links_machine"),
        CheckConstraint("version >= 1", name="ck_production_machine_links_version"),
    )

    equipment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT", name="fk_production_machine_links_equipment"),
        primary_key=True,
    )
    inventory_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    token_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("identity_api_tokens.id", ondelete="CASCADE", name="fk_production_machine_links_token"),
        nullable=False, unique=True,
    )
    machine_id: Mapped[str] = mapped_column(String(80), nullable=False)
    pc_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    ip_lan: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    ip_seen: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    bridge_version: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    service_version: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MachineSnapshotRow(Base):
    """The latest reading of a linked ОБ (migration 0193): raw words of the allow-list, as the bridge read them."""

    __tablename__ = "production_machine_snapshots"
    __table_args__ = (
        CheckConstraint(f"proto = {PROTO}", name="ck_production_machine_snapshots_proto"),
        CheckConstraint(f"link_state IN ({', '.join(repr(state) for state in LINK_STATES)})",
                        name="ck_production_machine_snapshots_link"),
        CheckConstraint("seq >= 0", name="ck_production_machine_snapshots_seq"),
        CheckConstraint("trim(boot) <> ''", name="ck_production_machine_snapshots_boot"),
    )

    equipment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("production_machine_links.equipment_id", ondelete="CASCADE",
                         name="fk_production_machine_snapshots_link"),
        primary_key=True,
    )
    link_state: Mapped[str] = mapped_column(String(16), nullable=False)
    boot: Mapped[str] = mapped_column(String(64), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    proto: Mapped[int] = mapped_column(Integer, nullable=False)
    registers: Mapped[dict] = mapped_column(JSON, nullable=False)
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class BridgeInfo:
    machine_id: str
    pc_name: str = ""
    ip_lan: str = ""
    ip_seen: str = ""
    bridge_version: str = ""
    service_version: str = ""


@dataclass(frozen=True)
class Snapshot:
    boot: str
    seq: int
    link_state: str
    registers: dict[int, int]
    error: str = ""


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _word(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= WORD_MAX:
        raise MachineReportInvalid(f"Значение {name} — не 16-битное слово")
    return value


def checked_registers(kind: str, raw: dict) -> dict:
    """The words of a report: every address on the kind's allow-list, every value a 16-bit word.
    ОБ.019 — Modbus holding registers by number; ОБ.099 — FINS words by area and address («D5006», «H60», «W80»)."""
    if kind == ob099.KIND:
        named: dict[str, int] = {}
        for key, value in (raw or {}).items():
            name = str(key).upper()
            if name not in ob099.ALLOWED:
                raise MachineReportInvalid(f"Слово {key} не входит в список чтения станка")
            named[name] = _word(name, value)
        return named
    allowed = OB019_ALLOWED if kind == KIND_OB019 else frozenset()
    result: dict[int, int] = {}
    for key, value in (raw or {}).items():
        try:
            address = int(str(key))
        except ValueError:
            raise MachineReportInvalid(f"Адрес регистра «{key}» — не число") from None
        if address not in allowed:
            raise MachineReportInvalid(f"Регистр D{address} не входит в список чтения станка")
        result[address] = _word(f"D{address}", value)
    return result


def report(session: Session, *, equipment_id: UUID, inventory: str, token_id: UUID, info: BridgeInfo,
           snapshot: Snapshot, now: datetime) -> tuple[MachineLinkRow, bool, bool]:
    """Bind on first use, check the binding, store the snapshot. → (link, bound now, snapshot accepted)."""
    kind = SUPPORTED.get(inventory)
    if kind is None:
        raise MachineRefused(f"{inventory} не подключается к ERP")
    machine_id = _clean(info.machine_id, 80)
    if not machine_id:
        raise MachineRefused("Нужен идентификатор ПК станка")
    registers = checked_registers(kind, snapshot.registers)
    if snapshot.link_state not in LINK_STATES:
        raise MachineReportInvalid("Неизвестное состояние связи")
    boot = _clean(snapshot.boot, 64)
    if not boot or snapshot.seq < 0:
        raise MachineReportInvalid("Нужны идентификатор запуска моста и номер снимка")
    link, bound = session.get(MachineLinkRow, equipment_id), False
    if link is None:
        taken = session.scalar(select(MachineLinkRow.inventory_number).where(MachineLinkRow.token_id == token_id))
        if taken is not None:
            raise MachineRefused(f"Токен моста уже привязан к {taken}")
        try:
            with session.begin_nested():
                session.add(MachineLinkRow(
                    equipment_id=equipment_id, inventory_number=inventory, kind=kind, token_id=token_id,
                    machine_id=machine_id, bound_at=now, version=1))
                session.flush()
            bound = True
        except IntegrityError:
            pass
        link = session.get(MachineLinkRow, equipment_id)
        if link is None:
            raise MachineRefused(f"{inventory} уже привязан к другому мосту")
    if link.token_id != token_id or link.machine_id != machine_id:
        raise MachineRefused(f"Этот ПК или токен не привязан к {link.inventory_number}")
    details = {
        "pc_name": _clean(info.pc_name, 128), "ip_lan": _clean(info.ip_lan, 64), "ip_seen": _clean(info.ip_seen, 64),
        "bridge_version": _clean(info.bridge_version, 32), "service_version": _clean(info.service_version, 64),
    }
    if any(getattr(link, name) != value for name, value in details.items()):
        session.execute(update(MachineLinkRow).where(MachineLinkRow.equipment_id == equipment_id,
                                                     MachineLinkRow.version == link.version)
                        .values(**details, version=link.version + 1))
    values = {"link_state": snapshot.link_state, "boot": boot, "seq": snapshot.seq, "proto": PROTO,
              "registers": {str(address): value for address, value in sorted(registers.items())},
              "error": _clean(snapshot.error, 500), "reported_at": now}
    accepted = session.execute(update(MachineSnapshotRow).where(
        MachineSnapshotRow.equipment_id == equipment_id,
        or_(MachineSnapshotRow.boot != boot, MachineSnapshotRow.seq < snapshot.seq),
    ).values(**values)).rowcount == 1
    if not accepted and session.get(MachineSnapshotRow, equipment_id) is None:
        try:
            with session.begin_nested():
                session.add(MachineSnapshotRow(equipment_id=equipment_id, **values))
                session.flush()
            accepted = True
        except IntegrityError:
            accepted = False  # a simultaneous report stored its snapshot first
    session.refresh(link)
    return link, bound, accepted


# --- reading ---

def _u32(words: dict[int, int], low: int) -> int | None:
    if low not in words or low + 1 not in words:
        return None
    return words[low] | (words[low + 1] << 16)


def _hundredths(value: int | None) -> float | None:
    return None if value is None else round(value / 100, 2)


def _text(words: dict[int, int], first: int, count: int) -> str | None:
    if any(address not in words for address in range(first, first + count)):
        return None
    raw = bytearray()
    for address in range(first, first + count):
        raw += bytes(((words[address] >> 8) & 0xFF, words[address] & 0xFF))
    text = raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    return text.strip()


def since(moment: datetime, now: datetime) -> timedelta:
    """Time from a stored moment to now; SQLite (tests, preview) returns it naive — in the zone of `now`."""
    if moment.tzinfo is None and now.tzinfo is not None:
        moment = moment.replace(tzinfo=now.tzinfo)
    elif moment.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=moment.tzinfo)
    return now - moment


def link_status(snapshot: MachineSnapshotRow | None, now: datetime) -> str:
    if snapshot is None:
        return "not_installed"
    if since(snapshot.reported_at, now) > STALE_AFTER:
        return "stale"
    return snapshot.link_state


def decode_ob019(words: dict[int, int]) -> dict:
    """Raw words of ОБ.019 → the live card's values; a word the snapshot lacks gives None («—» on the screen)."""
    state_code, alarm_code, mode_code = words.get(766), words.get(782), words.get(20)
    state_text, state_tone = OB019_STATES.get(state_code, (f"Код {state_code}", "neutral")) if state_code is not None \
        else ("—", "neutral")
    if alarm_code is None:
        alarm = {"code": None, "text": "—", "tone": "neutral"}
    else:
        alarm = {"code": alarm_code, "text": OB019_ALARMS.get(alarm_code, f"Код аварии {alarm_code}"),
                 "tone": "neutral" if alarm_code == 0 else "info" if alarm_code in OB019_NORMAL_ALARMS
                 else "warn" if alarm_code == 12 else "danger"}
    task_lamp = words.get(711)
    return {
        "state": {"code": state_code, "text": state_text, "tone": state_tone},
        "alarm": alarm,
        "mode": {"code": mode_code, "text": OB019_MODES.get(mode_code, f"Код {mode_code}") if mode_code is not None else "—"},
        "job": {
            "total_set": _u32(words, 22), "total_finished": _u32(words, 649), "batch_set": _u32(words, 26),
            "batch_finished": _u32(words, 645), "uph": _u32(words, 641), "program": words.get(1280),
            "order_quantity": _u32(words, 24), "order_finished": _u32(words, 643),
            "order_number": _text(words, 940, 15), "product": _text(words, 955, 15),
            "order_running": None if task_lamp is None else bool(task_lamp & 1),
        },
        "wire": {
            "length_mm": _hundredths(_u32(words, 32)), "strip_front_mm": _hundredths(_u32(words, 34)),
            "pull_front_mm": _hundredths(_u32(words, 36)), "strip_back_mm": _hundredths(_u32(words, 38)),
            "pull_back_mm": _hundredths(_u32(words, 40)),
            "diameter_mm": _hundredths(words.get(42)), "knife_back_mm": _hundredths(words.get(43)),
        },
    }


def snapshot_words(snapshot: MachineSnapshotRow | None) -> dict[int, int]:
    if snapshot is None:
        return {}
    return {int(key): int(value) for key, value in (snapshot.registers or {}).items()}


def decode(kind: str, snapshot: MachineSnapshotRow | None) -> dict | None:
    """The live card of a linked ОБ by its kind; None while no words came (link lost before the first reading)."""
    if snapshot is None or not snapshot.registers:
        return None
    if kind == ob099.KIND:
        return ob099.decode(ob099.words(snapshot.registers))
    return decode_ob019(snapshot_words(snapshot))


def bound_link(session: Session, *, inventory: str, token_id: UUID, machine_id: str) -> MachineLinkRow:
    """The link of an ОБ that this token and PC already reported a snapshot for — records need a bound bridge."""
    link = session.scalar(select(MachineLinkRow).where(MachineLinkRow.inventory_number == inventory))
    if link is None:
        raise MachineNotBound(f"{inventory}: сначала нужен снимок станка от его агента")
    if link.token_id != token_id or link.machine_id != _clean(machine_id, 80):
        raise MachineRefused(f"Этот ПК или токен не привязан к {link.inventory_number}")
    return link


def links(session: Session) -> list[tuple[MachineLinkRow, MachineSnapshotRow | None]]:
    rows = session.execute(select(MachineLinkRow, MachineSnapshotRow)
                           .join(MachineSnapshotRow, MachineSnapshotRow.equipment_id == MachineLinkRow.equipment_id,
                                 isouter=True)
                           .order_by(MachineLinkRow.inventory_number))
    return [(link, snapshot) for link, snapshot in rows]


def release(session: Session, inventory: str) -> bool:
    """Administrator (CLI): drop the binding of an ОБ so a replaced PC or a new token binds on its next report."""
    link = session.scalar(select(MachineLinkRow).where(MachineLinkRow.inventory_number == inventory))
    if link is None:
        return False
    snapshot = session.get(MachineSnapshotRow, link.equipment_id)
    if snapshot is not None:
        session.delete(snapshot)
        session.flush()
    session.delete(link)
    session.flush()
    return True
