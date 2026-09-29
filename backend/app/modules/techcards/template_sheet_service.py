"""Сценарии шаблонов-листов БД.ТК: каталог, черновик, правка, сохранение, отмена, удаление.

Порт SUPPLYMAP ErpTechmap.gs portalTcTplList/Draft/Save/DropDraft/Delete → TcTemplates.gs
tcTemplateCatalogForPortal, tcTemplateOpenDraft, tcTemplateNewDraft, tcTemplateSaveFromDraft,
tcTemplateDropDraft, deleteTemplate. Лист-черновик Google Sheets здесь — запись с сеткой значений.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .operation_cards import parse_operation_template
from .project_cards import TechcardValidationError
from .template_sheets import (
    DRAFT_PREFIX,
    TemplateDraft,
    TemplateDraftConflict,
    TemplateSheetNotFound,
    captured_block,
    draft_name,
    make_template_id,
    new_draft_title,
    normalize_grid,
    normalize_text,
    size_label,
)

LIBRARY_AGGREGATE = UUID("00000000-0000-0000-0000-000000000018")


def draft_result(draft: TemplateDraft, *, with_rows: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": str(draft.id),
        "draftSheet": draft.name,
        "existingTemplateId": draft.existing_template_id,
        "title": draft.title,
        "category": draft.category,
        "description": draft.description,
        "createdAt": draft.created_at.isoformat(),
        "createdBy": draft.created_by,
        "updatedAt": draft.updated_at.isoformat(),
    }
    if with_rows:
        result["rows"] = [list(row) for row in draft.rows]
        result["images"] = [dict(image) for image in draft.images]
    return result


def template_sheet_catalog(uow) -> dict[str, Any]:
    """tcTemplateCatalogForPortal: список шаблонов + открытые черновики."""
    with uow:
        rows = uow.operation_cards.sheets()
        drafts = uow.operation_cards.drafts()
        return {
            "list": [
                {
                    "id": row.id,
                    "title": row.title,
                    "category": row.category or "",
                    "description": row.description or "",
                    "size": size_label(row.raw_rows),
                    "updatedAt": (row.saved_at or row.imported_at).isoformat(),
                    "savedBy": row.saved_by or "",
                }
                for row in rows
            ],
            "drafts": [draft_result(draft) for draft in drafts],
        }


def open_template_draft(uow, *, template_id: str, title: str, actor: Actor, now: datetime) -> TemplateDraft:
    """templateId задан → черновик правки существующего (повторное «Редактировать» = свежий черновик);
    иначе новый пустой черновик с названием title (занятое название — ошибка)."""
    template_id = normalize_text(template_id)
    with uow:
        repo = uow.operation_cards
        if template_id:
            row = repo.sheet(template_id)
            if row is None:
                raise TemplateSheetNotFound(f'Шаблон "{template_id}" не найден.')
            name = draft_name(row.title)
            old = repo.draft_by_name(name)
            if old is not None:
                repo.delete_draft(old.id)
            draft = TemplateDraft(
                id=uuid4(), name=name, existing_template_id=row.id, title=row.title,
                category=row.category or "", description=row.description or "",
                rows=tuple(tuple(str(cell) for cell in item) for item in row.raw_rows),
                images=tuple(dict(image) for image in row.images or []),
                created_at=now, created_by=actor.name, updated_at=now,
            )
        else:
            clean = new_draft_title(title, now)
            name = draft_name(clean)
            if repo.draft_by_name(name) is not None:
                raise TemplateDraftConflict(f"Черновик «{clean}» уже открыт — заверши или удали его.")
            draft = TemplateDraft(
                id=uuid4(), name=name, existing_template_id="", title=clean, category="", description="",
                rows=(), images=(), created_at=now, created_by=actor.name, updated_at=now,
            )
        repo.put_draft(draft)
        uow.commit()
        return draft


def get_template_draft(uow, draft_id: UUID) -> TemplateDraft:
    with uow:
        draft = uow.operation_cards.draft(draft_id)
    if draft is None:
        raise TemplateSheetNotFound("Черновик не найден (уже сохранён или удалён?).")
    return draft


def edit_template_draft(uow, draft_id: UUID, *, rows: Sequence[Sequence[Any]], now: datetime) -> TemplateDraft:
    """Правка сетки черновика — то, что в SUPPLYMAP инженер делает в листе «Черновик — …»."""
    try:
        grid = normalize_grid(rows)
    except ValueError as error:
        raise TechcardValidationError(str(error)) from error
    with uow:
        draft = uow.operation_cards.draft(draft_id)
        if draft is None:
            raise TemplateSheetNotFound("Черновик не найден (уже сохранён или удалён?).")
        draft = replace(draft, rows=grid, updated_at=now)
        uow.operation_cards.put_draft(draft)
        uow.commit()
        return draft


def save_template_draft(
    uow, draft_id: UUID, *, title: str, category: str, description: str, actor: Actor, now: datetime,
) -> dict[str, Any]:
    """tcTemplateSaveFromDraft → saveSelectedRangeAsTemplateImpl_: блок черновика → каталог, черновик сносится.
    Существующий шаблон — тот, из которого открыт черновик (или, как в портале, с тем же названием)."""
    clean_title = normalize_text(title)
    if not clean_title:
        raise TechcardValidationError("Укажите название шаблона.")
    with uow:
        repo = uow.operation_cards
        draft = repo.draft(draft_id)
        if draft is None:
            raise TemplateSheetNotFound("Черновик не найден (уже сохранён или удалён?).")
        try:
            block = captured_block(draft.rows)
        except ValueError as error:
            raise TechcardValidationError(str(error)) from error
        catalog = repo.sheets()
        existing = next((row for row in catalog if draft.existing_template_id and row.id == draft.existing_template_id), None)
        if existing is None:
            existing = next((row for row in catalog if row.title == draft.title), None)
        template_id = existing.id if existing is not None else make_template_id(clean_title, (row.id for row in catalog))
        try:
            card = parse_operation_template(
                source_id=template_id, title=clean_title, category=normalize_text(category),
                rows=block, images=list(draft.images),
            )
        except ValueError as error:
            raise TechcardValidationError(str(error)) from error
        created = repo.save_sheet(card, description=normalize_text(description), saved_at=now, saved_by=actor.name)
        repo.delete_draft(draft.id)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=LIBRARY_AGGREGATE,
            event_type="techcards.template_sheet_saved", actor=actor, occurred_at=now,
            details={"id": template_id, "title": clean_title, "action": "created" if created else "updated",
                     "size": size_label(block)},
        ))
        uow.commit()
    return {
        "action": "created" if created else "updated",
        "id": template_id,
        "title": clean_title,
        "sizeLabel": size_label(block).replace("×", " x "),
    }


def drop_template_draft(uow, draft_id: UUID) -> dict[str, bool]:
    """tcTemplateDropDraft: отмена черновика без сохранения; нет такого — {dropped: false}."""
    with uow:
        draft = uow.operation_cards.draft(draft_id)
        dropped = draft is not None and draft.name.startswith(DRAFT_PREFIX) and uow.operation_cards.delete_draft(draft_id)
        uow.commit()
    return {"dropped": bool(dropped)}


def delete_template_sheet(uow, template_id: str, *, actor: Actor, now: datetime) -> dict[str, Any]:
    """deleteTemplateImpl_: шаблон уходит из каталога целиком."""
    template_id = normalize_text(template_id)
    if not template_id:
        raise TechcardValidationError("Не передан идентификатор шаблона.")
    with uow:
        row = uow.operation_cards.delete_sheet(template_id)
        if row is None:
            raise TemplateSheetNotFound(f"Шаблон «{template_id}» не найден.")
        title = row.title or template_id
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=LIBRARY_AGGREGATE,
            event_type="techcards.template_sheet_deleted", actor=actor, occurred_at=now,
            details={"id": template_id, "title": title},
        ))
        uow.commit()
    return {"deleted": True, "id": template_id, "title": title}
