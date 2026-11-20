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
from decimal import Decimal
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
# ОБ.099 — the BZW crimp presses ОБ.099.1/.2 (production/ob099.py; agent tools/machine-agent/ob099-agent.ps1, FINS read only).
SUPPORTED = {"ОБ.019": KIND_OB019, **{inventory: ob099.KIND for inventory in ob099.INVENTORIES}}

# ОБ whose bridge is planned but not written yet: shown in «Станки» as «Мост не установлен» (owner 18.11.2026), never
# accepted as a reporter. ОБ.099 has its own read-only agent now (SUPPORTED above).
@dataclass(frozen=True)
class PlannedMachine:
    device: str
    pc: str = ""
    ip: str = ""


PLANNED = {
    # BZW-886+Q2 cut/strip machine: a PLC with a vendor MES port, no ERP connection yet (memory bzw-machine-control).
    "ОБ.094": PlannedMachine(device="ПЛК станка"),
}
LINK_STATES = ("ok", "port_busy", "no_plc", "no_service")
# One name for every ERP agent on a PC (owner 18.11.2026: «Мост ERP» next to «Агент печати» confused him); the part
# after «·» says what it does there. The bridge and the print agent are still two programs — merging them is later.
AGENT_NAME = "Агент ERP"
AGENT_PARTS = ("печать", "приборы", "станок")
KIND_NAMES = {KIND_OB019: f"{AGENT_NAME} · станок", ob099.KIND: f"{AGENT_NAME} · станок"}
DEVICE_NAMES = {KIND_OB019: "ПЛК линии резки", ob099.KIND: "ПЛК пресса"}

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

# The vendor's operator panel program on the machine PC holds the bus: ERP neither writes nor commands. The screen never
# names the program (owner 19.11.2026).
HMI_OPEN = "Открыт пульт станка на ПК — закройте его, чтобы ERP могла управлять станком"
LINK_TEXTS = {
    "ok": ("На связи", "ok"),
    "port_busy": (HMI_OPEN, "warn"),
    "no_plc": ("ПЛК не отвечает", "danger"),
    "no_service": ("Служба mbd на ПК не отвечает", "danger"),
    "stale": ("Нет связи с ПК станка", "danger"),
    "not_installed": ("Агент не установлен", "neutral"),
    # A machine of БД.ОБ no ERP agent is planned for yet (owner 19.11.2026: «почему не все видны»).
    "not_connected": ("Нет подключения к ERP", "neutral"),
}

# Families of «Станки» (owner 19.11.2026): the machines of БД.ОБ grouped by what they do. The field «Операции» decides,
# the name only when it says nothing; the first family in this order wins («Автомат резка + опрессовка» → резка, «Пайка,
# Усадка» → пайка). Matched by word stems, so «Опрессовка терминалов» and «Ленточный обжим» are both опрессовка.
# «Резка» — everything that cuts, with stripping or crimping too; «Зачистка» — stripping and twisting without a cut
# (owner 20.11.2026: ОБ.100.x apart from ОБ.094–098).
FAMILY_STEMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Резка", ("резк",)),
    ("Зачистка", ("разделк", "свивк", "изоляци", "скрутк")),
    ("Опрессовка", ("опрессовк", "обжим")),
    ("Пайка и усадка", ("пайк", "лужен", "усадк")),
    ("Маркировка", ("маркир", "маркер", "этикет")),
)
# What the agents reach besides machines (owner 20.11.2026): network analysers are their own group, the printer and
# the MFP of the warehouse another; a printer bound to a marking machine stays in «Маркировка» as that machine.
ANALYSER_FAMILY = "Анализаторы цепей"
WAREHOUSE_PRINTERS = "Принтеры склада"
OTHER_FAMILY = "Прочее оборудование"
FAMILIES = (*(family for family, _stems in FAMILY_STEMS), ANALYSER_FAMILY, WAREHOUSE_PRINTERS, OTHER_FAMILY)


def machine_family(operations: list[str] | tuple[str, ...], name: str) -> str:
    for text in (", ".join(operations), name):
        words = text.casefold()
        for family, stems in FAMILY_STEMS:
            if any(stem in words for stem in stems):
                return family
    return OTHER_FAMILY


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
        CheckConstraint(f"last_state IS NULL OR last_state BETWEEN 0 AND {WORD_MAX}",
                        name="ck_production_machine_snapshots_last_state"),
        CheckConstraint("(last_state IS NULL) = (last_state_at IS NULL)",
                        name="ck_production_machine_snapshots_last_state_at"),
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
    # ОБ.019: the machine state D766 of the last snapshot read «На связи» and when (migration 0211). It outlives a
    # snapshot without words (the operator panel PXS_HMI holds the port): «Закрыть пульт на ПК» is decided on it
    # (production/machine_control.py `hmi_problem`). NULL — ERP never read the state of this machine.
    last_state: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_state_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    if kind == KIND_OB019 and snapshot.link_state == "ok" and 766 in registers:
        values |= {"last_state": registers[766], "last_state_at": now}  # kept as it was by a snapshot without words
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


# The agent's own error lines (ASCII, tools/machine-agent) → what people read; an unknown one stays as it came.
AGENT_ERRORS = (
    (("busy:", "PXS_HMI", "Access to the port", "denied"), HMI_OPEN),
    (("no reply", "timeout", "timed out"), "ПЛК не отвечает"),
    (("connection refused", "mbd unreachable", "No connection could be made"), "Служба обмена со станком на ПК не отвечает"),
)


def agent_error(text: str | None) -> str:
    """«mbd: busy: PXS_HMI is running, bus refused» → «Открыт пульт станка на ПК — закройте его…»."""
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    low = clean.lower()
    for needles, shown in AGENT_ERRORS:
        if any(needle.lower() in low for needle in needles):
            return shown
    return clean


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


# Settings of ОБ.019 grouped like its PXS_HMI pages (register map docs/data/ob019-register-map-2026-11-18.md,
# appendix «Все элементы PXS_HMI»), in the order of the vendor panel: Задание, Провод и зачистка, Средняя зачистка,
# Разделение жил, Намотка, Маркировка, Скорости (the last one is the speed block of the HMI page «Функции»). Labels are
# Russian renderings of the HMI captions (the Chinese caption decides where the English one is a copy-paste); a field
# the map gives no meaning for is «Параметр D<n> (назначение не установлено)» and is never written. D421.0 «Передача
# символов» is read only: the order transfer of PXS_HMI uses it (map §6, open question 8) until a sniffer shows how.
# `key`: ERP may write it from the card — the job, mode and quantities, the wire and strip lengths (slice 4, owner 19.11)
# and every named input of the five other pages (on-screen-keyboard inputs and switches of the HMI). The limits are the
# HMI's own input ranges (`minimum`/`maximum` in the field's units). A `bit0` switch is the low bit of its word: ERP
# sends only the intent (address, bit, 0/1) and mbd.ps1 reads the word and writes it back with that bit changed in one
# request (`setbit`, as the HMI does), so no other bit of the word is ever written from an old snapshot.
# Never writable (test): passwords D1682..D1687, the SHA-256 handshake D1203..D1211, the command bits D599/D600, the HMI
# page D730 and the date D1087..D1089 — mbd.ps1 writes the last ones itself for a held command, never on request.
@dataclass(frozen=True)
class SettingField:
    label: str
    address: int
    kind: str  # u16, i16, u32, i32, bit0, text15, mode
    scale: Decimal = Decimal(1)
    unit: str = ""
    key: str = ""
    maximum: Decimal | None = None
    minimum: Decimal = Decimal(0)

    @property
    def writable(self) -> bool:
        return bool(self.key)


def _f(label: str, address: int, kind: str, scale: str = "1", unit: str = "", key: str = "",
       maximum: str | None = None, minimum: str = "0") -> SettingField:
    return SettingField(label, address, kind, Decimal(scale), unit, key, None if maximum is None else Decimal(maximum),
                        Decimal(minimum))


def _unknown(address: int, kind: str, scale: str = "1", unit: str = "") -> SettingField:
    """A register the HMI shows without a caption the map could name: shown, never written."""
    return _f(f"Параметр D{address} (назначение не установлено)", address, kind, scale, unit)


MM, SEC = "0.01", "0.001"
# The register of the switch «Подъём подающих роликов» (1 = rollers lifted): D604.0 «Wheel» (Y18) of the Jog page — the owner's
# bench probe 19.11: one press lifts, the next lowers (D136.0 «Feed Wheel» is a cycle option, not this). Mirrors $script:FeedWheelAddress
# of ob019-bridge.ps1 (a backend test compares them).
OB019_FEED_WHEEL_ADDRESS = 604
OB019_SETTINGS: tuple[tuple[str, tuple[SettingField, ...]], ...] = (
    ("Задание", (
        _f("Режим", 20, "mode", key="mode"),
        _f("Тираж", 22, "u32", unit="шт", key="total_set", maximum="99999999"),
        _f("Пучок", 26, "u32", unit="шт", key="batch_set", maximum="99999999"),
        _f("Пауза между пучками", 28, "u16", SEC, "с", key="batch_delay_s", maximum="9.999"),
        _f("Количество по заказу", 24, "u32", unit="шт", key="order_quantity", maximum="99999999"),
        _f("Заказ", 940, "text15", key="order_number"),
        _f("Изделие", 955, "text15", key="product"),
        _f("Строка заказа", 938, "u32"),
        _f("Программа", 1280, "u16"),
    )),
    ("Провод и зачистка", (
        _f("Длина провода", 32, "u32", MM, "мм", key="length_mm", maximum="99999.99"),
        _f("Зачистка начала", 34, "u32", MM, "мм", key="strip_front_mm", maximum="999.99"),
        _f("Стягивание начала", 36, "u32", MM, "мм", key="pull_front_mm", maximum="999.99"),
        _f("Зачистка конца", 38, "u32", MM, "мм", key="strip_back_mm", maximum="999.99"),
        _f("Стягивание конца", 40, "u32", MM, "мм", key="pull_back_mm", maximum="999.99"),
        _f("Диаметр реза", 42, "u16", MM, "мм", key="diameter_mm", maximum="655.35"),
        _f("Отвод ножа", 43, "u16", MM, "мм", key="knife_back_mm", maximum="5.00"),
        # The inner layer of a multi-layer cable (FmMuli «Настройка зачистки», keyboard inputs; map §3.4: lengths u32
        # ×0,01 мм, diameter and knife back u16 ×0,01 мм — the outer block's types and HMI ranges). Written since the
        # runner 1.4.0 / mbd 1.4.0 (machine_writes.RUNNER_WORDS).
        _f("Внутренний слой: зачистка начала", 44, "u32", MM, "мм", key="inner_strip_front_mm", maximum="999.99"),
        _f("Внутренний слой: стягивание начала", 46, "u32", MM, "мм", key="inner_pull_front_mm", maximum="999.99"),
        _f("Внутренний слой: зачистка конца", 48, "u32", MM, "мм", key="inner_strip_back_mm", maximum="999.99"),
        _f("Внутренний слой: стягивание конца", 50, "u32", MM, "мм", key="inner_pull_back_mm", maximum="999.99"),
        _f("Внутренний слой: диаметр реза", 52, "u16", MM, "мм", key="inner_diameter_mm", maximum="655.35"),
        _f("Внутренний слой: отвод ножа", 53, "u16", MM, "мм", key="inner_knife_back_mm", maximum="5.00"),
        # CLAMP GAP.L./.R. (前轮间隙/后轮间隙): an input on the HMI, but the map gives no unit or scale — read only.
        _f("Зазор переднего прижима", 60, "u16"),
        _f("Зазор заднего прижима", 61, "u16"),
    )),
    # FmMcut: the switch «Midcut On» (中剥), the end and diameter corrections; the 4 × 8 table of the page has only row
    # numbers on the HMI — what its columns mean is not established.
    ("Средняя зачистка", (
        _f("Средняя зачистка", 132, "bit0", key="middle_strip"),
        _f("Поправка конца", 197, "i16", MM, "мм", key="middle_end_fix_mm", minimum="-5", maximum="5"),
        _f("Поправка диаметра", 199, "i16", MM, "мм", key="middle_dia_fix_mm", minimum="-5", maximum="5"),
        *(_unknown(first + 2 * row, "u32", MM, "мм") for first in (200, 216, 250, 266) for row in range(8)),
    )),
    # FmNcut (开排 — разделение): two switches without a caption, the lengths, intervals, cylinder delay and the die.
    ("Разделение жил", (
        _unknown(133, "bit0"), _unknown(142, "bit0"),
        _f("Разделение в начале", 69, "u32", MM, "мм", key="split_front_mm", maximum="9999.99"),
        _f("Разделение в конце", 71, "u32", MM, "мм", key="split_back_mm", maximum="999.99"),
        _f("Интервал разделения в начале", 67, "u16", MM, "мм", key="split_gap_front_mm", maximum="655.35"),
        _f("Интервал разделения в конце", 68, "u16", MM, "мм", key="split_gap_back_mm", maximum="655.35"),
        _f("Задержка цилиндра", 74, "u16", SEC, "с", key="split_delay_s", maximum="9.999"),
        _f("Шаг матрицы", 1703, "u32", MM, "мм", key="split_die_pitch_mm", maximum="9999.99"),
        _f("Ширина пуансона", 1705, "u16", MM, "мм", key="split_punch_width_mm", maximum="655.35"),
    )),
    # FmColl. D103 is also «Передний прижим» on «Скорости» (the HMI page «Функции» limits it to 0..99, FmColl does
    # not): the stricter limit holds for both keys, and one write may not carry both (plan_writes).
    ("Намотка", (
        _unknown(390, "bit0"),
        _f("Скорость намотки", 103, "u16", key="coil_speed", maximum="99"),
        _f("Поправка скорости", 395, "i16", key="coil_speed_fix", minimum="-9999", maximum="32767"),
        _f("Отвод при намотке", 391, "u16", MM, "мм", key="coil_back_mm", maximum="655.35"),
        _f("Хвост намотки", 392, "u32", MM, "мм", key="coil_end_mm", maximum="9999.99"),
        _f("Диаметр намотки", 394, "u16", MM, "мм", key="coil_dia_mm", maximum="655.35"),
        _f("Диаметр намотки, наименьший", 1750, "u16", MM, "мм", key="coil_dia_min_mm", maximum="655.35"),
        _f("Диаметр намотки, наибольший", 1751, "u16", MM, "мм", key="coil_dia_max_mm", maximum="655.35"),
        _f("Коэффициент намотки, наименьший", 1752, "u16", MM, key="coil_ratio_min", minimum="0.5", maximum="9.99"),
        _f("Коэффициент намотки, наибольший", 1753, "u16", MM, key="coil_ratio_max", minimum="0.5", maximum="9.99"),
    )),
    # FmPrint + FmPrintSTR; the switches and numbers named by the map §3.5 and the order table (AddrParam.bin).
    ("Маркировка", (
        _f("Маркировка", 400, "bit0", key="marking"),
        _f("Текст у начала", 440, "text15", key="mark_front"), _f("Текст в середине", 455, "text15", key="mark_middle"),
        _f("Текст у конца", 470, "text15", key="mark_back"),
        _f("Метка у начала", 405, "u32", MM, "мм", key="mark_pos_front_mm", maximum="9999.99"),
        _f("Метка у конца", 407, "u32", MM, "мм", key="mark_pos_back_mm", maximum="9999.99"),
        _f("Средние метки по шагу", 422, "bit0", key="mark_by_step"),
        _f("Шаг средних меток", 409, "u32", MM, "мм", key="mark_step_mm", maximum="9999.99"),
        _f("Число средних меток", 411, "u32", unit="шт", key="mark_count", maximum="9999"),
        _f("Длина текста у конца", 413, "u32", MM, "мм", key="mark_tail_mm", maximum="999.99"),
        _f("Расстояние до головки", 401, "u32", MM, "мм", key="mark_head_mm", maximum="9999.99"),
        _f("Удержание сигнала печати", 404, "u16", SEC, "с", key="mark_hold_s", maximum="9.999"),
        _f("Скорость роликов при печати", 415, "u16", key="mark_roll_speed", maximum="99"),
        _f("Передача текста в маркиратор", 421, "bit0"),
        _f("Ширина символа", 423, "u16", MM, "мм", key="mark_char_mm", maximum="99.99"),
        _f("Размер шрифта", 430, "i16", key="mark_font", minimum="1", maximum="6"),
        _f("Жирность", 431, "i16", key="mark_bold", minimum="1", maximum="9"),
        _f("Делитель частоты", 432, "i16", key="mark_divider", minimum="4", maximum="60"),
        _f("Поворот текста у начала", 434, "bit0", key="mark_turn_front"),
        _f("Поворот текста в середине", 435, "bit0", key="mark_turn_middle"),
        _f("Поворот текста у конца", 436, "bit0", key="mark_turn_back"),
        _f("Отход провода при маркировке", 1722, "u32", MM, "мм", key="mark_drop_mm", maximum="9999.99"),
    )),
    # The speed block of FmFunction, 0..99 each.
    ("Скорости", (
        _f("Подача провода", 100, "u16", key="speed_feed", maximum="99"),
        _f("Нож", 101, "u16", key="speed_cutter", maximum="99"),
        _f("Трубка", 102, "u16", key="speed_pipe", maximum="99"),
        _f("Передний прижим", 103, "u16", key="speed_clamp_front", maximum="99"),
        _f("Задний прижим", 104, "u16", key="speed_clamp_back", maximum="99"),
        _f("Замедление при резке", 120, "u16", key="speed_cut_slow", maximum="99"),
        _f("Зачистка", 121, "u16", key="speed_strip", maximum="99"),
        _f("Вывод провода", 122, "u16", key="speed_out", maximum="99"),
        _f("Прокатка плёнки", 123, "u16", key="speed_film", maximum="99"),
        _f("Подъём подающих роликов", OB019_FEED_WHEEL_ADDRESS, "bit0", key="feed_wheel"),
    )),
)
# Registers ERP never writes, whatever the settings above say (backend test against the three write lists).
OB019_NEVER_WRITTEN: frozenset[int] = frozenset(
    set(range(1682, 1688)) | set(range(1203, 1212)) | {599, 600, 730} | set(range(1087, 1090)))
# Axis positions (Jog page): read by the HMI, not yet by the bridge; the scale is not confirmed at the machine.
OB019_POSITIONS: tuple[SettingField, ...] = tuple(
    _f(label, 660 + 2 * axis, "i32", MM, "мм") for axis, label in enumerate((
        "Передние ролики", "Задние ролики", "Нож", "Трубка", "Передний прижим", "Задний прижим", "Сдвиг ножа")))
_WORDS = {"u16": 1, "i16": 1, "bit0": 1, "mode": 1, "u32": 2, "i32": 2, "text15": 15}
OB019_FIELDS: dict[str, SettingField] = {
    field.key: field for _title, fields in OB019_SETTINGS for field in fields if field.key}
TEXT_CHARS = 30


def field_words(field: SettingField) -> range:
    return range(field.address, field.address + _WORDS[field.kind])


# What a write command may set (mirrors ob019-writer.ps1 $Writable / $WritableBits and mbd.ps1 allowedWrite /
# allowedBits; a backend test compares them): whole words of the numbers and texts, single bits of the switches.
OB019_WRITTEN_WORDS: frozenset[int] = frozenset(
    address for field in OB019_FIELDS.values() if field.kind != "bit0" for address in field_words(field))
OB019_WRITTEN_BITS: frozenset[tuple[int, int]] = frozenset(
    (field.address, 0) for field in OB019_FIELDS.values() if field.kind == "bit0")


# What the bridge reads and sends (Dn = holding register n): the state words and every setting above. Mirrors
# `$script:Allowed` in ob019-bridge.ps1; 32-bit values take two words (low word first), strings 15 words.
OB019_ALLOWED: frozenset[int] = frozenset(
    {20, 641, 642, 643, 644, 645, 646, 649, 650, 711, 766, 782, 1280}
    | {address for _title, fields in OB019_SETTINGS for field in fields for address in field_words(field)}
)
REGISTERS_MAX = max(len(OB019_ALLOWED), len(ob099.ALLOWED))


def _field_value(words: dict[int, int], field: SettingField) -> object:
    if field.kind == "text15":
        return _text(words, field.address, 15)
    if any(address not in words for address in field_words(field)):
        return None
    if field.kind == "bit0":
        return bool(words[field.address] & 1)
    raw = words[field.address] if _WORDS[field.kind] == 1 else words[field.address] | (words[field.address + 1] << 16)
    if field.kind == "i16" and raw >= 0x8000:
        raw -= 0x10000
    elif field.kind == "i32" and raw >= 0x80000000:
        raw -= 0x100000000
    return raw if field.scale == 1 else float(Decimal(raw) * field.scale)


def _readable(field: SettingField) -> bool:
    return all(address in OB019_ALLOWED for address in field_words(field))


def setting_group(title: str, fields: tuple[SettingField, ...], words: dict[int, int]) -> dict:
    """One HMI page: its fields with the values of the last snapshot; `read` — the bridge reads it; `key` — ERP writes
    it from the card («Записать на станок»)."""
    result = []
    for field in fields:
        value = _field_value(words, field) if _readable(field) else None
        item = {"label": field.label, "value": value, "unit": field.unit, "read": _readable(field),
                "key": field.key or None, "kind": field.kind}
        if field.kind == "mode":
            item["text"] = None if value is None else OB019_MODES.get(int(value), f"Код {value}")
        result.append(item)
    return {"title": title, "fields": result}


def ob019_settings(words: dict[int, int]) -> list[dict]:
    return [setting_group(title, fields, words) for title, fields in OB019_SETTINGS]


class MachineWriteInvalid(ValueError):
    """A value the machine cannot hold, or a register ERP does not write (422)."""


def _number(value: object, label: str) -> Decimal:
    text = "" if isinstance(value, bool) else str(value if value is not None else "").strip().replace(",", ".")
    try:
        number = Decimal(text.replace(" ", ""))
    except ArithmeticError:
        raise MachineWriteInvalid(f"{label}: нужно число") from None
    if not number.is_finite():
        raise MachineWriteInvalid(f"{label}: нужно число")
    return number


SWITCH_ON = {"1", "true", "вкл", "on"}
SWITCH_OFF = {"0", "false", "выкл", "off"}


def encode_field(key: str, value: object) -> tuple[SettingField, list[int], object]:
    """A value for the machine → the words of its registers: 32-bit low word first, signed values in two's complement;
    a text is ASCII, two characters a word, high byte first, the tail filled with zeros so no rest of an old text
    survives. A switch gives [1] or [0]: the value of its bit 0, written by mbd's `setbit`, never as a word.
    → (field, words, value)."""
    field = OB019_FIELDS.get(key)
    if field is None:
        raise MachineWriteInvalid("Этот параметр ERP на станок не записывает")
    if field.kind == "bit0":
        text = str(value).strip().lower() if value is not None else ""
        if text not in SWITCH_ON | SWITCH_OFF:
            raise MachineWriteInvalid(f"{field.label}: «Вкл» или «Выкл»")
        on = text in SWITCH_ON
        return field, [int(on)], on
    if field.kind == "text15":
        text = " ".join(str(value or "").split())
        if len(text) > TEXT_CHARS:
            raise MachineWriteInvalid(f"{field.label}: не длиннее {TEXT_CHARS} знаков")
        if any(not 32 <= ord(char) <= 126 for char in text):
            raise MachineWriteInvalid(f"{field.label}: только латинские буквы, цифры и знаки — кириллицу станок "
                                      "не хранит")
        raw = text.encode("ascii").ljust(TEXT_CHARS, b"\x00")
        return field, [(raw[2 * index] << 8) | raw[2 * index + 1] for index in range(15)], text
    number = _number(value, field.label)
    if field.kind == "mode":
        if number not in (0, 1, 2):
            raise MachineWriteInvalid("Режим: пошаговый, один цикл или автомат")
        return field, [int(number)], int(number)
    if number < field.minimum or (field.maximum is not None and number > field.maximum):
        raise MachineWriteInvalid(f"{field.label}: значение вне диапазона станка")
    raw = number / field.scale
    if raw != raw.to_integral_value():
        step = format(field.scale.normalize(), "f").replace(".", ",")
        raise MachineWriteInvalid(f"{field.label}: точность станка — {step}" + (f" {field.unit}" if field.unit else ""))
    raw_int = int(raw)
    if field.kind in ("u16", "i16"):
        if not (-0x8000 <= raw_int <= 0x7FFF if field.kind == "i16" else 0 <= raw_int <= WORD_MAX):
            raise MachineWriteInvalid(f"{field.label}: значение вне диапазона станка")
        return field, [raw_int & WORD_MAX], number
    if field.kind == "u32" and raw_int > 0xFFFFFFFF:
        raise MachineWriteInvalid(f"{field.label}: значение вне диапазона станка")
    raw_int &= 0xFFFFFFFF
    return field, [raw_int & WORD_MAX, raw_int >> 16], number


def field_value(key: str, words: dict[int, int]) -> object:
    """The value of a writable field in a snapshot (None — not read)."""
    return _field_value(words, OB019_FIELDS[key])


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
