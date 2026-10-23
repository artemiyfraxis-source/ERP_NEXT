"""Рисунки стандартов к строкам СТО (migration 0148): строка таблицы `techcard_std_figures` и её репозиторий."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Integer, LargeBinary, String, UniqueConstraint, Uuid, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

from .std_figures import MAX_FIGURE_BYTES, StdFigure


class StdFigureRow(Base):
    __tablename__ = "techcard_std_figures"
    __table_args__ = (
        CheckConstraint("sto_code <> ''", name="ck_techcard_std_figures_sto_code"),
        CheckConstraint("position >= 1", name="ck_techcard_std_figures_position"),
        CheckConstraint("role IN ('norm', 'defect', 'ref')", name="ck_techcard_std_figures_role"),
        CheckConstraint("source_kind IN ('gost', 'ipc')", name="ck_techcard_std_figures_source_kind"),
        CheckConstraint("caption <> ''", name="ck_techcard_std_figures_caption"),
        CheckConstraint("content_type IN ('image/png', 'image/jpeg')", name="ck_techcard_std_figures_content_type"),
        CheckConstraint("length(sha256) = 64", name="ck_techcard_std_figures_sha256"),
        CheckConstraint("width > 0 AND height > 0", name="ck_techcard_std_figures_dimensions"),
        CheckConstraint(f"size > 0 AND size <= {MAX_FIGURE_BYTES}", name="ck_techcard_std_figures_size"),
        UniqueConstraint("sto_code", "position", name="uq_techcard_std_figures_code_position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    sto_code: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    caption: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    # The list goes out without the bytes; they are read only when a picture is shown.
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


_FIELDS = (
    "sto_code", "position", "role", "source_kind", "caption", "content_type", "sha256", "width", "height", "size",
    "created_at", "created_by",
)


def _domain(row: StdFigureRow) -> StdFigure:
    return StdFigure(id=row.id, **{field: getattr(row, field) for field in _FIELDS})


class SqlAlchemyStdFigureRepository:
    def __init__(self, session: Session):
        self.session = session

    def all(self) -> list[StdFigure]:
        rows = self.session.scalars(select(StdFigureRow).order_by(StdFigureRow.sto_code, StdFigureRow.position))
        return [_domain(row) for row in rows]

    def content(self, figure_id: UUID) -> tuple[StdFigure, bytes] | None:
        row = self.session.get(StdFigureRow, figure_id)
        return (_domain(row), row.content) if row is not None else None

    def replace(self, sto_code: str, figures: list[tuple[StdFigure, bytes]]) -> None:
        """Набор строки СТО целиком: старые строки удаляются сразу (до вставки — UNIQUE (код, позиция))."""
        self.session.execute(delete(StdFigureRow).where(StdFigureRow.sto_code == sto_code))
        for figure, content in figures:
            self.session.add(StdFigureRow(
                id=figure.id, content=content, **{field: getattr(figure, field) for field in _FIELDS},
            ))
        self.session.flush()

    def delete_code(self, sto_code: str) -> int:
        result = self.session.execute(delete(StdFigureRow).where(StdFigureRow.sto_code == sto_code))
        return int(result.rowcount or 0)
