"""Immutable measured revisions. Call writes inside a unit of work; the caller owns commit/rollback."""

from dataclasses import asdict
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint,
                        Integer, JSON, LargeBinary, String, UniqueConstraint, Uuid, func, select, update)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base
from .application import ConcurrentProductionUpdate
from .domain import ProductionValidationError
from .infrastructure import ProductionInstrumentRow
from .rf_infrastructure import RfDetailsRow
from .rf_kit_characterization import (
    CHARACTERIZED_DEFINITION, LOADS_DEFINITION, KitStandard, check_completeness,
    check_revision, curve_metadata, evidence_snapshot, generation_hash,
)


class RfKitStandardRow(Base):
    __tablename__ = "production_rf_kit_standards"
    __table_args__ = (
        UniqueConstraint("kit_instrument_id", "piece_label", name="uq_rf_kit_piece_label"),
        CheckConstraint("length(trim(piece_label)) BETWEEN 1 AND 100 AND piece_label = trim(piece_label)", name="ck_rf_kit_piece_label"),
        CheckConstraint("kind IN ('OPEN','SHORT','LOAD','THRU')", name="ck_rf_kit_piece_kind"),
        CheckConstraint("gender IN ('male','female')", name="ck_rf_kit_piece_gender"),
        CheckConstraint("(kind = 'THRU' AND gender2 IS NOT NULL AND gender2 = gender) OR (kind <> 'THRU' AND gender2 IS NULL)", name="ck_rf_kit_piece_gender2"),
        CheckConstraint("f_min_hz >= 0 AND f_max_hz > f_min_hz AND f_max_hz <= 6000000000", name="ck_rf_kit_piece_band"),
        CheckConstraint("NOT (gender = 'female' AND kind IN ('OPEN','SHORT')) OR f_max_hz <= 3000000000", name="ck_rf_kit_piece_female_band"),
        CheckConstraint("version >= 1", name="ck_rf_kit_piece_version"),
        ForeignKeyConstraint(["id", "current_revision_id"],
                             ["production_rf_standard_characterizations.standard_id", "production_rf_standard_characterizations.id"],
                             name="fk_rf_kit_current_revision", ondelete="RESTRICT", use_alter=True),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    kit_instrument_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False)
    piece_label: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    gender: Mapped[str] = mapped_column(String(8), nullable=False)
    gender2: Mapped[str | None] = mapped_column(String(8))
    f_min_hz: Mapped[float] = mapped_column(Float, nullable=False)
    f_max_hz: Mapped[float] = mapped_column(Float, nullable=False)
    current_revision_id: Mapped[UUID | None] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)


class RfStandardCharacterizationRow(Base):
    __tablename__ = "production_rf_standard_characterizations"
    __table_args__ = (
        UniqueConstraint("standard_id", "revision", name="uq_rf_standard_revision"),
        UniqueConstraint("standard_id", "sha256", name="uq_rf_standard_sha256"),
        UniqueConstraint("standard_id", "id", name="uq_rf_standard_revision_owner"),
        CheckConstraint("revision >= 1", name="ck_rf_standard_revision"),
        CheckConstraint("format IN ('s1p','s2p')", name="ck_rf_standard_format"),
        CheckConstraint("length(content) BETWEEN 1 AND 2097152", name="ck_rf_standard_content"),
        CheckConstraint("length(sha256) = 64 AND length(grid_hash) = 64", name="ck_rf_standard_hashes"),
        CheckConstraint("points BETWEEN 2 AND 200000", name="ck_rf_standard_points"),
        CheckConstraint("f_min_hz >= 0 AND f_max_hz > f_min_hz AND f_max_hz <= 6000000000", name="ck_rf_standard_band"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    standard_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_rf_kit_standards.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[str] = mapped_column(String(3), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    grid_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    f_min_hz: Mapped[float] = mapped_column(Float, nullable=False)
    f_max_hz: Mapped[float] = mapped_column(Float, nullable=False)
    raw_artifact_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("production_vna_artifacts.id", ondelete="RESTRICT"))
    provenance: Mapped[dict] = mapped_column(JSON(none_as_null=True), nullable=False)
    validation: Mapped[dict] = mapped_column(JSON(none_as_null=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


def _standard(row: RfKitStandardRow) -> KitStandard:
    return KitStandard(**{key: getattr(row, key) for key in KitStandard.__dataclass_fields__})


def _actor(actor: str) -> str:
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 255:
        raise ProductionValidationError("Укажите автора изменения меры")
    return actor


class RfKitRepository:
    def __init__(self, session: Session):
        self.session = session

    def _kit(self, kit_id: UUID, expected_version: int | None = None, *, lock: bool = True) -> RfDetailsRow:
        # Match the existing instrument-tree -> RF lock order. In particular, registering
        # a piece must not acquire its kit FK lock after a concurrent RF editor holds the tree.
        # Readers (lock=False) take no row locks: a view-only GET must not block or queue behind writers.
        card = select(ProductionInstrumentRow.id).where(ProductionInstrumentRow.id == kit_id)
        details = select(RfDetailsRow).where(RfDetailsRow.instrument_id == kit_id)
        if lock:
            card, details = card.with_for_update(), details.with_for_update()
        if self.session.scalar(card) is None:
            raise KeyError("Набор СИ не найден")
        row = self.session.scalar(details.execution_options(populate_existing=True))
        if row is None:
            raise KeyError("Набор СИ не найден")
        if (row.role, row.kit_definition) not in (("load_kit", LOADS_DEFINITION), ("solt_kit", CHARACTERIZED_DEFINITION)):
            raise ProductionValidationError("Меры регистрируются только для набора SMA-MF06")
        if expected_version is not None and row.version != expected_version:
            raise ConcurrentProductionUpdate("Набор изменён — обновите карточку")
        return row

    def _advance(self, kit: RfDetailsRow, **values):
        # The aggregate CAS also serializes SQLite writers, where FOR UPDATE is ignored.
        changed = self.session.execute(update(RfDetailsRow).where(
            RfDetailsRow.instrument_id == kit.instrument_id, RfDetailsRow.version == kit.version,
        ).values(version=kit.version + 1, **values))
        if changed.rowcount != 1:
            raise ConcurrentProductionUpdate("Набор изменён — обновите карточку")

    def standards(self, kit_id: UUID) -> list[RfKitStandardRow]:
        return list(self.session.scalars(select(RfKitStandardRow).where(
            RfKitStandardRow.kit_instrument_id == kit_id).order_by(RfKitStandardRow.piece_label)
            .execution_options(populate_existing=True)))

    def register(self, kit_id: UUID, standard: KitStandard, *, expected_kit_version: int,
                 actor: str, now: datetime) -> RfKitStandardRow:
        actor = _actor(actor)
        kit = self._kit(kit_id, expected_kit_version)
        if kit.characterization_generation:
            raise ProductionValidationError("Нельзя менять состав активированного набора")
        existing = self.standards(kit_id)
        if any(row.piece_label == standard.piece_label or _standard(row).signature == standard.signature for row in existing):
            raise ProductionValidationError("Мера с такой маркировкой или назначением уже зарегистрирована")
        self._advance(kit)
        row = RfKitStandardRow(id=uuid4(), kit_instrument_id=kit_id, **asdict(standard), version=1,
                               created_at=now, updated_at=now, created_by=actor, updated_by=actor)
        self.session.add(row)
        self.session.flush()
        return row

    def append_revision(self, standard_id: UUID, *, expected_version: int, format: str, content: bytes,
                        provenance: dict, validation: dict, actor: str, now: datetime,
                        raw_artifact_id: UUID | None = None) -> RfStandardCharacterizationRow:
        """T3 entry point: store pending evidence, never activate implicitly or overwrite a revision."""
        actor = _actor(actor)
        standard = self.session.get(RfKitStandardRow, standard_id)
        if standard is None:
            raise KeyError("Мера не найдена")
        kit = self._kit(standard.kit_instrument_id)
        self.session.refresh(standard)
        if standard.version != expected_version:
            raise ConcurrentProductionUpdate("Мера изменена — обновите карточку")
        metadata = curve_metadata(content, format)
        provenance, validation = evidence_snapshot(provenance, validation)
        # Failed validation evidence may be retained, but even pending curves obey identity and band limits.
        check_revision(_standard(standard), format, metadata, {"valid": True})
        if raw_artifact_id is not None:
            from .vna_session_infrastructure import VnaArtifactRow
            artifact = self.session.get(VnaArtifactRow, raw_artifact_id)
            if artifact is None or (str(artifact.session_id), str(artifact.command_id)) != (provenance["session_id"], provenance["command_id"]):
                raise ProductionValidationError("Исходный файл не соответствует сеансу и команде измерения")
        table = RfStandardCharacterizationRow
        if self.session.scalar(select(table.id).where(table.standard_id == standard_id, table.sha256 == metadata.sha256)):
            raise ProductionValidationError("Этот файл меры уже сохранён")
        number = (self.session.scalar(select(func.max(table.revision)).where(table.standard_id == standard_id)) or 0) + 1
        self._advance(kit)
        changed = self.session.execute(update(RfKitStandardRow).where(
            RfKitStandardRow.id == standard_id, RfKitStandardRow.version == expected_version,
        ).values(version=expected_version + 1, updated_at=now, updated_by=actor))
        if changed.rowcount != 1:
            raise ConcurrentProductionUpdate("Мера изменена — обновите карточку")
        row = table(id=uuid4(), standard_id=standard_id, revision=number, format=format, content=content,
                    **asdict(metadata), provenance=provenance, validation=validation,
                    raw_artifact_id=raw_artifact_id, created_at=now, created_by=actor)
        self.session.add(row)
        self.session.flush()
        return row

    def activate(self, kit_id: UUID, revisions: dict[UUID, UUID], *, expected_kit_version: int,
                 expected_standard_versions: dict[UUID, int], actor: str, now: datetime) -> dict:
        """Publish one complete snapshot under the aggregate lock; errors require caller rollback.

        Only the newest evidence for each piece is eligible. A failed replacement cannot
        be hidden by republishing an older revision. Pending evidence is not current evidence.
        """
        actor = _actor(actor)
        kit = self._kit(kit_id, expected_kit_version)
        standards = self.standards(kit_id)
        check_completeness([_standard(row) for row in standards])
        ids = {row.id for row in standards}
        if set(revisions) != ids or set(expected_standard_versions) != ids:
            raise ProductionValidationError("Выберите ревизию и версию каждой из восьми мер")
        manifest = []
        for standard in standards:
            if standard.version != expected_standard_versions[standard.id]:
                raise ConcurrentProductionUpdate("Мера изменена — обновите карточку")
            revision = self.session.scalar(select(RfStandardCharacterizationRow).where(
                RfStandardCharacterizationRow.standard_id == standard.id).order_by(
                RfStandardCharacterizationRow.revision.desc()).limit(1).execution_options(populate_existing=True))
            if revision is None or revision.id != revisions[standard.id]:
                raise ProductionValidationError("Для активации нужна последняя ревизия каждой меры")
            metadata = curve_metadata(revision.content, revision.format)
            if any(getattr(revision, key) != value for key, value in asdict(metadata).items()):
                raise ProductionValidationError("Контрольная сумма или сетка ревизии не совпадает с файлом")
            _, validation = evidence_snapshot(revision.provenance, revision.validation)
            check_revision(_standard(standard), revision.format, metadata, validation)
            manifest.append({"standard_id": str(standard.id), "revision_id": str(revision.id),
                             "revision": revision.revision, "sha256": revision.sha256})
        generation = kit.characterization_generation + 1
        digest = generation_hash(kit_id, generation, manifest)
        version = kit.version + 1   # the kit version this publication creates (what the audit and caller must see)
        self._advance(kit, role="solt_kit", kit_definition=CHARACTERIZED_DEFINITION,
                      characterization_generation=generation, characterization_hash=digest)
        for standard in standards:
            changed = self.session.execute(update(RfKitStandardRow).where(
                RfKitStandardRow.id == standard.id, RfKitStandardRow.version == expected_standard_versions[standard.id],
            ).values(current_revision_id=revisions[standard.id], version=standard.version + 1,
                     updated_at=now, updated_by=actor))
            if changed.rowcount != 1:
                raise ConcurrentProductionUpdate("Мера изменена — обновите карточку")
        self.session.flush()
        return {"kit_id": str(kit_id), "version": version, "generation": generation, "hash": digest,
                "revisions": manifest}

    def snapshot(self, kit_id: UUID) -> dict:
        """Read-only view: no row locks; the version recheck below detects a concurrent publication."""
        kit = self._kit(kit_id, lock=False)
        version = kit.version
        rows = self.standards(kit_id)
        current_ids = [row.current_revision_id for row in rows if row.current_revision_id]
        summaries = {row.id: {key: getattr(row, key) for key in (
            "id", "revision", "format", "sha256", "grid_hash", "points", "f_min_hz", "f_max_hz", "created_at", "created_by")}
            for row in self.session.scalars(select(RfStandardCharacterizationRow).where(
                RfStandardCharacterizationRow.id.in_(current_ids)))} if current_ids else {}
        # SQLite ignores FOR UPDATE; detect a publication between the aggregate and child reads.
        if self.session.scalar(select(RfDetailsRow.version).where(RfDetailsRow.instrument_id == kit_id)) != version:
            raise ConcurrentProductionUpdate("Набор изменён во время чтения — обновите карточку")
        return {"kit_id": kit_id, "version": version, "generation": kit.characterization_generation,
                "hash": kit.characterization_hash, "kit_definition": kit.kit_definition,
                "standards": [{"id": row.id, **asdict(_standard(row)), "version": row.version,
                               "current_revision": summaries.get(row.current_revision_id)} for row in rows]}
