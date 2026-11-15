"""RF facts attached to the existing registry UUID; registry quantities remain authoritative."""

from dataclasses import asdict, replace
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, String, Text, Uuid, delete, func, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base
from .application import ConcurrentProductionUpdate
from .infrastructure import ProductionInstrumentFileRow
from .rf_interfaces import RfDetails, RfInterface


class RfDetailsRow(Base):
    __tablename__ = "production_rf_details"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_rf_details_version"),
        CheckConstraint("role IN ('unknown','vna','adapter','test_cable','auto_calibration','solt_kit','load','load_kit')", name="ck_rf_details_role"),
        CheckConstraint("f_min_ghz IS NULL OR (f_min_ghz >= 0 AND f_min_ghz <= 1000)", name="ck_rf_details_min"),
        CheckConstraint("max_points IS NULL OR (role = 'vna' AND max_points BETWEEN 2 AND 10000001)", name="ck_rf_details_points"),
        CheckConstraint("kit_definition = '' OR (role = 'solt_kit' AND kit_definition = '6650F27-full') OR (role = 'load_kit' AND kit_definition = 'SMA-MF06-loads')", name="ck_rf_details_kit"),
        CheckConstraint("NOT confirmed OR (role <> 'unknown' AND length(trim(source)) > 0)", name="ck_rf_details_confirmed"),
    )
    instrument_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey("production_instruments.id", ondelete="RESTRICT"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer(), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="ck_rf_details_bool"), nullable=False)
    source: Mapped[str] = mapped_column(Text(), nullable=False)
    f_min_ghz: Mapped[float | None] = mapped_column(Float(), nullable=True)
    max_points: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    kit_definition: Mapped[str] = mapped_column(String(64), nullable=False)


class RfInterfaceRow(Base):
    __tablename__ = "production_rf_interfaces"
    __table_args__ = (
        CheckConstraint("position IN (1, 2)", name="ck_rf_interfaces_position"),
        CheckConstraint("series IN ('SMA','3.5mm','N','TNC','BNC','F','RP-SMA','тип III')", name="ck_rf_interfaces_series"),
        CheckConstraint("body IN ('вилка','розетка')", name="ck_rf_interfaces_body"),
        CheckConstraint("center IN ('штырь','гнездо')", name="ck_rf_interfaces_center"),
        CheckConstraint("impedance_ohm IS NULL OR (impedance_ohm > 0 AND impedance_ohm <= 1000)", name="ck_rf_interfaces_impedance"),
        CheckConstraint("f_max_ghz IS NULL OR (f_max_ghz > 0 AND f_max_ghz <= 1000)", name="ck_rf_interfaces_max"),
        CheckConstraint("NOT f_confirmed OR (impedance_ohm IS NOT NULL AND f_max_ghz IS NOT NULL AND length(trim(f_source)) > 0)", name="ck_rf_interfaces_confirmed"),
    )
    instrument_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey("production_rf_details.instrument_id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer(), primary_key=True)
    series: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(String(16), nullable=False)
    center: Mapped[str] = mapped_column(String(16), nullable=False)
    impedance_ohm: Mapped[float | None] = mapped_column(Float(), nullable=True)
    f_max_ghz: Mapped[float | None] = mapped_column(Float(), nullable=True)
    f_confirmed: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="ck_rf_interfaces_bool"), nullable=False)
    f_source: Mapped[str] = mapped_column(Text(), nullable=False)


class RfRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self, instrument_id: UUID | None = None) -> dict[UUID, RfDetails]:
        ports = {}
        port_query = select(RfInterfaceRow).order_by(RfInterfaceRow.position)
        details_query = select(RfDetailsRow)
        if instrument_id is not None:
            port_query = port_query.where(RfInterfaceRow.instrument_id == instrument_id)
            details_query = details_query.where(RfDetailsRow.instrument_id == instrument_id)
        for p in self.session.scalars(port_query):
            ports.setdefault(p.instrument_id, []).append(RfInterface(**{
                key: getattr(p, key) for key in RfInterface.__dataclass_fields__}))
        return {row.instrument_id: RfDetails(interfaces=tuple(ports.get(row.instrument_id, ())), **{
            key: getattr(row, key) for key in RfDetails.__dataclass_fields__ if key != "interfaces"
        }) for row in self.session.scalars(details_query)}

    def get(self, instrument_id: UUID) -> RfDetails:
        return self.list(instrument_id).get(instrument_id, RfDetails())

    def characterization_files(self, instrument_ids):
        """Stream only eligible small Touchstone attachments, never all registry passports."""
        if not instrument_ids:
            return
        row = ProductionInstrumentFileRow
        query = select(row.instrument_id, row.id, row.content).where(
            row.instrument_id.in_(instrument_ids), row.kind == "Прочее",
            func.lower(row.file_name).like("%.s2p"), func.length(row.content) <= 2 * 1024 * 1024,
        ).order_by(row.uploaded_at.desc(), row.id).execution_options(yield_per=1)
        yield from self.session.execute(query)

    def save(self, instrument_id: UUID, details: RfDetails) -> RfDetails:
        # The caller holds the registry tree lock, also serializing initial inserts.
        current = self.session.get(RfDetailsRow, instrument_id)
        if (current.version if current else 0) != details.version:
            raise ConcurrentProductionUpdate("ВЧ-интерфейсы изменены другим пользователем — обновите запись")
        saved = replace(details, version=details.version + 1)
        values = asdict(saved)
        values.pop("interfaces")
        if current is None:
            self.session.add(RfDetailsRow(instrument_id=instrument_id, **values))
            self.session.flush()
        else:
            changed = self.session.execute(update(RfDetailsRow).where(
                RfDetailsRow.instrument_id == instrument_id, RfDetailsRow.version == details.version,
            ).values(**values))
            if changed.rowcount != 1:
                raise ConcurrentProductionUpdate("ВЧ-интерфейсы изменены другим пользователем — обновите запись")
        self.session.execute(delete(RfInterfaceRow).where(RfInterfaceRow.instrument_id == instrument_id))
        self.session.add_all(RfInterfaceRow(instrument_id=instrument_id, position=i, **asdict(port))
                             for i, port in enumerate(saved.interfaces, 1))
        self.session.flush()
        return saved
