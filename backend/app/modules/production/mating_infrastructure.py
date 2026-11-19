"""Append-only mating ledger. Callers hold the instrument tree lock for writes.

Each side of an instrument is a chain: seq 1, 2, 3… with the running total stored on every row. The unique
(instrument, side, seq) key turns a concurrent append into a conflict, CHECK keeps every total non-negative.
"""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base
from .application import ConcurrentProductionUpdate
from .domain import ProductionValidationError
from .instrument_characterization import Characterization, characterization
from .instrument_control import ControlDue, control_due, control_since
from .matings import end_names, status_for, validate_entry
from .rf_infrastructure import RfRepository

MATING_CHECKS = {
    "ck_mating_side": "side IN ('A','B')",
    "ck_mating_seq": "seq >= 1",
    "ck_mating_delta": "delta <> 0",
    "ck_mating_total": "total_after >= 0",
    "ck_mating_first": "seq > 1 OR total_after = delta",
    "ck_mating_kind": "kind IN ('measurement','calibration','manual','initial','correction')",
    "ck_mating_negative": "delta > 0 OR kind = 'correction'",
    "ck_mating_initial": "kind <> 'initial' OR seq = 1",
    "ck_mating_note": "kind NOT IN ('initial','correction') OR length(trim(note)) > 0",
    "ck_mating_source": "(kind IN ('measurement','calibration') AND source IS NOT NULL) OR "
                        "(kind IN ('manual','initial','correction') AND source IS NULL)",
}


class MatingBatchRow(Base):
    __tablename__ = "production_mating_batches"
    __table_args__ = (
        CheckConstraint("length(trim(source)) > 0", name="ck_mating_batch_source"),
        CheckConstraint("length(payload_hash) = 64", name="ck_mating_batch_hash"),
    )
    source: Mapped[str] = mapped_column(String(200), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstrumentMatingRow(Base):
    __tablename__ = "production_instrument_matings"
    __table_args__ = (
        UniqueConstraint("instrument_id", "side", "seq", name="uq_mating_chain"),
        *(CheckConstraint(sql, name=name) for name, sql in MATING_CHECKS.items()),
        Index("ix_mating_instrument_created", "instrument_id", "created_at"),
        Index("ix_mating_source", "source"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    instrument_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False)
    side: Mapped[str] = mapped_column(String(1), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    total_after: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(200), ForeignKey("production_mating_batches.source", ondelete="RESTRICT"), nullable=True)
    actor: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def event_result(row: InstrumentMatingRow) -> dict:
    return {"id": str(row.id), "instrument_id": str(row.instrument_id), "end": row.side, "delta": row.delta,
            "total_after": row.total_after, "kind": row.kind, "note": row.note, "source": row.source,
            "actor": row.actor, "created_at": row.created_at}


def latest_characterization(session, instrument_id: UUID) -> Characterization | None:
    """The newest named measurement; a corrupt/replaced newest upload never revives an older one."""
    from .infrastructure import SqlAlchemyInstrumentFileRepository

    for _, file_id in RfRepository(session).latest_characterization_file_ids([instrument_id]):
        file = SqlAlchemyInstrumentFileRepository(session).get(instrument_id, file_id)
        return characterization(file) if file else None
    return None


def instrument_control(session, instrument_id: UUID, today: date,
                       measured: Characterization | None = None) -> ControlDue | None:
    """12 months or 100 matings since the last measurement, whichever first; None - never measured."""
    measured = measured or latest_characterization(session, instrument_id)
    if measured is None:
        return None
    return control_due(measured.measured_on, MatingRepository(session).matings_since(instrument_id, measured.measured_on), today)


def control_result(control: ControlDue | None) -> dict | None:
    if control is None:
        return None
    return {"measured_on": control.measured_on.isoformat(), "due_on": control.due_on.isoformat() if control.due_on else None,
            "matings_since": control.matings_since, "matings_left": control.matings_left, "overdue": control.overdue,
            "line": control.line}


def recorded_sides(session, instrument_id: UUID) -> set[str]:
    return set(session.scalars(select(InstrumentMatingRow.side).where(InstrumentMatingRow.instrument_id == instrument_id).distinct()))


class MatingRepository:
    def __init__(self, session):
        self.session = session

    def _last(self, instrument_id: UUID, side: str) -> tuple[int, int, str | None]:
        row = InstrumentMatingRow
        last = self.session.execute(select(row.seq, row.total_after).where(row.instrument_id == instrument_id, row.side == side)
                                    .order_by(row.seq.desc()).limit(1)).first()
        if last is None:
            return 0, 0, None
        first_kind = self.session.scalar(select(row.kind).where(row.instrument_id == instrument_id, row.side == side, row.seq == 1))
        return last.seq, last.total_after, first_kind

    def snapshot(self, instrument_id: UUID) -> dict:
        rf = RfRepository(self.session).get(instrument_id)
        resource = rf.mating_resource
        ends = []
        for side in end_names(rf):
            seq, total, first_kind = self._last(instrument_id, side)
            ends.append({"end": side, "total": total, "version": seq,
                         "percent_used": round(total * 100 / resource, 1) if resource else None,
                         "status": status_for(total, resource), "initial_recorded": first_kind == "initial",
                         "can_initialize": seq == 0})
        worst = max(ends, key=lambda end: end["total"])
        row = InstrumentMatingRow
        events = list(self.session.scalars(select(row).where(row.instrument_id == instrument_id)
                                           .order_by(row.created_at.desc(), row.seq.desc(), row.side.desc()).limit(50)))
        # A tape measure or a multimeter has no RF connector: the card shows no counter for it.
        return {"instrument_id": str(instrument_id), "resource": resource, "ends": ends, "status": worst["status"],
                "has_connectors": bool(rf.interfaces) or bool(events), "events": [event_result(event) for event in events]}

    def matings_since(self, instrument_id: UUID, measured_on: date) -> int:
        """Matings of the busier side since the start of the measurement day; the «initial» entry is history before the
        counter, not a mating after the measurement."""
        row = InstrumentMatingRow
        totals = self.session.execute(
            select(row.side, func.sum(row.delta))
            .where(row.instrument_id == instrument_id, row.created_at >= control_since(measured_on), row.kind != "initial")
            .group_by(row.side)).all()
        return max((max(0, int(total or 0)) for _, total in totals), default=0)

    def append(self, instrument_id: UUID, *, end: str, delta: int, kind: str, note: str, source: str | None,
               actor: str, now: datetime, version: int | None = None) -> InstrumentMatingRow:
        rf = RfRepository(self.session).get(instrument_id)
        if end not in end_names(rf):
            raise ProductionValidationError("Сторона B есть только у элемента с двумя ВЧ-интерфейсами в карточке")
        seq, total, _ = self._last(instrument_id, end)
        if version is not None and version != seq:
            raise ConcurrentProductionUpdate("Сочленения изменены другим пользователем — обновите карточку")
        note = note.strip()
        validate_entry(end=end, delta=delta, kind=kind, note=note, total=total, first=seq == 0)
        event = InstrumentMatingRow(id=uuid4(), instrument_id=instrument_id, side=end, seq=seq + 1, delta=delta,
                                    total_after=total + delta, kind=kind, note=note, source=source, actor=actor, created_at=now)
        self.session.add(event)
        self.session.flush()
        return event
