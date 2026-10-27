"""«Справочник поставщиков»: pure helpers (name key, INN, domains, categories) and repository functions over the
purchasing session. Procurement lines never come in here — callers pass them as plain data (workflows port)."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.shared.inn import inn_valid  # the same check as the document reader (app.shared.doc_reader)

from .infrastructure import QuoteRow, RfqSupplierRow, SupplierNameRow, SupplierRow

CATEGORIES: dict[str, str] = {
    "cable": "Кабель",
    "connector": "Разъёмы",
    "ekb": "ЭКБ",
    "consumables": "Расходники",
    "tools": "Инструмент",
    "equipment": "Оборудование",
    "other": "Прочее",
}

INN_ERROR = "ИНН не проходит проверку контрольной суммы"

_LEGAL = {
    "ооо", "оао", "зао", "ао", "пао", "нао", "ип", "оу", "тоо", "нпо", "нпп", "нпф", "кфх", "гк", "чп",
    "llc", "ltd", "limited", "inc", "co", "corp", "gmbh", "sia", "ag", "sa", "bv", "oy", "ab", "pte", "company",
}
_QUOTES = "«»\"'“”„‘’`´"
_WORD = re.compile(r"[\w.]+", re.UNICODE)


class SupplierError(ValueError):
    """A refusal with an HTTP status: 404 not found, 409 conflict, 422 invalid data."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# ---- pure helpers ------------------------------------------------------------------------------------------------


def supplier_key(name: str) -> str:
    """Casefolded name without legal forms, quotes and extra spaces: «ООО «Чип и Дип»» → «чип и дип»."""
    folded = str(name or "").casefold().replace("ё", "е")
    for quote in _QUOTES:
        folded = folded.replace(quote, " ")
    words = [word.strip(".") for word in _WORD.findall(folded)]
    words = [word for word in words if word]
    kept = [word for word in words if word not in _LEGAL]
    if not kept:
        kept = words
    return " ".join(kept) if kept else " ".join(folded.split())


def normalize_inn(value: Any) -> str | None:
    text = re.sub(r"[\s-]", "", str(value or ""))
    if not text:
        return None
    if len(text) not in (10, 12) or not inn_valid(text):
        raise SupplierError(INN_ERROR)
    return text


def normalize_domain(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)
    text = text.split("/")[0].split("?")[0].split("#")[0].split("@")[-1].split(":")[0]
    return text.removeprefix("www.").strip(".")


def normalize_domains(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        domain = normalize_domain(value)
        if not domain:
            continue
        if " " in domain or "." not in domain:
            raise SupplierError(f"«{value}» не похоже на домен")
        if domain not in out:
            out.append(domain)
    return out


def normalize_categories(values: list[str]) -> list[str]:
    bad = [v for v in values if v not in CATEGORIES]
    if bad:
        raise SupplierError(f"Нет такой категории: {', '.join(bad)}")
    return [key for key in CATEGORIES if key in values]


def looks_like_domain(text: str) -> bool:
    domain = normalize_domain(text)
    return bool(domain) and " " not in domain and "." in domain


# ---- repository --------------------------------------------------------------------------------------------------


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _email(value: Any) -> str | None:
    text = _clean(value)
    if text and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        raise SupplierError("Почта указана неверно")
    return text


def serialize(row: SupplierRow) -> dict[str, Any]:
    return {
        "id": str(row.id), "name": row.name, "inn": row.inn, "categories": list(row.categories or []), "is_key": row.is_key,
        "contact_person": row.contact_person, "phone": row.phone, "email": row.email, "site": row.site,
        "domains": list(row.domains or []), "notes": row.notes, "archived": row.archived_at is not None,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None, "version": row.version,
    }


def get(session: Session, supplier_id: UUID) -> SupplierRow:
    row = session.get(SupplierRow, supplier_id)
    if row is None:
        raise SupplierError("Поставщик не найден", 404)
    return row


def _register_name(session: Session, key: str, supplier_id: UUID, now: datetime) -> None:
    if not key:
        return
    existing = session.get(SupplierNameRow, key)
    if existing is None:
        session.add(SupplierNameRow(name_key=key, supplier_id=supplier_id, created_at=now))
    else:
        existing.supplier_id = supplier_id


def _check_unique(session: Session, key: str, inn: str | None, own: UUID | None) -> None:
    other = session.scalar(select(SupplierRow).where(SupplierRow.name_key == key))
    if other is not None and other.id != own:
        raise SupplierError(f"Поставщик с таким названием уже есть: {other.name}", 409)
    if inn:
        other = session.scalar(select(SupplierRow).where(SupplierRow.inn == inn))
        if other is not None and other.id != own:
            raise SupplierError(f"Поставщик с таким ИНН уже есть: {other.name}", 409)


def create(session: Session, data: dict[str, Any], actor_id: UUID | None, now: datetime) -> SupplierRow:
    name = " ".join(str(data.get("name") or "").split())
    if not name:
        raise SupplierError("Название обязательно")
    key = supplier_key(name)
    inn = normalize_inn(data.get("inn"))
    _check_unique(session, key, inn, None)
    row = SupplierRow(
        id=uuid4(), name=name, name_key=key, inn=inn, categories=normalize_categories(list(data.get("categories") or [])),
        is_key=bool(data.get("is_key", False)), contact_person=_clean(data.get("contact_person")), phone=_clean(data.get("phone")),
        email=_email(data.get("email")), site=_clean(data.get("site")), domains=normalize_domains(list(data.get("domains") or [])),
        notes=_clean(data.get("notes")), version=1, created_at=now, created_by=actor_id,
    )
    session.add(row)
    _register_name(session, key, row.id, now)
    session.flush()
    return row


def update(session: Session, supplier_id: UUID, version: int, data: dict[str, Any], actor_id: UUID | None, now: datetime) -> SupplierRow:
    row = get(session, supplier_id)
    if row.version != version:
        raise SupplierError("Карточку уже изменили — обновите и повторите", 409)
    if "name" in data:
        name = " ".join(str(data["name"] or "").split())
        if not name:
            raise SupplierError("Название обязательно")
        key = supplier_key(name)
        _check_unique(session, key, None, row.id)
        row.name, row.name_key = name, key
        _register_name(session, key, row.id, now)
    if "inn" in data:
        inn = normalize_inn(data["inn"])
        _check_unique(session, row.name_key, inn, row.id)
        row.inn = inn
    if "categories" in data:
        row.categories = normalize_categories(list(data["categories"] or []))
    if "is_key" in data:
        row.is_key = bool(data["is_key"])
    for field_name in ("contact_person", "phone", "site", "notes"):
        if field_name in data:
            setattr(row, field_name, _clean(data[field_name]))
    if "email" in data:
        row.email = _email(data["email"])
    if "domains" in data:
        row.domains = normalize_domains(list(data["domains"] or []))
    row.version += 1
    row.updated_at, row.updated_by = now, actor_id
    session.flush()
    return row


def set_archived(session: Session, supplier_id: UUID, archived: bool, actor_id: UUID | None, now: datetime) -> SupplierRow:
    row = get(session, supplier_id)
    row.archived_at = now if archived else None
    row.version += 1
    row.updated_at, row.updated_by = now, actor_id
    session.flush()
    return row


def map_name(session: Session, name: str, supplier_id: UUID, now: datetime) -> str:
    get(session, supplier_id)
    key = supplier_key(name)
    if not key:
        raise SupplierError("Название обязательно")
    _register_name(session, key, supplier_id, now)
    session.flush()
    return key


def keys_by_supplier(session: Session, supplier_id: UUID) -> set[str]:
    return set(session.scalars(select(SupplierNameRow.name_key).where(SupplierNameRow.supplier_id == supplier_id)))


def supplier_by_key(session: Session) -> dict[str, UUID]:
    return {row.name_key: row.supplier_id for row in session.scalars(select(SupplierNameRow))}


# ---- procurement statistics --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineFacts:
    """What the directory needs from a procurement line (filled by the workflows port)."""

    id: str
    supplier: str
    status: str
    name: str
    article: str | None
    quantity: Decimal | None
    unit: str | None
    unit_price_with_vat: Decimal | None
    actual_sum: Decimal | None
    order_number: str | None
    order_date: date | None
    paid_at: date | None
    expected_arrival: date | None
    actual_arrival: date | None


CLOSED_STATUSES = ("Готово", "Отменено")


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def lines_of(lines: list[LineFacts], keys: set[str]) -> list[LineFacts]:
    return [line for line in lines if supplier_key(line.supplier) in keys]


def list_totals(lines: list[LineFacts], names: dict[str, UUID]) -> dict[UUID, tuple[int, Decimal]]:
    """Per supplier: lines count and Σ actual_sum of paid lines."""
    totals: dict[UUID, tuple[int, Decimal]] = {}
    for line in lines:
        owner = names.get(supplier_key(line.supplier))
        if owner is None:
            continue
        count, total = totals.get(owner, (0, Decimal("0")))
        paid = line.actual_sum if line.paid_at is not None and line.actual_sum is not None else Decimal("0")
        totals[owner] = (count + 1, total + paid)
    return totals


def procurement_stats(lines: list[LineFacts]) -> dict[str, Any]:
    paid = [line for line in lines if line.paid_at is not None]
    paid_sum = sum((line.actual_sum or Decimal("0") for line in paid), Decimal("0"))
    received = [line for line in lines if line.actual_arrival is not None]
    judged = [line for line in received if line.expected_arrival is not None]
    on_time = [line for line in judged if line.actual_arrival <= line.expected_arrival]  # type: ignore[operator]
    open_by_stage = Counter(line.status for line in lines if line.status not in CLOSED_STATUSES)
    dated = sorted(
        (line for line in lines if line.order_date or line.paid_at),
        key=lambda line: line.order_date or line.paid_at, reverse=True,  # type: ignore[arg-type,return-value]
    )
    last = [
        {
            "date": (line.order_date or line.paid_at).isoformat(),  # type: ignore[union-attr]
            "name": line.name, "article": line.article, "quantity": _num(line.quantity), "unit": line.unit,
            "unit_price_with_vat": _num(line.unit_price_with_vat), "order_number": line.order_number,
        }
        for line in dated[:10]
    ]
    return {
        "lines_total": len(lines), "paid_lines": len(paid), "paid_sum": str(paid_sum),
        "received": len(received), "received_with_date": len(judged), "on_time": len(on_time),
        "on_time_pct": round(len(on_time) * 100 / len(judged)) if judged else None,
        "open_by_stage": dict(open_by_stage), "last_purchases": last,
    }


def rfq_stats(session: Session, supplier_id: UUID) -> dict[str, Any]:
    sent = session.scalar(
        select(func.count()).select_from(RfqSupplierRow).where(RfqSupplierRow.supplier_id == supplier_id, RfqSupplierRow.sent_at.is_not(None)),
    ) or 0
    answered = session.scalar(
        select(func.count(func.distinct(QuoteRow.rfq_id))).where(QuoteRow.supplier_id == supplier_id),
    ) or 0
    quotes = session.scalar(select(func.count()).select_from(QuoteRow).where(QuoteRow.supplier_id == supplier_id)) or 0
    return {
        "sent": sent, "answered": answered, "quotes": quotes,
        "answered_pct": round(min(answered, sent) * 100 / sent) if sent else None,
    }


# ---- import from procurement -------------------------------------------------------------------------------------


@dataclass
class _Group:
    key: str
    texts: Counter = field(default_factory=Counter)
    domains: list[str] = field(default_factory=list)
    mapped_name: str | None = None


def plan_import(session: Session, supplier_texts: dict[str, int], domain_map: dict[str, str]) -> dict[str, Any]:
    """Group the free-text supplier names of procurement lines by supplier_key. A text that is a domain known to the
    references map resolves to the mapped name; the map's domains are attached to the group of that name."""
    by_domain = {normalize_domain(domain): value for domain, value in domain_map.items() if value and value.strip()}
    groups: dict[str, _Group] = {}
    for text, count in supplier_texts.items():
        text = " ".join(str(text or "").split())
        if not text:
            continue
        resolved = by_domain.get(normalize_domain(text)) if looks_like_domain(text) else None
        key = supplier_key(resolved or text)
        group = groups.setdefault(key, _Group(key))
        group.texts[text] += count
        if resolved:
            group.mapped_name = resolved
    for domain, value in by_domain.items():
        group = groups.get(supplier_key(value))
        if group is not None:
            group.mapped_name = group.mapped_name or value
            if domain not in group.domains:
                group.domains.append(domain)
    owners = supplier_by_key(session)
    suppliers = {row.id: row for row in session.scalars(select(SupplierRow))}
    new: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    for group in sorted(groups.values(), key=lambda g: g.key):
        variants = [{"text": text, "lines": count} for text, count in sorted(group.texts.items(), key=lambda kv: (-kv[1], kv[0]))]
        name = group.mapped_name or variants[0]["text"]
        owner_id = owners.get(group.key) or owners.get(supplier_key(name))
        lines = sum(group.texts.values())
        if owner_id is None:
            new.append({"name": name, "key": group.key, "variants": variants, "domains": group.domains, "lines": lines})
        else:
            row = suppliers[owner_id]
            known = {key for key, sid in owners.items() if sid == owner_id}
            existing.append({
                "supplier_id": str(owner_id), "name": row.name, "key": group.key, "variants": variants, "lines": lines,
                "new_variants": [v for v in variants if supplier_key(v["text"]) not in known],
                "new_domains": [d for d in group.domains if d not in (row.domains or [])],
            })
    return {"new": new, "existing": existing}


def apply_import(session: Session, plan: dict[str, Any], actor_id: UUID | None, now: datetime) -> dict[str, int]:
    created = 0
    updated = 0
    for item in plan["new"]:
        row = SupplierRow(
            id=uuid4(), name=item["name"], name_key=supplier_key(item["name"]), categories=[], is_key=False,
            domains=list(item["domains"]), version=1, created_at=now, created_by=actor_id,
        )
        session.add(row)
        _register_name(session, row.name_key, row.id, now)
        for variant in item["variants"]:
            _register_name(session, supplier_key(variant["text"]), row.id, now)
        created += 1
    for item in plan["existing"]:
        if not item["new_variants"] and not item["new_domains"]:
            continue
        row = session.get(SupplierRow, UUID(item["supplier_id"]))
        for variant in item["new_variants"]:
            _register_name(session, supplier_key(variant["text"]), row.id, now)
        if item["new_domains"]:
            row.domains = [*(row.domains or []), *item["new_domains"]]
            row.version += 1
            row.updated_at, row.updated_by = now, actor_id
        updated += 1
    session.flush()
    return {"created": created, "updated": updated}


def matches_search(row: SupplierRow, query: str) -> bool:
    haystack = " ".join([row.name, row.inn or "", " ".join(row.domains or []), row.site or "", row.email or ""]).casefold().replace("ё", "е")
    return all(word in haystack for word in query.casefold().replace("ё", "е").split())


_STOCK_SOURCES = ("склад", "со склада", "с склада", "из склада", "наш склад")


def is_stock_source(text: str | None) -> bool:
    """«Склад» written in the supplier column means «take it from our stock», not a company to ask for quotes."""
    value = " ".join(str(text or "").split()).casefold().strip(" .")
    return value in _STOCK_SOURCES or value.startswith("склад ")
