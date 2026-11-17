"""Append-only mating ledger. Callers hold the instrument tree lock for writes."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, case, func, select, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base
from .domain import ProductionValidationError
from .matings import end_names, status_for, validate_entry
from .rf_infrastructure import RfRepository


class MatingBatchRow(Base):
    __tablename__ = "production_mating_batches"
    __table_args__ = (CheckConstraint("length(trim(source)) > 0", name="ck_mating_batch_source"),)
    source: Mapped[str] = mapped_column(String(200), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class InstrumentMatingRow(Base):
    __tablename__ = "production_instrument_matings"
    __table_args__ = (
        CheckConstraint("end IN ('A','B')", name="ck_mating_end"),
        CheckConstraint("delta <> 0", name="ck_mating_delta"),
        CheckConstraint("kind IN ('measurement','calibration','manual','initial','correction')", name="ck_mating_kind"),
        CheckConstraint("delta > 0 OR kind = 'correction'", name="ck_mating_negative"),
        CheckConstraint("kind NOT IN ('initial','correction') OR length(trim(note)) > 0", name="ck_mating_note"),
        Index("ix_mating_instrument_created", "instrument_id", "created_at"),
        Index("ix_mating_source", "source"),
        Index("uq_mating_initial_end", "instrument_id", "end", unique=True,
              sqlite_where=text("kind = 'initial'"), postgresql_where=text("kind = 'initial'")),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    instrument_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False)
    end: Mapped[str] = mapped_column(String(1), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    actor: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def event_result(row):
    return {key: str(getattr(row, key)) if key in ("id", "instrument_id") else getattr(row, key)
            for key in ("id", "instrument_id", "end", "delta", "kind", "note", "source", "actor", "created_at")}


class MatingRepository:
    def __init__(self, session):
        self.session = session

    def totals(self, instrument_ids):
        if not instrument_ids:
            return {}
        row = InstrumentMatingRow
        return {(instrument_id, end): total for instrument_id, end, total in self.session.execute(
            select(row.instrument_id, row.end, func.sum(row.delta)).where(row.instrument_id.in_(instrument_ids))
            .group_by(row.instrument_id, row.end))}

    def summaries(self, instrument_ids, details=None):
        details = details if details is not None else RfRepository(self.session).list()
        totals = self.totals(instrument_ids)
        from .rf_interfaces import RfDetails
        result = {}
        for instrument_id in instrument_ids:
            rf = details.get(instrument_id, RfDetails())
            values = {end: totals.get((instrument_id, end), 0) for end in end_names(rf)}
            maximum = max(values.values())
            resource = rf.mating_resource
            result[instrument_id] = dict(resource=resource, totals={"A": values["A"], "B": values.get("B")},
                                         max_total=maximum, percent_used=round(maximum * 100 / resource, 2) if resource else None,
                                         status=status_for(maximum, resource))
        return result

    def snapshot(self, instrument_id):
        summary = self.summaries([instrument_id])[instrument_id]
        row = InstrumentMatingRow
        initialized = set(self.session.scalars(select(row.end).where(row.instrument_id == instrument_id, row.kind == "initial")))
        recorded = set(self.session.scalars(select(row.end).where(row.instrument_id == instrument_id).distinct()))
        resource = summary["resource"]
        ends = [dict(end=end, total=total, percent_used=round(total * 100 / resource, 2) if resource else None,
                     status=status_for(total, resource), initial_recorded=end in initialized, can_initialize=end not in recorded)
                for end, total in summary["totals"].items() if total is not None]
        events = self.session.scalars(select(row).where(row.instrument_id == instrument_id)
                                      .order_by(row.created_at.desc(), row.id.desc()).limit(50))
        return dict(instrument_id=str(instrument_id), resource=resource, ends=ends, status=summary["status"],
                    events=[event_result(event) for event in events])

    def append(self, instrument_id, *, end, delta, kind, note, source, actor, now):
        rf = RfRepository(self.session).get(instrument_id)
        if end not in end_names(rf):
            raise ProductionValidationError("Сторона B доступна только при двух ВЧ-интерфейсах в карточке")
        row = InstrumentMatingRow
        total, count, initialized = self.session.execute(select(func.coalesce(func.sum(row.delta), 0), func.count(row.id),
            func.max(case((row.kind == "initial", 1), else_=0))).where(row.instrument_id == instrument_id, row.end == end)).one()
        validate_entry(end=end, delta=delta, kind=kind, note=note, total=total, initial_recorded=bool(initialized))
        if kind == "initial" and count:
            raise ProductionValidationError("Начальное значение задаётся до первого сочленения")
        event = row(id=uuid4(), instrument_id=instrument_id, end=end, delta=delta, kind=kind, note=note.strip(),
                    source=source, actor=actor, created_at=now)
        self.session.add(event)
        self.session.flush()
        return event
