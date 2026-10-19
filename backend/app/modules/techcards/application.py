"""Use cases for persistent project techcards."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime
import re
from typing import Any, Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .project_cards import ProjectTechcard, TechcardRevision, TechcardStatus, TechcardValidationError
from .operation_cards import (
    OperationCardTemplate,
    OperationRecordEdit,
    normalize_record_edit,
    parse_operation_library,
    parse_operation_template,
)
from .etk_builder import _route_ops, build_etk
from .etk_live import (
    VIEW_KEYS, fresh_card, load_templates, seen_versions, stamp_templates, template_for, template_record,
)
from .domain.route import RouteError, parse_route, route_json, route_totals
from .project_cards import operation_key
from .scheme_corrections import EVENT as SCHEME_CORRECTED, scheme_correction
from .template_sheets import make_template_id


class TechcardRepository(Protocol):
    def get(self, card_id: UUID) -> ProjectTechcard | None: ...
    def by_key(self, project: str, item_index: str) -> ProjectTechcard | None: ...
    def list(self) -> Sequence[ProjectTechcard]: ...
    def for_project(self, project: str) -> Sequence[ProjectTechcard]: ...
    def save(self, card: ProjectTechcard) -> None: ...
    def add_revision(self, revision: TechcardRevision) -> None: ...
    def revisions(self, card_id: UUID) -> Sequence[TechcardRevision]: ...


class OperationCardRepository(Protocol):
    def list(self) -> Sequence[OperationCardTemplate]: ...
    def get(self, source_id: str) -> OperationCardTemplate | None: ...
    def edited_records(self) -> list[str]: ...
    def replace(
        self, cards: tuple[OperationCardTemplate, ...], *, source_commit: str, imported_at: datetime,
    ) -> None: ...
    def edit(self, code: str, edit: OperationRecordEdit, *, changed_at: datetime, changed_by: str) -> int: ...
    def metadata(self) -> dict | None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class UnitOfWork(Protocol):
    techcards: TechcardRepository
    operation_cards: OperationCardRepository
    audit: AppendOnlyRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class CreateProjectTechcardCommand:
    project: str
    item_index: str
    product_name: str
    source_calculation: str
    deadline: date | None
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ChangeProjectTechcardCommand:
    card_id: UUID
    expected_version: int
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class RebuildProjectTechcardCommand(ChangeProjectTechcardCommand):
    document: dict[str, Any]
    # Конфиг генератора как пришёл (до норм справочников); None — входы карты остаются прежними.
    inputs: dict[str, Any] | None = None


@dataclass(frozen=True)
class AutoBuildProjectTechcardCommand(ChangeProjectTechcardCommand):
    config: dict[str, Any]
    technology_source: dict[str, Any] | None = None
    # The raw generator config as received, before `with_technology` fills in the reference norms — stored on the card
    # so it can be derived again; None — the card keeps the inputs it has.
    inputs: dict[str, Any] | None = None
    # «Поправить вручную»: the generator config the manual one was converted from — only to rebuild the automatic
    # scheme the correction is logged against (scheme_corrections.py), never used as a scheme.
    generator_origin: dict[str, Any] | None = None
    # Why the ETK is rebuilt — the revision journal line; a batch rebuild (workflows) names its own reason, and the
    # audit event carries it.
    reason: str = "Автопересборка"


@dataclass(frozen=True)
class EditTechcardOperationCommand(ChangeProjectTechcardCommand):
    operation_key: str
    operation: dict[str, Any]
    # «Сохранить также в шаблон»: the edited card goes into the operation's template too (every ETK with this
    # operation shows it), and the operation stays live — it follows its template, not a hand-edited copy.
    to_template: bool = False


@dataclass(frozen=True)
class SaveTechcardRouteCommand(ChangeProjectTechcardCommand):
    """«ПФ» of the ETK by hand: the block scheme (`document.route`) with blocks, their operations in the order of work
    and edges; totals are counted anew."""

    route: dict[str, Any]
    # The split came from the generator's rule (`pf_split`, the move of old ETKs onto ПФ) — stored as «generated»;
    # the revision says why it was made.
    generated: bool = False
    reason: str = "Правка ПФ"


@dataclass(frozen=True)
class ImportIssuedTechcardCommand:
    """A techcard issued outside ERP (SUPPLYMAP «Техкарты»): its route becomes the issued ETK of the product."""

    project: str
    item_index: str
    product_name: str
    source_calculation: str
    document: dict[str, Any]
    reason: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class EditOperationRecordCommand:
    source_id: str
    record: dict[str, Any]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ReplacedOperationLibrary:
    templates: int
    overwritten_edited: tuple[str, ...]


@dataclass(frozen=True)
class ReplaceOperationLibraryCommand:
    source_commit: str
    templates: tuple[dict[str, Any], ...]
    actor: Actor
    now: datetime
    force: bool = False


class OperationLibraryEditedError(TechcardValidationError):
    """SUPPLYMAP tcRecordsSync: records edited in the portal are not silently overwritten by a library reload."""


def _current(command: ChangeProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    card = uow.techcards.get(command.card_id)
    if card is None:
        raise KeyError(f"Project techcard not found: {command.card_id}")
    if card.version != command.expected_version:
        raise TechcardValidationError("Карточка проекта изменилась после загрузки формы")
    return card


def _revision(card: ProjectTechcard, reason: str, actor: Actor, now: datetime, source: str = "manual") -> TechcardRevision:
    if card.document is None or card.revision <= 0:
        raise TechcardValidationError("Нельзя записать пустую ревизию ЭТК")
    return TechcardRevision(
        id=uuid4(), project_card_id=card.id, revision=card.revision,
        reason=reason, actor_name=actor.name, occurred_at=now,
        document=card.document, source=source,
    )


def _with_inputs(card: ProjectTechcard, inputs: dict[str, Any] | None) -> ProjectTechcard:
    return replace(card, inputs=deepcopy(inputs)) if isinstance(inputs, dict) else card


def _with_operation_override(card: ProjectTechcard, key: str, value: dict[str, Any] | None) -> ProjectTechcard:
    """The technologist's hand edit of one operation kept beside the document (`overrides.operations[key]`); None
    takes it out."""
    overrides = dict(card.overrides or {})
    operations = {name: item for name, item in (overrides.get("operations") or {}).items() if name != key}
    if value is not None:
        operations[key] = deepcopy(value)
    if operations:
        overrides["operations"] = operations
    else:
        overrides.pop("operations", None)
    return replace(card, overrides=overrides or None)


def _with_route_override(card: ProjectTechcard, route: dict[str, Any]) -> ProjectTechcard:
    return replace(card, overrides={**(card.overrides or {}), "route": deepcopy(route)})


def _route_override(document: dict[str, Any]) -> dict[str, Any]:
    """The stored manual ПФ route in the override form: blocks name their operations by key (positions do not survive
    a rebuild), the edges stay as they are."""
    route = document.get("route") or {}
    keys = {str(item.get("position")): operation_key(item) for item in document.get("operations") or []}
    return {
        "source": "manual",
        "blocks": [{
            "key": block.get("key"), "name": block.get("name") or "", "final": bool(block.get("final")),
            **({"kit": True} if block.get("kit") else {}),
            "ops": [keys.get(str(position), str(position)) for position in block.get("ops") or []],
        } for block in route.get("blocks") or []],
        "edges": deepcopy(route.get("edges") or []),
    }


def create_project_techcard(command: CreateProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        if uow.techcards.by_key(command.project.strip(), command.item_index.strip()) is not None:
            raise TechcardValidationError("Карточка такого изделия проекта уже существует")
        card = ProjectTechcard.create(
            project=command.project, item_index=command.item_index,
            product_name=command.product_name, source_calculation=command.source_calculation,
            deadline=command.deadline, now=command.now,
        )
        uow.techcards.save(card)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=card.id, event_type="techcards.project_created",
            actor=command.actor, occurred_at=command.now,
            details={"project": card.project, "item_index": card.item_index},
        ))
        uow.commit()
        return card


def _hand_route(document: dict[str, Any], raw: object, generated: bool = False) -> dict[str, Any]:
    """`document.route` from a scheme made by hand: checked against the document's operations (RouteError explains
    what is wrong), operations keep the given order inside a block, totals come from the ETK time rows. `generated` —
    the split is the generator's rule, not a hand edit (source «generated»)."""
    ops, final_name, quantity = _route_ops(document, strict=False)
    if not ops:
        raise RouteError("нет операций")
    route = replace(parse_route(raw, ops, final_name, manual=True), generated=generated)
    return route_json(route, route_totals(route, {op.position: op.minutes for op in ops}, quantity))


def _operation_keys(document: dict[str, Any] | None) -> list[str] | None:
    try:
        return [operation_key(item) for item in (document or {}).get("operations") or []]
    except (AttributeError, TechcardValidationError):
        return None


def _keep_hand_route(previous: dict[str, Any] | None, document: dict[str, Any]) -> dict[str, Any]:
    """A rebuild keeps the ПФ the technologist cut by hand (`document.route`, source manual) while the operations are
    the same ones in the same places and the new build brings no hand-made scheme of its own (`build_config.route`).
    A split by the generator's rule (source generated — an old ETK moved onto ПФ) is kept the same way while the new
    build has no split of its own (derived); a new generated split replaces it. A scheme the generator drew in the
    config (`build_config.route` with source «generated», «Сложная сборка») is a generated one too: the hand-cut ПФ
    win over it."""
    old = (previous or {}).get("route")
    new = document.get("route") if isinstance(document, dict) else None
    old_source = old.get("source") if isinstance(old, dict) else None
    new_source = new.get("source") if isinstance(new, dict) else None
    if old_source not in ("manual", "generated") or new_source == "manual" or (old_source == "generated" and new_source == "generated"):
        return document
    keys = _operation_keys(previous)
    if not keys or keys != _operation_keys(document):
        return document
    try:
        return {**document, "route": _hand_route(document, old, generated=old_source == "generated")}
    except (RouteError, AttributeError, IndexError, KeyError, TypeError, ValueError):
        return document


def rebuild_project_techcard(command: RebuildProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        current = _current(command, uow)
        updated = _with_inputs(current.rebuild(_keep_hand_route(current.document, command.document), command.now), command.inputs)
        uow.techcards.save(updated)
        if updated.revision > current.revision:
            uow.techcards.add_revision(_revision(updated, "Пересборка", command.actor, command.now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=updated.id, event_type="techcards.project_rebuilt",
            actor=command.actor, occurred_at=command.now,
            details={"revision": updated.revision},
        ))
        uow.commit()
        return updated


def auto_build_project_techcard(command: AutoBuildProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        current = _current(command, uow)
        metadata = uow.operation_cards.metadata()
        if metadata is None:
            raise TechcardValidationError("Библиотека операционных карт не загружена")
        library = uow.operation_cards.list()
        try:
            document = stamp_templates(build_etk(
                command.config,
                library,
                operation_library_commit=metadata["source_commit"],
                technology_source=command.technology_source,
            ), library)
        except ValueError as error:
            raise TechcardValidationError(str(error)) from error
        updated = _with_inputs(current.rebuild(_keep_hand_route(current.document, document), command.now), command.inputs)
        uow.techcards.save(updated)
        if updated.revision > current.revision:
            uow.techcards.add_revision(_revision(updated, command.reason, command.actor, command.now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=updated.id, event_type="techcards.project_auto_built",
            actor=command.actor, occurred_at=command.now,
            details={
                "revision": updated.revision,
                "operations": len(document["operations"]),
                "warnings": len(document["warnings"]),
                "operation_library_commit": metadata["source_commit"],
                **({"reason": command.reason} if command.reason != "Автопересборка" else {}),
            },
        ))
        correction = scheme_correction(document, command.generator_origin, current.document)
        if correction is not None:
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type=SCHEME_CORRECTED,
                actor=command.actor, occurred_at=command.now,
                details={
                    "project": updated.project, "item_index": updated.item_index, "card_id": str(updated.id),
                    "revision": updated.revision, "operation_library_commit": metadata["source_commit"],
                    **correction,
                },
            ))
        uow.commit()
        return updated


def issue_project_techcard(command: ChangeProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        updated = _current(command, uow).issue(command.now)
        uow.techcards.save(updated)
        uow.techcards.add_revision(_revision(updated, "Первая выдача", command.actor, command.now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=updated.id, event_type="techcards.project_issued",
            actor=command.actor, occurred_at=command.now,
            details={"revision": updated.revision},
        ))
        uow.commit()
        return updated


def import_issued_techcard(command: ImportIssuedTechcardCommand, uow: UnitOfWork) -> ProjectTechcard | None:
    """Create or take the product's card and issue it with the imported document. A card already issued in ERP
    is left as is (None): ERP issues and edits are never overwritten by an import. A draft gives way."""
    with uow:
        current = uow.techcards.by_key(command.project.strip(), command.item_index.strip())
        if current is not None and current.status is TechcardStatus.ISSUED:
            return None
        card = current or ProjectTechcard.create(
            project=command.project, item_index=command.item_index, product_name=command.product_name,
            source_calculation=command.source_calculation, deadline=None, now=command.now,
        )
        issued = card.rebuild(command.document, command.now).issue(command.now)
        uow.techcards.save(issued)
        uow.techcards.add_revision(_revision(issued, command.reason, command.actor, command.now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=issued.id, event_type="techcards.project_imported",
            actor=command.actor, occurred_at=command.now,
            details={"project": issued.project, "item_index": issued.item_index, "reason": command.reason},
        ))
        uow.commit()
        return issued


def _edited_operation(operation: dict[str, Any], actor: Actor, now: datetime, *, stamp: bool = True) -> dict[str, Any]:
    """SUPPLYMAP TcBuild.gs etkApplyOpEdit_: the operation's card (steps, tooling, safety, defects) goes through the
    same normalization as a library record; the operation is stamped as edited (at, by) — not when the edit goes into
    the template too (`stamp` False: the operation stays live). The card name stays; the view-only keys of the read
    (`card_source`…) are not stored."""
    operation = {key: value for key, value in operation.items() if key not in VIEW_KEYS and (stamp or key != "edited")}
    card = operation.get("card")
    if not isinstance(card, dict):
        raise TechcardValidationError(
            f"У операции {operation.get('position') or operation.get('code') or ''} нет карточки — "
            "сначала заведи её в «Шаблонах техкарт»"
        )
    try:
        record = normalize_record_edit({**card, "name": str(card.get("name") or operation.get("code") or "")})
    except ValueError as error:
        raise TechcardValidationError(str(error)) from error
    return {
        **operation,
        "card": {
            **card,
            "safety": record.safety, "defects": record.defects,
            "steps": [{**step.__dict__, "tolerance": "" if step.tolerance == "-" else step.tolerance} for step in record.steps],
            "tooling": [tool.__dict__ for tool in record.tooling],
        },
        **({"edited": {"at": now.strftime("%d.%m.%Y %H:%M"), "by": actor.name}} if stamp else {}),
    }


def _edit_reason(operation: dict[str, Any]) -> str:
    """SUPPLYMAP etkApplyOpEdit_ log line: «Правка операции N (CODE — suffix)»."""
    suffix = str(operation.get("connector") or "").strip()
    position = str(operation.get("position") or "").strip()
    code = str(operation.get("code") or "").strip()
    return f"Правка операции {position + ' ' if position else ''}({code}{f' — {suffix}' if suffix else ''})"


def discard_project_techcard_draft(command: ChangeProjectTechcardCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        updated = _current(command, uow).discard_draft(command.now)
        uow.techcards.save(updated)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=updated.id, event_type="techcards.project_draft_discarded",
            actor=command.actor, occurred_at=command.now, details={},
        ))
        uow.commit()
        return updated


def _card_to_template(
    current: ProjectTechcard, command: EditTechcardOperationCommand, card: dict[str, Any], uow: UnitOfWork,
) -> dict[str, Any]:
    """«Сохранить также в шаблон»: the edited card written into the operation's template (one record per operation
    code — every (A)/(B) block of it, as «Шаблоны техкарт» saves it), in the same unit of work. What the technologist
    left as shown goes back with the template's placeholders (etk_live.template_record). Returns the ETK document
    stamped with the template versions its cards were filled from before the edit, so every operation of this
    template — this one included — is filled anew from the edited template on read."""
    document = current.document or {}
    stored = next(item for item in document.get("operations") or [] if operation_key(item) == command.operation_key)
    templates = load_templates(uow.operation_cards, [document])
    template = template_for(stored, {item.source_id: item for item in templates}, templates)
    if template is None:
        raise TechcardValidationError("У операции нет шаблона в библиотеке — сохранить правку в шаблон нельзя")
    # What the template gives this operation now: what the technologist left of it goes back with the placeholders.
    filled = fresh_card(document, command.operation_key, templates)
    try:
        edit = normalize_record_edit(template_record(template, filled, card))
    except ValueError as error:
        raise TechcardValidationError(str(error)) from error
    uow.operation_cards.edit(template.code, edit, changed_at=command.now, changed_by=command.actor.name)
    uow.audit.add(AuditEvent(
        id=uuid4(),
        aggregate_id=UUID("00000000-0000-0000-0000-000000000018"),
        event_type="techcards.operation_record_edited",
        actor=command.actor,
        occurred_at=command.now,
        details={"code": template.code, "steps": str(len(edit.steps)), "tooling": str(len(edit.tooling)),
                 "from_etk": f"{current.project} {current.item_index}"},
    ))
    source = document.get("source") if isinstance(document.get("source"), dict) else {}
    return {**document, "source": {**source, "templates": seen_versions(document, templates, current.updated_at)}}


def edit_project_operation(command: EditTechcardOperationCommand, uow: UnitOfWork) -> ProjectTechcard:
    with uow:
        current = _current(command, uow)
        operation = _edited_operation(command.operation, command.actor, command.now, stamp=not command.to_template)
        updated = current.edit_operation(command.operation_key, operation, command.now, live=command.to_template)
        stored = next((item for item in (current.document or {}).get("operations") or []
                       if operation_key(item) == command.operation_key), {})
        # The edit rides along with the inputs; an edit that went into the template too follows the template again.
        updated = _with_operation_override(updated, command.operation_key, None if command.to_template else {
            "operation": operation,
            "base_template_version": (((current.document or {}).get("source") or {}).get("templates") or {}).get(
                str(stored.get("template_id") or "").strip(), ""),
        })
        reason = _edit_reason(operation)
        if command.to_template:
            stamped = _card_to_template(current, command, operation["card"], uow)
            updated = replace(updated, document={**updated.document, "source": stamped["source"]})
            reason = f"{reason} — и в шаблон"
        uow.techcards.save(updated)
        if updated.revision > current.revision:
            uow.techcards.add_revision(_revision(updated, reason, command.actor, command.now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=updated.id, event_type="techcards.operation_edited",
            actor=command.actor, occurred_at=command.now,
            details={"operation_key": command.operation_key, "revision": updated.revision,
                     **({"to_template": True} if command.to_template else {})},
        ))
        uow.commit()
        return updated


def save_project_route(command: SaveTechcardRouteCommand, uow: UnitOfWork) -> ProjectTechcard:
    """«Сохранить ПФ»: the ETK's operations grouped into semi-finished products by hand. An issued ETK gets a new
    revision (as an operation edit does); a draft just keeps the scheme. The operations and `build_config` stay."""
    with uow:
        current = _current(command, uow)
        if current.document is None:
            raise TechcardValidationError("Электронная техкарта ещё не собрана")
        try:
            route = _hand_route(current.document, command.route, generated=command.generated)
        except RouteError as error:
            raise TechcardValidationError(str(error)) from error
        document = deepcopy(current.document)
        document["route"] = route
        issued = current.status is TechcardStatus.ISSUED
        updated = replace(
            current, document=document, revision=current.revision + 1 if issued else current.revision,
            version=current.version + 1, updated_at=command.now,
        )
        updated = _with_route_override(updated, _route_override(document))
        uow.techcards.save(updated)
        if issued:
            uow.techcards.add_revision(_revision(updated, command.reason, command.actor, command.now))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=updated.id, event_type="techcards.route_edited",
            actor=command.actor, occurred_at=command.now,
            details={"revision": updated.revision, "blocks": len(route["blocks"]), "source": route["source"]},
        ))
        uow.commit()
        return updated


def edit_operation_record(command: EditOperationRecordCommand, uow: UnitOfWork) -> OperationCardTemplate:
    """SUPPLYMAP TcRecordDb.gs tcRecordSave_: the record of one operation code is written whole; a new operation
    cannot be started this way — only an existing record is edited. Stamps who/when edited."""
    try:
        edit = normalize_record_edit(command.record)
    except ValueError as error:
        raise TechcardValidationError(str(error)) from error
    with uow:
        current = uow.operation_cards.get(command.source_id)
        if current is None:
            raise KeyError(f"Operation card not found: {command.source_id}")
        uow.operation_cards.edit(current.code, edit, changed_at=command.now, changed_by=command.actor.name)
        uow.audit.add(AuditEvent(
            id=uuid4(),
            aggregate_id=UUID("00000000-0000-0000-0000-000000000018"),
            event_type="techcards.operation_record_edited",
            actor=command.actor,
            occurred_at=command.now,
            details={"code": current.code, "steps": str(len(edit.steps)), "tooling": str(len(edit.tooling))},
        ))
        uow.commit()
        return current.with_edit(edit, changed_at=command.now, changed_by=command.actor.name)


# ── Новая операция: карточка-шаблон (БД.ТК) под новый код; строку БД.ОП пишет app/workflows/new_operation.py ──

MAX_OPERATION_CODE = 64
MAX_OPERATION_NAME = 200
_OPERATION_CODE = re.compile(r"^[^\s|]+$")
# Пустая карточка: шапка листа, как у шаблонов ERP, и один шаг-подсказка — без шага шаблон не читается.
BLANK_STEP = "‹опишите операцию›"
_BLANK_HEAD = (
    ("ПРЕДПРИЯТИЕ", "", "ОБОЗНАЧЕНИЕ ДОКУМЕНТА", "", "", "РЕВИЗИЯ", "РМ"),
    ("ООО «ЗЭП «ФРАКСИС»", "", "", "", "", "‹рев.›"),
    ("НАИМЕНОВАНИЕ ИЗДЕЛИЯ (СБОРКИ ПО КД)", "", "", "", "  КОД ИЗДЕЛИЯ", "НОМЕР ПРОЕКТА"),
    ("‹наименование изделия по КД›",),
    ("Обозначение", "", "Содержание операции"),
    ("", "№ ", "Наименование ", "", "", "", "№ Инв."),
    ("", "№ ", "ОПИСАНИЕ ОПЕРАЦИИ, ПРИМЕНЯЕМЫХ МАТЕРИАЛОВ И ОСНАСТКИ", "", "", "", "Допуск"),
    ("Операция", "1", BLANK_STEP, "", "", "", "-"),
)


@dataclass(frozen=True)
class NewOperation:
    code: str
    name: str
    copy_of: str = ""
    category: str = ""


def new_operation(code: object, name: object, copy_of: object = "", category: object = "") -> NewOperation:
    """The request cleaned and checked: the code becomes the title «КОД | Название» of the card, so it has no spaces
    and no «|»."""
    clean_code, clean_name = str(code or "").strip(), " ".join(str(name or "").split())
    if not clean_code:
        raise TechcardValidationError("Укажите номер операции")
    if len(clean_code) > MAX_OPERATION_CODE or not _OPERATION_CODE.match(clean_code):
        raise TechcardValidationError(
            f"Номер операции — до {MAX_OPERATION_CODE} знаков без пробелов и «|», например CUT_WIRE_auto")
    if not clean_name:
        raise TechcardValidationError("Укажите название операции")
    if len(clean_name) > MAX_OPERATION_NAME:
        raise TechcardValidationError(f"Название операции — до {MAX_OPERATION_NAME} знаков")
    return NewOperation(clean_code, clean_name, str(copy_of or "").strip(), " ".join(str(category or "").split()))


def operation_template_codes(uow: UnitOfWork) -> set[str]:
    return {row.code for row in uow.operation_cards.sheets()}


def create_operation_templates_in(
    operation: NewOperation, actor: Actor, now: datetime, uow: UnitOfWork,
) -> list[OperationCardTemplate]:
    """The card template(s) of a new operation inside an open unit of work (nothing committed). Blank — the sheet head
    and one hint step; a copy — every block (A)/(B) of `copy_of` with its steps, tooling, defects, safety and pictures,
    an ERP edit of the record included. Saved as an ERP sheet (`saved_at`): a SUPPLYMAP library reload keeps it."""
    repo = uow.operation_cards
    catalog = repo.sheets()
    taken = operation.code.casefold()
    if any(row.code.casefold() == taken for row in catalog):
        raise TechcardValidationError(
            f"Операция с номером «{operation.code}» уже есть — номер операции должен быть уникальным")
    if operation.copy_of:
        sources = sorted((row for row in catalog if row.code == operation.copy_of), key=lambda row: (row.side, row.id))
        if not sources:
            raise TechcardValidationError(f"Операции «{operation.copy_of}» для копии нет среди карточек операций")
    else:
        sources = []
    ids = {row.id for row in catalog}
    created: list[OperationCardTemplate] = []
    edit: OperationRecordEdit | None = None
    for source in sources or [None]:
        title = f"{operation.code} | {operation.name}" + (f" ({source.side})" if source is not None and source.side else "")
        if source is None:
            rows: list[list[str]] = [[title], *(list(row) for row in _BLANK_HEAD)]
            images, category = None, operation.category
        else:
            rows = [list(row) for row in source.raw_rows] or [[]]
            rows[0] = [title, *rows[0][1:]]
            images, category = source.images, operation.category or source.category
            current = repo.get(source.id)
            if current is not None and current.changed_at is not None:
                edit = OperationRecordEdit(
                    name=operation.name, safety=current.safety, defects=current.defects, revision=current.revision,
                    steps=current.steps, tooling=current.tooling,
                )
        template_id = make_template_id(title, ids)
        ids.add(template_id)
        try:
            card = parse_operation_template(source_id=template_id, title=title, category=category, rows=rows,
                                            images=images)
        except ValueError as error:
            raise TechcardValidationError(str(error)) from error
        if card.code != operation.code:
            raise TechcardValidationError(f"Номер «{operation.code}» занят служебной карточкой — выберите другой")
        description = f"Новая операция, копия {operation.copy_of}" if source is not None else "Новая операция"
        repo.save_sheet(card, description=description, saved_at=now, saved_by=actor.name)
        created.append(card)
    if edit is not None:
        # Одна запись на код: правка исходной записи ложится на все блоки копии (как tcRecordsByCode_).
        repo.edit(operation.code, edit, changed_at=now, changed_by=actor.name)
        created = [card.with_edit(edit, changed_at=now, changed_by=actor.name) for card in created]
    return created


# Кто «сохранил» шаблон поставки ERP: по этой подписи сид отличает свой шаблон от пересохранённого человеком.
ERP_TEMPLATE_AUTHOR = "ERP (шаблоны поставки)"


@dataclass(frozen=True)
class SeedErpTemplatesCommand:
    templates: tuple[dict[str, Any], ...]
    actor: Actor
    now: datetime


def seed_erp_templates(command: SeedErpTemplatesCommand, uow: UnitOfWork) -> dict[str, list[str]]:
    """Шаблоны карт, которых нет в БД.ТК SUPPLYMAP (`backend/data/techcard-library-erp.json`), — в библиотеку как
    сохранённые в ERP: снимок SUPPLYMAP их не затирает. Нет шаблона — создаётся; свой прежний — обновляется, если
    поставка изменилась; пересохранённый человеком или пришедший из SUPPLYMAP с тем же id — не трогается."""
    result: dict[str, list[str]] = {"created": [], "updated": [], "kept": []}
    cards = []
    for item in command.templates:
        try:
            cards.append(parse_operation_template(
                source_id=str(item.get("id") or ""), title=str(item.get("title") or ""),
                category=str(item.get("category") or ""), rows=item.get("rows") or [], images=item.get("images"),
            ))
        except ValueError as error:
            raise TechcardValidationError(str(error)) from error
    with uow:
        repo = uow.operation_cards
        for card in cards:
            row = repo.sheet(card.source_id)
            same = row is not None and [list(item) for item in row.raw_rows or []] == [list(item) for item in card.raw_rows]
            if row is not None and (row.saved_by != ERP_TEMPLATE_AUTHOR or same):
                result["kept"].append(card.title)
                continue
            repo.save_sheet(card, description="Шаблон ERP (в БД.ТК SUPPLYMAP его нет)", saved_at=command.now,
                            saved_by=ERP_TEMPLATE_AUTHOR)
            result["updated" if row is not None else "created"].append(card.title)
        if result["created"] or result["updated"]:
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=UUID("00000000-0000-0000-0000-000000000018"),
                event_type="techcards.erp_templates_seeded", actor=command.actor, occurred_at=command.now,
                details={"created": "; ".join(result["created"]), "updated": "; ".join(result["updated"])},
            ))
        uow.commit()
    return result


def replace_operation_library(command: ReplaceOperationLibraryCommand, uow: UnitOfWork) -> ReplacedOperationLibrary:
    source_commit = str(command.source_commit or "").strip().lower()
    if len(source_commit) != 40 or any(character not in "0123456789abcdef" for character in source_commit):
        raise TechcardValidationError("Нужен полный commit SUPPLYMAP из 40 hex-символов")
    try:
        cards = parse_operation_library(command.templates)
    except ValueError as error:
        raise TechcardValidationError(str(error)) from error
    with uow:
        edited = uow.operation_cards.edited_records()
        if edited and not command.force:
            raise OperationLibraryEditedError(
                "Записи правлены в портале, пересборка из БД.ТК их затрёт: " + "; ".join(edited)
                + '. Затереть сознательно — повторить с force.'
            )
        uow.operation_cards.replace(cards, source_commit=source_commit, imported_at=command.now)
        uow.audit.add(AuditEvent(
            id=uuid4(),
            aggregate_id=UUID("00000000-0000-0000-0000-000000000018"),
            event_type="techcards.operation_library_replaced",
            actor=command.actor,
            occurred_at=command.now,
            details={
                "source_commit": source_commit, "templates": str(len(cards)),
                "overwritten_edited": "; ".join(edited),
            },
        ))
        uow.commit()
    return ReplacedOperationLibrary(templates=len(cards), overwritten_edited=tuple(edited))
