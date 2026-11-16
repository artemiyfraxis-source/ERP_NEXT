"""Read-only comparison of fulfillment lines against current BOM consumption.

On the server, from the compose directory:
    docker compose run --rm -T api python -m app.workflows.units_report --project 708К.

No historical BOM snapshot exists: proposals use current per-item consumption and run,
never reverse a rounded request quantity. Ambiguous/split requirements need manual review.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
import sys
from typing import Any, Iterable

from app.modules.fulfillment.application import kit_key
from app.modules.fulfillment.domain import FulfillmentKind, FulfillmentRequest, FulfillmentStatus
from app.modules.requests.domain import normalize_unit
from app.modules.techcards.kitsets import Kitset, component_unit, project_key
from app.workflows.item_names import reference_records
from app.workflows.project_kitset import material_units


def _key(value: str) -> str:
    return " ".join(value.split()).casefold()


def report_lines(
    cards: Iterable[Kitset], requests: Iterable[FulfillmentRequest], *, reference_units: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Report proposals only; a current BOM match is evidence, not historical identity."""
    assemblies = defaultdict(list)
    for card in cards:
        for assembly in card.assemblies:
            key = kit_key(f"{card.project} · {assembly.index}")
            if key:
                assemblies[key].append(assembly)
    result = []
    for request in requests:
        if request.status is FulfillmentStatus.CANCELLED:
            continue
        candidates = assemblies.get(kit_key(request.purpose), [])
        if len(candidates) != 1:
            continue
        assembly = candidates[0]
        requirements = defaultdict(list)
        for line in request.lines:
            requirements[line.requirement_id].append(line)
        for siblings in requirements.values():
            first = siblings[0]
            matches = [component for component in assembly.components if (
                _key(component.article) == _key(first.article) if first.article
                else _key(component.name or component.article) == _key(first.name)
            )]
            if not matches:
                continue
            units = {component_unit(component, reference_units) for component in matches}
            correct_unit = next(iter(units)) if len(units) == 1 else None
            quantity = sum((component.batch(assembly.run, unit=correct_unit) for component in matches)) if correct_unit else None
            confidence = "current_bom"
            same_article_groups = sum(
                _key(group[0].article) == _key(first.article) if first.article
                else _key(group[0].name) == _key(first.name)
                for group in requirements.values()
            )
            if len(matches) != 1 or same_article_groups != 1:
                confidence = "ambiguous"
            elif len(siblings) != 1:
                confidence = "split_requirement"
            if correct_unit is None:
                confidence = "incompatible_bom_units"
            if request.kind is FulfillmentKind.EXTRA and correct_unit is not None:
                if all(normalize_unit(line.unit) == correct_unit for line in siblings):
                    continue
                confidence = "extra_issue"
            if confidence == "current_bom" and first.requirement == quantity and normalize_unit(first.unit) == correct_unit:
                continue
            if confidence != "current_bom" and all(normalize_unit(line.unit) == correct_unit for line in siblings):
                if sum(line.requirement for line in siblings) == quantity and same_article_groups == 1:
                    continue
            for line in siblings:
                result.append({
                    "request_id": str(request.id), "number": request.number, "status": request.status.value,
                    "version": request.version, "purpose": request.purpose, "line_id": str(line.id),
                    "requirement_id": str(line.requirement_id), "article": line.article, "name": line.name,
                    "current_quantity": str(line.requirement), "current_unit": line.unit,
                    "correct_quantity": str(quantity) if confidence == "current_bom" else None,
                    "correct_unit": correct_unit, "bom_requirement_quantity": str(quantity) if quantity is not None else None,
                    "candidate_units": sorted(units),
                    "confidence": confidence, "basis": "current_bom_not_historical_snapshot",
                    "lot_id": str(line.lot.lot_id) if line.lot else None,
                    "grn": line.lot.grn if line.lot else None,
                    "reserved": str(line.reserved), "issued": str(line.issued),
                    "returned_good": str(line.returned_good), "defective_returned": str(line.defective_returned),
                    "defective_missing": str(line.defective_missing),
                })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка единиц заявок по текущему составу, без записи в БД.")
    parser.add_argument("--project", help="№ проекта; без параметра — все проекты")
    args = parser.parse_args(argv)
    url = os.environ.get("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL", file=sys.stderr)
        return 2
    from app.database import build_engine

    engine = build_engine(url)
    try:
        rows = read_report(engine, args.project)
    finally:
        engine.dispose()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({
        "read_only": True, "affected_lines": len(rows),
        "affected_requests": len({row["request_id"] for row in rows}), "lines": rows,
    }, ensure_ascii=False, indent=2))
    return 0


def read_report(engine, project: str | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import event
    from sqlalchemy.orm import sessionmaker

    from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
    from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
    from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork

    def read_only(connection):
        if engine.dialect.name == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        elif engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA query_only = ON")

    # Apply to every transaction, including those opened by separate module units of work.
    event.listen(engine, "begin", read_only)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
            cards = uow.kitsets.list()
        if project:
            cards = [card for card in cards if project_key(card.project) == project_key(project)]
        units = material_units(reference_records(lambda: SqlAlchemyReferencesUnitOfWork(sessions)))
        with SqlAlchemyFulfillmentUnitOfWork(sessions) as uow:
            requests = []
            cursor = None
            while page := uow.fulfillment.list(limit=200, after_id=cursor):
                requests.extend(page)
                cursor = page[-1].id
        return report_lines(cards, requests, reference_units=units)
    finally:
        event.remove(engine, "begin", read_only)


if __name__ == "__main__":
    sys.exit(main())
