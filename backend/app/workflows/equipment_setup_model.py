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
with the application's ERP_DATABASE_URL. Only units whose every CSV row says «да» (or records a decision, «решение …»)
in `reviewed` are taken; an unknown or ambiguous inventory number refuses the whole run. Units of one equipment model →
the model's setup model (the most common answer) plus overrides for the units that differ. Idempotent: keyed by the equipment model / unit (ОБ) and param_key, a repeated run changes nothing. Every
created or changed model / override is journaled (`audit_events`, `equipment_setup.model_seeded` /
`equipment_setup.unit_override_seeded`, actor «Модель наладки (CSV владельца)»)."""

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
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
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
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow
from app.shared.http_auth import Principal

from .machine_writes_api import machines_edit_principal
from .machines_api import machines_view_principal

router = APIRouter(prefix="/api/production/setup-model", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
Reader = Annotated[Principal, Depends(machines_view_principal)]
Editor = Annotated[Principal, Depends(machines_edit_principal)]
NOT_SET = "не задано"
SEED_ACTOR = "Модель наладки (CSV владельца)"
SEED_JOURNAL_ACTOR = Actor(id=UUID(int=0), name=SEED_ACTOR, department="Система")


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


def unit_setup(session, inventory: str, now: datetime, principal: Principal) -> dict | None:
    """The setup part of one unit's card; None — no such unit (or the number names several). Open tasks and the setup
    journal only as far as `principal` may read them (see `open_tasks`)."""
    matches = [unit for unit in units(session) if unit.inventory == inventory_of(inventory)]
    if len(matches) != 1:
        return None
    unit = matches[0]
    store = SqlAlchemySetupModels(session)
    model = effective(store.models().get(unit.model_id), store.overrides().get(unit.id))
    configuration = store.configuration(unit.id)
    link = connections(session, now).get(unit.id)
    tasks, journal = open_tasks(session, unit.id, principal)
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
        "open_tasks": tasks, "history": history(session, unit.id, now) if journal else [],
    }


SETUP_STATUSES = {"New": "Назначена", "InProgress": "В работе", "Blocked": "Заблокирована"}
HISTORY_LIMIT = 20


def open_tasks(session, unit_id: UUID, principal: Principal | None = None) -> tuple[list[dict], bool]:
    """The unit's open setup tasks (Назначена / В работе / Заблокирована: «Ждёт наладки» until the task is «Готова»)
    with the operation they set it up for, and whether the unit's setup journal may be read. With `principal` — only
    what the task list shows this employee (`readable_setup`: own site; a site master all tasks, anyone else only
    their own)."""
    from app.modules.equipment_setup.infrastructure import SetupTaskRow
    from app.modules.equipment_setup.queries import readable_setup
    from .equipment_setup_context import SqlAlchemySetupContext
    from .machines_api import SETUP_OPEN
    rows = list(session.scalars(select(SetupTaskRow).where(SetupTaskRow.machine_id == unit_id,
                                                           SetupTaskRow.status.in_(SETUP_OPEN))
                                .order_by(SetupTaskRow.created_at)))
    journal, confirmable = True, set()
    if principal is not None:
        context = SqlAlchemySetupContext(session, resolve_issued_operation=lambda reference: None)
        rows, confirmable, journal = readable_setup(unit_id, rows, context, principal=principal)
    result = []
    for row in rows:
        result.append({"id": str(row.id), "version": row.version, "status": SETUP_STATUSES.get(row.status, row.status),
                       "operation": operation_text(session, row.snapshot or {}),
                       "planned_start": (row.snapshot or {}).get("planned_start"),
                       "planned_finish": (row.snapshot or {}).get("planned_finish"),
                       "mine": principal is not None and row.technician_id == principal.actor.id,
                       "can_confirm": row.id in confirmable})
    return result, journal


def operation_text(session, snapshot: dict) -> str:
    """«Проект · изделие · операция · ЭТК n» of a setup task's snapshot."""
    from app.modules.production.infrastructure import ProductionOperationRow, ProductionTaskRow
    operation = snapshot.get("operation") or {}
    task_id, step_id = _uuid(operation.get("production_task_id")), _uuid(operation.get("production_operation_id"))
    task = session.get(ProductionTaskRow, task_id) if task_id else None
    step = session.get(ProductionOperationRow, step_id) if step_id else None
    position = operation.get("etk_position")
    parts = [task.project if task else "", task.item if task else "", step.stage if step else "",
             f"ЭТК {position}" if position else ""]
    return " · ".join(part for part in parts if part) or "Операция недоступна"


def _uuid(value) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


def history(session, unit_id: UUID, now: datetime) -> list[dict]:
    """«Настроено по заданию / вручную» of the unit, newest first: who, when, for what, the values."""
    from app.shared.persistence import AuditEventRow
    from .machines_api import SETUP_CONFIGURED
    rows = session.scalars(select(AuditEventRow).where(AuditEventRow.aggregate_id == unit_id,
                                                       AuditEventRow.event_type == SETUP_CONFIGURED)
                           .order_by(AuditEventRow.occurred_at.desc()).limit(HISTORY_LIMIT))
    result = []
    # Same moment (a fast double press, the tests' fixed clock): the configuration version decides.
    ordered = sorted(rows, key=lambda event: (event.occurred_at, int((event.details or {}).get("version") or 0)),
                     reverse=True)
    for event in ordered:
        details = event.details or {}
        at = event.occurred_at if event.occurred_at.tzinfo else event.occurred_at.replace(tzinfo=now.tzinfo)
        result.append({"at": at.isoformat(), "by": details.get("by", ""), "operation": details.get("operation", ""),
                       "reason": details.get("reason", ""), "values": json.loads(details.get("values") or "{}")})
    return result


NO_UNIT = "{inventory}: в БД.ОБ нет такой единицы (или номер не единственный)"


@router.get("/{inventory}")
def setup_model_card(inventory: str, principal: Reader, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        result = unit_setup(uow.session, inventory, now, principal)
    if result is None:
        raise HTTPException(status_code=404, detail=NO_UNIT.format(inventory=inventory))
    return result


class ConfigurationPayload(BaseModel):
    """«Настроено по заданию» (`task_id`, `task_version` — the version the card showed) or «Настроено вручную» (no task,
    `reason` required): the values the machine holds now. With a setup model the keys are its parameters (numbers for
    parameters with a unit); without one — free names of what was set."""
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    task_id: UUID | None = None
    task_version: int | None = Field(default=None, ge=1)
    values: dict[str, str] = Field(default_factory=dict, max_length=60)
    wire: str = Field(default="", max_length=200)
    contact: str = Field(default="", max_length=200)
    reason: str = Field(default="", max_length=500)


def _free_values(values: dict[str, str]) -> dict[str, str]:
    result = {}
    for label, value in values.items():
        name, text = " ".join(str(label).split()), " ".join(str(value).split())
        if not name or len(name) > 160:
            raise SetupValidationError("Название параметра: от 1 до 160 символов")
        if len(text) > 200:
            raise SetupValidationError(f"{name}: не длиннее 200 символов")
        if text:
            result[name] = text
    return dict(sorted(result.items()))


def _unit(session, inventory: str) -> Unit:
    matches = [unit for unit in units(session) if unit.inventory == inventory_of(inventory)]
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail=NO_UNIT.format(inventory=inventory))
    return matches[0]


def _write(session, audit, unit: Unit, payload: ConfigurationPayload, principal: Principal, now: datetime,
           operation: str) -> tuple[int, dict[str, str]]:
    """Validate the values, store them as the unit's current configuration (version check) and journal them."""
    from app.modules.equipment_setup.setup_model import setup_values
    from app.shared.events import AuditEvent
    from .machines_api import SETUP_CONFIGURED
    store = SqlAlchemySetupModels(session)
    model = _effective(unit, store.models(), store.overrides())
    values = setup_values(model, payload.values) if model is not None and model.parameters \
        else _free_values(payload.values)
    if not values:
        raise SetupValidationError("Укажите хотя бы одно значение")
    version = store.put_configuration(
        unit.id, values, wire=" ".join(payload.wire.split()), contact=" ".join(payload.contact.split()),
        actor_id=principal.actor.id, actor_name=principal.actor.name, now=now,
        expected_version=payload.expected_version)
    audit.add(AuditEvent(
        id=uuid4(), aggregate_id=unit.id, event_type=SETUP_CONFIGURED, actor=principal.actor, occurred_at=now,
        details={"inventory": unit.inventory, "task_id": str(payload.task_id or ""), "operation": operation,
                 "reason": " ".join(payload.reason.split()), "by": principal.actor.name, "version": str(version),
                 "values": json.dumps(values, ensure_ascii=False)}))
    return version, values


def _http(action):
    from app.modules.equipment_setup.application import (ConcurrentSetupUpdate, SetupPermissionError,
                                                          SetupResourceConflict)
    from app.modules.equipment_setup.setup_model_infrastructure import ConcurrentConfiguration
    try:
        return action()
    except SetupPermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (ConcurrentSetupUpdate, SetupResourceConflict, ConcurrentConfiguration) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error.args[0])) from error
    except ValueError as error:  # both setup validation errors
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{inventory}/configuration")
def record_configuration(inventory: str, payload: ConfigurationPayload, principal: Editor, factory: Factory,
                         request: Request, now: Now) -> dict:
    """The machine is set: what it holds now is stored as its current configuration (who / when), journaled with the
    task's operation or the reason. By task — the assigned technician or the master of the machine's site; the task goes
    to «Готова» through the existing transitions in the same transaction (that clears «Ждёт наладки»); by hand — an
    administrator, or an employee of the machine's site."""
    from app.modules.equipment_setup.application import ready_by_configuration
    from app.modules.equipment_setup.queries import may_record_by_hand
    from .equipment_setup_context import SqlAlchemySetupContext
    if payload.task_id is None:
        if not payload.reason.strip():
            raise HTTPException(status_code=422, detail="Настроено вручную: укажите причину")
        with factory() as uow:
            session = uow.session
            unit = _unit(session, inventory)

            def by_hand():
                context = SqlAlchemySetupContext(session, resolve_issued_operation=lambda reference: None)
                may_record_by_hand(unit.id, context, principal=principal)
                return _write(session, uow.audit, unit, payload, principal, now, "")
            version, values = _http(by_hand)
            uow.commit()
        return {"ok": True, "version": version, "values": values}
    setup = getattr(request.app.state, "equipment_setup_uow_factory", None)
    if setup is None:
        raise HTTPException(status_code=503, detail="Задания наладки ещё не подключены")
    written: dict = {}

    def record(uow, task) -> None:
        from app.modules.equipment_setup.infrastructure import task_payload
        unit = _unit(uow.session, inventory)
        if unit.id != task.machine_id:
            raise HTTPException(status_code=409, detail="Это задание наладки — для другого станка")
        operation = operation_text(uow.session, task_payload(task))
        written["version"], written["values"] = _write(uow.session, uow.audit, unit, payload, principal, now,
                                                       operation)

    task = _http(lambda: ready_by_configuration(payload.task_id, payload.task_version, setup(), principal=principal,
                                                now=now, record=record))
    return {"ok": True, "version": written["version"], "values": written["values"], "task_status": task.status.value}


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
        outcome = store.put_model(model_id, base, source=source, actor_name=SEED_ACTOR, now=now)
        plan["models"][names] = f"{base.label}, параметров {len(base.parameters)}: {outcome}"
        if outcome != "без изменений":
            _journal(session, model_id, "equipment_setup.model_seeded", now, units=names, outcome=outcome,
                     requires_setup=base.label, parameters=", ".join(item.key for item in base.parameters),
                     source=source)
        for inventory in sorted(members):
            override = overrides.get(inventory)
            unit_id = by_inventory[inventory][0].id
            outcome = store.put_override(unit_id, override, actor_name=SEED_ACTOR, now=now)
            if outcome != "без изменений":
                plan["overrides"][inventory] = outcome
                _journal(session, unit_id, "equipment_setup.unit_override_seeded", now, unit=inventory, outcome=outcome,
                         requires_setup=REQUIRES_LABELS[override.requires_setup] if override and override.requires_setup
                         else "как у модели",
                         parameters=", ".join(("−" if change.removed else "") + change.key
                                              for change in (override.parameters if override else ())),
                         source=source)
    session.flush()
    counts = {label: sum(1 for item in proposal if item.model.requires_setup == code) for code, label in REQUIRES_LABELS.items()}
    result = {"applied": apply, "units": len(proposal), "by_class": counts, "skipped_not_reviewed": skipped, **plan}
    if apply:
        session.commit()
    else:
        session.rollback()
    return result


def _journal(session, aggregate_id: UUID, event_type: str, now: datetime, **details: str) -> None:
    session.add(AuditEventRow(id=uuid4(), aggregate_id=aggregate_id, event_type=event_type,
                              actor_id=SEED_JOURNAL_ACTOR.id, actor_name=SEED_JOURNAL_ACTOR.name,
                              actor_department=SEED_JOURNAL_ACTOR.department, occurred_at=now,
                              details={name: str(value)[:500] for name, value in details.items()}))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Модель наладки БД.ОБ из CSV, проверенного владельцем")
    parser.add_argument("--seed", required=True, type=Path, help="CSV (колонка reviewed = «да» или «решение …» у каждой строки ОБ)")
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
