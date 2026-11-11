"""Read-only M0 rehearsal: python -m app.workflows.molex_match_report [--json].

Pairs describe newly equal identities, not allocation instructions or additive quantities.
No matching call site, historical ETK, reservation or persisted purchasing key is changed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.modules.fulfillment.domain import FulfillmentStatus, project_of
from app.modules.fulfillment.infrastructure import FulfillmentLineRow, FulfillmentRequestRow
from app.modules.procurement.document_match import code_key
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing.infrastructure import ItemRow, UpdLineRow
from app.modules.receiving.infrastructure import ReceivingLineRow
from app.modules.references.infrastructure import TechOperationRecordRow
from app.modules.requests.infrastructure import RequestRow
from app.modules.techcards.connector_housings import housing_of
from app.modules.techcards.etk_parts import kit_article, use_article
from app.modules.techcards.kitset_infrastructure import KitsetRow
from app.modules.techcards.kitsets import KitComponent, project_key
from app.modules.warehouse.infrastructure import StockLotRow
from app.shared.article_key import article_key, is_molex_article
from app.shared.article_maker import normalize_maker
from app.shared.project_number import project_from_purpose

SEAMS = ("arrival_matches", "_supply_match", "_supply_key", "etk_parts.use_article", "procurement code_key", "housing_of")


@dataclass(frozen=True)
class _Entry:
    kind: str
    id: str
    article: str
    name: str = ""
    manufacturer: str = ""
    project: str = ""
    kb: str = ""
    quantity: str = ""
    unit: str = ""
    grn: str = ""
    package: str = ""
    available: str = ""
    used_article: str = ""
    kd_article: str = ""
    waiting: bool = False
    stored_column: str = ""
    stored_key: str | None = None
    evidence: str = ""
    identity_conflicts: tuple[str, ...] = ()
    lot_id: str = ""
    receipt_id: str = ""

    @property
    def comparison_article(self) -> str:
        # _supply_match falls back to a name only when no article was supplied.
        if not self.article.strip() and self.kind in {"lot", "request", "purchase", "fulfillment"}:
            return self.name
        return self.article

    @property
    def key(self) -> str:
        return article_key(self.comparison_article, manufacturer=self.manufacturer, catalog_molex=bool(self.evidence))

    @property
    def molex(self) -> bool:
        return is_molex_article(self.comparison_article, manufacturer=self.manufacturer, catalog_molex=bool(self.evidence))


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _rows(session: Session, model: Any) -> list[dict]:
    # Core selects also avoid populating/version-tracking ORM entities in the caller's session.
    return [dict(row) for row in session.execute(select(model.__table__)).mappings()]


def _load(session: Session) -> tuple[list[_Entry], dict[str, dict]]:
    entries: list[_Entry] = []
    purchases = {str(row["id"]): row for row in _rows(session, ProcurementLineRow)}
    source_requests = {str(row["id"]): row for row in _rows(session, RequestRow)}
    receipts = {str(row["id"]): row for row in _rows(session, ReceivingLineRow)}
    arrivals: dict[str, dict] = {}
    for row in _rows(session, StockLotRow):
        lot_id = str(row["id"])
        entries.append(_Entry(
            "lot", lot_id, row["article"], row["name"], row["manufacturer"],
            quantity=str(row["inbound"]), unit=row["unit"], grn=row["grn"], package=row["package"],
            available=str(max(Decimal(0), row["inbound"] - row["written_off"] - row["reserved"])),
        ))
        sources = []
        for receiving_id in row["source_receiving_ids"]:
            raw_id = str(receiving_id)
            base_id, marker, revision = raw_id.rpartition(":r")
            lookup_id = base_id if marker and revision.isascii() and revision.isdigit() else raw_id
            receipt = receipts.get(lookup_id)
            if receipt is None:
                continue
            purchase = purchases.get(str(receipt["procurement_line_id"]), {})
            sources.append({
                "receipt_id": raw_id,
                "project": purchase.get("project_reference") or receipt["goal"],
                "articles": [purchase.get("design_article"), receipt["article"]],
                "names": [purchase.get("calculated_name"), receipt["name"], receipt["document_name"]],
            })
            # arrival_matches sees the purchase/receiving articles, never the lot's supplier code.
            for field, kind in (("articles", "arrival"), ("names", "arrival_name")):
                for index, article in enumerate(sources[-1][field]):
                    if article:
                        entries.append(_Entry(
                            kind, f"{lot_id}/{raw_id}/{field}/{index}", article,
                            manufacturer=receipt["manufacturer"] or row["manufacturer"],
                            project=sources[-1]["project"], unit=receipt["unit"],
                            quantity=str(receipt["fact_quantity"]) if receipt["fact_quantity"] is not None else "",
                            lot_id=lot_id, receipt_id=raw_id,
                        ))
        arrivals[lot_id] = {"sources": sources}
    for row in _rows(session, KitsetRow):
        for assembly in row["assemblies"]:
            for position, comp in enumerate(assembly.get("components") or []):
                component = KitComponent(
                    article=comp.get("art", ""), name=comp.get("name", ""), type=comp.get("type", ""),
                    per_unit=Decimal(comp.get("per") or "0"),
                    scrap_pct=Decimal(comp["scrap"]) if comp.get("scrap") is not None else None,
                )
                entries.append(_Entry(
                    "kit", f'{row["project"]}/{assembly["index"]}/{position}', component.article, component.name,
                    project=row["project"], kb=assembly["index"],
                    quantity=str(component.batch(Decimal(assembly.get("run") or "0"))), unit=component.unit,
                    used_article=kit_article(component.article, component.name)["art"],
                ))
    requests = {str(row["id"]): row for row in _rows(session, FulfillmentRequestRow)}
    open_statuses = {s.value for s in (FulfillmentStatus.NEW, FulfillmentStatus.UNDERFILLED, FulfillmentStatus.COMPLETE)}
    for row in _rows(session, FulfillmentLineRow):
        request = requests[str(row["request_id"])]
        entries.append(_Entry(
            "fulfillment", str(row["id"]), row["article"], row["name"],
            project=project_key(project_of(request["purpose"])) or request["project_key"]
                    or project_from_purpose(request["purpose"]) or "",
            kb=request["item_index"] or "", quantity=str(row["requirement"]), unit=row["unit"],
            waiting=(bool(project_of(request["purpose"])) and request["status"] in open_statuses
                     and row["lot_id"] is None and not row["off_book"]
                     and row["reserved"] == 0 and row["issued"] == 0),
        ))
    for row in source_requests.values():
        entries.append(_Entry(
            "request", str(row["id"]), row["design_article"] or "", row["name"],
            project=row["project_reference"] or project_from_purpose(row["purpose"]) or "",
            quantity=str(row["quantity"]), unit=row["unit"],
        ))
    for row in purchases.values():
        request = source_requests.get(str(row["source_request_id"]), {})
        entries.append(_Entry(
            "purchase", str(row["id"]), str(row["design_article"] or "").strip() or request.get("design_article") or "",
            row["calculated_name"] or request.get("name") or "",
            project=row["project_reference"] or request.get("project_reference") or project_from_purpose(request.get("purpose")) or "",
            quantity=str(row["actual_quantity"] if row["actual_quantity"] is not None else row["calculated_quantity"]),
            unit=row["actual_unit"] or row["calculated_unit"],
        ))
    for row in _rows(session, ItemRow):
        entries.append(_Entry(
            "item", str(row["id"]), row["part_number"] or "", row["name"], row["manufacturer"] or "",
            unit=row["unit"], stored_column="purchasing_items.part_key", stored_key=row["part_key"],
        ))
    for row in _rows(session, UpdLineRow):
        entries.append(_Entry(
            "upd", str(row["id"]), row["article"], row["name"], unit=row["unit"],
            quantity=str(row["quantity"]) if row["quantity"] is not None else "",
            stored_column="purchasing_upd_lines.article_key", stored_key=row["article_key"],
        ))
    for row in _rows(session, TechOperationRecordRow):
        fields = row["fields"]
        # Contacts/housings use these explicit catalogue fields, not free-text maker mentions.
        maker = fields.get("terManufacturer") or fields.get("Производитель") or ""
        for field in ("terArticle", "terArticleBag", "article", "Артикул"):
            if fields.get(field):
                entries.append(_Entry("catalog", f'{row["id"]}/{field}', str(fields[field]), manufacturer=maker))
    return entries, arrivals


def _with_evidence(entries: list[_Entry]) -> list[_Entry]:
    # Only exact stored spelling establishes catalogue identity. Never transfer it by numeric length.
    makers: dict[str, set[str]] = defaultdict(set)
    numeric_makers: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        maker = normalize_maker(entry.manufacturer)
        if maker:
            makers[_text(entry.comparison_article)].add(maker)
            forced_key = article_key(entry.comparison_article, manufacturer="Molex")
            if forced_key.isascii() and forced_key.isdigit():
                numeric_makers[forced_key].add(maker)
    result = []
    for entry in entries:
        evidence = "catalogue/manufacturer exact article" if (
            not entry.manufacturer and makers[_text(entry.comparison_article)] == {"MOLEX"}
        ) else ""
        conflicts = numeric_makers[article_key(entry.comparison_article, manufacturer="Molex")] - {"MOLEX"}
        result.append(replace(entry, evidence=evidence, identity_conflicts=tuple(sorted(conflicts))))
    return result


def _pair(left: _Entry, right: _Entry) -> dict:
    issues = []
    if not left.unit or not right.unit:
        issues.append("неизвестная единица измерения")
    elif _text(left.unit) != _text(right.unit):
        issues.append("разные единицы измерения")
    conflicts = sorted(set(left.identity_conflicts) | set(right.identity_conflicts))
    if conflicts:
        issues.append("производитель требует проверки: " + ", ".join(conflicts))
    return {"key": left.key, "left": asdict(left), "right": asdict(right), "review_reasons": issues,
            "left_match_field": "article" if left.article.strip() else "name",
            "right_match_field": "article" if right.article.strip() else "name"}


def _housing(entry: _Entry) -> dict | None:
    old, new = housing_of(entry.article), housing_of(entry.key)
    def identity(layout: dict | None) -> dict | None:
        return {key: layout[key] for key in ("family", "n", "rows", "cols")} if layout else None
    if identity(old) == identity(new):
        return None
    return {"entry": asdict(entry), "key": entry.key, "before": identity(old), "after": identity(new)}


def report(session: Session) -> dict:
    """Suppress autoflush even if the caller has pending ORM changes; caller owns isolation."""
    with session.no_autoflush:
        entries, arrivals = _load(session)
    aliases = [replace(entry, kind="kit_used", article=entry.used_article, kd_article=entry.article)
               for entry in entries if entry.kind == "kit" and entry.used_article != entry.article]
    entries = _with_evidence(entries + aliases)
    original_keys = {entry.id: entry.key for entry in entries if entry.kind == "kit"}
    entries = [entry for entry in entries if entry.kind != "kit_used" or entry.key != original_keys[entry.id]]
    legacy_supply: dict[tuple[str, str], set[str]] = defaultdict(set)
    legacy_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    for entry in entries:
        if entry.kind == "kit":
            legacy_supply[project_key(entry.project), _text(entry.article)].update(
                (_text(entry.article), _text(entry.used_article)),
            )
            legacy_names[project_key(entry.project), _text(entry.article)].add(_text(entry.name))
    groups: dict[str, list[_Entry]] = defaultdict(list)
    for entry in entries:
        if entry.molex and entry.key and entry.key.isascii() and entry.key.isdigit():
            groups[entry.key].append(entry)
    changes: dict[str, list[dict]] = {seam: [] for seam in SEAMS}
    collisions, stored = [], []
    stored_index: dict[tuple[str, str | None], list[_Entry]] = defaultdict(list)
    for entry in entries:
        if entry.stored_column:
            stored_index[entry.stored_column, entry.stored_key].append(entry)
    lot_by_id = {entry.id: entry for entry in entries if entry.kind == "lot"}
    for key, group in sorted(groups.items()):
        group.sort(key=lambda entry: (entry.kind, entry.id))
        if len({entry.comparison_article for entry in group}) > 1:
            collisions.append({"key": key, "articles": sorted({entry.comparison_article for entry in group}),
                               "entries": [asdict(entry) for entry in group]})
        for column in ("purchasing_items.part_key", "purchasing_upd_lines.article_key"):
            rows = [entry for entry in group if entry.stored_column == column]
            if not rows:
                continue
            # A new key may also occupy another article's existing key, outside this Molex group.
            others = [entry for entry in stored_index[column, key] if entry.id not in {row.id for row in rows}]
            newly_equal = len({entry.stored_key for entry in rows}) > 1 or (
                column == "purchasing_items.part_key" and len(rows) > 1 and any(row.stored_key is None for row in rows)
            )
            if newly_equal or others:
                stored.append({"column": column, "key": key,
                               "entries": [asdict(entry) for entry in rows + others]})
        kits = [entry for entry in group if entry.kind in {"kit", "kit_used"}]
        lots = [entry for entry in group if entry.kind == "lot"]
        targets = [entry for entry in group if entry.kind in {"kit", "fulfillment", "request", "purchase", "upd", "item"}]
        for entry in group:
            if entry.kind in {"arrival", "arrival_name"} or not entry.article.strip():
                continue
            applied = use_article(entry.article, entry.name)
            if applied["kd"] and article_key(entry.name, manufacturer=entry.manufacturer,
                                             catalog_molex=bool(entry.evidence)) == key:
                changes["etk_parts.use_article"].append({
                    "entry": asdict(entry), "before": applied, "after": {"art": entry.article, "kd": ""},
                })
            housing = _housing(entry)
            if housing:
                changes["housing_of"].append(housing)
        for lot in lots:
            for target in targets:
                if lot.article.strip() and target.article.strip() and code_key(lot.article) != code_key(target.article):
                    changes["procurement code_key"].append(_pair(lot, target))
        seen_arrivals = set()
        for arrival in (entry for entry in group if entry.kind in {"arrival", "arrival_name"}):
            source = next(source for source in arrivals[arrival.lot_id]["sources"]
                          if source["receipt_id"] == arrival.receipt_id)
            for target in (entry for entry in group if entry.kind == "fulfillment" and entry.waiting):
                if bool(target.article.strip()) != (arrival.kind == "arrival"):
                    continue
                field = "articles" if target.article.strip() else "names"
                pair_id = arrival.lot_id, arrival.receipt_id, target.id
                if pair_id in seen_arrivals or not project_key(arrival.project):
                    continue
                if project_key(arrival.project) != project_key(target.project):
                    continue
                if _text(target.comparison_article) in {_text(article) for article in source[field] if article}:
                    continue
                pair = _pair(arrival, target)
                pair["left"] = asdict(lot_by_id[arrival.lot_id])
                pair["arrival_source"] = {**source, "quantity": arrival.quantity, "key": key, "match_field": field}
                changes["arrival_matches"].append(pair)
                seen_arrivals.add(pair_id)
        for document in (entry for entry in group if entry.kind == "upd"):
            for order in (entry for entry in group if entry.kind in {"purchase", "request", "item"}):
                if code_key(document.article) != code_key(order.comparison_article):
                    changes["procurement code_key"].append(_pair(document, order))
        for kit in kits:
            for source in group:
                if source.kind not in {"lot", "request", "purchase", "fulfillment"}:
                    continue
                if source.project and project_key(source.project) != project_key(kit.project):
                    continue
                if source.kind != "lot" and not source.project:
                    continue
                if _text(source.comparison_article) != _text(kit.article):
                    if kit.kind == "kit" and source.article.strip():
                        changes["_supply_key"].append(_pair(source, kit))
                    identity = project_key(kit.project), _text(kit.kd_article or kit.article)
                    legacy_aliases = legacy_supply[identity]
                    baseline = legacy_aliases if source.article.strip() else legacy_aliases | legacy_names[identity]
                    if _text(source.comparison_article) not in baseline:
                        changes["_supply_match"].append(_pair(source, kit))
    manufacturer_conflicts = [
        {"key": key, "manufacturers": sorted({maker for entry in group for maker in entry.identity_conflicts}),
         "entries": [asdict(entry) for entry in entries if entry.manufacturer
                     and article_key(entry.comparison_article, manufacturer="Molex") == key]}
        for key, group in sorted(groups.items()) if any(entry.identity_conflicts for entry in group)
    ]
    return {
        "policy": "molex-m0", "read_only": True,
        "scope": "Кандидаты по артикулам; количества не суммировать. Резервы и исторические ЭТК не пересчитываются. "
                 "Приходы показаны только при сохранённой связи партии с приёмкой и проектом. "
                 "_supply_key: потенциальные совпадения ключей выбора, без имитации выбора пользователя.",
        "scanned": dict(sorted((kind, sum(entry.kind == kind for entry in entries)) for kind in {e.kind for e in entries})),
        "changes": changes, "canonical_collisions": collisions, "stored_key_collisions": stored,
        "manufacturer_conflicts": manufacturer_conflicts,
    }


def format_report(result: dict) -> str:
    lines = ["Сравнение артикулов Molex — только чтение", result["scope"]]
    for seam, rows in result["changes"].items():
        lines.append(f"\n{seam}: {len(rows)}")
        lines.extend(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    for field, title in (("canonical_collisions", "Коллизии артикулов"),
                         ("stored_key_collisions", "Коллизии сохранённых ключей"),
                         ("manufacturer_conflicts", "Разногласия производителей")):
        lines.append(f'\n{title}: {len(result[field])}')
        lines.extend(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in result[field])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сравнить ключи Molex без изменения базы.")
    parser.add_argument("--json", action="store_true", help="Показать отчёт в JSON")
    args = parser.parse_args(argv)
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine

    configured = make_url(url)
    if configured.get_backend_name() == "sqlite" and configured.database not in (None, "", ":memory:"):
        # mode=ro also prevents accidentally creating an empty database at a mistyped path.
        database = configured.database.replace("\\", "/")
        configured = configured.set(database=database if database.startswith("file:") else "file:" + database,
                                    query={**configured.query, "mode": "ro", "uri": "true"})
        url = configured.render_as_string(hide_password=False)
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            if engine.dialect.name == "postgresql":
                connection = connection.execution_options(isolation_level="REPEATABLE READ")
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            elif engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA query_only = ON")
                # sqlite3's legacy transaction mode does not start a snapshot for SELECT alone.
                connection.exec_driver_sql("BEGIN")
            sessions = sessionmaker(bind=connection, expire_on_commit=False, autoflush=False, class_=Session)
            with sessions() as session:
                result = report(session)
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else format_report(result))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
