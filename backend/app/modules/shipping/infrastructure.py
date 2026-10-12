"""SQLAlchemy persistence for finished goods and shipments."""

from __future__ import annotations

import json

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, Uuid, and_, func, or_,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker, validates

from app.shared.persistence import AuditEventRow, Base, SqlAlchemyAuditRepository, SqlAlchemyOutboxRepository
from app.shared.project_number import normalize_project_number
from .domain import (
    ADDRESSEE_REQUISITE_FIELDS, BoxItem, DeliveryTerms, FinishedGood, GoodMove, GoodsState, MOVE_ROUTES, MoveContext,
    MoveKind, ProjectAddressee, Shipment, ShipmentBox, ShipmentLine, ShipmentStatus, StoredFile, UpdType,
    balance_moves,
)


# Остаток ГП по состояниям (0111): принято ≥ упаковано ≥ отгружено ≥ отгружено до ERP ≥ 0.
FINISHED_GOODS_CHECKS = {
    "ck_finished_goods_sent_nonneg": "before_erp >= 0 AND sent >= before_erp",
    "ck_finished_goods_packed_over_sent": "shipped >= sent",
    "ck_finished_goods_accepted_over_packed": "accepted >= shipped",
}


class FinishedGoodRow(Base):
    __tablename__ = "finished_goods"
    __table_args__ = (
        Index("ux_finished_goods_project_item", "project", "item_index", unique=True),
        *(CheckConstraint(sql, name=name) for name, sql in FINISHED_GOODS_CHECKS.items()),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    project: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    item_index: Mapped[str] = mapped_column(String(255), nullable=False)
    customer: Mapped[str] = mapped_column(String(512), nullable=False)
    article: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    ordered: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    shipped: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    received_at: Mapped[date | None] = mapped_column(Date(), nullable=True)
    grn: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    note: Mapped[str] = mapped_column(Text(), nullable=False)
    sent: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    before_erp: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    # № проекта для сравнения (`normalize_project_number`): трек проекта читает по индексу; ставится при записи
    # `project`, NULL — строка мимо ORM (читается и сверяется в Python).
    project_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}

    @validates("project")
    def _keep_project_key(self, _name: str, value: str) -> str:
        self.project_key = normalize_project_number(value)
        return value


class ShipmentRow(Base):
    __tablename__ = "shipments"
    __table_args__ = (Index("ix_shipments_created_id", "created_at", "id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    customer: Mapped[str] = mapped_column(String(512), nullable=False)
    receiver: Mapped[str] = mapped_column(String(512), nullable=False)
    shipping_point: Mapped[str] = mapped_column(Text(), nullable=False)
    contact: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(128), nullable=False)
    terms: Mapped[str | None] = mapped_column(String(32), nullable=True)
    carrier: Mapped[str] = mapped_column(String(255), nullable=False)
    deadline: Mapped[date | None] = mapped_column(Date(), nullable=True)
    upd_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    upd_number: Mapped[str] = mapped_column(String(255), nullable=False)
    upd_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    upd_url: Mapped[str] = mapped_column(Text(), nullable=False)
    pl_number: Mapped[str] = mapped_column(String(255), nullable=False)
    dn_number: Mapped[str] = mapped_column(String(255), nullable=False)
    track_number: Mapped[str] = mapped_column(String(255), nullable=False)
    packed_at: Mapped[date | None] = mapped_column(Date(), nullable=True)
    shipped_at: Mapped[date | None] = mapped_column(Date(), nullable=True)
    manager: Mapped[str] = mapped_column(String(255), nullable=False)
    manager_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    note: Mapped[str] = mapped_column(Text(), nullable=False)
    tk_note: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    # Поля адресата из карточки проекта, через запятую (Shipment.inherited).
    inherited_fields: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class ShipmentBoxRow(Base):
    """One place of a shipment (`ОТГРУЗКА.МЕСТА`); composition is JSON [{item_index, quantity}]."""

    __tablename__ = "shipment_boxes"
    shipment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"), primary_key=True)
    place: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(255), nullable=False)
    dims: Mapped[str] = mapped_column(String(255), nullable=False)
    net: Mapped[str] = mapped_column(String(64), nullable=False)
    gross: Mapped[str] = mapped_column(String(64), nullable=False)
    seal: Mapped[str] = mapped_column(String(255), nullable=False)
    composition: Mapped[str] = mapped_column(Text(), nullable=False)


class ShipmentLineRow(Base):
    __tablename__ = "shipment_lines"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    shipment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True)
    finished_good_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("finished_goods.id", ondelete="RESTRICT"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    item_index: Mapped[str] = mapped_column(String(255), nullable=False)
    article: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    requested: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    picked: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    grn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Как у FinishedGoodRow: отправки проекта находятся по индексу ключа позиций.
    project_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    @validates("project")
    def _keep_project_key(self, _name: str, value: str) -> str:
        self.project_key = normalize_project_number(value)
        return value


def _in(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _route(kind: MoveKind, pairs) -> str:
    return " OR ".join(
        f"(kind = '{kind.value}' AND from_state = '{source.value}' AND to_state = '{target.value}')"
        for source, target in sorted(pairs, key=lambda pair: (pair[0].value, pair[1].value))
    )


# Журнал ГП (0111). Порядок значений в IN важен сеялке CI (tests/migrations/seed_any_revision): она берёт первое —
# 'accept' из 'production' в 'accepted', и такой ход проходит проверку маршрута.
MOVE_STATES_FROM = ("production", "accepted", "packed", "shipped")
MOVE_STATES_TO = ("accepted", "production", "packed", "shipped")
FINISHED_GOODS_MOVE_CHECKS = {
    "ck_finished_goods_moves_kind": _in("kind", [kind.value for kind in MoveKind]),
    "ck_finished_goods_moves_from_state": _in("from_state", MOVE_STATES_FROM),
    "ck_finished_goods_moves_to_state": _in("to_state", MOVE_STATES_TO),
    "ck_finished_goods_moves_quantity": "quantity > 0",
    "ck_finished_goods_moves_route": " OR ".join(
        _route(kind, pairs) for kind, pairs in MOVE_ROUTES.items()
    ),
    "ck_finished_goods_moves_correction_reason": "kind <> 'correction' OR reason <> ''",
}


class FinishedGoodMoveRow(Base):
    """Журнал ГП: ход штук КБ из состояния в состояние. Только дополняется; остаток — счётчики `finished_goods`,
    меняются вместе с ходом в одной транзакции (`SqlAlchemyFinishedGoodsRepository.add`)."""

    __tablename__ = "finished_goods_moves"
    __table_args__ = (
        Index("ix_finished_goods_moves_kb", "project_key", "item_index"),
        *(CheckConstraint(sql, name=name) for name, sql in FINISHED_GOODS_MOVE_CHECKS.items()),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    finished_good_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("finished_goods.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    project_key: Mapped[str] = mapped_column(String(255), nullable=False)
    item_index: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    from_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    shipment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text(), nullable=False, default="")


def _move(row: FinishedGoodMoveRow) -> GoodMove:
    return GoodMove(
        id=row.id, finished_good_id=row.finished_good_id, project_key=row.project_key, item_index=row.item_index,
        kind=MoveKind(row.kind), quantity=row.quantity, from_state=GoodsState(row.from_state),
        to_state=GoodsState(row.to_state), actor=row.actor, occurred_at=row.occurred_at,
        shipment_id=row.shipment_id, actor_id=row.actor_id, reason=row.reason or "",
    )


class ProjectAddresseeRow(Base):
    """Project card's shipping defaults; a shipment copies them at creation."""

    __tablename__ = "shipping_project_addressees"
    project: Mapped[str] = mapped_column(String(255), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    receiver: Mapped[str] = mapped_column(String(512), nullable=False)
    shipping_point: Mapped[str] = mapped_column(Text(), nullable=False)
    contact: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(128), nullable=False)
    terms: Mapped[str | None] = mapped_column(String(32), nullable=True)
    carrier: Mapped[str] = mapped_column(String(255), nullable=False)
    upd_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    po: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    contract: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    spec: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    invoice: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    pays: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    customer_address: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    hours: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class ShippingSequenceRow(Base):
    __tablename__ = "shipping_sequences"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


def _good(row: FinishedGoodRow) -> FinishedGood:
    return FinishedGood(
        id=row.id, version=row.version, project=row.project, item_index=row.item_index,
        customer=row.customer, article=row.article, name=row.name, ordered=row.ordered,
        unit=row.unit, accepted=row.accepted, shipped=row.shipped,
        received_at=row.received_at, grn=row.grn, note=row.note, sent=row.sent, before_erp=row.before_erp,
    )


class SqlAlchemyFinishedGoodsRepository:
    def __init__(self, session: Session): self.session = session
    def get(self, good_id: UUID) -> FinishedGood | None:
        row = self.session.get(FinishedGoodRow, good_id)
        return _good(row) if row is not None else None
    def by_key(self, project: str, item_index: str) -> FinishedGood | None:
        row = self.session.scalar(select(FinishedGoodRow).where(and_(FinishedGoodRow.project == project, FinishedGoodRow.item_index == item_index)))
        return _good(row) if row is not None else None
    def list(self, limit: int = 500) -> list[FinishedGood]:
        rows = self.session.scalars(select(FinishedGoodRow).order_by(FinishedGoodRow.project, FinishedGoodRow.item_index).limit(limit))
        return [_good(row) for row in rows]
    def reserve_grn(self, year: int) -> str:
        key = f"grn-{year % 100:02d}"
        row = self.session.scalar(select(ShippingSequenceRow).where(ShippingSequenceRow.name == key).with_for_update())
        if row is None:
            row = ShippingSequenceRow(name=key, value=0); self.session.add(row); self.session.flush()
        row.value += 1
        return f"ГП.{year % 100:02d}.{row.value:07d}"
    def for_project(self, project: str) -> list[FinishedGood]:
        """ГП одного проекта точно по `normalize_project_number` (по индексу ключа), а не первые N всей таблицы."""
        key = normalize_project_number(project)
        if not key:
            return []
        rows = self.session.scalars(select(FinishedGoodRow).where(
            or_(FinishedGoodRow.project_key == key, FinishedGoodRow.project_key.is_(None)),
        ).order_by(FinishedGoodRow.project, FinishedGoodRow.item_index))
        return [_good(row) for row in rows if normalize_project_number(row.project) == key]
    def by_project(self, project: str) -> list[FinishedGood]:
        rows = self.session.scalars(select(FinishedGoodRow).where(FinishedGoodRow.project == project).order_by(FinishedGoodRow.item_index))
        return [_good(row) for row in rows]
    def in_shipments(self, good_ids) -> set[UUID]:
        if not good_ids: return set()
        return set(self.session.scalars(select(ShipmentLineRow.finished_good_id).where(ShipmentLineRow.finished_good_id.in_(list(good_ids))).distinct()))
    def remove(self, good_id: UUID) -> None:
        row = self.session.get(FinishedGoodRow, good_id)
        if row is not None: self.session.delete(row)
    def add(self, good: FinishedGood, move: MoveContext | None = None) -> tuple[GoodMove, ...]:
        """Строка ГП и, если её счётчики изменились, ходы журнала в той же транзакции (`balance_moves`): остаток по
        состояниям без записи в журнал не меняется. Версия строки сторожит гонку двух ходов одной КБ."""
        row = self.session.get(FinishedGoodRow, good.id)
        before = _good(row) if row is not None else None
        moves = balance_moves(before, good, move, normalize_project_number(good.project) or good.project.strip())
        if row is None:
            row = FinishedGoodRow(id=good.id); self.session.add(row)
        for key in ("version", "project", "item_index", "customer", "article", "name", "ordered", "unit", "accepted", "shipped", "received_at", "grn", "note", "sent", "before_erp"):
            setattr(row, key, getattr(good, key))
        for item in moves:
            self.session.add(FinishedGoodMoveRow(
                id=item.id, finished_good_id=item.finished_good_id, project_key=item.project_key,
                item_index=item.item_index, kind=item.kind.value, quantity=item.quantity,
                from_state=item.from_state.value, to_state=item.to_state.value, shipment_id=item.shipment_id,
                actor=item.actor, actor_id=item.actor_id, occurred_at=item.occurred_at, reason=item.reason,
            ))
        return moves
    def in_journal(self, good_ids) -> set[UUID]:
        if not good_ids: return set()
        return set(self.session.scalars(select(FinishedGoodMoveRow.finished_good_id).where(FinishedGoodMoveRow.finished_good_id.in_(list(good_ids))).distinct()))
    def move_exists(self, move_id: UUID) -> bool:
        return self.session.get(FinishedGoodMoveRow, move_id) is not None
    def moves_for_project(self, project: str, limit: int = 2000) -> list[GoodMove]:
        """Журнал ГП проекта (по `normalize_project_number`), новые сверху."""
        key = normalize_project_number(project)
        if not key:
            return []
        rows = self.session.scalars(select(FinishedGoodMoveRow).where(FinishedGoodMoveRow.project_key == key).order_by(
            FinishedGoodMoveRow.occurred_at.desc(), FinishedGoodMoveRow.id,
        ).limit(limit))
        return [_move(row) for row in rows]
    def journal_totals(self, good_ids=None) -> dict[UUID, dict[tuple[str, str], Decimal]]:
        """Σ штук журнала по (из, в) на строку ГП — сверка с её счётчиками."""
        query = select(
            FinishedGoodMoveRow.finished_good_id, FinishedGoodMoveRow.from_state, FinishedGoodMoveRow.to_state,
            func.sum(FinishedGoodMoveRow.quantity),
        ).group_by(FinishedGoodMoveRow.finished_good_id, FinishedGoodMoveRow.from_state, FinishedGoodMoveRow.to_state)
        if good_ids is not None:
            query = query.where(FinishedGoodMoveRow.finished_good_id.in_(list(good_ids)))
        totals: dict[UUID, dict[tuple[str, str], Decimal]] = {}
        for good_id, source, target, total in self.session.execute(query):
            totals.setdefault(good_id, {})[(source, target)] = Decimal(total or 0)
        return totals


class SqlAlchemyShipmentRepository:
    def __init__(self, session: Session): self.session = session
    def _domain(self, row: ShipmentRow) -> Shipment:
        lines = self.session.scalars(select(ShipmentLineRow).where(ShipmentLineRow.shipment_id == row.id).order_by(ShipmentLineRow.position))
        return Shipment(
            id=row.id, number=row.number, version=row.version, created_at=row.created_at,
            status=ShipmentStatus(row.status), customer=row.customer, receiver=row.receiver,
            shipping_point=row.shipping_point, contact=row.contact, phone=row.phone,
            terms=DeliveryTerms(row.terms) if row.terms else None, carrier=row.carrier, deadline=row.deadline,
            upd_type=UpdType(row.upd_type) if row.upd_type else None, upd_number=row.upd_number, upd_date=row.upd_date,
            upd_url=row.upd_url, pl_number=row.pl_number, dn_number=row.dn_number,
            track_number=row.track_number, packed_at=row.packed_at, shipped_at=row.shipped_at,
            manager=row.manager, note=row.note, manager_id=row.manager_id, tk_note=row.tk_note or "",
            inherited=frozenset(filter(None, (row.inherited_fields or "").split(","))),
            boxes=tuple(ShipmentBox(
                place=box.place, kind=box.kind, dims=box.dims, net=box.net, gross=box.gross, seal=box.seal,
                items=tuple(BoxItem(item["item_index"], Decimal(item["quantity"])) for item in json.loads(box.composition or "[]")),
            ) for box in self.session.scalars(select(ShipmentBoxRow).where(ShipmentBoxRow.shipment_id == row.id).order_by(ShipmentBoxRow.place))),
            lines=tuple(ShipmentLine(
                id=line.id, finished_good_id=line.finished_good_id, project=line.project,
                item_index=line.item_index, article=line.article, name=line.name,
                requested=line.requested, picked=line.picked, unit=line.unit, grn=line.grn,
            ) for line in lines),
        )
    def get(self, shipment_id: UUID) -> Shipment | None:
        row = self.session.get(ShipmentRow, shipment_id)
        return self._domain(row) if row is not None else None
    def list(self, limit: int = 200) -> list[Shipment]:
        rows = self.session.scalars(select(ShipmentRow).order_by(ShipmentRow.created_at.desc(), ShipmentRow.id.desc()).limit(limit))
        return [self._domain(row) for row in rows]
    def for_project(self, project: str) -> list[Shipment]:
        """Отправки, где есть позиция проекта (точно по `normalize_project_number`, по индексу ключа позиций)."""
        key = normalize_project_number(project)
        if not key:
            return []
        ids = select(ShipmentLineRow.shipment_id).where(
            or_(ShipmentLineRow.project_key == key, ShipmentLineRow.project_key.is_(None)),
        )
        rows = self.session.scalars(
            select(ShipmentRow).where(ShipmentRow.id.in_(ids)).order_by(ShipmentRow.created_at, ShipmentRow.id)
        )
        return [
            shipment for shipment in map(self._domain, rows)
            if any(normalize_project_number(line.project) == key for line in shipment.lines)
        ]
    def open_for_project(self, project: str) -> list[Shipment]:
        """Незакрытые отправки проекта (проект отправки — по первой позиции, `shipHeadEff_`)."""
        ids = select(ShipmentLineRow.shipment_id).where(ShipmentLineRow.project == project)
        rows = self.session.scalars(select(ShipmentRow).where(
            ShipmentRow.id.in_(ids), ShipmentRow.status.not_in((ShipmentStatus.CLOSED.value, ShipmentStatus.CANCELLED.value)),
        ).order_by(ShipmentRow.created_at, ShipmentRow.id))
        return [shipment for shipment in map(self._domain, rows) if shipment.lines and shipment.lines[0].project == project]
    def created_details(self, shipment_id: UUID) -> dict | None:
        """Подробности события «shipping.created» отправки: в них ключ повтора и отпечаток тела «Отгрузить»."""
        details = self.session.scalar(select(AuditEventRow.details).where(
            AuditEventRow.aggregate_id == shipment_id, AuditEventRow.event_type == "shipping.created",
        ).order_by(AuditEventRow.occurred_at).limit(1))
        return dict(details) if isinstance(details, dict) else None
    def reserve_number(self, day: date) -> str:
        stamp = day.strftime("%Y%m%d"); key = f"shipment-{stamp}"
        row = self.session.scalar(select(ShippingSequenceRow).where(ShippingSequenceRow.name == key).with_for_update())
        if row is None:
            row = ShippingSequenceRow(name=key, value=0); self.session.add(row); self.session.flush()
        row.value += 1
        return f"ОТГ-{stamp}-{row.value:02d}"
    def document_sequence(self, project: str) -> int:
        key = "document:" + sha256(project.encode("utf-8")).hexdigest()[:48]
        row = self.session.scalar(select(ShippingSequenceRow).where(ShippingSequenceRow.name == key).with_for_update())
        if row is None:
            row = ShippingSequenceRow(name=key, value=0); self.session.add(row); self.session.flush()
        row.value += 1
        return row.value
    def add(self, shipment: Shipment) -> None:
        row = self.session.get(ShipmentRow, shipment.id)
        if row is None:
            row = ShipmentRow(id=shipment.id); self.session.add(row)
        for key in (
            "number", "version", "created_at", "customer", "receiver", "shipping_point",
            "contact", "phone", "carrier", "deadline", "upd_number", "upd_date", "upd_url",
            "pl_number", "dn_number", "track_number", "packed_at", "shipped_at", "manager", "note",
            "manager_id", "tk_note",
        ):
            setattr(row, key, getattr(shipment, key))
        row.status = shipment.status.value; row.terms = shipment.terms.value if shipment.terms else None
        row.upd_type = shipment.upd_type.value if shipment.upd_type else None
        row.inherited_fields = ",".join(sorted(shipment.inherited))
        existing = {line.id: line for line in self.session.scalars(select(ShipmentLineRow).where(ShipmentLineRow.shipment_id == shipment.id))}
        for position, line in enumerate(shipment.lines):
            stored = existing.pop(line.id, None)
            if stored is None:
                stored = ShipmentLineRow(id=line.id, shipment_id=shipment.id); self.session.add(stored)
            stored.position = position
            for key in ("finished_good_id", "project", "item_index", "article", "name", "requested", "picked", "unit", "grn"):
                setattr(stored, key, getattr(line, key))
        boxes = {box.place: box for box in self.session.scalars(select(ShipmentBoxRow).where(ShipmentBoxRow.shipment_id == shipment.id))}
        for box in shipment.boxes:
            stored_box = boxes.pop(box.place, None)
            if stored_box is None:
                stored_box = ShipmentBoxRow(shipment_id=shipment.id, place=box.place); self.session.add(stored_box)
            stored_box.kind = box.kind; stored_box.dims = box.dims; stored_box.net = box.net
            stored_box.gross = box.gross; stored_box.seal = box.seal
            stored_box.composition = json.dumps(
                [{"item_index": item.item_index, "quantity": format(item.quantity, "f")} for item in box.items], ensure_ascii=False,
            )
        for stale in boxes.values():
            self.session.delete(stale)


class SqlAlchemyProjectAddresseeRepository:
    def __init__(self, session: Session): self.session = session
    def get(self, project: str) -> ProjectAddressee | None:
        row = self.session.get(ProjectAddresseeRow, project)
        if row is None:
            return None
        return ProjectAddressee(
            project=row.project, version=row.version, receiver=row.receiver,
            shipping_point=row.shipping_point, contact=row.contact, phone=row.phone,
            terms=DeliveryTerms(row.terms) if row.terms else None, carrier=row.carrier,
            upd_type=UpdType(row.upd_type) if row.upd_type else None,
            updated_by=row.updated_by, updated_at=row.updated_at,
            **{key: getattr(row, key) or "" for key in ADDRESSEE_REQUISITE_FIELDS},
        )
    def list(self) -> list[ProjectAddressee]:
        return [self.get(project) for project in self.session.scalars(select(ProjectAddresseeRow.project).order_by(ProjectAddresseeRow.project))]
    def add(self, addressee: ProjectAddressee) -> None:
        row = self.session.get(ProjectAddresseeRow, addressee.project)
        if row is None:
            row = ProjectAddresseeRow(project=addressee.project); self.session.add(row)
        for key in ("version", "receiver", "shipping_point", "contact", "phone", "carrier", "updated_by", "updated_at", *ADDRESSEE_REQUISITE_FIELDS):
            setattr(row, key, getattr(addressee, key))
        row.terms = addressee.terms.value if addressee.terms else None
        row.upd_type = addressee.upd_type.value if addressee.upd_type else None


class ShippingFileRow(Base):
    """Файл УПД отправки (`shipUpdAttach_` клал его в папку Drive «Отгрузочные документы/УПД»)."""

    __tablename__ = "shipping_files"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    shipment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SqlAlchemyUpdFileRepository:
    def __init__(self, session: Session): self.session = session
    def add(self, stored: StoredFile, content: bytes) -> None:
        self.session.add(ShippingFileRow(
            id=stored.id, shipment_id=stored.shipment_id, file_name=stored.file_name, content_type=stored.content_type,
            size=stored.size, content=content, uploaded_by=stored.uploaded_by, uploaded_at=stored.uploaded_at,
        ))
    def get(self, file_id: UUID) -> tuple[StoredFile, bytes] | None:
        row = self.session.get(ShippingFileRow, file_id)
        if row is None:
            return None
        return StoredFile(
            id=row.id, shipment_id=row.shipment_id, file_name=row.file_name, content_type=row.content_type,
            size=row.size, uploaded_by=row.uploaded_by, uploaded_at=row.uploaded_at,
        ), row.content


class SqlAlchemyShippingUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory; self.session: Session | None = None; self.committed = False
    def __enter__(self):
        self.committed = False; self.session = self.session_factory()
        self.goods = SqlAlchemyFinishedGoodsRepository(self.session)
        self.shipments = SqlAlchemyShipmentRepository(self.session)
        self.addressees = SqlAlchemyProjectAddresseeRepository(self.session)
        self.files = SqlAlchemyUpdFileRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        self.outbox = SqlAlchemyOutboxRepository(self.session)
        return self
    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None: return
        if exc_type is not None or not self.committed: self.session.rollback()
        self.session.close()
    def commit(self) -> None:
        if self.session is None: raise RuntimeError("Unit of Work has not been entered")
        self.session.commit(); self.committed = True
