"""«Станки» (owner 18.11.2026: «в разделе производство — станки: всё, что мы подключили, и управление ими»), slice 1:
read only. A bridge on the machine PC (`tools/machine-agent/ob019-bridge.ps1`) reads an allow-list of PLC registers
through the local Modbus service and reports them to ERP outbound (pull transport, API token scope `machine:report`).

Binding (migration 0193): the first report of a `machine:report` token for a supported ОБ binds that token and that
PC to the ОБ (trust on first use, like the print agent's legacy tokens; tokens are issued by an administrator only).
From then on only the same token from the same PC reports for it — anything else is refused (403). A token serves one
ОБ (UNIQUE), an ОБ has one link (PK). The latest snapshot per ОБ is kept (no history in this slice); an older or
repeated sequence number of the same bridge run is ignored, never written over a newer one.

Decoding is pure (`decode_ob019`): raw 16-bit words → Russian states and values (register map:
docs/data/ob019-register-map-2026-11-18.md). Staleness is computed on read: no report for `STALE_AFTER` → «Нет связи
с ПК станка» (the E-stop of ОБ.019 also powers its PC off)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

SCOPE = "machine:report"
PROTO = 1
STALE_AFTER = timedelta(seconds=10)
KIND_OB019 = "ob019_modbus"
KINDS = (KIND_OB019,)
# The ОБ an ERP bridge exists for (inventory number → kind). Others are refused until a bridge is written for them.
SUPPORTED = {"ОБ.019": KIND_OB019}

# ОБ whose bridge is planned but not written yet: shown in «Станки» as «Мост не установлен» (owner 18.11.2026), never
# accepted as a reporter. ОБ.099 — servo crimp press BZW (Omron CP2E, BZW2.5 on its PC; docs/data/ob099-*). The
# catalogue keeps it as ОБ.099.1/ОБ.099.2, so the row takes its name from those units.
@dataclass(frozen=True)
class PlannedMachine:
    device: str
    pc: str = ""
    ip: str = ""


PLANNED = {
    "ОБ.099": PlannedMachine(device="ПЛК пресса", pc="DESKTOP-K6M7O0U", ip="192.168.1.82"),
    # BZW-886+Q2 cut/strip machine: a PLC with a vendor MES port, no ERP connection yet (memory bzw-machine-control).
    "ОБ.094": PlannedMachine(device="ПЛК станка"),
}
LINK_STATES = ("ok", "port_busy", "no_plc", "no_service")
# One name for every ERP agent on a PC (owner 18.11.2026: «Мост ERP» next to «Агент печати» confused him); the part
# after «·» says what it does there. The bridge and the print agent are still two programs — merging them is later.
AGENT_NAME = "Агент ERP"
AGENT_PARTS = ("печать", "приборы", "станок")
KIND_NAMES = {KIND_OB019: f"{AGENT_NAME} · станок"}
DEVICE_NAMES = {KIND_OB019: "ПЛК линии резки"}

# Registers the ОБ.019 bridge may read and send (Dn = holding register n). Mirrors `$script:Allowed` in
# ob019-bridge.ps1; 32-bit values take two words (low word first), strings 15 words.
OB019_ALLOWED: frozenset[int] = frozenset(
    {20, 22, 23, 24, 25, 26, 27, *range(32, 44), 641, 642, 643, 644, 645, 646, 649, 650, 711, 766, 782,
     *range(940, 970), 1280}
)
WORD_MAX = 0xFFFF

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
    "not_installed": ("Агент не установлен", "neutral"),
}


class MachineRefused(Exception):
    """The token or the PC may not report for this ОБ (403)."""


class MachineReportInvalid(ValueError):
    """The snapshot breaks the protocol: an address outside the allow-list, a value outside 16 bits (422)."""


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


def checked_registers(kind: str, raw: dict) -> dict[int, int]:
    """The words of a report: every address on the kind's allow-list, every value a 16-bit word."""
    allowed = OB019_ALLOWED if kind == KIND_OB019 else frozenset()
    result: dict[int, int] = {}
    for key, value in (raw or {}).items():
        try:
            address = int(str(key))
        except ValueError:
            raise MachineReportInvalid(f"Адрес регистра «{key}» — не число") from None
        if address not in allowed:
            raise MachineReportInvalid(f"Регистр D{address} не входит в список чтения станка")
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= WORD_MAX:
            raise MachineReportInvalid(f"Значение D{address} — не 16-битное слово")
        result[address] = value
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
        raise MachineReportInvalid("Нужны идентификатор запуска агента станка и номер снимка")
    link, bound = session.get(MachineLinkRow, equipment_id), False
    if link is None:
        taken = session.scalar(select(MachineLinkRow.inventory_number).where(MachineLinkRow.token_id == token_id))
        if taken is not None:
            raise MachineRefused(f"Токен агента станка уже привязан к {taken}")
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
            raise MachineRefused(f"{inventory} уже привязан к другому агенту станка")
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


# Settings of ОБ.019 grouped like its PXS_HMI pages (register map docs/data/ob019-register-map-2026-11-18.md, PR #919):
# (label, first register, kind, scale, unit). A group whose registers are not on the bridge's allow-list yet shows its
# fields without values — the bridge reads them once the allow-list grows (editing comes with the commands slice).
SettingField = tuple[str, int, str, float, str]
OB019_SETTINGS: tuple[tuple[str, tuple[SettingField, ...]], ...] = (
    ("Задание", (
        ("Тираж", 22, "u32", 1, "шт"), ("Пучок", 26, "u32", 1, "шт"), ("Пауза между пучками", 28, "u16", 0.001, "с"),
        ("Программа", 1280, "u16", 1, ""), ("Заказ", 940, "text15", 1, ""), ("Изделие", 955, "text15", 1, ""),
        ("Количество по заказу", 24, "u32", 1, "шт"),
    )),
    ("Провод и зачистка", (
        ("Длина", 32, "u32", 0.01, "мм"), ("Зачистка начала", 34, "u32", 0.01, "мм"),
        ("Стягивание начала", 36, "u32", 0.01, "мм"), ("Зачистка конца", 38, "u32", 0.01, "мм"),
        ("Стягивание конца", 40, "u32", 0.01, "мм"), ("Диаметр провода", 42, "u16", 0.01, "мм"),
        ("Отвод ножа", 43, "u16", 0.01, "мм"),
    )),
    ("Средняя зачистка", (
        ("Средняя зачистка", 132, "bit0", 1, ""), ("Поправка конца", 197, "i16", 0.01, "мм"),
        ("Поправка диаметра", 199, "i16", 0.01, "мм"),
    )),
    ("Маркировка", (
        ("Маркировка", 400, "bit0", 1, ""), ("Расстояние до головки", 401, "u32", 0.01, "мм"),
        ("Метка у начала", 405, "u32", 0.01, "мм"), ("Метка у конца", 407, "u32", 0.01, "мм"),
        ("Шаг средних меток", 409, "u32", 0.01, "мм"), ("Число средних меток", 411, "u32", 1, "шт"),
        ("Скорость роликов при печати", 415, "u16", 1, ""), ("Текст у начала", 440, "text15", 1, ""),
        ("Текст в середине", 455, "text15", 1, ""), ("Текст у конца", 470, "text15", 1, ""),
    )),
    ("Скорости", (
        ("Подача провода", 100, "u16", 1, ""), ("Нож", 101, "u16", 1, ""), ("Трубка", 102, "u16", 1, ""),
        ("Передний прижим", 103, "u16", 1, ""), ("Задний прижим", 104, "u16", 1, ""),
        ("Замедление при резке", 120, "u16", 1, ""), ("Зачистка", 121, "u16", 1, ""), ("Вывод провода", 122, "u16", 1, ""),
    )),
)
# Axis positions (Jog page): read by the HMI, not yet by the bridge; the scale is not confirmed at the machine.
OB019_POSITIONS: tuple[SettingField, ...] = (
    ("Передние ролики", 660, "i32", 0.01, "мм"), ("Задние ролики", 662, "i32", 0.01, "мм"), ("Нож", 664, "i32", 0.01, "мм"),
    ("Трубка", 666, "i32", 0.01, "мм"), ("Передний прижим", 668, "i32", 0.01, "мм"),
    ("Задний прижим", 670, "i32", 0.01, "мм"), ("Сдвиг ножа", 672, "i32", 0.01, "мм"),
)
_WORDS = {"u16": 1, "i16": 1, "bit0": 1, "u32": 2, "i32": 2, "text15": 15}


def _field_value(words: dict[int, int], address: int, kind: str, scale: float) -> object:
    if kind == "text15":
        return _text(words, address, 15)
    if any(address + offset not in words for offset in range(_WORDS[kind])):
        return None
    if kind == "bit0":
        return bool(words[address] & 1)
    raw = words[address] if _WORDS[kind] == 1 else words[address] | (words[address + 1] << 16)
    if kind == "i16" and raw >= 0x8000:
        raw -= 0x10000
    elif kind == "i32" and raw >= 0x80000000:
        raw -= 0x100000000
    return raw if scale == 1 else round(raw * scale, 3)


def _readable(address: int, kind: str) -> bool:
    return all(address + offset in OB019_ALLOWED for offset in range(_WORDS[kind]))


def setting_group(title: str, fields: tuple[SettingField, ...], words: dict[int, int]) -> dict:
    """One HMI page: its fields with the values of the last snapshot; a field's `read` — the bridge reads it."""
    return {"title": title, "fields": [
        {"label": label, "value": _field_value(words, address, kind, scale) if _readable(address, kind) else None,
         "unit": unit, "read": _readable(address, kind)}
        for label, address, kind, scale, unit in fields]}


def ob019_settings(words: dict[int, int]) -> list[dict]:
    return [setting_group(title, fields, words) for title, fields in OB019_SETTINGS]


def snapshot_words(snapshot: MachineSnapshotRow | None) -> dict[int, int]:
    if snapshot is None:
        return {}
    return {int(key): int(value) for key, value in (snapshot.registers or {}).items()}


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
