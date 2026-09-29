"""Launch production from an issued techcard (ProdTasks.gs prodLaunchable_ / prodLaunch_, TcApi.gs tcRoutingFromConfig_)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from .domain import ProductionStatus, ProductionTask
from .plan import kit_stage_key


@dataclass(frozen=True)
class RouteStep:
    position: int
    code: str
    stage: str
    description: str
    seconds_per_unit: Decimal
    setup_seconds: Decimal


def item_id(project: str, index: str) -> str:
    """tcKitItemId_: the item of a kit position is project + index in upper case."""
    return str(project or "").strip() + str(index or "").strip().upper()


def _seconds(value: object) -> Decimal:
    try:
        number = Decimal(str(value if value is not None else "").replace(" ", "").replace(",", ".") or "0")
    except InvalidOperation:
        return Decimal(0)
    return number if number.is_finite() and number > 0 else Decimal(0)


def route_from_document(document: Mapping[str, Any] | None) -> tuple[RouteStep, ...]:
    """One route step per ETK operation, as tcRoutingFromConfig_ builds the «Техкарты» rows.

    The generator config (build_config.ops) is the SUPPLYMAP source of stage/code/times; the built ETK
    operation fills what the config lacks (a card code resolved from the operation library, a title)."""
    if not isinstance(document, Mapping):
        return ()
    built = [item for item in document.get("operations") or [] if isinstance(item, Mapping)]
    config = document.get("build_config")
    config_ops = [item for item in (config.get("ops") if isinstance(config, Mapping) else None) or [] if isinstance(item, Mapping)]
    count = len(config_ops) or len(built)
    steps: list[RouteStep] = []
    for index in range(count):
        op = config_ops[index] if index < len(config_ops) else {}
        etk = built[index] if index < len(built) else {}
        times = etk.get("times") if isinstance(etk.get("times"), Mapping) else {}
        stage = str(op.get("stage") or op.get("typeLabel") or etk.get("title") or op.get("type") or etk.get("type") or "").strip()
        stage = stage or f"Этап {index + 1}"
        code = str(op.get("opNumber") or op.get("code") or etk.get("code") or "").strip()
        side = str(op.get("side") or etk.get("side") or "").strip()
        description = str(op.get("descr") or op.get("title") or etk.get("title") or stage).strip()
        steps.append(RouteStep(
            position=index + 1,
            code=code,
            stage=stage,
            description=description + (f" (ст. {side})" if side else ""),
            seconds_per_unit=_seconds(op.get("tOp") if op else times.get("operation_seconds")),
            setup_seconds=_seconds(op.get("tPrep") if op else times.get("setup_seconds")),
        ))
    return tuple(steps)


def launched_quantity(tasks: Iterable[ProductionTask]) -> dict[str, int]:
    """«Уже запущено N шт»: quantities of non-cancelled tasks per kit position (kit_stage_key)."""
    result: dict[str, int] = {}
    for task in tasks:
        if task.status is ProductionStatus.CANCELLED:
            continue
        key = kit_stage_key(task.project, task.item)
        result[key] = result.get(key, 0) + task.quantity
    return result


def operation_codes(routes: Sequence[tuple[RouteStep, ...]], skill_codes: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    """prodOpCodes_: codes from issued routes plus codes already in the skills matrix, sorted by code."""
    seen: dict[str, str] = {}
    for route in routes:
        for step in route:
            if step.code and step.code not in seen:
                seen[step.code] = step.stage
    for code, name in skill_codes:
        if code and code not in seen:
            seen[code] = name
    return [{"code": code, "name": seen[code]} for code in sorted(seen)]
