"""«Наладка оборудования» ↔ ОБ.019 (plan 18.11.2026, slice 2; no machine writes — those come with slices 3/4).

The operator (Слава) first sees what the machine holds, then corrects; ERP collects the corrections and learns the setup
(owner 18.11: «по началу будет криво»):
- proposal = the newest recorded recipe of this machine for the wire, else the ETK nominal of the cutting group
  (strip A/B, full/partial, cut length) or, «вне проекта», the wire the operator picks (Ø жилы from the reference);
- live PLC values come from «Станки» (`GET /api/production/machines/{id}`, slice 1) — the screen reads them itself;
- each correction → AuditEvent `equipment_setup.ob019_correction`; «Сохранить версию» → the next recipe version.

HTTP (`/api/production/equipment-setup/ob019/{machine_id}`): GET `context` (parameters, wires, cutting-group jobs),
GET `proposal?wire=…|group_id=…&length_mm=…`, POST `corrections`, POST `versions`.

Seed of the owner-approved recipe (18.11: ПВАМ 0,35 — strip 5/5, pull-off 5/5, Dia 0.80, back-off 0.10), dry run by
default: `python -m app.workflows.ob019_setup --seed [--apply]` with the application's ERP_DATABASE_URL."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
import json
import os
import re
import sys
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.modules.equipment_setup.api import setup_uow_factory
from app.modules.equipment_setup.application import ConcurrentSetupUpdate, SetupPermissionError
from app.modules.equipment_setup.queries import list_recipes
from app.modules.equipment_setup.recipe_application import (Ob019CorrectionCommand, SaveOb019VersionCommand, ob019_machine,
                                                            record_ob019_correction, save_ob019_version)
from app.modules.equipment_setup.recipes import (ETK_STRIP_LABELS, OB019_INVENTORY, OB019_PARAMETERS, EtkNominal,
                                                 ManualRecipeVersion, decimal_value, latest_for_wire, ob019_proposal,
                                                 ob019_recipe_fields, recipe_wire, wire_key)
from app.modules.production.cutting_infrastructure import CuttingGroupRow, CuttingMemberRow
from app.modules.references.cable_structure import shown
from app.modules.references.cables import hookup_series, hookup_series_rest
from app.modules.references.infrastructure import SqlAlchemyCableStructureRepository
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor

router = APIRouter(prefix="/api/production/equipment-setup/ob019", tags=["production"])
Factory = Annotated[Callable, Depends(setup_uow_factory)]
Who = Annotated[Principal, Depends(authenticated_principal)]
Now = Annotated[datetime, Depends(moscow_now)]

WIRE_CUTS = {"cutWire", "cutWireStrip"}
GROUP_STATES = ("open", "frozen")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
RECIPE_NAMESPACE = uuid5(NAMESPACE_URL, "erp-next:equipment-setup:ob019-recipe")


def recipe_id_for(machine_id: UUID, wire: str) -> UUID:
    """One recipe per machine and wire: its versions are the history of that setup."""
    return uuid5(RECIPE_NAMESPACE, f"{machine_id}:{wire_key(wire)}")


# --- wires: «ПВАМ 0,35» = series + section of the база монтажных проводов ---

def wire_label(article: str, sections=()) -> str:
    """«ПВАМ 0,35 красный» → «ПВАМ 0,35» (colour does not change the setup); a mark outside the base stays as written."""
    text = " ".join(str(article or "").split())
    found = hookup_series_rest(text)
    if found:
        number = _NUMBER.search(found[1])
        if number:
            if "AWG" in found[1].upper():
                return f"{found[0]} {number.group()}AWG"
            return f"{found[0]} {shown(decimal_value(number.group()))}"
    return text


def _wires(session) -> dict[str, Decimal | None]:
    """label → Ø жилы of every hook-up wire of the reference (the largest when one label has several)."""
    structure = SqlAlchemyCableStructureRepository(session)
    gauges = {gauge.id: gauge for gauge in structure.gauges()}
    result: dict[str, Decimal | None] = {}
    for wire in structure.hookups():
        gauge = gauges.get(wire.gauge_id)
        if gauge is None:
            continue
        series = hookup_series(wire.mark) or wire.series
        by_awg = "AWG" in wire.mark.upper() and gauge.awg
        label = f"{series} {gauge.awg}AWG" if by_awg else f"{series} {shown(gauge.section_mm2)}"
        if label not in result or (gauge.conductor_d_mm or 0) > (result[label] or 0):
            result[label] = gauge.conductor_d_mm
    return result


def _conductor(wires: dict[str, Decimal | None], wire: str) -> Decimal | None:
    key = wire_key(wire)
    return next((value for label, value in wires.items() if wire_key(label) == key), None)


# --- jobs of cutting groups: the ETK nominal of the wire cut ---

def _strip_side(strip: dict, side: str) -> tuple[Decimal | None, bool]:
    if not strip.get(side):
        return Decimal(0), False
    mode = str(strip.get("mode" + side.upper()) or "").casefold()
    return decimal_value(strip.get("len" + side.upper())), "partial" in mode or "част" in mode


def group_nominal(recipe: dict) -> EtkNominal | None:
    """The wire cut of a frozen cutting recipe (production/cutting.py snapshot); None — not a wire cut (coax, …)."""
    operations = [op for op in recipe.get("operations") or [] if op.get("type") in WIRE_CUTS]
    if not operations:
        return None
    strip = next((op.get("fields", {}).get("strip") for op in operations if (op.get("fields") or {}).get("strip")), None) or {}
    front, partial_front = _strip_side(strip, "a")
    back, partial_back = _strip_side(strip, "b")
    return EtkNominal(wire_label(recipe.get("cable", "")), None, front, back, partial_front, partial_back)


def group_choices(groups, members) -> list[dict]:
    """One choice per group and cut length (only length and labels vary inside a group)."""
    by_group: dict[UUID, list] = {}
    for member in members:
        by_group.setdefault(member.group_id, []).append(member)
    choices = []
    for group in groups:
        nominal = group_nominal(group.recipe or {})
        if nominal is None:
            continue
        lengths: dict[str, Decimal] = {}
        for member in by_group.get(group.id, []):
            length = decimal_value((member.row_snapshot or {}).get("cut_length_mm"))
            if length is not None:
                text = format(length.normalize(), "f")
                lengths[text] = lengths.get(text, Decimal(0)) + Decimal(member.blank_quantity)
        for text, quantity in sorted(lengths.items(), key=lambda item: Decimal(item[0])):
            choices.append({"key": f"{group.id}:{text}", "group_id": str(group.id), "number": group.number,
                "project": group.project_key, "cable": (group.recipe or {}).get("cable", ""), "wire": nominal.wire,
                "length_mm": text, "quantity": format(quantity.normalize(), "f"),
                "strip_front_mm": _text(nominal.strip_front_mm), "strip_back_mm": _text(nominal.strip_back_mm)})
    return choices


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _groups(session, site: str):
    groups = list(session.scalars(select(CuttingGroupRow).where(CuttingGroupRow.state.in_(GROUP_STATES),
        CuttingGroupRow.site == site).order_by(CuttingGroupRow.created_at.desc()).limit(100)))
    members = session.scalars(select(CuttingMemberRow).where(CuttingMemberRow.state == "active",
        CuttingMemberRow.group_id.in_([group.id for group in groups]))) if groups else []
    return groups, list(members)


# --- HTTP ---

def _http(action):
    try:
        return action()
    except SetupPermissionError as error:
        raise HTTPException(403, str(error)) from error
    except ConcurrentSetupUpdate as error:
        raise HTTPException(409, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, str(error.args[0])) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def _versions(machine_id, factory, principal) -> tuple[ManualRecipeVersion, ...]:
    return list_recipes(machine_id, factory(), principal=principal, limit=200, offset=0)


def _parameters() -> list[dict]:
    return [{"key": item.key, "label": item.label, "unit": item.unit, "register": item.register,
             "in_recipe": item.in_recipe} for item in OB019_PARAMETERS]


@router.get("/{machine_id}/context")
def context(machine_id: UUID, principal: Who, factory: Factory) -> dict:
    def run():
        machine = ob019_machine(machine_id, factory(), principal=principal)
        versions = _versions(machine_id, factory, principal)
        with factory() as uow:
            wires = _wires(uow.session)
            groups, members = _groups(uow.session, machine.site)
        recorded = {}
        for version in versions:
            label = recipe_wire(version)
            if label and wire_key(label) not in recorded:
                recorded[wire_key(label)] = label
        names = {wire_key(label): label for label in wires} | recorded
        return {
            "machine": {"id": str(machine.id), "inventory_number": machine.inventory_number, "site": machine.site},
            "parameters": _parameters(),
            "wires": [{"label": label, "conductor_mm": _text(_conductor(wires, label)),
                       "has_recipe": key in recorded} for key, label in sorted(names.items(), key=lambda item: item[1])],
            "groups": group_choices(groups, members),
        }
    return _http(run)


@router.get("/{machine_id}/proposal")
def proposal(machine_id: UUID, principal: Who, factory: Factory, wire: Annotated[str, Query(max_length=200)] = "",
             group_id: UUID | None = None, length_mm: Annotated[str, Query(max_length=20)] = "") -> dict:
    def run():
        machine = ob019_machine(machine_id, factory(), principal=principal)
        versions = _versions(machine_id, factory, principal)
        length = decimal_value(length_mm) if length_mm else None
        if length_mm and (length is None or length <= 0):
            raise ValueError("Длина провода: нужно положительное число")
        with factory() as uow:
            wires = _wires(uow.session)
            if group_id is not None:
                group = uow.session.get(CuttingGroupRow, group_id)
                if group is None or group.site != machine.site:
                    raise KeyError("Группа резки не найдена на площадке станка")
                nominal = group_nominal(group.recipe or {})
                if nominal is None:
                    raise ValueError("В группе нет резки провода")
            else:
                if not wire.strip():
                    raise ValueError("Выберите провод")
                nominal = EtkNominal(wire_label(wire))
        nominal = EtkNominal(nominal.wire, nominal.length_mm, nominal.strip_front_mm, nominal.strip_back_mm,
                             nominal.partial_front, nominal.partial_back, _conductor(wires, nominal.wire))
        recipe = latest_for_wire(versions, nominal.wire)
        target = recipe.recipe_id if recipe else recipe_id_for(machine.id, nominal.wire)
        values = ob019_proposal(recipe, nominal, length)
        labels = {item.key: item for item in OB019_PARAMETERS}
        return {
            "wire": nominal.wire,
            "recipe": None if recipe is None else {"recipe_id": str(recipe.recipe_id), "version": recipe.version,
                "title": recipe.title, "created_at": recipe.created_at.isoformat(), "source": recipe.source},
            "publish": {"recipe_id": str(target),
                        "expected_version": max((v.version for v in versions if v.recipe_id == target), default=0)},
            "etk": None if group_id is None else {"strip_front_mm": _text(nominal.strip_front_mm),
                "strip_back_mm": _text(nominal.strip_back_mm), "partial_front": nominal.partial_front,
                "partial_back": nominal.partial_back},
            "values": [{"key": item.key, "label": labels[item.key].label, "unit": labels[item.key].unit,
                        "register": labels[item.key].register, "value": _text(item.value), "source": item.source}
                       for item in values],
        }
    return _http(run)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CorrectionPayload(Payload):
    parameter: str = Field(min_length=1, max_length=40)
    actual: str = Field(min_length=1, max_length=20)
    proposed: str | None = Field(default=None, max_length=20)
    live: str | None = Field(default=None, max_length=20)
    wire: str = Field(default="", max_length=200)
    context: str = Field(default="", max_length=300)
    quantity: int | None = Field(default=None, strict=True, ge=1, le=1_000_000)


class VersionPayload(Payload):
    recipe_id: UUID
    expected_version: int = Field(strict=True, ge=0)
    wire: str = Field(min_length=1, max_length=200)
    values: dict[str, str] = Field(max_length=len(OB019_PARAMETERS))
    etk_strip_front_mm: str | None = Field(default=None, max_length=20)
    etk_strip_back_mm: str | None = Field(default=None, max_length=20)
    context: str = Field(default="", max_length=300)


def _number(value: str | None, label: str) -> Decimal | None:
    if value in (None, ""):
        return None
    number = decimal_value(value)
    if number is None:
        raise ValueError(f"{label}: нужно число")
    return number


@router.post("/{machine_id}/corrections")
def correction(machine_id: UUID, payload: CorrectionPayload, principal: Who, factory: Factory, now: Now) -> dict:
    def run():
        actual = _number(payload.actual, "Фактическое значение")
        command = Ob019CorrectionCommand(machine_id, payload.parameter, actual, _number(payload.proposed, "Предложение"),
            _number(payload.live, "Значение станка"), payload.wire, payload.context, payload.quantity)
        return record_ob019_correction(command, factory(), principal=principal, now=now)
    return _http(run)


@router.post("/{machine_id}/versions")
def version(machine_id: UUID, payload: VersionPayload, principal: Who, factory: Factory, now: Now) -> dict:
    def run():
        unknown = set(payload.values) - {item.key for item in OB019_PARAMETERS}
        if unknown:
            raise ValueError("Неизвестный параметр ОБ.019")
        etk = {"strip_front_mm": _number(payload.etk_strip_front_mm, ETK_STRIP_LABELS["strip_front_mm"]),
               "strip_back_mm": _number(payload.etk_strip_back_mm, ETK_STRIP_LABELS["strip_back_mm"])}
        command = SaveOb019VersionCommand(payload.recipe_id, payload.expected_version, machine_id,
            wire_label(payload.wire), dict(payload.values), etk, payload.context)
        recipe = save_ob019_version(command, factory(), principal=principal, now=now)
        return {"recipe_id": str(recipe.recipe_id), "version": recipe.version, "title": recipe.title,
                "created_at": recipe.created_at.isoformat()}
    return _http(run)


# --- seed: the owner-approved first recipe (18.11.2026) ---

SEED_WIRE = "ПВАМ 0,35"
SEED_VALUES = {"strip_front_mm": "5", "pull_front_mm": "5", "strip_back_mm": "5", "pull_back_mm": "5",
               "diameter_mm": "0.80", "knife_back_mm": "0.10"}
SEED_SOURCE = ("Владелец 18.11.2026: проверено на ОБ.019 — зачистка 5/5 мм, стягивание 5/5, Dia 0,80 "
               "(0,90 надрезал мелко), отвод ножа 0,10 — «всё отлично»")
SEED_ACTOR = Actor(uuid5(NAMESPACE_URL, "erp-next:ob019-recipe-seed"), "Запись рецепта ОБ.019", "ERP")


def seed(session, *, now: datetime, apply: bool) -> dict:
    from app.modules.equipment_setup.recipe_infrastructure import SqlAlchemyRecipes
    from app.shared.persistence import SqlAlchemyAuditRepository
    from .production_resource_context import machine_units

    units = [unit for unit in machine_units(session) if unit.inventory_number == OB019_INVENTORY]
    if len(units) != 1:
        return {"applied": False, "refused": f"{OB019_INVENTORY}: в БД.ОБ найдено записей — {len(units)}, нужна одна"}
    unit = units[0]
    recipes = SqlAlchemyRecipes(session)
    existing = latest_for_wire(recipes.list_versions(unit.id, limit=200, offset=0), SEED_WIRE)
    if existing is not None:
        return {"applied": False, "skipped": f"Рецепт {SEED_WIRE} уже есть: версия {existing.version}",
                "recipe_id": str(existing.recipe_id)}
    geometry, panel = ob019_recipe_fields(SEED_WIRE, SEED_VALUES,
                                          {"strip_front_mm": Decimal(5), "strip_back_mm": Decimal(5)})
    recipe = ManualRecipeVersion(recipe_id_for(unit.id, SEED_WIRE), 1, unit.id, f"{SEED_WIRE} · {OB019_INVENTORY}",
                                 geometry, panel, SEED_SOURCE, SEED_ACTOR.id, now)
    plan = {"applied": apply, "machine_id": str(unit.id), "recipe_id": str(recipe.recipe_id), "version": 1,
            "geometry": [vars(item) for item in geometry], "panel_settings": [vars(item) for item in panel]}
    if apply:
        recipes.append(recipe, expected_version=0)
        SqlAlchemyAuditRepository(session).add(AuditEvent(uuid4(), recipe.recipe_id, "equipment_setup.recipe_published",
            SEED_ACTOR, now, {"version": "1", "machine_id": str(unit.id), "inventory_number": unit.inventory_number,
                              "source": SEED_SOURCE}))
        session.commit()
    return plan


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Первый рецепт ОБ.019 (ПВАМ 0,35), одобренный владельцем 18.11.2026")
    parser.add_argument("--seed", action="store_true", required=True)
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
    from sqlalchemy.orm import Session

    from app.database import build_engine

    engine = build_engine(url)
    try:
        with Session(engine, expire_on_commit=False) as session:
            result = seed(session, now=moscow_now(), apply=args.apply)
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
