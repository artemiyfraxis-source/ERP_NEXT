"""ОБ.099 — servo crimp press BZW (Omron CP2E-N20DT-D, BZW2.5 and the OES CFM2103 force monitor on its PC .82), slice 5:
READ ONLY (stage 0 of docs/data/ob099-erp-control-design-2026-11-18.md). An agent on the press PC
(`tools/machine-agent/ob099-agent.ps1`) reads, never writes:

- the PLC over FINS/UDP, command 01 01 only (memory area read): an allow-list of words (`ALLOWED`, keys «D5006»,
  «H60», «W80») sent as the snapshot of `production.machines` (the same report endpoint and binding as ОБ.019);
- the per-crimp log of BZW2.5 `D:\\BZW\\logs\\yyyyMMdd.txt` (one line per CFM cycle: peak, time, OK/NG, the job
  strings) → `CrimpRecordRow`, append-only, deduplicated by the line's place in its file;
- the alarm journal `alarmrecord` of MySQL `bzw2.5` under the SELECT-only user `erp_ro` → `MachineAlarmRecordRow`.

Decoding is pure (`decode`): addresses, scales and texts from docs/data/ob099-bzw25-full-map-2026-11-18.md (§3, §5).
The catalogue keeps two presses (ОБ.099.1, ОБ.099.2); the agent is installed with the number of its press.
NG in the BZW log = the CFM curve deviation (GrossCDA) only: BZW2.5 writes a sigma failure as OK (map §6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, Float, ForeignKey, Index, String, UniqueConstraint, Uuid, func, select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.clock import MOSCOW
from app.shared.persistence import Base

KIND = "ob099_fins"
INVENTORIES = ("ОБ.099.1", "ОБ.099.2")
RESULTS = ("OK", "NG")
TEXT_MAX = 64
SCAN_MAX = 128
ALARM_TEXT_MAX = 300
BATCH_MAX = 500

# Words the agent reads (FINS 01 01) and may send. Mirrors `$script:Allowed` in tools/machine-agent/ob099-agent.ps1.
# D — DM words; a DTn double word is Dn (low) + Dn+1 (high). H60/H61 — options (HR), W80…W84 — alarms and statuses (WR).
_SCALARS = (5005, 5006, 5008, 5010, 5012, 5013, 5021, 5022, 5032, 5036, 5046, 5047)
_DOUBLES = (5100, 5102, 5104, 5106, 5108, 5110, 5112, 5114, 5118, 5124, 5128, 5132, 5142)
_STRINGS = (5500, 5510, 5520, 5530, 5540)
ALLOWED: frozenset[str] = frozenset(
    {f"D{a}" for a in _SCALARS}
    | {f"D{a + i}" for a in _DOUBLES for i in (0, 1)}
    | {f"D{a + i}" for a in _STRINGS for i in range(10)}
    | {"H60", "H61", "W80", "W81", "W82", "W83", "W84"}
)

# W80 alarms (map §5): bit → Russian text.
ALARMS = {
    0: "Авария сервопривода", 1: "Защитный кожух не закрыт", 2: "Сработала световая завеса",
    3: "Брак обжима (CFM)", 4: "Ошибка обучения CFM", 5: "Станок не сброшен",
    7: "Датчик исходного положения цилиндра переноса захвата", 8: "Датчик рабочего положения цилиндра переноса захвата",
    9: "Датчик исходного положения цилиндра переноса приёмника",
    10: "Датчик рабочего положения цилиндра переноса приёмника", 11: "Нет клемм в конце дорожки",
    13: "Фиксирующий цилиндр не в исходном", 14: "Позиционирующий цилиндр не в исходном",
    15: "Введённая высота вне пределов",
}
# Warnings of the running line of BZW2.5 outside W80 (map §3.5): (word, bit, text, tone).
WARNINGS = (
    ("W83", 5, "Ресурс ножа обжима исчерпан", "danger"),
    ("W83", 6, "Ресурс ножа отсечки отходов исчерпан", "danger"),
    ("W84", 13, "Неисправность педали", "danger"),
    ("W83", 3, "Партия выполнена", "info"),
    ("W83", 4, "Общий план выполнен", "info"),
)
# alarmrecord.AlarmInfo is written in the BZW2.5 interface language (English now, Chinese before): → Russian.
ALARM_JOURNAL_TEXTS = {
    "servo alarm": ALARMS[0], "伺服报警": ALARMS[0],
    "the safety guard is not closed": ALARMS[1], "安全防护罩未关闭": ALARMS[1],
    "abnormal safety grating": ALARMS[2], "安全光栅异常": ALARMS[2],
    "abnormal terminal crimping": ALARMS[3], "端子压接异常": ALARMS[3],
    "stress learning failure": ALARMS[4], "压力学习失败": ALARMS[4],
    "device not reset": ALARMS[5], "设备未复位": ALARMS[5],
    "there is no material at the end of the track": ALARMS[11], "轨道端部无物料": ALARMS[11],
    "abnormal origin position of fixed cylinder": ALARMS[13], "固定气缸原点位异常": ALARMS[13],
    "abnormal origin position of positioning cylinder": ALARMS[14], "定位气缸原点位异常": ALARMS[14],
    "input height exceeds the range": ALARMS[15], "输入高度超出范围": ALARMS[15],
}
NG_REASON = "Отклонение кривой усилия (CFM)"
WARN_PCT = 90


class CrimpRecordRow(Base):
    """One crimp of a press as its log wrote it (append-only). Linking to a task, КБ or applicator comes later."""

    __tablename__ = "production_crimp_records"
    __table_args__ = (
        UniqueConstraint("equipment_id", "source_key", name="uq_production_crimp_records_source"),
        CheckConstraint("trim(source_key) <> ''", name="ck_production_crimp_records_key"),
        CheckConstraint("result IN ('OK', 'NG')", name="ck_production_crimp_records_result"),
        CheckConstraint("peak IS NULL OR peak >= 0", name="ck_production_crimp_records_peak"),
        Index("ix_production_crimp_records_time", "equipment_id", "crimped_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    equipment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT", name="fk_production_crimp_records_equipment"),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    crimped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result: Mapped[str] = mapped_column(String(2), nullable=False)
    peak: Mapped[float | None] = mapped_column(Float, nullable=True)
    equipment_no: Mapped[str] = mapped_column(String(TEXT_MAX), nullable=False, default="", server_default="")
    product: Mapped[str] = mapped_column(String(TEXT_MAX), nullable=False, default="", server_default="")
    wire: Mapped[str] = mapped_column(String(TEXT_MAX), nullable=False, default="", server_default="")
    terminal: Mapped[str] = mapped_column(String(TEXT_MAX), nullable=False, default="", server_default="")
    applicator: Mapped[str] = mapped_column(String(TEXT_MAX), nullable=False, default="", server_default="")
    scan_code: Mapped[str] = mapped_column(String(SCAN_MAX), nullable=False, default="", server_default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MachineAlarmRecordRow(Base):
    """One line of a machine's own alarm journal (append-only), as the machine wrote it."""

    __tablename__ = "production_machine_alarm_records"
    __table_args__ = (
        UniqueConstraint("equipment_id", "source_key", name="uq_production_machine_alarm_records_source"),
        CheckConstraint("trim(source_key) <> ''", name="ck_production_machine_alarm_records_key"),
        CheckConstraint("trim(text) <> ''", name="ck_production_machine_alarm_records_text"),
        Index("ix_production_machine_alarm_records_time", "equipment_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    equipment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT", name="fk_production_machine_alarm_records_equipment"),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(String(400), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text: Mapped[str] = mapped_column(String(ALARM_TEXT_MAX), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class Crimp:
    key: str
    at: datetime
    result: str
    peak: float | None = None
    equipment_no: str = ""
    product: str = ""
    wire: str = ""
    terminal: str = ""
    applicator: str = ""
    scan_code: str = ""


@dataclass(frozen=True)
class Alarm:
    key: str
    at: datetime
    text: str


def _shop_time(moment: datetime) -> datetime:
    """An aware moment in the shop's zone (stored so on SQLite too, where the zone is dropped)."""
    return moment.astimezone(MOSCOW) if moment.tzinfo is not None else moment.replace(tzinfo=MOSCOW)


def _fresh_keys(session: Session, row_type, equipment_id: UUID, keys: list[str]) -> set[str]:
    taken: set[str] = set()
    for start in range(0, len(keys), 200):
        taken |= set(session.scalars(select(row_type.source_key).where(
            row_type.equipment_id == equipment_id, row_type.source_key.in_(keys[start:start + 200]))))
    return set(keys) - taken


def _add_new(session: Session, rows: list) -> int:
    """Insert rows; one already stored by a simultaneous request is skipped, not an error."""
    if not rows:
        return 0
    try:
        with session.begin_nested():
            session.add_all(rows)
            session.flush()
        return len(rows)
    except IntegrityError:
        added = 0
        for row in rows:
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
                added += 1
            except IntegrityError:
                pass
        return added


def store(session: Session, *, equipment_id: UUID, crimps: list[Crimp], alarms: list[Alarm], now: datetime) -> tuple[int, int]:
    """Append the agent's records; a record with a key already stored for that press is ignored. → (crimps, alarms) added."""
    received = _shop_time(now)
    unique_crimps = {crimp.key: crimp for crimp in crimps}
    fresh = _fresh_keys(session, CrimpRecordRow, equipment_id, list(unique_crimps))
    crimp_rows = [CrimpRecordRow(
        id=uuid4(), equipment_id=equipment_id, source_key=crimp.key, crimped_at=_shop_time(crimp.at), result=crimp.result,
        peak=crimp.peak, equipment_no=crimp.equipment_no, product=crimp.product, wire=crimp.wire, terminal=crimp.terminal,
        applicator=crimp.applicator, scan_code=crimp.scan_code, received_at=received,
    ) for key, crimp in unique_crimps.items() if key in fresh]
    unique_alarms = {alarm.key: alarm for alarm in alarms}
    fresh = _fresh_keys(session, MachineAlarmRecordRow, equipment_id, list(unique_alarms))
    alarm_rows = [MachineAlarmRecordRow(
        id=uuid4(), equipment_id=equipment_id, source_key=alarm.key, occurred_at=_shop_time(alarm.at), text=alarm.text,
        received_at=received,
    ) for key, alarm in unique_alarms.items() if key in fresh]
    return _add_new(session, crimp_rows), _add_new(session, alarm_rows)


# --- reading ---

def words(registers: dict | None) -> dict[str, int]:
    return {str(key).upper(): int(value) for key, value in (registers or {}).items()}


def _d(words_: dict[str, int], address: int) -> int | None:
    return words_.get(f"D{address}")


def _u32(words_: dict[str, int], low: int) -> int | None:
    lo, hi = _d(words_, low), _d(words_, low + 1)
    return None if lo is None or hi is None else lo | (hi << 16)


def _scaled(value: int | None, scale: float, digits: int = 3) -> float | None:
    return None if value is None else round(value * scale, digits)


def _text(words_: dict[str, int], first: int) -> str | None:
    """A 10-word ASCII field: high byte first, padded with NUL."""
    if any(f"D{first + i}" not in words_ for i in range(10)):
        return None
    raw = bytearray()
    for i in range(10):
        word = words_[f"D{first + i}"]
        raw += bytes(((word >> 8) & 0xFF, word & 0xFF))
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def _bit(word: int | None, bit: int) -> bool | None:
    return None if word is None else bool((word >> bit) & 1)


def _state(words_: dict[str, int], active: list[dict]) -> dict:
    w83, h60 = words_.get("W83"), words_.get("H60")
    if w83 is None and h60 is None:
        return {"text": "—", "tone": "neutral"}
    if any(alarm["tone"] == "danger" and alarm["source"] == "W80" for alarm in active):
        return {"text": "Авария", "tone": "danger"}
    if _bit(w83, 13):
        return {"text": "Сброс выполняется", "tone": "info"}
    if _bit(w83, 12):
        return {"text": "Нужен сброс", "tone": "warn"}
    if _bit(w83, 8):
        return {"text": "Калибровка", "tone": "info"}
    if _bit(w83, 10):
        return {"text": "Приработка", "tone": "info"}
    if _bit(w83, 11):
        return {"text": "CMK-проверка", "tone": "info"}
    if _bit(h60, 7):
        return {"text": "Работа", "tone": "ok"}
    if _bit(w83, 14):
        return {"text": "Готов", "tone": "ok"}
    return {"text": "Ожидание", "tone": "neutral"}


def _active_alarms(words_: dict[str, int]) -> list[dict]:
    found = []
    w80 = words_.get("W80")
    if w80:
        for bit in range(16):
            if _bit(w80, bit):
                found.append({"code": f"W80.{bit:02d}", "text": ALARMS.get(bit, f"Авария W80.{bit:02d}"), "tone": "danger",
                              "source": "W80"})
    for word, bit, text, tone in WARNINGS:
        if _bit(words_.get(word), bit):
            found.append({"code": f"{word}.{bit:02d}", "text": text, "tone": tone, "source": word})
    return found


def _tool(name: str, life: int | None, used: int | None) -> dict:
    share = None if life is None or used is None or life <= 0 else used * 100 / life
    tone = "neutral" if share is None else "danger" if used >= life else "warn" if share >= WARN_PCT else "ok"
    pct = None if share is None else round(share, 1)
    left = None if life is None or used is None else max(0, life - used)
    return {"name": name, "life": life, "used": used, "left": left, "used_pct": pct, "tone": tone}


def decode(words_: dict[str, int]) -> dict:
    """Allow-listed words → the live card; a word the snapshot lacks gives None («—» on the screen)."""
    active = _active_alarms(words_)
    h60 = words_.get("H60")
    return {
        "state": _state(words_, active),
        "alarms": [{key: value for key, value in alarm.items() if key != "source"} for alarm in active],
        "job": {
            "product": _text(words_, 5500), "wire": _text(words_, 5510), "terminal": _text(words_, 5520),
            "applicator": _text(words_, 5530), "equipment_no": _text(words_, 5540),
        },
        "crimp": {
            "height_mm": _scaled(_d(words_, 5006), 0.001), "tolerance_mm": _scaled(_d(words_, 5032), 0.001),
            "height_min_mm": _scaled(_d(words_, 5047), 0.001), "height_max_mm": _scaled(_d(words_, 5046), 0.001),
            "cfm_on": _bit(h60, 2), "work_mode": _bit(h60, 7),
        },
        "counters": {
            "total_set": _u32(words_, 5100), "batch_set": _u32(words_, 5102),
            "total_done": _u32(words_, 5104), "batch_done": _u32(words_, 5106),
        },
        "tools": [
            _tool("Нож обжима", _u32(words_, 5108), _u32(words_, 5110)),
            _tool("Нож отсечки отходов", _u32(words_, 5112), _u32(words_, 5114)),
        ],
    }


def journal_text(raw: str) -> str:
    return ALARM_JOURNAL_TEXTS.get(" ".join(raw.split()).lower().rstrip("!. "), raw)


def _moment(value: datetime) -> str:
    return _shop_time(value).isoformat()


def today(session: Session, equipment_id: UUID, now: datetime) -> dict:
    """The shop day of `now`: crimps OK/NG with NG reasons, the last crimp, the alarm journal grouped by text."""
    day = _shop_time(now).date()
    start = datetime.combine(day, time.min, tzinfo=MOSCOW)
    end = start + timedelta(days=1)
    counts = dict(session.execute(select(CrimpRecordRow.result, func.count()).where(
        CrimpRecordRow.equipment_id == equipment_id, CrimpRecordRow.crimped_at >= start, CrimpRecordRow.crimped_at < end,
    ).group_by(CrimpRecordRow.result)).all())
    good, bad = int(counts.get("OK", 0)), int(counts.get("NG", 0))
    last = session.scalars(select(CrimpRecordRow).where(
        CrimpRecordRow.equipment_id == equipment_id, CrimpRecordRow.crimped_at >= start, CrimpRecordRow.crimped_at < end,
    ).order_by(CrimpRecordRow.crimped_at.desc(), CrimpRecordRow.source_key.desc()).limit(1)).first()
    grouped: dict[str, dict] = {}
    for row in session.scalars(select(MachineAlarmRecordRow).where(
            MachineAlarmRecordRow.equipment_id == equipment_id, MachineAlarmRecordRow.occurred_at >= start,
            MachineAlarmRecordRow.occurred_at < end).order_by(MachineAlarmRecordRow.occurred_at)):
        text = journal_text(row.text)
        entry = grouped.setdefault(text, {"text": text, "count": 0, "last_at": None})
        entry["count"] += 1
        entry["last_at"] = _moment(row.occurred_at)
    return {
        "day": day.isoformat(),
        "total": good + bad, "ok": good, "ng": bad,
        "ng_pct": None if good + bad == 0 else round(bad * 100 / (good + bad), 1),
        "ng_reasons": [{"text": NG_REASON, "count": bad}] if bad else [],
        "last": None if last is None else {
            "at": _moment(last.crimped_at), "result": last.result, "peak": last.peak, "terminal": last.terminal,
            "applicator": last.applicator,
        },
        "alarms": sorted(grouped.values(), key=lambda entry: (-entry["count"], entry["text"])),
    }
