"""SQLAlchemy persistence for project kitsets («Наборы») and their КД attachments."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Index, Integer, LargeBinary, String, Text, Uuid, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

from .kitsets import KitAssembly, KitComponent, KitDocument, KitLot, Kitset, project_key


class KitsetRow(Base):
    __tablename__ = "techcard_kitsets"
    __table_args__ = (Index("ux_techcard_kitsets_project_key", "project_key", unique=True),)

    project: Mapped[str] = mapped_column(String(255), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    customer: Mapped[str] = mapped_column(String(512), nullable=False)
    calc_source: Mapped[str] = mapped_column(String(1024), nullable=False)
    assemblies: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class KitDocumentRow(Base):
    __tablename__ = "techcard_kitset_documents"
    __table_args__ = (Index("ix_techcard_kitset_documents_project", "project", "item_index"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    item_index: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)


def _component_json(component: KitComponent) -> dict[str, Any]:
    lot = component.lot
    return {
        "art": component.article, "type": component.type, "name": component.name, "link": component.link,
        "per": str(component.per_unit), "scrap": str(component.scrap_pct),
        "lot": None if lot is None else {
            "lot_id": str(lot.lot_id) if lot.lot_id else None,
            "warehouse": lot.warehouse, "grn": lot.grn, "package": lot.package,
        },
    }


def _component(value: dict[str, Any]) -> KitComponent:
    lot = value.get("lot")
    return KitComponent(
        article=value.get("art", ""), type=value.get("type", ""), name=value.get("name", ""),
        link=value.get("link", ""), per_unit=Decimal(value.get("per") or "0"),
        scrap_pct=Decimal(value.get("scrap") or "0"),
        lot=None if not lot else KitLot(
            lot_id=UUID(lot["lot_id"]) if lot.get("lot_id") else None,
            warehouse=lot.get("warehouse", ""), grn=lot.get("grn", ""), package=lot.get("package", ""),
        ),
    )


class SqlAlchemyKitsetRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: KitsetRow) -> Kitset:
        return Kitset(
            project=row.project, customer=row.customer, calc_source=row.calc_source,
            assemblies=tuple(
                KitAssembly(
                    index=item["index"], name=item.get("name", ""), run=Decimal(item.get("run") or "0"),
                    components=tuple(_component(component) for component in item.get("components") or []),
                )
                for item in row.assemblies
            ),
            updated_at=row.updated_at, updated_by=row.updated_by, created_at=row.created_at, version=row.version,
        )

    def get(self, project: str) -> Kitset | None:
        row = self.session.scalar(select(KitsetRow).where(KitsetRow.project_key == project_key(project)))
        return self._domain(row) if row is not None else None

    def list(self) -> list[Kitset]:
        return [self._domain(row) for row in self.session.scalars(select(KitsetRow).order_by(KitsetRow.project))]

    def save(self, kitset: Kitset) -> None:
        row = self.session.scalar(select(KitsetRow).where(KitsetRow.project_key == project_key(kitset.project)))
        if row is None:
            row = KitsetRow(project=kitset.project, project_key=project_key(kitset.project))
            self.session.add(row)
        row.version = kitset.version
        row.customer = kitset.customer
        row.calc_source = kitset.calc_source
        row.assemblies = [
            {
                "index": assembly.index, "name": assembly.name, "run": str(assembly.run),
                "components": [_component_json(component) for component in assembly.components],
            }
            for assembly in kitset.assemblies
        ]
        row.created_at = kitset.created_at
        row.updated_at = kitset.updated_at
        row.updated_by = kitset.updated_by

    def delete(self, project: str) -> int:
        result = self.session.execute(delete(KitsetRow).where(KitsetRow.project_key == project_key(project)))
        return result.rowcount or 0

    # ── КД ──

    @staticmethod
    def _document(row: KitDocumentRow) -> KitDocument:
        return KitDocument(
            id=row.id, project=row.project, item_index=row.item_index, file_name=row.file_name,
            content_type=row.content_type, size=row.size, uploaded_at=row.uploaded_at, uploaded_by=row.uploaded_by,
        )

    def documents(self, project: str) -> list[KitDocument]:
        rows = self.session.scalars(
            select(KitDocumentRow).where(KitDocumentRow.project == project).order_by(KitDocumentRow.uploaded_at)
        )
        return [self._document(row) for row in rows]

    def add_document(self, document: KitDocument, content: bytes) -> None:
        self.session.add(KitDocumentRow(
            id=document.id, project=document.project, item_index=document.item_index,
            file_name=document.file_name, content_type=document.content_type, size=document.size,
            content=content, uploaded_at=document.uploaded_at, uploaded_by=document.uploaded_by,
        ))

    def document_content(self, document_id: UUID) -> tuple[KitDocument, bytes] | None:
        row = self.session.get(KitDocumentRow, document_id)
        return None if row is None else (self._document(row), row.content)

    def delete_document(self, project: str, document_id: UUID) -> int:
        result = self.session.execute(
            delete(KitDocumentRow).where(KitDocumentRow.project == project, KitDocumentRow.id == document_id)
        )
        return result.rowcount or 0

    def delete_documents(self, project: str) -> int:
        result = self.session.execute(delete(KitDocumentRow).where(KitDocumentRow.project == project))
        return result.rowcount or 0
