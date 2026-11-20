"""ОБ.019 speed history (owner 20.11.2026: «история скорости станков», plan machine-speed-history S1): the write path.

The snapshot table keeps only the latest reading; this table keeps a row each time something that matters for speed
changes, so «норма vs факт» (S2) can sum running time over pieces. `record` runs from the bridge's report
(workflows/machines_api.agent_report) for an accepted snapshot of ОБ.019 with the link ok and the counter D649/650 and
the state D766 present. A row is written when
- there is no row yet for the machine, or the bridge restarted (`boot` differs) — reason `start`;
- any part of T = (T.Finish D649/650, batch D645/646, order D643/644, state D766, open run, setup key) changed —
  reason `run`, `setup`, `counter` or `state` (the first of these that changed). UPH (D641/642) and the alarm D782 ride
  along but never write a row by themselves. A counter that went down (reset, new job) is written like any change.
`recorded_at` is the server's receive time, never before the machine's previous row (a clock stepped back is clamped).
UNIQUE(equipment_id, boot, seq): a snapshot re-sent under the same number writes nothing, silently. History never fails
a machine report: the whole of `record` (reads and insert) runs in a savepoint, and any other error is logged with the
machine and swallowed — the snapshot is still saved. Reports of one machine are serialized by the snapshot row the report
updates first (production/machines.report), so the «previous row» read here does not race.

The setup key is canonical text: wire | length D32 | strip D34/D38 | pull D36/D40 | marking (D400 bit 0, which of the
texts D440/D455/D470 are non-empty, D411 middle marks); lengths in mm without trailing zeros; a word the snapshot lacks
is «?».

Growth: the bridge polls every 2 s, so a running machine writes up to ~1 row per 2 s — about 14.4k rows per 8-hour shift,
roughly 0.4–1.4 GB a year per shift. Retention: 3 years, no purge job yet; revisit within a year.
The run's write key is ON DELETE SET NULL: `machines_admin release` deletes the link and (by cascade) its writes; history
keeps the run kind and counters, only the write reference becomes NULL."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, PrimaryKeyConstraint, String,
    UniqueConstraint, Uuid, select, text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

from . import machine_runs as runs
from . import machines as rules
from .cutting_infrastructure import CuttingGroupRow
from .manual_jobs import ManualJobRow

TABLE = "production_machine_speed_rows"
U32_MAX = 0xFFFFFFFF
REASONS = ("start", "counter", "state", "run", "setup")
UNIQUE_NAME = "uq_production_machine_speed_rows_seq"
FK_EQUIPMENT = "fk_production_machine_speed_rows_equipment"
FK_RUN = "fk_production_machine_speed_rows_run"
WIRE_MAX = 200
SETUP_MAX = 300
UNKNOWN = "?"
# Marking words: D400 switch, D411/412 number of middle marks, D440..D484 the three texts (15 words each).
MARK_TEXTS = (("F", 440), ("M", 455), ("B", 470))
MARK_WORDS = (400, 411, 412, *range(440, 485))
UNIQUE_VIOLATION = "23505"  # PostgreSQL SQLSTATE
log = logging.getLogger(__name__)
_SQL = lambda values: ", ".join(repr(value) for value in values)  # noqa: E731


class MachineSpeedRow(Base):
    """One change of an ОБ.019 counter, state, run or setup (migration 0212), append-only."""

    __tablename__ = TABLE
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_production_machine_speed_rows"),
        UniqueConstraint("equipment_id", "boot", "seq", name=UNIQUE_NAME),
        CheckConstraint("trim(boot) <> ''", name="ck_production_machine_speed_rows_boot"),
        CheckConstraint("seq >= 0", name="ck_production_machine_speed_rows_seq"),
        *(CheckConstraint(f"{name} BETWEEN 0 AND {U32_MAX}", name=f"ck_production_machine_speed_rows_{name}")
          for name in ("counter_total", "counter_batch", "counter_order", "uph")),
        CheckConstraint("state_code BETWEEN 0 AND 65535", name="ck_production_machine_speed_rows_state"),
        CheckConstraint("alarm_code IS NULL OR alarm_code BETWEEN 0 AND 65535",
                        name="ck_production_machine_speed_rows_alarm"),
        CheckConstraint(f"run_kind IS NULL OR run_kind IN ({_SQL(runs.KINDS)})",
                        name="ck_production_machine_speed_rows_run_kind"),
        CheckConstraint("run_write_id IS NULL OR run_kind IS NOT NULL", name="ck_production_machine_speed_rows_run"),
        CheckConstraint("trim(setup_key) <> ''", name="ck_production_machine_speed_rows_setup"),
        CheckConstraint(f"reason IN ({_SQL(REASONS)})", name="ck_production_machine_speed_rows_reason"),
        Index("ix_production_machine_speed_rows_time", "equipment_id", "recorded_at"),
        # The newest row of a machine (`record`): equipment_id = … ORDER BY id DESC LIMIT 1.
        Index("ix_production_machine_speed_rows_equipment_id_id", "equipment_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer(), "sqlite"), Identity())
    equipment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT", name=FK_EQUIPMENT),
        nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    boot: Mapped[str] = mapped_column(String(64), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    counter_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    counter_batch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    counter_order: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uph: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state_code: Mapped[int] = mapped_column(Integer, nullable=False)
    alarm_code: Mapped[int | None] = mapped_column(Integer)
    run_write_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("production_machine_writes.id", ondelete="SET NULL",
                         name=FK_RUN))
    run_kind: Mapped[str | None] = mapped_column(String(16))
    wire_key: Mapped[str] = mapped_column(String(WIRE_MAX), nullable=False, default="", server_default="")
    setup_key: Mapped[str] = mapped_column(String(SETUP_MAX), nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)


def plain_wire_key(text: str) -> str:
    """The same normalization as equipment_setup.recipes.wire_key (spaces, case, «0.35» = «0,35»)."""
    return " ".join(str(text or "").split()).casefold().replace(".", ",")


def _mm(words: dict[int, int], low: int) -> str:
    value = rules._u32(words, low)
    return UNKNOWN if value is None else format((Decimal(value) / 100).normalize(), "f")


def mark_key(words: dict[int, int]) -> str:
    """«0» marking off; «1/F-B/3» on, texts at the start and the end, 3 middle marks; «?» — words not in the snapshot."""
    if any(address not in words for address in MARK_WORDS):
        return UNKNOWN
    if not words[400] & 1:
        return "0"
    texts = "".join(flag if rules._text(words, first, 15) else "-" for flag, first in MARK_TEXTS)
    return f"1/{texts}/{rules._u32(words, 411)}"


def setup_key(wire: str, words: dict[int, int]) -> str:
    key = (f"{wire}|L{_mm(words, 32)}|S{_mm(words, 34)}/{_mm(words, 38)}|P{_mm(words, 36)}/{_mm(words, 40)}"
           f"|M{mark_key(words)}")
    return key[:SETUP_MAX]


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


def open_run(session: Session, equipment_id: UUID,
             wire_of: Callable[[str], str]) -> tuple[UUID | None, str | None, str]:
    """The machine's open run → (its write id, kind, wire key); (None, None, '') — no open run (the HMI's own job)."""
    row = runs.current(session, equipment_id)
    if not runs.is_open(row):
        return None, None, ""
    run = runs.info(row) or {}
    wire = ""
    if run["kind"] == "manual":
        job_id = _uuid(run.get("job_id"))
        job = session.get(ManualJobRow, job_id) if job_id else None
        wire = job.wire if job else ""
    else:
        group_id = _uuid(run.get("group_id"))
        group = session.get(CuttingGroupRow, group_id) if group_id else None
        wire = str((group.recipe or {}).get("cable") or "") if group else ""
    return row.id, run["kind"], (wire_of(wire) if wire.strip() else "")[:WIRE_MAX]


def _reason(last: MachineSpeedRow | None, boot: str, values: dict) -> str | None:
    if last is None or last.boot != boot:
        return "start"
    if last.run_write_id != values["run_write_id"]:
        return "run"
    if last.setup_key != values["setup_key"]:
        return "setup"
    if (last.counter_total, last.counter_batch, last.counter_order) != (
            values["counter_total"], values["counter_batch"], values["counter_order"]):
        return "counter"
    if last.state_code != values["state_code"]:
        return "state"
    return None


def _duplicate(error: IntegrityError) -> bool:
    """The UNIQUE (equipment, boot, seq): PostgreSQL (psycopg 3) by SQLSTATE and constraint name, SQLite by the columns
    its message names."""
    orig = getattr(error, "orig", None)
    if getattr(orig, "sqlstate", None) is not None:
        return (orig.sqlstate == UNIQUE_VIOLATION
                and getattr(getattr(orig, "diag", None), "constraint_name", None) == UNIQUE_NAME)
    text = str(orig if orig is not None else error).lower()
    return f"unique constraint failed: {TABLE}.equipment_id, {TABLE}.boot, {TABLE}.seq" in text


def record(session: Session, link: rules.MachineLinkRow, words: dict[int, int], boot: str, seq: int, now: datetime,
           *, wire_of: Callable[[str], str] = plain_wire_key) -> MachineSpeedRow | None:
    """Write a history row for an accepted ОБ.019 snapshot when T changed. → the new row; None — nothing to write
    (not ОБ.019, no counter or state, nothing changed, or this boot/seq is already written) or the history failed
    (logged): it never fails the report. `wire_of` — wire text → its key (the workflow passes the setup module's
    label + key, which drops the colour)."""
    if link.kind != rules.KIND_OB019:
        return None
    equipment_id = link.equipment_id
    try:
        with session.begin_nested():
            return _record(session, link, words, boot, seq, now, wire_of)
    except IntegrityError as error:
        if not _duplicate(error):
            log.exception("История скорости станка %s не записана", equipment_id)
        return None
    except Exception:
        log.exception("История скорости станка %s не записана", equipment_id)
        return None


def _record(session: Session, link: rules.MachineLinkRow, words: dict[int, int], boot: str, seq: int, now: datetime,
            wire_of: Callable[[str], str]) -> MachineSpeedRow | None:
    total, state = runs.counter(words), words.get(766)
    if total is None or state is None:
        return None
    boot = rules._clean(boot, 64)
    if not boot or seq < 0:
        return None
    run_write_id, run_kind, wire = open_run(session, link.equipment_id, wire_of)
    values = {
        "counter_total": total, "counter_batch": rules._u32(words, 645) or 0,
        "counter_order": rules._u32(words, 643) or 0, "uph": rules._u32(words, 641) or 0,
        "state_code": state, "alarm_code": words.get(782), "run_write_id": run_write_id, "run_kind": run_kind,
        "wire_key": wire, "setup_key": setup_key(wire, words),
    }
    last = session.scalar(select(MachineSpeedRow).where(MachineSpeedRow.equipment_id == link.equipment_id)
                          .order_by(MachineSpeedRow.id.desc()).limit(1))
    reason = _reason(last, boot, values)
    if reason is None:
        return None
    recorded_at = now
    if last is not None and runs.aware(now) < runs.aware(last.recorded_at):
        recorded_at = last.recorded_at  # the server clock stepped back: never before the previous row
    row = MachineSpeedRow(equipment_id=link.equipment_id, recorded_at=recorded_at, boot=boot, seq=seq, reason=reason,
                          **values)
    if session.get_bind().dialect.name == "postgresql":
        # Every FK is DEFERRABLE INITIALLY DEFERRED (migrations/env.py): checked now, inside the savepoint, a broken key
        # is this history row's failure — not a failed commit of the whole machine report.
        session.execute(text(f"SET CONSTRAINTS {FK_EQUIPMENT}, {FK_RUN} IMMEDIATE"))
    session.add(row)
    session.flush()
    return row
