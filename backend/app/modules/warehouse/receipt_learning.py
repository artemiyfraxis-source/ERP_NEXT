"""Explicit supplier corrections. Invoice evidence stays immutable; rules are review suggestions."""

from __future__ import annotations

import json
from hashlib import sha256
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.shared.inn import OWN_INNS, inn_valid

from .domain import ReceiptConflict, WarehouseValidationError
from .infrastructure import ReceiptLineConfirmationRow, SupplierItemRuleRow, WarehouseReceiptRow
from .scan_jobs import ScanJobRow
from .upd_lines import number_text, quantity_of


def item_key(value: str) -> str:
    # Keep punctuation, colour, dimensions and packaging; never use the fuzzy article matcher here.
    return " ".join(str(value or "").split()).casefold()


def name_hash(value: str) -> str:
    """Bound the indexed name size while preserving the full normalized name key."""
    return sha256(item_key(value).encode("utf-8")).hexdigest()


def supplier_key(inn: str, name: str, confirm_name: bool = False) -> str:
    inn = str(inn or "").strip()
    if inn:
        if not inn_valid(inn) or inn in OWN_INNS:
            raise WarehouseValidationError("ИНН поставщика не прошёл проверку")
        return f"inn:{inn}"
    return f"name:{item_key(name)}" if confirm_name and item_key(name) else ""


def with_provenance(answer: dict) -> dict:
    """Stable within the stored file, even when printed row numbers repeat across pages."""
    file_id = answer["scan_file"].rsplit("/", 1)[-1]
    supplier = answer.get("source_supplier", answer["recognised"])
    rows = []
    for index, row in enumerate(answer["lines"], 1):
        original = dict(row["original"]) if "original" in row else {
            key: value for key, value in row.items() if key not in {"learning", "file_id", "source_line_key"}
        }
        original.setdefault("invoice_quantity", "")
        original.setdefault("invoice_unit", "")
        identity = json.dumps({"original": original, "supplier": supplier.get("supplier", ""),
                               "inn": supplier.get("inn", "")}, ensure_ascii=False, sort_keys=True)
        fingerprint = sha256(identity.encode("utf-8")).hexdigest()[:24]
        line_key = row.get("source_line_key") or f"line:{index}"
        if not line_key.endswith(f":{fingerprint}"):
            line_key = f"{line_key}:{fingerprint}"
        rows.append({**row, "file_id": file_id, "source_line_key": line_key, "original": original})
    return {**answer, "lines": rows}


def original_line(session, file_id: UUID, line_key: str) -> tuple[dict, dict]:
    """Read evidence from the stored answer, never from the browser's corrected row."""
    results = session.scalars(select(ScanJobRow.result).where(
        ScanJobRow.file_id == file_id, ScanJobRow.result != "",
    ).order_by(ScanJobRow.created_at, ScanJobRow.id))
    for result in results:
        answer = json.loads(result)
        for row in answer["lines"]:
            if row.get("source_line_key") == line_key and "original" in row:
                return row["original"], answer.get("source_supplier", answer["recognised"])
    raise WarehouseValidationError("Исходная строка УПД не найдена — распознайте файл заново")


def _find(session, scope: str, original: dict, *, lock: bool = False):
    if not scope:
        return None
    query = select(SupplierItemRuleRow).where(
        SupplierItemRuleRow.supplier_key == scope,
        SupplierItemRuleRow.supplier_code_key == item_key(original.get("supplier_code", "")),
        SupplierItemRuleRow.name_hash == name_hash(original.get("upd_name") or original.get("name", "")),
    )
    return session.scalar(query.with_for_update() if lock else query)


def _scope(inn: str, name: str, confirmed: bool, found: dict) -> str:
    scope = supplier_key(inn, name, confirmed)
    raw_inn = str(found.get("inn") or "")
    if inn_valid(raw_inn) and raw_inn not in OWN_INNS and raw_inn != inn:
        raise WarehouseValidationError("ИНН поставщика отличается от исходного УПД")
    if not inn and confirmed and item_key(name) != item_key(found.get("supplier", "")):
        raise WarehouseValidationError("Название поставщика отличается от исходного УПД")
    return scope


def rule_suggestion(session, scope: str, original: dict) -> dict | None:
    if original.get("learning_eligible") is False:
        return None
    rule = _find(session, scope, original)
    if rule is None:
        return None
    result = {
        "rule_id": str(rule.id), "rule_version": rule.version, "active": rule.active,
        "supplier_key": scope, "article": rule.article, "manufacturer": rule.manufacturer,
        "stock_unit": rule.stock_unit, "pack_size": number_text(rule.pack_size),
        "quantity_basis": rule.quantity_basis, "quantity": None, "warning": "",
    }
    if not rule.active:
        return result
    # The schema keeps the invoice unit in the immutable learning evidence. A later invoice may use base units.
    evidence = session.scalar(select(ReceiptLineConfirmationRow).where(
        ReceiptLineConfirmationRow.rule_id == rule.id,
        ReceiptLineConfirmationRow.rule_version == rule.version,
        ReceiptLineConfirmationRow.learned.is_(True),
    ).order_by(ReceiptLineConfirmationRow.confirmed_at.desc(), ReceiptLineConfirmationRow.id.desc()).limit(1))
    convert = evidence.accepted.get("request", {}).get("learning", {}).get("convert_quantity", False) if evidence else False
    result["convert_quantity"] = convert or rule.pack_size is not None
    if not result["convert_quantity"]:
        original_unit = item_key(original.get("unit", ""))
        if original_unit != item_key(rule.stock_unit):
            result["warning"] = "Единица УПД отличается от складской — укажите пересчёт количества"
        return result
    unit = item_key(original.get("invoice_unit", ""))
    learned_unit = item_key(evidence.original.get("invoice_unit", "")) if evidence else ""
    quantity = quantity_of(original.get("invoice_quantity"))
    if quantity is None or not unit or not learned_unit:
        result["warning"] = "Неизвестна единица или количество по УПД — проверьте пересчёт"
        return result
    if rule.quantity_basis == "packs" and learned_unit == item_key(rule.stock_unit):
        result["warning"] = "Упаковки и единицы склада обозначены одинаково — проверьте пересчёт"
        return result
    if unit == learned_unit:
        quantity *= rule.pack_size if rule.quantity_basis == "packs" else 1
    elif unit != item_key(rule.stock_unit):
        result["warning"] = "Единица УПД изменилась — проверьте пересчёт"
        return result
    result["quantity"] = number_text(quantity)
    return result


def preview(uow, *, file_id: UUID, source_line_key: str, supplier_inn: str, supplier: str,
            confirm_supplier_name: bool = False) -> dict:
    with uow:
        original, found = original_line(uow.session, file_id, source_line_key)
        scope = _scope(supplier_inn, supplier, confirm_supplier_name, found)
        return {"original": original, "learning": rule_suggestion(uow.session, scope, original)}


@dataclass(frozen=True)
class LearningChoice:
    file_id: UUID
    source_line_key: str
    action: str = "none"  # none / remember / deactivate
    confirm_supplier_name: bool = False
    pack_size: str = ""
    quantity_basis: str = "base"
    convert_quantity: bool = False
    rule_id: UUID | None = None
    rule_version: int | None = None


def request_snapshot(command) -> dict:
    return json.loads(json.dumps({
        "receipt_id": command.receipt_id, "item": asdict(command.item), "labels": command.labels,
        "confirm_expected": command.confirm_expected, "learning": asdict(command.learning) if command.learning else None,
    }, default=str, sort_keys=True))


def lock_acceptance(session, receipt_id: UUID) -> None:
    # First SQL statement: serializes retries on PostgreSQL and reserves SQLite's writer before any reads.
    session.execute(update(WarehouseReceiptRow).where(WarehouseReceiptRow.id == receipt_id).values(
        version=WarehouseReceiptRow.version,
    ))


def previous_acceptance(session, command):
    previous = session.scalar(select(ReceiptLineConfirmationRow).where(
        ReceiptLineConfirmationRow.acceptance_id == command.acceptance_id,
    ))
    if previous is not None and previous.accepted["request"] != request_snapshot(command):
        raise ReceiptConflict("Повтор приёмки с тем же номером содержит другие данные")
    return previous


def confirm_line(session, command, receipt, lot, job_id, label_error):
    choice = command.learning
    original, found = original_line(session, choice.file_id, choice.source_line_key)
    scope = _scope(receipt.supplier_inn, receipt.supplier, choice.confirm_supplier_name, found)
    if choice.action not in {"none", "remember", "deactivate"}:
        raise WarehouseValidationError("Неизвестное действие с правилом поставщика")
    if choice.action != "none" and not scope:
        raise WarehouseValidationError("Без ИНН подтвердите правило по названию поставщика")
    rule = _find(session, scope, original, lock=True)
    if choice.rule_id is not None:
        if rule is None or rule.id != choice.rule_id or rule.version != choice.rule_version:
            raise ReceiptConflict("Правило поставщика изменено — проверьте строку заново")
    elif choice.rule_version is not None or (rule is not None and choice.action != "none"):
        raise ReceiptConflict("Правило поставщика уже существует — проверьте строку заново")
    linked = rule if choice.rule_id is not None else None
    if choice.action == "remember":
        if original.get("learning_eligible") is False:
            raise WarehouseValidationError("Строка реестра не сопоставлена с исходной строкой УПД — обучение недоступно")
        code = item_key(original.get("supplier_code", ""))
        name = item_key(original.get("upd_name") or original.get("name", ""))
        if not code and not name:
            raise WarehouseValidationError("В исходной строке нет кода и наименования поставщика")
        if len(code) > 255 or len(name) > 2000:
            raise WarehouseValidationError("Код или наименование поставщика слишком длинные для правила")
        try:
            pack = Decimal(choice.pack_size.replace(",", ".")) if choice.pack_size.strip() else None
        except InvalidOperation:
            raise WarehouseValidationError("Размер упаковки должен быть положительным числом") from None
        if pack is not None and (not pack.is_finite() or pack <= 0 or pack >= Decimal("1e12")
                                 or pack != pack.quantize(Decimal("0.000001"))):
            raise WarehouseValidationError("Размер упаковки — положительное число, до 6 знаков после запятой")
        if choice.quantity_basis not in {"packs", "base"} or (choice.quantity_basis == "packs" and pack is None):
            raise WarehouseValidationError("Для количества в упаковках укажите размер упаковки")
        if (pack is not None or choice.convert_quantity) and (not quantity_of(original.get("invoice_quantity")) or not original.get("invoice_unit")):
            raise WarehouseValidationError("Для пересчёта нужны исходные количество и единица УПД")
        if len(lot.unit) > 16 or len(lot.article) > 255 or len(lot.manufacturer) > 255:
            raise WarehouseValidationError("Артикул, производитель или единица слишком длинные для правила")
        if rule is None:
            rule = SupplierItemRuleRow(id=uuid4(), supplier_key=scope, supplier_code_key=code, name_key=name,
                                       name_hash=name_hash(name),
                                       version=1, created_at=command.now, created_by=command.actor.name)
            session.add(rule)
        else:
            rule.version += 1
        rule.article, rule.manufacturer, rule.stock_unit = lot.article, lot.manufacturer, lot.unit
        rule.pack_size, rule.quantity_basis, rule.active = pack, choice.quantity_basis, True
        rule.updated_at, rule.updated_by = command.now, command.actor.name
        session.flush()
        linked = rule
    elif choice.action == "deactivate":
        if rule is None or choice.rule_id is None:
            raise WarehouseValidationError("Выберите правило для отключения")
        rule.active, rule.version = False, rule.version + 1
        rule.updated_at, rule.updated_by = command.now, command.actor.name
        linked = rule
    session.add(ReceiptLineConfirmationRow(
        id=uuid4(), acceptance_id=command.acceptance_id, file_id=choice.file_id, receipt_id=receipt.id, lot_id=lot.id,
        rule_id=linked.id if linked else None, rule_version=linked.version if linked else None,
        source_line_key=choice.source_line_key, supplier_key=scope,
        original={**original, "supplier": found.get("supplier", ""), "supplier_inn": found.get("inn", "")},
        accepted={"request": request_snapshot(command), "article": lot.article, "quantity": str(lot.inbound),
                  "unit": lot.unit, "manufacturer": lot.manufacturer,
                  "label_job_id": str(job_id) if job_id else None, "label_error": label_error},
        learned=choice.action == "remember", confirmed_at=command.now, confirmed_by=command.actor.name,
    ))
