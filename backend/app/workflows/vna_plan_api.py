"""Read-only composition of the pinned assembly, authoritative norm and RF registry."""

from dataclasses import replace
from datetime import datetime
from typing import Annotated, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.modules.production.api import production_uow_factory, techcards_uow_factory
from app.modules.production.autoplan_api import references_uow_factory
from app.modules.production.domain import ProductionValidationError
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.rf_interfaces import RfDetails, parse_interfaces
from app.modules.production.vna_planner import RfAsset, plan_connections
from app.modules.production.vna_profiles import derive_profile
from app.modules.production.vna_characterization import characterization_band
from app.modules.references.coax_vswr import cable_card, components_of, parse_number
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_overview_principal
from .coax_vswr_api import CoaxRefs, _task_etk, coax_config, task_norm

router = APIRouter(prefix="/api/production", tags=["production"])


def _dut_interfaces(coax, rows):
    result, problems = [], []
    for key, label in (("sideA", "А"), ("sideB", "Б")):
        article = str((coax.get(key) or {}).get("article") or "").strip().casefold()
        matching = [row for row in rows if article and str(row.get("coaxArticle") or row.get("Артикул") or "").strip().casefold() == article]
        candidates = set()
        for row in matching:
            ports = parse_interfaces(str(row.get("coaxType") or row.get("Тип/Серия") or ""))
            ohms, maximum = parse_number(row.get("Волновое сопротивление, Ом")), parse_number(row.get("Частота до, ГГц"))
            if len(ports) == 1 and ohms and maximum:
                candidates.add(replace(ports[0], impedance_ohm=ohms, f_max_ghz=maximum, f_confirmed=True,
                                       f_source="Справочник коаксиальных разъёмов"))
            else:
                candidates.add(None)
        if len(candidates) != 1 or None in candidates:
            problems.append(f"Сторона {label}: интерфейс, сопротивление или полоса разъёма не определены однозначно в справочнике")
        else:
            result.append(next(iter(candidates)))
    return tuple(result), problems


def _available(item, registry, now):
    seen = set()
    current = item
    while current is not None:
        if current.id in seen or current.status.value == "Не используется":
            return False
        seen.add(current.id)
        if current.id == item.id and current.kind.value == "Средство измерений":
            try:
                current.validate_for(now)
            except ProductionValidationError:
                return False
        if current.parent_id is None:
            return True
        current = registry.get(current.parent_id)
        if current is None:
            return False
    return False


@router.get("/vna-plan")
def vna_plan(task_id: UUID, operation_id: UUID, instrument_id: UUID,
             _principal: Annotated[Principal, Depends(production_overview_principal)],
             now: Annotated[datetime, Depends(moscow_now)],
             factory: Annotated[Callable, Depends(production_uow_factory)],
             techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)],
             refs_factory: Annotated[Callable | None, Depends(references_uow_factory)]):
    with factory() as uow:
        task = uow.production.get(task_id)
        if task is None:
            raise HTTPException(404, "Задание не найдено")
        if not any(operation.id == operation_id for operation in task.operations):
            raise HTTPException(404, "Операция не принадлежит заданию")
        registry = {item.id: item for item in uow.instruments.list()}
        if instrument_id not in registry:
            raise HTTPException(404, "ВАЦ не найден")
        repository = RfRepository(uow.session)
        details = repository.list()
        # A filename alone is insufficient: require two-port data, 50 ohms and complete band coverage.
        characterizations = {}
        candidates = [key for key, rf in details.items() if rf.role == "adapter" and rf.confirmed
                      and key in registry and registry[key].quantity == 1]
        for item_id, file_id, content in repository.characterization_files(candidates):
            characterized_band = characterization_band(content)
            if characterized_band:
                characterizations.setdefault(item_id, []).append((file_id, characterized_band))
    document, etk, current = _task_etk(techcards_factory, task)
    refs = CoaxRefs(refs_factory)
    norm = task_norm(document, current, etk.get("current_revision"), refs, now)
    coax = coax_config(document) or {}
    loaded = refs.get()
    rows, cards, _ = loaded or ((), (), ())
    component_norm = components_of(norm)
    cable = component_norm.get("cable") or norm.get("cable") or {}
    card = cable_card(cards, str(cable.get("name") or coax.get("cableArt") or coax.get("cableName") or ""))
    velocity_pct = card.params.get("velocity_pct") if card else None
    length = component_norm.get("lengthM") or norm.get("lengthM")
    rf = details.get(instrument_id, RfDetails())
    band = (rf.f_min_ghz, min(p.f_max_ghz for p in rf.interfaces)) if (
        rf.confirmed and rf.f_min_ghz is not None and len(rf.interfaces) == 2 and all(p.f_max_ghz is not None for p in rf.interfaces)) else None
    profile = derive_profile(norm, length_m=length, velocity_factor=float(velocity_pct) / 100 if velocity_pct else None,
                             max_points=rf.max_points if rf.confirmed else None, instrument_band_ghz=band)
    dut, problems = _dut_interfaces(coax, rows)
    if card is None or card.params.get("impedance_ohm") != 50:
        problems.append("Не подтверждено волновое сопротивление кабеля 50 Ом")
    assets = [RfAsset(item.id, f"{item.inventory_number} · {item.name}", details.get(item.id, RfDetails()),
                      item.quantity, _available(item, registry, now)) for item in registry.values()]
    instrument = next(a for a in assets if a.id == instrument_id)
    low, high = profile["band_mhz"]
    if not problems and low is not None and high is not None and 0 < low < high:
        assets = [replace(a, characterization_file_id=next((file_id for file_id, covered in characterizations.get(a.id, ())
                  if covered[0] <= low / 1000 and covered[1] >= high / 1000), None)) for a in assets]
        chains = plan_connections(instrument, dut, assets, (low / 1000, high / 1000))
    else:
        chains = dict(status="blocked", problems=problems or ["Не задана полоса нормы"], method=None,
                      calibration_connections=[], measurement_connections=[], calibration_plane=[], automatic_acceptance=False)
    status = "blocked" if profile["status"] == "blocked" or chains["status"] == "blocked" else chains["status"]
    if chains.get("mode") == "one_port_reversal":
        profile = {**profile, "traces": ["S11"], "required_orientations": ["А", "Б"], "one_attempt": True}
    return dict(status=status, label={"ready": "План подготовлен", "blocked": "Нужны исходные данные", "qualified": "Требуется согласование метода"}[status],
                task_id=str(task_id), operation_id=str(operation_id), instrument_id=str(instrument_id), etk=etk,
                profile=profile, chains=chains, automatic_acceptance=status == "ready")
