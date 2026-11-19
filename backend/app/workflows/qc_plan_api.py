"""Что проверяет ОТК у коробки задания (окно «Очереди ОТК», владелец 19.11: «очень слабо и неудобно»).

Только чтение, из того, что уже есть: ЭТК задания (закреплённая ревизия) — шаги «Контроль» её операций с допусками;
СТО ФРАК-001-2026 — типовые дефекты разделов этих операций (они же — список причин брака) и метод контроля каждого
дефекта; замеры КСВН задания и норма из ЭТК; реестр СИ — какие действующие СИ подходят к проверкам. Проверки — четыре
вида: внешний осмотр, размеры, КСВН, прозвонка; вид без единого источника не показывается.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.modules.production.api import production_uow_factory, techcards_uow_factory
from app.modules.production.autoplan_api import references_uow_factory
from app.modules.production.domain import ProductionValidationError
from app.modules.production.launchable import stage_name
from app.modules.production.traceability import InstrumentKind
from app.modules.production.vswr import VERDICT_NAMES, VswrMeasurement, VswrVerdict, list_vswr_for_task
from app.modules.techcards.etk_builder import VSWR_METHOD, vswr_in_sample
from app.modules.techcards.td_sto import sto_defects, sto_reference, sto_section_defects
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_overview_principal

from .coax_vswr_api import NO_ETK_REASON, CoaxRefs, _norm_result, _num, _task, _task_etk, coax_config, etk_control, task_norm, undefined_norm

router = APIRouter(prefix="/api/production/qc", tags=["production"])

CHECK_TITLES = {"visual": "Внешний осмотр", "dimensions": "Размеры", "vswr": "КСВН", "continuity": "Прозвонка"}
_VSWR = re.compile(r"КСВН|S11|S22", re.IGNORECASE)
_CONTINUITY = re.compile(r"прозвон|целостност|обрыв|замыкан|тестер|сопротивлени[ея] (?:цеп|изоляц)", re.IGNORECASE)
_DIMENSIONS = re.compile(r"\d\s*мм\b|±|длин|размер|штангенциркул|рулетк|линейк|микрометр|высот[аы] обжима", re.IGNORECASE)
_VISUAL_METHOD = re.compile(r"визуально|лупа", re.IGNORECASE)
_DIMENSION_METHOD = re.compile(r"штангенциркул|линейк|рулетк|микрометр", re.IGNORECASE)
_CONTINUITY_METHOD = re.compile(r"тестер", re.IGNORECASE)
_VSWR_METHOD = re.compile(r"ВАЦ", re.IGNORECASE)
# Измерительный инструмент из метода СТО или текста шага ЭТК → как узнать его в реестре СИ.
_TOOLS: tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...] = (
    ("Штангенциркуль", re.compile(r"штангенциркул", re.IGNORECASE), re.compile(r"штангенциркул|ШЦ", re.IGNORECASE)),
    ("Микрометр", re.compile(r"микрометр", re.IGNORECASE), re.compile(r"микрометр", re.IGNORECASE)),
    ("Линейка", re.compile(r"линейк", re.IGNORECASE), re.compile(r"линейк", re.IGNORECASE)),
    ("Рулетка", re.compile(r"рулетк", re.IGNORECASE), re.compile(r"рулетк", re.IGNORECASE)),
)
_TESTER = re.compile(r"тестер|мультиметр|прозвон", re.IGNORECASE)
_ANALYSER = re.compile(r"анализатор цепей|ВАЦ", re.IGNORECASE)


def check_kind(operation_type: str, text: str, tolerance: str) -> str:
    """Вид проверки шага «Контроль» ЭТК: КСВН, прозвонка, размеры, иначе — внешний осмотр."""
    both = f"{text} {tolerance}"
    if operation_type == "coaxVswr" or _VSWR.search(both):
        return "vswr"
    if _CONTINUITY.search(both):
        return "continuity"
    if _DIMENSIONS.search(both):
        return "dimensions"
    return "visual"


def _operation_title(operation: Mapping[str, Any]) -> str:
    card = operation.get("card") if isinstance(operation.get("card"), Mapping) else {}
    title = str(operation.get("title") or card.get("name") or "").strip() or stage_name(str(operation.get("type") or ""))
    position = operation.get("position")
    return f"{position}. {title}" if position not in (None, "") else title


def _control_steps(operations: list[Mapping[str, Any]]) -> dict[str, list[dict[str, str]]]:
    found: dict[str, list[dict[str, str]]] = {kind: [] for kind in CHECK_TITLES}
    for operation in operations:
        card = operation.get("card") if isinstance(operation.get("card"), Mapping) else {}
        for step in card.get("steps") or ():
            if not isinstance(step, Mapping) or str(step.get("kind") or "") != "Контроль":
                continue
            text, tolerance = str(step.get("text") or "").strip(), str(step.get("tolerance") or "").strip()
            kind = check_kind(str(operation.get("type") or ""), text, tolerance)
            found[kind].append({"operation": _operation_title(operation), "text": text, "tolerance": tolerance})
    return found


def _sections(rows: list[dict[str, str]], method: re.Pattern[str]) -> list[str]:
    return list(dict.fromkeys(row["section"] for row in rows if method.search(row["method"])))


def _methods(rows: list[dict[str, str]], method: re.Pattern[str]) -> list[str]:
    """Методы контроля СТО одного вида, без уточнений в скобках и без повторов: «Визуально», «Лупа 4×»."""
    found: dict[str, str] = {}
    for row in rows:
        for part in re.split(r"[;/,]", row["method"]):
            part = re.sub(r"\s*\([^)]*\)", "", part).strip()
            if part and method.search(part):
                found.setdefault(part.casefold(), part[:1].upper() + part[1:])
    return list(found.values())


def _sto_ref(sections: list[str]) -> str:
    sto = sto_reference()["designation"]
    return f"{sto}, разд. {', '.join(sections)}" if sections else sto


def _vswr_progress(uow_factory, task, control: Mapping[str, Any], norm: Mapping[str, Any]) -> dict[str, Any]:
    latest: dict[UUID, VswrMeasurement] = {}
    for item in list_vswr_for_task(task.id, uow_factory()):
        latest[item.serial_id] = item
    units = []
    measured = 0
    required = [position for position in range(1, task.quantity + 1) if vswr_in_sample(control, position)]
    with uow_factory() as uow:
        for position in range(1, task.quantity + 1):
            serial = uow.serials.by_position(task.id, position)
            item = latest.get(serial.id) if serial is not None else None
            if item is None:
                continue
            measured += position in required
            units.append({"position": position, "serial_number": serial.serial_number, "verdict": item.verdict.value,
                          "verdict_name": VERDICT_NAMES[item.verdict], "max_vswr": _num(item.max_vswr),
                          "instrument_id": str(item.instrument_id)})
    peaks = [unit["max_vswr"] for unit in units if unit["max_vswr"] is not None]
    shown = _norm_result(norm)
    return {
        "mode": control["mode"], "every": control.get("every"), "total": len(required), "measured": measured,
        "failed": sum(unit["verdict"] == VswrVerdict.FAIL.value for unit in units),
        "max_vswr": max(peaks) if peaks else None, "norm_vswr_max": shown["vswr_max"], "band_mhz": shown["band_mhz"],
        "norm_status": shown["status"], "norm_reason": shown["reason"], "units": units,
    }


def _usable(registry, now: datetime) -> list:
    usable = []
    for item in registry:
        if item.kind is not InstrumentKind.MEASURING or item.status.value != "Действует":
            continue
        try:
            item.validate_for(now)
        except ProductionValidationError:
            continue
        usable.append(item)
    return sorted(usable, key=lambda item: item.inventory_number)


def _instrument(item, purpose: str) -> dict[str, str]:
    return {"id": str(item.id), "inventory_number": item.inventory_number, "name": item.name, "purpose": purpose}


@router.get("/plan/{task_id}")
def qc_plan(
    task_id: UUID,
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], Any], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    refs_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
) -> dict:
    """Проверки ОТК задания по ЭТК и СТО, нужные СИ из реестра, ход замеров КСВН и причины брака по СТО."""
    task = _task(uow_factory, task_id)
    document, etk, current = _task_etk(techcards_factory, task)
    operations = [operation for operation in document.get("operations") or () if isinstance(operation, Mapping)]
    defects = sto_defects([str(operation.get("code") or "") for operation in operations])
    steps = _control_steps(operations)
    coax = coax_config(document) is not None
    vswr = None
    if coax:
        norm = (task_norm(document, current, etk["current_revision"], CoaxRefs(refs_factory), now) if etk["techcard_id"]
                else undefined_norm(NO_ETK_REASON))
        vswr = _vswr_progress(uow_factory, task, etk_control(document, norm), norm)
        # КСВН и затухание вне нормы (СТО 4.8) — причины брака любой коаксиальной сборки, даже без операций раздела 4.8.
        known = {row["code"] for row in defects}
        defects += [row for row in sto_section_defects("4.8") if _VSWR_METHOD.search(row["method"]) and row["code"] not in known]

    checks: list[dict[str, Any]] = []
    visual = _sections(defects, _VISUAL_METHOD)
    if steps["visual"] or visual:
        checks.append({"kind": "visual", "title": CHECK_TITLES["visual"], "method": _sto_ref(visual),
                       "tools": _methods(defects, _VISUAL_METHOD), "items": steps["visual"]})
    dimension_rows = _sections(defects, _DIMENSION_METHOD)
    if steps["dimensions"] or dimension_rows:
        checks.append({"kind": "dimensions", "title": CHECK_TITLES["dimensions"], "method": _sto_ref(dimension_rows),
                       "tools": _methods(defects, _DIMENSION_METHOD), "items": steps["dimensions"]})
    if vswr is not None or steps["vswr"]:
        none = vswr is not None and vswr["mode"] == "none"
        checks.append({"kind": "vswr", "title": CHECK_TITLES["vswr"], "required": not none,
                       "method": f"{VSWR_METHOD}; {_sto_ref(_sections(defects, _VSWR_METHOD) or ['4.8'])}",
                       "tools": ["ВАЦ (S11, S22)"], "items": steps["vswr"]})
    continuity = _sections(defects, _CONTINUITY_METHOD)
    if steps["continuity"] or continuity:
        checks.append({"kind": "continuity", "title": CHECK_TITLES["continuity"], "method": _sto_ref(continuity),
                       "tools": _methods(defects, _CONTINUITY_METHOD) or ["Тестер цепей"], "items": steps["continuity"]})

    with uow_factory() as uow:
        registry = list(uow.instruments.list())
    usable = _usable(registry, now)
    picked: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []

    def pick(tool: str, purpose: str, pattern: re.Pattern[str]) -> None:
        found = next((item for item in usable if pattern.search(f"{item.name} {item.instrument_type}")), None)
        if found is None:
            missing.append({"tool": tool, "purpose": purpose})
        elif all(entry["id"] != str(found.id) for entry in picked):
            picked.append(_instrument(found, purpose))

    kinds = {check["kind"] for check in checks if check.get("required", True)}
    if "dimensions" in kinds:
        # Инструмент — из текстов «Контроль» ЭТК, иначе из методов СТО; без указаний — штангенциркуль.
        said = " ".join(f"{item['text']} {item['tolerance']}" for item in steps["dimensions"])
        tools = [entry for entry in _TOOLS if entry[1].search(said)]
        if not tools:
            methods = " ".join(next(check["tools"] for check in checks if check["kind"] == "dimensions"))
            tools = [entry for entry in _TOOLS if entry[1].search(methods)][:1] or [_TOOLS[0]]
        for tool, _said, registry_pattern in tools:
            pick(tool, CHECK_TITLES["dimensions"], registry_pattern)
    if "vswr" in kinds:
        used = [unit["instrument_id"] for unit in (vswr or {}).get("units", [])]
        by_id = {str(item.id): item for item in usable}
        analysers = [by_id[item_id] for item_id in dict.fromkeys(used) if item_id in by_id]
        for item in analysers:
            picked.append(_instrument(item, CHECK_TITLES["vswr"]))
        if not analysers:
            pick("ВАЦ", CHECK_TITLES["vswr"], _ANALYSER)
    if "continuity" in kinds:
        pick("Тестер цепей", CHECK_TITLES["continuity"], _TESTER)

    return {
        "task": {"id": str(task.id), "number": task.number, "project": task.project, "item": task.item,
                 "name": task.name, "quantity": task.quantity},
        "etk": {"item_index": etk["item_index"], "product_name": etk["product_name"], "revision": etk["revision"],
                "current_revision": etk["current_revision"]},
        "checks": checks,
        "instruments": picked,
        "missing_instruments": missing,
        "vswr": vswr,
        "defects": [{"code": row["code"], "section": row["section"], "defect": row["defect"],
                     "criterion": row["criterion"], "class": row["class"]} for row in defects],
        "sto": sto_reference(),
    }
