"""RF facts attached to the existing registry UUID; registry quantities remain authoritative."""

from dataclasses import asdict, replace
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, String, Text, Uuid, delete, func, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base
from .application import ConcurrentProductionUpdate
from .infrastructure import ProductionInstrumentFileRow
from .rf_interfaces import RfDetails, RfInterface
from .vna_characterization import is_characterization_file


# Only an adapter or a test cable carries an assigned norm; the id itself is checked against rf-norms.json on save.
NORM_CHECK = "norm_id = '' OR (role IN ('adapter','test_cable') AND norm_id = trim(norm_id))"
KIT_CHECK = "kit_definition = '' OR (role = 'solt_kit' AND kit_definition IN ('6650F27-full','SMA-MF06-characterized')) OR (role = 'load_kit' AND kit_definition = 'SMA-MF06-loads')"
GENERATION_CHECK = "(kit_definition = 'SMA-MF06-characterized' AND characterization_generation > 0 AND characterization_hash IS NOT NULL AND length(characterization_hash) = 64) OR (kit_definition <> 'SMA-MF06-characterized' AND characterization_generation = 0 AND characterization_hash IS NULL)"


class RfDetailsRow(Base):
    __tablename__ = "production_rf_details"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_rf_details_version"),
        CheckConstraint("mating_resource IS NULL OR mating_resource > 0", name="ck_rf_details_mating_resource"),
        CheckConstraint("role IN ('unknown','vna','adapter','test_cable','auto_calibration','solt_kit','load','load_kit')", name="ck_rf_details_role"),
        CheckConstraint("f_min_ghz IS NULL OR (f_min_ghz >= 0 AND f_min_ghz <= 1000)", name="ck_rf_details_min"),
        CheckConstraint("max_points IS NULL OR (role = 'vna' AND max_points BETWEEN 2 AND 10000001)", name="ck_rf_details_points"),
        CheckConstraint(KIT_CHECK, name="ck_rf_details_kit"),
        CheckConstraint(GENERATION_CHECK, name="ck_rf_details_characterization"),
        CheckConstraint("NOT confirmed OR (role <> 'unknown' AND length(trim(source)) > 0)", name="ck_rf_details_confirmed"),
        CheckConstraint(NORM_CHECK, name="ck_rf_details_norm"),
    )
    instrument_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey("production_instruments.id", ondelete="RESTRICT"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer(), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="ck_rf_details_bool"), nullable=False)
    source: Mapped[str] = mapped_column(Text(), nullable=False)
    f_min_ghz: Mapped[float | None] = mapped_column(Float(), nullable=True)
    max_points: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    kit_definition: Mapped[str] = mapped_column(String(64), nullable=False)
    mating_resource: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    norm_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    characterization_generation: Mapped[int] = mapped_column(Integer(), nullable=False, server_default="0")
    characterization_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


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

    def _named_characterization_file_ids(self, instrument_ids):
        if not instrument_ids:
            return
        row = ProductionInstrumentFileRow
        query = select(row.instrument_id, row.id, row.file_name).where(
            row.instrument_id.in_(instrument_ids), func.lower(row.file_name).like("%.s2p"),
        ).order_by(row.uploaded_at.desc(), row.id.desc()).execution_options(yield_per=1)
        for instrument_id, file_id, file_name in self.session.execute(query):
            if is_characterization_file(file_name):
                yield instrument_id, file_id

    def latest_characterization_file_ids(self, instrument_ids):
        """Select before validation: a corrupt newest upload must never revive older evidence."""
        seen = set()
        for instrument_id, file_id in self._named_characterization_file_ids(instrument_ids):
            if instrument_id not in seen:
                seen.add(instrument_id)
                yield instrument_id, file_id

    def characterization_history_file_ids(self, instrument_id, limit=10):
        """Bound the public history to the ten most recent previous named uploads."""
        files = self._named_characterization_file_ids([instrument_id])
        next(files, None)  # Current file, including an invalid replacement, is never history.
        for index, (_, file_id) in enumerate(files):
            if index >= limit:
                break
            yield file_id

    def characterization_files(self, instrument_ids):
        """Load only the latest named measurements, retaining the planner's payload bound."""
        row = ProductionInstrumentFileRow
        for instrument_id, file_id in self.latest_characterization_file_ids(instrument_ids):
            content = self.session.scalar(select(row.content).where(
                row.id == file_id, func.length(row.content) <= 2 * 1024 * 1024,
            ))
            yield instrument_id, file_id, content

    def save(self, instrument_id: UUID, details: RfDetails) -> RfDetails:
        # The caller holds the registry tree lock, also serializing initial inserts.
        current = self.session.get(RfDetailsRow, instrument_id)
        if (current.version if current else 0) != details.version:
            raise ConcurrentProductionUpdate("ВЧ-интерфейсы изменены другим пользователем — обновите запись")
        # A side with mating history is a physical connector: it may not disappear or turn into another one.
        from .mating_infrastructure import recorded_sides
        from .matings import end_names
        from .domain import ProductionValidationError
        if details.kit_definition == "SMA-MF06-characterized" or (current and current.kit_definition == "SMA-MF06-characterized"):
            if current is None or (details.role, details.kit_definition) != (current.role, current.kit_definition):
                raise ProductionValidationError("Состав охарактеризованного набора меняется только при активации ревизий мер")
        recorded = recorded_sides(self.session, instrument_id)
        if recorded - set(end_names(details)):
            raise ProductionValidationError("Нельзя удалить сторону с историей сочленений")
        from .instrument_norm_assignment import norm_problem
        if problem := norm_problem(details):
            raise ProductionValidationError(problem)
        before = self.get(instrument_id)
        # «Нагрузка» redefines the physical sides (one connector whatever the interfaces): with history it is fixed.
        if recorded and before.role != details.role and "load" in (before.role, details.role):
            raise ProductionValidationError("Нельзя сменить назначение «Нагрузка» у элемента с историей сочленений")
        for end in sorted(recorded):
            position = 0 if end == "A" else 1
            if position < len(before.interfaces):
                old = before.interfaces[position]
                if position >= len(details.interfaces) or any(
                        getattr(old, key) != getattr(details.interfaces[position], key) for key in ("series", "body", "center")):
                    raise ProductionValidationError("Нельзя изменить физический разъём стороны с историей сочленений")
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
