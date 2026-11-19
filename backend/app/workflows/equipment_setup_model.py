"""Setup model of БД.ОБ for «Станки + наладка» (owner 18–19.11.2026) — read API and the guarded seed.

Rules: app/modules/equipment_setup/setup_model.py; tables: setup_model_infrastructure.py (migration 0199).

HTTP (read only; the same gate as «Станки»: Производство or Задания, view):
- `GET /api/production/setup-model` → `{rows, counts}`: every БД.ОБ unit with its effective class (нет / простая /
  полная, or «не задано» before the seed), number of parameters, whether an ERP agent reaches it and whether it has a
  current configuration. The screen's tabs «Станки онлайн / без подключения / Все» filter these rows.
- `GET /api/production/setup-model/{ОБ}` → one unit: parameters with sources and registers, current configuration,
  эталоны (newest version first per wire/contact), machine corrections, connection.

Seed (owner-reviewed CSV only; dry run by default):
`python -m app.workflows.equipment_setup_model --seed docs/data/equipment-setup-model-proposal-2026-11-18.csv [--apply]`
with the application's ERP_DATABASE_URL. Only units whose every CSV row says «да» in `reviewed` are taken; an unknown
or ambiguous inventory number refuses the whole run. Units of one equipment model → the model's setup model (the most
common answer) plus overrides for the units that differ."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.modules.equipment_setup.setup_model import (REQUIRES_LABELS, SOURCE_LABELS, SetupModel, SetupValidationError,
                                                     effective, model_and_overrides, read_proposal)
from app.modules.equipment_setup.setup_model_infrastructure import SqlAlchemySetupModels
from app.modules.production import machines as rules
from app.modules.production.api import production_uow_factory
from app.modules.references.equipment import INV_KEY, NAME_KEYS, field
from app.modules.references.equipment_catalogs import CATALOG_TYPES
from app.modules.references.infrastructure import EQUIPMENT_ROWS
from app.modules.warehouse.marking_profiles import MarkingBindingRow
from app.modules.warehouse.scan_agents import LIVE_FOR, ScanAgentRow
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal

from .machines_api import machines_view_principal

router = APIRouter(prefix="/api/production/setup-model", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
Reader = Annotated[Principal, Depends(machines_view_principal)]
NOT_SET = "не задано"
SEED_ACTOR = "Модель наладки (CSV владельца)"


def inventory_of(value: object) -> str:
    return "".join(str(value or "").split()).upper()


class Unit:
    __slots__ = ("id", "model_id", "catalog", "inventory", "name", "article")

    def __init__(self, row):
        fields = row.fields or {}
        self.id, self.model_id, self.catalog = row.id, row.equipment_model_id, row.tab
        self.inventory = inventory_of(field(fields, INV_KEY, "inventoryNumber"))
        self.name = field(fields, *NAME_KEYS) or row.display_text
        self.article = field(fields, "Артикул")


def units(session) -> list[Unit]:
    """Every unit of every БД.ОБ catalog (оборудование, СИ, обеспечивающее, аппликаторы, матрицы, инструмент)."""
    found = [Unit(row) for model in EQUIPMENT_ROWS.values() for row in session.scalars(select(model))]
    return sorted(found, key=lambda unit: (unit.catalog, unit.inventory, str(unit.id)))


def connections(session, now: datetime) -> dict[UUID, dict]:
    """Units an ERP agent reaches: a machine bridge (ОБ.019) or the print agent of a marking printer (ОБ.101/102)."""
    found: dict[UUID, dict] = {}
    for link, snapshot in rules.links(session):
        state = rules.link_status(snapshot, now)
        found[link.equipment_id] = {"agent": rules.KIND_NAMES[link.kind], "pc": link.pc_name,
                                    "live": state == "ok", "state": rules.LINK_TEXTS[state][0]}
    agents = {agent.machine_id: agent for agent in session.scalars(select(ScanAgentRow))}
    for binding in session.scalars(select(MarkingBindingRow)):
        agent = agents.get(binding.machine_id)
        seen = agent.last_seen if agent else None
        if seen is not None and seen.tzinfo is None:
            seen = seen.replace(tzinfo=now.tzinfo)
        live = seen is not None and now - seen <= LIVE_FOR
        found.setdefault(binding.equipment_id, {
            "agent": f"{rules.AGENT_NAME} · печать", "pc": agent.pc_name if agent else "", "live": live,
            "state": f"Принтер {binding.printer}: " + ("на связи" if live else "нет связи")})
    return found


def _class(model: SetupModel | None) -> dict:
    if model is None:
        return {"code": None, "label": NOT_SET}
    return {"code": model.requires_setup, "label": REQUIRES_LABELS[model.requires_setup]}


def _effective(unit: Unit, models, overrides) -> SetupModel | None:
    return effective(models.get(unit.model_id), overrides.get(unit.id))


@router.get("")
def setup_model_list(_principal: Reader, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        store = SqlAlchemySetupModels(session)
        models, overrides, configured = store.models(), store.overrides(), store.configured_units()
        reach = connections(session, now)
        rows, counts = [], {"none": 0, "simple": 0, "full": 0, "unset": 0, "online": 0}
        for unit in units(session):
            model = _effective(unit, models, overrides)
            link = reach.get(unit.id)
            counts[model.requires_setup if model else "unset"] += 1
            counts["online"] += bool(link)
            rows.append({"unit_id": str(unit.id), "inventory": unit.inventory, "name": unit.name,
                         "model": unit.article, "catalog": CATALOG_TYPES.get(unit.catalog, unit.catalog),
                         "requires_setup": _class(model), "parameters": len(model.parameters) if model else 0,
                         "own_setup": unit.id in overrides, "online": bool(link),
                         "connection": link["state"] if link else "", "configured": unit.id in configured})
    return {"rows": rows, "counts": counts}


def _parameter(item) -> dict:
    return {"key": item.key, "label": item.label, "unit": item.unit, "source": SOURCE_LABELS[item.source],
            "source_field": item.source_field, "register": item.register}


def unit_setup(session, inventory: str, now: datetime) -> dict | None:
    """The setup part of one unit's card; None — no such unit (or the number names several)."""
    matches = [unit for unit in units(session) if unit.inventory == inventory_of(inventory)]
    if len(matches) != 1:
        return None
    unit = matches[0]
    store = SqlAlchemySetupModels(session)
    model = effective(store.models().get(unit.model_id), store.overrides().get(unit.id))
    configuration = store.configuration(unit.id)
    link = connections(session, now).get(unit.id)
    return {
        "unit_id": str(unit.id), "inventory": unit.inventory, "name": unit.name, "model": unit.article,
        "requires_setup": _class(model), "reason": model.reason if model else "",
        "parameters": [_parameter(item) for item in model.parameters] if model else [],
        "configuration": None if configuration is None else {
            "tooling_unit_id": str(configuration.tooling_unit_id) if configuration.tooling_unit_id else None,
            "blades": configuration.blades, "wire": configuration.wire, "contact": configuration.contact,
            "reference_id": str(configuration.reference_id) if configuration.reference_id else None,
            "values": dict(configuration.values), "set_by": configuration.set_by_name,
            "set_at": configuration.set_at.isoformat() if configuration.set_at else None,
            "version": configuration.version},
        "references": [{"id": str(item.id), "wire": item.wire, "contact": item.contact, "version": item.version,
                        "values": dict(item.values), "note": item.note, "created_by": item.created_by_name,
                        "created_at": item.created_at.isoformat()} for item in store.references(unit.id)],
        "corrections": [{"parameter": item.parameter_key, "wire": item.wire, "offset": str(item.offset),
                         "reason": item.reason} for item in store.corrections(unit.id)],
        "online": bool(link), "connection": link,
    }


@router.get("/{inventory}")
def setup_model_card(inventory: str, _principal: Reader, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        result = unit_setup(uow.session, inventory, now)
    if result is None:
        raise HTTPException(status_code=404, detail=f"{inventory}: в БД.ОБ нет такой единицы (или номер не единственный)")
    return result


# --- seed from the owner-reviewed CSV ---

def seed(session, rows, *, now: datetime, apply: bool, source: str) -> dict:
    try:
        proposal, skipped = read_proposal(rows)
    except SetupValidationError as error:
        return {"applied": False, "refused": str(error)}
    by_inventory: dict[str, list[Unit]] = {}
    for unit in units(session):
        if unit.inventory:
            by_inventory.setdefault(unit.inventory, []).append(unit)
    problems = [f"{item.inventory}: в БД.ОБ {len(by_inventory.get(item.inventory, []))} единиц с этим номером"
                for item in proposal if len(by_inventory.get(item.inventory, [])) != 1]
    if problems:
        return {"applied": False, "refused": "; ".join(problems)}
    grouped: dict[UUID, dict[str, SetupModel]] = {}
    for item in proposal:
        grouped.setdefault(by_inventory[item.inventory][0].model_id, {})[item.inventory] = item.model
    store = SqlAlchemySetupModels(session)
    plan = {"models": {}, "overrides": {}}
    for model_id, members in sorted(grouped.items(), key=lambda pair: min(pair[1])):
        base, overrides = model_and_overrides(members)
        names = ", ".join(sorted(members))
        plan["models"][names] = f"{base.label}, параметров {len(base.parameters)}: " + store.put_model(
            model_id, base, source=source, actor_name=SEED_ACTOR, now=now)
        for inventory in sorted(members):
            outcome = store.put_override(by_inventory[inventory][0].id, overrides.get(inventory), actor_name=SEED_ACTOR,
                                         now=now)
            if outcome != "без изменений":
                plan["overrides"][inventory] = outcome
    session.flush()
    counts = {label: sum(1 for item in proposal if item.model.requires_setup == code) for code, label in REQUIRES_LABELS.items()}
    result = {"applied": apply, "units": len(proposal), "by_class": counts, "skipped_not_reviewed": skipped, **plan}
    if apply:
        session.commit()
    else:
        session.rollback()
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Модель наладки БД.ОБ из CSV, проверенного владельцем")
    parser.add_argument("--seed", required=True, type=Path, help="CSV (колонка reviewed = «да» у каждой строки ОБ)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="записать (по умолчанию только показать)")
    mode.add_argument("--dry-run", action="store_true", help="только показать (по умолчанию)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    with args.seed.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    from sqlalchemy.orm import Session

    from app.database import build_engine

    engine = build_engine(url)
    try:
        with Session(engine, expire_on_commit=False) as session:
            result = seed(session, rows, now=moscow_now(), apply=args.apply, source=args.seed.name)
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if "refused" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
