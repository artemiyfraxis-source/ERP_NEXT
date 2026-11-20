"""«ВАЦ — рабочее место» → «Экраны» (S5b, owner 19.11: «мне надо, чтобы я мог настраивать экраны»).

The screens the bench puts on S2VNA, editable by ОТК: list and read (ОТК view), create, save a new version (CAS on
`version`), switch off / on (ОТК edit). Every change goes to the audit journal with the screen before and after.
«Показать на ВАЦ» lives with the session (`vna_bench_api.show_screen`): it needs the operator's calibrated session.
Refusals are `detail: {code, message}` like the rest of the bench (frontend/src/vnaBenchApi.ts).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import Field, ValidationError
from sqlalchemy.exc import IntegrityError

from app.modules.production.domain import ProductionValidationError
from app.modules.production.vna_screen_infrastructure import (ScreenConflict, VnaScreenRepository, VnaScreenRow,
                                                              snapshot as _snapshot)
from app.modules.production.vna_screens import ScreenDef
from app.modules.production.vna_sessions import StrictModel
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, require_section
from app.shared.identity import Actor
from .vna_measurement_api import Factory, Now, Person

router = APIRouter(tags=["production"])
PREFIX = "/api/production/vna-bench/screens"
# A named gate of the access contract (tests/identity/test_level_gates.py DEPENDENCIES): ОТК reads the screens.
vna_screens_view_principal = require_section("quality", "view", detail="Раздел «ОТК» вам не открыт.")
Viewer = Annotated[Principal, Depends(vna_screens_view_principal)]
FIELDS = {"param": "параметр", "format": "формат", "scale": "шкала", "limit": "линия", "fixed": "своё число",
          "pdiv": "деление", "rlev": "уровень", "rpos": "позиция", "name": "название", "purpose": "назначение",
          "split": "расположение", "traces": "графики", "markers_hz": "маркеры", "note": "описание", "value": "число"}


class ScreenInput(ScreenDef):
    is_default: bool = False     # the bench draws this screen for its purpose


class ScreenSave(ScreenInput):
    version: int = Field(ge=1, strict=True)


class ActiveInput(StrictModel):
    version: int = Field(ge=1, strict=True)


def _number(value) -> str:
    text = f"{value:g}" if isinstance(value, float) else str(value)
    return text.replace(".", ",").replace("e+", "·10^").replace("-", "−")


def _russian(error: dict) -> str:
    """Pydantic's English bound messages in Russian; our own validators already speak Russian."""
    kind, ctx = error.get("type", ""), error.get("ctx") or {}
    bounds = {"greater_than": "должно быть больше {gt}", "greater_than_equal": "должно быть не меньше {ge}",
              "less_than": "должно быть меньше {lt}", "less_than_equal": "должно быть не больше {le}"}
    if kind in bounds:
        return bounds[kind].format(**{k: _number(v) for k, v in ctx.items()})
    if kind in ("too_long", "too_short"):
        limit = ctx.get("max_length") if kind == "too_long" else ctx.get("min_length")
        unit = "символов" if isinstance(error.get("input"), str) else "элементов"
        return f"{'не больше' if kind == 'too_long' else 'не меньше'} {limit} {unit}"
    if kind in ("string_too_long", "string_too_short"):
        limit = ctx.get("max_length") if kind == "string_too_long" else ctx.get("min_length")
        return f"{'не больше' if kind == 'string_too_long' else 'не меньше'} {limit} символов"
    if kind == "string_pattern_mismatch":
        return "пустое значение или служебные символы"
    if kind == "finite_number":
        return "нужно конечное число"
    if kind in ("literal_error", "missing", "extra_forbidden", "int_type", "int_from_float", "float_type", "float_parsing",
                "int_parsing", "bool_type", "bool_parsing", "string_type", "list_type", "model_type", "dict_type"):
        return "недопустимое значение"
    if kind == "value_error":
        return str(error.get("msg", "")).removeprefix("Value error, ")
    return "недопустимое значение"


def _message(error: ValidationError) -> str:
    """The first problem in Russian with where it is: «График 2, деление: должно быть больше 0»."""
    first = error.errors()[0]
    where = []
    location = list(first.get("loc", ()))
    if len(location) >= 2 and location[0] == "traces" and isinstance(location[1], int):
        where.append(f"График {location[1] + 1}")
        location = location[2:]
    elif len(location) >= 2 and location[0] == "markers_hz" and isinstance(location[1], int):
        where.append(f"Маркер {location[1] + 1}")
        location = location[2:]
    where += [FIELDS[p] for p in location if isinstance(p, str) and p in FIELDS][-1:]
    return ": ".join(filter(None, (", ".join(where), _russian(first)))) or "Экран не принят"


def _parsed(model, raw: Any):
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        raise HTTPException(422, {"code": "invalid", "message": _message(error)}) from error


@contextmanager
def _refusals():
    try:
        yield
    except HTTPException:
        raise
    except KeyError as error:
        raise HTTPException(404, {"code": "not_found", "message": str(error.args[0]) if error.args else "Не найдено"}) from error
    except ScreenConflict as error:
        raise HTTPException(409, {"code": "stale", "message": str(error)}) from error
    except IntegrityError as error:
        raise HTTPException(409, {"code": "stale", "message": "Экран изменили одновременно с вами — откройте его заново"}) from error
    except ProductionValidationError as error:
        raise HTTPException(422, {"code": "invalid", "message": str(error)}) from error


def view(row: VnaScreenRow) -> dict:
    return dict(id=str(row.id), name=row.name, purpose=row.purpose, is_default=row.is_default, split=row.split,
                traces=row.traces, markers_hz=row.markers_hz, note=row.note, active=row.active, version=row.version,
                updated_by=row.updated_by_name or None, updated_at=row.updated_at)


def _actor(principal: Principal) -> Actor:
    return Actor(id=principal.actor.id, name=principal.actor.name, department=principal.actor.department)


def _journal(uow, row: VnaScreenRow, event: str, principal, now, before: dict | None = None):
    """Every change of a screen, with the whole screen before and after (the journal is its version history)."""
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.id, event_type=f"production.vna_screen_{event}",
                             actor=_actor(principal), occurred_at=now,
                             details={"name": row.name, "before": before, "after": _snapshot(row)}))


def _journal_demoted(uow, repository: VnaScreenRepository, principal, now):
    for other, before in repository.demoted:
        _journal(uow, other, "default_moved", principal, now, before)


@router.get(PREFIX)
def list_screens(principal: Viewer, now: Now, factory: Factory):
    with _refusals(), factory() as uow:
        repository = VnaScreenRepository(uow.session)
        seeded = repository.ensure_seeded(now)
        rows = [view(row) for row in repository.all()]
        if seeded:
            uow.commit()
        return {"screens": rows}


@router.get(PREFIX + "/{screen_id}")
def get_screen(screen_id: UUID, principal: Viewer, factory: Factory):
    with _refusals(), factory() as uow:
        return view(VnaScreenRepository(uow.session).get(screen_id))


@router.post(PREFIX)
def create_screen(principal: Person, now: Now, factory: Factory, payload: Annotated[dict, Body()]):
    data = _parsed(ScreenInput, payload)
    spec = ScreenDef.model_validate(data.model_dump(exclude={"is_default"}))
    with _refusals(), factory() as uow:
        repository = VnaScreenRepository(uow.session)
        repository.ensure_seeded(now)
        row = repository.create(spec, data.is_default, _actor(principal), now)
        _journal(uow, row, "created", principal, now)
        _journal_demoted(uow, repository, principal, now)
        result = view(row)
        uow.commit()
        return result


@router.put(PREFIX + "/{screen_id}")
def save_screen(screen_id: UUID, principal: Person, now: Now, factory: Factory, payload: Annotated[dict, Body()]):
    """«Сохранить»: a new version of the screen; a stale `version` is refused (another tab or person saved first)."""
    data = _parsed(ScreenSave, payload)
    spec = ScreenDef.model_validate(data.model_dump(exclude={"is_default", "version"}))
    with _refusals(), factory() as uow:
        repository = VnaScreenRepository(uow.session)
        row = repository.get(screen_id)
        before = _snapshot(row)
        row = repository.update(row, data.version, spec, data.is_default, _actor(principal), now)
        _journal(uow, row, "saved", principal, now, before)
        _journal_demoted(uow, repository, principal, now)
        result = view(row)
        uow.commit()
        return result


def _set_active(screen_id: UUID, payload: ActiveInput, active: bool, principal, now, factory):
    with _refusals(), factory() as uow:
        repository = VnaScreenRepository(uow.session)
        row = repository.get(screen_id)
        before = _snapshot(row)
        row = repository.set_active(row, payload.version, active, _actor(principal), now)
        _journal(uow, row, "activated" if active else "deactivated", principal, now, before)
        result = view(row)
        uow.commit()
        return result


@router.post(PREFIX + "/{screen_id}/deactivate")
def deactivate_screen(screen_id: UUID, payload: ActiveInput, principal: Person, now: Now, factory: Factory):
    """«Отключить»: the screen leaves the lists of the bench; the default of a purpose cannot be switched off."""
    return _set_active(screen_id, payload, False, principal, now, factory)


@router.post(PREFIX + "/{screen_id}/activate")
def activate_screen(screen_id: UUID, payload: ActiveInput, principal: Person, now: Now, factory: Factory):
    return _set_active(screen_id, payload, True, principal, now, factory)

