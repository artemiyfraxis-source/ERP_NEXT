"""Append-only VNA check journal (form В.1). No update or delete path exists; CHECKs tie each verdict to its numbers."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text, Uuid, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base
from .vna_checks import due_items, KIND_LABELS

# Frozen in migration 0195_vna_check_journal; change only through a new migration.
VNA_CHECKS = {
    "ck_vna_check_kind": "kind IN ('session','flex','control_standard','kit_standards')",
    "ck_vna_check_source": "length(trim(source)) > 0",
    "ck_vna_check_hash": "length(payload_hash) = 64",
    "ck_vna_check_temperatures": "(vna_temperature_c IS NULL OR vna_temperature_c BETWEEN -40 AND 125) AND "
                                 "(acm_temperature_c IS NULL OR acm_temperature_c BETWEEN -40 AND 125)",
    "ck_vna_check_values": "(confidence_tx_dev_db IS NULL OR confidence_tx_dev_db >= 0) AND "
                           "(confidence_rx_dev_db IS NULL OR confidence_rx_dev_db >= 0) AND "
                           "(load_p1_vswr IS NULL OR load_p1_vswr >= 1) AND (load_p2_vswr IS NULL OR load_p2_vswr >= 1) AND "
                           "(vswr_dev IS NULL OR vswr_dev >= 0) AND (vswr_limit IS NULL OR vswr_limit > 0) AND "
                           "(s21_dev_db IS NULL OR s21_dev_db >= 0) AND (s21_limit_db IS NULL OR s21_limit_db > 0) AND "
                           "(kit_ratio IS NULL OR kit_ratio >= 0)",
    "ck_vna_check_standard": "standard_inventory IS NULL OR length(trim(standard_inventory)) > 0",
    "ck_vna_check_session_only": "kind = 'session' OR (confidence_passed IS NULL AND confidence_tx_dev_db IS NULL)",
    "ck_vna_check_loads": "kind IN ('session','flex') OR (load_p1_vswr IS NULL AND load_p2_vswr IS NULL)",
    "ck_vna_check_session": "kind <> 'session' OR (confidence_passed IS NOT NULL AND confidence_tx_dev_db IS NOT NULL AND "
                            "(NOT confidence_passed OR (confidence_tx_dev_db <= 0.1 AND load_p1_vswr IS NOT NULL AND "
                            "load_p2_vswr IS NOT NULL)) AND passed = (CASE WHEN confidence_passed AND load_p1_vswr <= 1.06 "
                            "AND load_p2_vswr <= 1.06 THEN TRUE ELSE FALSE END))",
    "ck_vna_check_flex": "kind <> 'flex' OR (s21_dev_db IS NOT NULL AND vswr_dev IS NOT NULL AND load_p1_vswr IS NOT NULL AND "
                         "load_p2_vswr IS NOT NULL AND passed = (CASE WHEN s21_dev_db <= 0.05 AND load_p1_vswr <= 1.06 AND "
                         "load_p2_vswr <= 1.06 THEN TRUE ELSE FALSE END))",
    "ck_vna_check_control": "kind <> 'control_standard' OR (standard_inventory IS NOT NULL AND vswr_dev IS NOT NULL AND "
                            "vswr_limit IS NOT NULL AND s21_dev_db IS NOT NULL AND s21_limit_db IS NOT NULL AND "
                            "passed = (CASE WHEN vswr_dev <= vswr_limit AND s21_dev_db <= s21_limit_db THEN TRUE ELSE FALSE END))",
    "ck_vna_check_kit": "kind <> 'kit_standards' OR (standard_inventory IS NOT NULL AND kit_ratio IS NOT NULL AND "
                        "passed = (CASE WHEN kit_ratio <= 1 THEN TRUE ELSE FALSE END))",
}
VALUE_FIELDS = ("confidence_passed", "confidence_tx_dev_db", "confidence_rx_dev_db", "load_p1_vswr", "load_p2_vswr",
                "standard_inventory", "reference", "vswr_dev", "vswr_limit", "s21_dev_db", "s21_limit_db", "kit_ratio")


class VnaCheckRow(Base):
    __tablename__ = "production_vna_checks"
    __table_args__ = (
        *(CheckConstraint(sql, name=name) for name, sql in VNA_CHECKS.items()),
        Index("ix_vna_check_instrument_kind", "instrument_id", "kind", "performed_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    instrument_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    operator: Mapped[str] = mapped_column(String(200), nullable=False)
    actor: Mapped[str] = mapped_column(String(300), nullable=False)
    calibration_id: Mapped[str] = mapped_column(String(64), nullable=False)
    acm_serial: Mapped[str] = mapped_column(String(128), nullable=False)
    profile: Mapped[str] = mapped_column(String(300), nullable=False)
    vna_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    acm_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence_tx_dev_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_rx_dev_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_p1_vswr: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_p2_vswr: Mapped[float | None] = mapped_column(Float, nullable=True)
    standard_inventory: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reference: Mapped[str] = mapped_column(String(300), nullable=False)
    vswr_dev: Mapped[float | None] = mapped_column(Float, nullable=True)
    vswr_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    s21_dev_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    s21_limit_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    kit_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)


def record_result(row: VnaCheckRow) -> dict:
    return {"id": str(row.id), "kind": row.kind, "kind_label": KIND_LABELS[row.kind], "passed": row.passed,
            "performed_at": row.performed_at, "recorded_at": row.recorded_at, "source": row.source, "operator": row.operator,
            "actor": row.actor, "calibration_id": row.calibration_id, "acm_serial": row.acm_serial, "profile": row.profile,
            "vna_temperature_c": row.vna_temperature_c, "acm_temperature_c": row.acm_temperature_c,
            **{name: getattr(row, name) for name in VALUE_FIELDS}, "note": row.note}


class VnaCheckRepository:
    def __init__(self, session):
        self.session = session

    def by_source(self, source: str) -> VnaCheckRow | None:
        return self.session.scalar(select(VnaCheckRow).where(VnaCheckRow.source == source))

    def add(self, row: VnaCheckRow) -> VnaCheckRow:
        self.session.add(row)
        self.session.flush()
        return row

    def has_records(self, instrument_id: UUID) -> bool:
        return self.session.scalar(select(VnaCheckRow.id).where(VnaCheckRow.instrument_id == instrument_id).limit(1)) is not None

    def due(self, instrument_id: UUID, now: datetime) -> list[dict]:
        row = VnaCheckRow
        latest = {}
        for kind in ("control_standard", "kit_standards"):
            last = self.session.execute(select(row.performed_at, row.passed).where(row.instrument_id == instrument_id, row.kind == kind)
                                        .order_by(row.performed_at.desc(), row.recorded_at.desc()).limit(1)).first()
            if last is None:
                continue
            last_pass = self.session.scalar(select(func.max(row.performed_at)).where(
                row.instrument_id == instrument_id, row.kind == kind, row.passed.is_(True)))
            latest[kind] = (last.performed_at, bool(last.passed), last_pass)
        return due_items(latest, now)

    def journal(self, instrument_id: UUID, limit: int = 200) -> list[dict]:
        row = VnaCheckRow
        rows = self.session.scalars(select(row).where(row.instrument_id == instrument_id)
                                    .order_by(row.performed_at.desc(), row.recorded_at.desc()).limit(limit))
        return [record_result(item) for item in rows]
