"""Persistent project techcards and electronic-techcard revisions."""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class TechcardValidationError(ValueError):
    pass


class TechcardStatus(str, Enum):
    DRAFT = "Черновик"
    ISSUED = "Выдана"


def _positive_number(value: object) -> bool:
    try:
        return float(str(value or "0").replace(",", ".")) > 0
    except ValueError:
        return False


# «Своя операция» ручной сборки (etk_builder): не из библиотеки, кода карты у неё нет — ключ держит её номер в списке.
CUSTOM_OP = "customOp"


def operation_key(operation: dict[str, Any]) -> str:
    code = str(operation.get("code") or "").strip()
    connector = str(operation.get("connector") or "").strip()
    side = str(operation.get("side") or "").strip()
    if not code and not (operation.get("type") == CUSTOM_OP and connector):
        raise TechcardValidationError("У операции ЭТК отсутствует код")
    if operation.get("operation_key"):
        key = str(operation["operation_key"])
        if not operation.get("opKey") or key != f"{code}::op:{operation['opKey']}::{side}":
            raise TechcardValidationError("Смысловой ключ операции не соответствует её коду и стороне")
        return key
    return f"{code}::{connector}::{side}"


def operation_aliases(previous: Mapping[str, Any] | None, document: Mapping[str, Any]) -> dict[str, str]:
    """Map legacy keys using the saved, aligned recipe, never the reordered positions or code alone."""
    previous = previous or {}
    old_ops = previous.get("operations") or []
    config = previous.get("build_config") or {}
    raw_ops = config.get("ops") or []
    targets = {operation_key(op) for op in document.get("operations") or []}
    candidates: dict[str, set[str]] = {}
    for index, old in enumerate(old_ops):
        key = operation_key(old)
        raw = raw_ops[index] if len(old_ops) == len(raw_ops) else None
        # A legacy positional key can now name another operation of the same material.
        # When the saved recipe carries semantic identity, it takes precedence even
        # if the old positional key happens to exist in the rebuilt document.
        semantic = (not old.get("operation_key") and config.get("mode") != "manual"
                    and isinstance(raw, Mapping) and raw.get("opKey"))
        if semantic:
            target = None
            if (old.get("position") == index + 1 and old.get("type") == raw.get("type")
                    and str(old.get("side") or "") == str(raw.get("side") or "")
                    and (not raw.get("templateId") or str(old.get("template_id") or "") == str(raw["templateId"]))):
                stable = f"{str(old.get('code') or '').strip()}::op:{raw['opKey']}::{str(old.get('side') or '').strip()}"
                if stable in targets:
                    target = stable
            if target is None and key in targets:
                raise TechcardValidationError("Смысловая операция не сопоставлена: позиционный ключ занят другой операцией")
        else:
            target = key if key in targets else None
        if target is not None:
            candidates.setdefault(key, set()).add(target)
    sources: dict[str, set[str]] = {}
    for key, values in candidates.items():
        for target in values:
            sources.setdefault(target, set()).add(key)
    if any(len(values) > 1 for values in candidates.values()) or any(len(keys) > 1 for keys in sources.values()):
        raise TechcardValidationError("Несколько операций имеют одну смысловую идентичность — требуется проверка технолога")
    mapped = {key: next(iter(values)) for key, values in candidates.items()
              if len(values) == 1}
    for alias, old_target in (previous.get("operation_key_aliases") or {}).items():
        target = mapped.get(old_target, old_target if old_target in targets else None)
        if target is not None and (alias not in mapped or mapped[alias] == target):
            mapped[alias] = target
    return {key: target for key, target in mapped.items() if key != target}


def resolved_operation_key(key: str, document: Mapping[str, Any]) -> str | None:
    target = (document.get("operation_key_aliases") or {}).get(key, key)
    return target if target in {operation_key(op) for op in document.get("operations") or []} else None


def with_operation_identity(edited: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(edited))
    for field in ("position", "code", "connector", "side", "operation_key", "opKey"):
        if field in base:
            result[field] = base[field]
        else:
            result.pop(field, None)
    return result


def document_sketches(sketches: Sequence[OperationSketch], current: Mapping[str, Any] | None,
                     target: Mapping[str, Any] | None = None) -> list[OperationSketch]:
    """Present live sketch attachments under the keys of the displayed (possibly pinned) revision."""
    current = current or {}
    target = target if target is not None else current
    aliases = current.get("operation_key_aliases") or {}
    keys = {aliases.get(operation_key(op), operation_key(op)): operation_key(op)
            for op in target.get("operations") or []}
    return [replace(sketch, operation_key=keys[aliases.get(sketch.operation_key, sketch.operation_key)])
            for sketch in sketches if aliases.get(sketch.operation_key, sketch.operation_key) in keys]


def migrated_overrides(overrides: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    """Persist the one-way key migration after a conflict-free derivation."""
    result = deepcopy(dict(overrides))
    operations = {operation_key(op): op for op in document.get("operations") or []}
    if isinstance(result.get("operations"), dict):
        edits = {}
        for key, edit in result["operations"].items():
            target = resolved_operation_key(key, document)
            if target is None:
                raise TechcardValidationError("Правка операции не сопоставлена с новой ЭТК")
            if target in edits:
                raise TechcardValidationError("Несколько правок сопоставлены с одной операцией ЭТК")
            if isinstance(edit, dict) and isinstance(edit.get("operation"), Mapping):
                edit["operation"] = with_operation_identity(edit["operation"], operations[target])
            edits[target] = edit
        result["operations"] = edits
    route = result.get("route")
    if isinstance(route, dict):
        for block in route.get("blocks") or []:
            mapped = [resolved_operation_key(key, document) for key in block.get("ops") or []]
            if None in mapped:
                raise TechcardValidationError("Операция сохранённой схемы ПФ не сопоставлена с новой ЭТК")
            block["ops"] = mapped
    return result

def _document(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TechcardValidationError("ЭТК должна быть JSON-объектом")
    operations = value.get("operations")
    if not isinstance(operations, list) or not operations:
        raise TechcardValidationError("В ЭТК должна быть хотя бы одна операция")
    keys = [operation_key(item) for item in operations if isinstance(item, dict)]
    if len(keys) != len(operations) or len(set(keys)) != len(keys):
        raise TechcardValidationError("Операции ЭТК должны иметь уникальные код и сторону")
    return deepcopy(value)


@dataclass(frozen=True)
class ProjectTechcard:
    id: UUID
    version: int
    project: str
    item_index: str
    product_name: str
    source_calculation: str
    deadline: date | None
    status: TechcardStatus
    revision: int
    document: dict[str, Any] | None
    manual_operation_keys: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    issued_at: datetime | None = None
    # Входы и правки для автоматической ревизии: конфиг генератора как пришёл (до норм справочников), правки технолога.
    inputs: dict[str, Any] | None = None
    overrides: dict[str, Any] | None = None
    review_note: str | None = None
    derived_hash: str | None = None

    @classmethod
    def create(
        cls, *, project: str, item_index: str, product_name: str,
        source_calculation: str, deadline: date | None, now: datetime,
    ) -> ProjectTechcard:
        values = [str(value or "").strip() for value in (project, item_index, product_name)]
        if not all(values):
            raise TechcardValidationError("Проект, индекс изделия и наименование обязательны")
        return cls(
            id=uuid4(), version=1, project=values[0], item_index=values[1],
            product_name=values[2], source_calculation=str(source_calculation or "").strip(),
            deadline=deadline, status=TechcardStatus.DRAFT, revision=0,
            document=None, manual_operation_keys=(), created_at=now, updated_at=now,
        )

    def rebuild(self, document: dict[str, Any], now: datetime) -> ProjectTechcard:
        rebuilt = _document(document)
        aliases = operation_aliases(self.document, rebuilt)
        if aliases:
            rebuilt["operation_key_aliases"] = aliases
        manual_keys = tuple(resolved_operation_key(key, rebuilt) for key in self.manual_operation_keys)
        if None in manual_keys:
            raise TechcardValidationError("Правка технолога не сопоставлена с операцией — проверьте ЭТК перед пересборкой")
        if self.document and self.manual_operation_keys:
            old_by_key = {resolved_operation_key(operation_key(item), rebuilt): item
                          for item in self.document["operations"]}
            rebuilt["operations"] = [
                with_operation_identity(old_by_key[operation_key(item)], item)
                if operation_key(item) in manual_keys else item
                for item in rebuilt["operations"]
            ]
        revision = self.revision + 1 if self.status is TechcardStatus.ISSUED else self.revision
        return replace(
            self, document=rebuilt, revision=revision, manual_operation_keys=manual_keys,
            overrides=migrated_overrides(self.overrides, rebuilt) if self.overrides else self.overrides,
            version=self.version + 1, updated_at=now,
        )

    def discard_draft(self, now: datetime) -> ProjectTechcard:
        """«Удалить созданные» (SUPPLYMAP deleteGenerated): собранный черновик убирается, выданная ЭТК — нет."""
        if self.status is TechcardStatus.ISSUED:
            raise TechcardValidationError("Выданную ЭТК удалить нельзя — пересоберите её новой ревизией")
        if self.document is None:
            raise TechcardValidationError("Черновик ЭТК ещё не собран")
        return replace(
            self, document=None, manual_operation_keys=(), inputs=None, overrides=None, review_note=None,
            derived_hash=None,
            version=self.version + 1, updated_at=now,
        )

    def issue(self, now: datetime) -> ProjectTechcard:
        if self.document is None:
            raise TechcardValidationError("Сначала соберите электронную техкарту")
        if self.status is TechcardStatus.ISSUED:
            raise TechcardValidationError("Электронная техкарта уже выдана")
        config = self.document.get("build_config") if isinstance(self.document, dict) else None
        operations = config.get("ops") if isinstance(config, dict) else []
        if any(str(item.get("type") or "").startswith("cutWire") for item in operations or []):
            missing = [
                str(item.get("art") or item.get("name") or "—")
                for item in config.get("wires") or []
                if not _positive_number(item.get("length"))
            ]
            if missing:
                raise TechcardValidationError(
                    f"Не указана длина резки: {', '.join(missing)} — укажите длину по КД."
                )
        return replace(
            self, status=TechcardStatus.ISSUED, revision=1,
            issued_at=now, updated_at=now, version=self.version + 1,
        )

    def edit_operation(
        self, key: str, operation: dict[str, Any], now: datetime, *, live: bool = False,
    ) -> ProjectTechcard:
        """A hand edit of one operation — a new revision. The operation is kept through rebuilds as edited
        (`manual_operation_keys`); `live` — the edit went into the template too, so the operation follows its template
        again (etk_live) and leaves the hand-edited ones."""
        if self.document is None:
            raise TechcardValidationError("Электронная техкарта ещё не собрана")
        if self.status is not TechcardStatus.ISSUED:
            # SUPPLYMAP portalEtkSaveOp: ЭТК-файл появляется только при выдаче, до неё «Электронной ТК нет».
            raise TechcardValidationError("Электронной ТК нет — правка операции возможна после выдачи ЭТК")
        updated_operation = deepcopy(operation)
        if operation_key(updated_operation) != key:
            raise TechcardValidationError("Код и сторона операции при правке не меняются")
        operations = list(self.document["operations"])
        indexes = [index for index, item in enumerate(operations) if operation_key(item) == key]
        if not indexes:
            raise TechcardValidationError("Операция ЭТК не найдена")
        operations[indexes[0]] = updated_operation
        document = deepcopy(self.document)
        document["operations"] = operations
        return replace(
            self, document=document, revision=self.revision + 1,
            manual_operation_keys=(
                tuple(item for item in self.manual_operation_keys if item != key) if live
                else tuple(dict.fromkeys((*self.manual_operation_keys, key)))
            ),
            version=self.version + 1, updated_at=now,
        )


@dataclass(frozen=True)
class TechcardRevision:
    id: UUID
    project_card_id: UUID
    revision: int
    reason: str
    actor_name: str
    occurred_at: datetime
    document: dict[str, Any]
    # manual — выпустил человек (выдача, правка, пересборка); auto — система после правки справочника/правила.
    source: str = "manual"
    diff: dict[str, Any] | None = None


@dataclass(frozen=True)
class OperationSketch:
    """The picture attached to one ETK operation (key = `operation_key`); the bytes travel separately."""

    operation_key: str
    file_name: str
    content_type: str
    size: int
    uploaded_by: str
    uploaded_at: datetime
