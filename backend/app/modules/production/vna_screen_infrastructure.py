"""production_vna_screens: the VNA bench screens the owner edits («ВАЦ — рабочее место» → «Экраны», S5b).

One row per named screen. The bench takes the default screen of each purpose; the other active screens are views the
operator may put on S2VNA at any time («Показать на ВАЦ», apply_screen only). `traces` holds `vna_screens.TraceDef`
dumps validated by the protocol v2 contract before every write; the DB keeps the invariants it can: one default per
purpose, a default is active, the split and version bounds. Callers own the transaction.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint, Uuid,
                        select, text, update)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base
from .domain import ProductionValidationError
from .vna_screens import NAME_MAX, NOTE_MAX, PURPOSES, ScreenDef, presets

SCREEN_CHECKS = {
    "ck_vna_screens_purpose": "purpose IN (" + ",".join(repr(p) for p in PURPOSES) + ")",
    "ck_vna_screens_split": "split BETWEEN 1 AND 16",
    "ck_vna_screens_version": "version >= 1",
    "ck_vna_screens_name": "trim(name) <> ''",
    "ck_vna_screens_default_active": "NOT is_default OR active",
}
DEFAULT_SQLITE, DEFAULT_PG = "is_default = 1", "is_default"


class ScreenConflict(Exception):
    """The screen changed since it was read (CAS) or the change would leave a purpose without its default."""


class VnaScreenRow(Base):
    __tablename__ = "production_vna_screens"
    __table_args__ = (
        UniqueConstraint("name", name="uq_vna_screens_name"),
        *(CheckConstraint(sql, name=name) for name, sql in SCREEN_CHECKS.items()),
        Index("uq_vna_screens_default", "purpose", unique=True,
              sqlite_where=text(DEFAULT_SQLITE), postgresql_where=text(DEFAULT_PG)),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    split: Mapped[int] = mapped_column(Integer, nullable=False)
    traces: Mapped[list] = mapped_column(JSON, nullable=False)
    markers_hz: Mapped[list] = mapped_column(JSON, nullable=False)
    note: Mapped[str] = mapped_column(String(NOTE_MAX), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def definition_of(row: VnaScreenRow) -> ScreenDef:
    return ScreenDef(name=row.name, purpose=row.purpose, split=row.split, traces=row.traces, markers_hz=row.markers_hz,
                     note=row.note)


def snapshot(row: VnaScreenRow) -> dict:
    """The whole screen as the journal keeps it before and after a change."""
    return dict(name=row.name, purpose=row.purpose, split=row.split, traces=row.traces, markers_hz=row.markers_hz,
                note=row.note, is_default=row.is_default, active=row.active, version=row.version)


def stored(spec: ScreenDef) -> dict:
    data = spec.model_dump(mode="json")
    return dict(name=data["name"], purpose=data["purpose"], split=data["split"], traces=data["traces"],
                markers_hz=data["markers_hz"], note=data["note"])


class VnaScreenRepository:
    def __init__(self, session: Session):
        self.session = session
        self.demoted: list[tuple[VnaScreenRow, dict]] = []   # screens that lost default status in this unit of work

    def all(self) -> list[VnaScreenRow]:
        rows = self.session.scalars(select(VnaScreenRow)).all()
        order = {p: i for i, p in enumerate(PURPOSES)}
        return sorted(rows, key=lambda r: (not r.active, order[r.purpose], not r.is_default, r.name))

    def get(self, screen_id: UUID) -> VnaScreenRow:
        row = self.session.get(VnaScreenRow, screen_id)
        if row is None:
            raise KeyError("Экран ВАЦ не найден")
        return row

    def default(self, purpose: str) -> ScreenDef:
        """The screen the bench draws for `purpose`. An empty table (tests, a fresh database before 0207 seeds it) →
        the code preset; a filled table without a default for the purpose is an error, never a silent preset."""
        row = self.session.scalar(select(VnaScreenRow).where(VnaScreenRow.purpose == purpose,
                                                             VnaScreenRow.is_default.is_(True)))
        if row is not None:
            return definition_of(row)
        if self.session.scalar(select(VnaScreenRow.id).limit(1)) is None:
            return presets()[purpose]
        raise ProductionValidationError(f"Нет основного экрана для назначения «{purpose}» — назначьте его в «Экраны»")

    def ensure_seeded(self, now) -> bool:
        """An empty table (a database built without 0207, e.g. the test schema) gets the six code presets as the
        defaults, the same rows the migration seeds; True when it wrote them."""
        if self.session.scalar(select(VnaScreenRow.id).limit(1)) is not None:
            return False
        for spec in presets().values():
            self.session.add(VnaScreenRow(id=uuid4(), is_default=True, active=True, version=1, updated_by=None,
                                          updated_by_name="", updated_at=now, **stored(spec)))
        self.session.flush()
        return True

    def _name_free(self, name: str, own: UUID | None = None):
        clash = self.session.scalar(select(VnaScreenRow.id).where(VnaScreenRow.name == name))
        if clash is not None and clash != own:
            raise ProductionValidationError(f"Экран «{name}» уже есть — выберите другое название")

    def _take_default(self, row: VnaScreenRow, actor, now):
        """`row` becomes the default of its purpose. The previous default steps down first (one default per purpose)
        as a change of its own: version + 1, so an editor still holding it cannot silently take the default back, and
        it is listed in `self.demoted` (with the screen before) for the journal."""
        for other in list(self.session.scalars(select(VnaScreenRow).where(
                VnaScreenRow.purpose == row.purpose, VnaScreenRow.is_default.is_(True), VnaScreenRow.id != row.id))):
            before = snapshot(other)
            changed = self.session.execute(update(VnaScreenRow).where(
                VnaScreenRow.id == other.id, VnaScreenRow.version == other.version).values(
                is_default=False, version=other.version + 1, updated_by=actor.id, updated_by_name=actor.name,
                updated_at=now))
            if changed.rowcount != 1:
                raise ScreenConflict("Основной экран этого назначения уже изменили — откройте экраны заново")
            self.session.refresh(other)
            self.demoted.append((other, before))
        self.session.flush()

    def create(self, spec: ScreenDef, make_default: bool, actor, now) -> VnaScreenRow:
        self._name_free(spec.name)
        row = VnaScreenRow(id=uuid4(), is_default=False, active=True, version=1, updated_by=actor.id,
                           updated_by_name=actor.name, updated_at=now, **stored(spec))
        self.session.add(row)
        self.session.flush()
        if make_default:
            self._take_default(row, actor, now)
            row.is_default = True
            self.session.flush()
        return row

    def update(self, row: VnaScreenRow, version: int, spec: ScreenDef, make_default: bool, actor, now) -> VnaScreenRow:
        if row.version != version:
            raise ScreenConflict("Экран уже изменили — откройте его заново")
        if row.is_default and (not make_default or spec.purpose != row.purpose):
            raise ScreenConflict("Это основной экран своего назначения — сначала сделайте основным другой экран")
        if make_default and not row.active:
            raise ScreenConflict("Отключённый экран не может быть основным — сначала включите его")
        self._name_free(spec.name, row.id)
        values = dict(stored(spec), version=version + 1, updated_by=actor.id, updated_by_name=actor.name,
                      updated_at=now)
        # CAS on the row itself: a parallel save that passed the check above still loses here.
        changed = self.session.execute(update(VnaScreenRow).where(VnaScreenRow.id == row.id,
                                                                  VnaScreenRow.version == version).values(**values))
        if changed.rowcount != 1:
            raise ScreenConflict("Экран уже изменили — откройте его заново")
        self.session.refresh(row)
        if make_default and not row.is_default:
            self._take_default(row, actor, now)
            row.is_default = True
            self.session.flush()
        return row

    def set_active(self, row: VnaScreenRow, version: int, active: bool, actor, now) -> VnaScreenRow:
        if row.version != version:
            raise ScreenConflict("Экран уже изменили — откройте его заново")
        if not active and row.is_default:
            raise ScreenConflict("Основной экран отключить нельзя — сначала сделайте основным другой экран")
        changed = self.session.execute(update(VnaScreenRow).where(VnaScreenRow.id == row.id,
                                                                  VnaScreenRow.version == version).values(
            active=active, version=version + 1, updated_by=actor.id, updated_by_name=actor.name, updated_at=now))
        if changed.rowcount != 1:
            raise ScreenConflict("Экран уже изменили — откройте его заново")
        self.session.refresh(row)
        return row
