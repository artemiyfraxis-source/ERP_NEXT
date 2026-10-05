"""Launch production from an issued techcard (ProdTasks.gs prodLaunchable_ / prodLaunch_, TcApi.gs tcRoutingFromConfig_)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from app.modules.techcards.etk_builder import operation_norms

from .domain import ProductionOperation, ProductionStatus, ProductionTask, ProductionValidationError, link_inputs
from .plan import kit_stage_key


@dataclass(frozen=True)
class BlockInput:
    """A predecessor block feeding this one: «per_unit» pieces of its semi-finished product per one output."""

    block: str
    per_unit: Decimal


@dataclass(frozen=True)
class RouteStep:
    position: int
    code: str
    stage: str
    description: str
    seconds_per_unit: Decimal
    setup_seconds: Decimal
    # Схема полуфабрикатов (ARCH-TRACK-1): блок шага и его входы. Только данные: цех по-прежнему идёт по position.
    # Шаги одного блока делят block/block_name/final/inputs; inputs — блоки-предшественники, не шаги.
    block: str = ""
    block_name: str = ""
    final: bool = False
    inputs: tuple[BlockInput, ...] = ()


# Типы операций генератора ТК (tcGenLogic ops[].type) по-русски. Конфиг генератора не пишет stage/typeLabel, поэтому
# маршрут (tcRoutingFromConfig_) падал на служебный type — cutWireStrip, prsTerm… Мастеру нужны названия
# (владелец, 30.09). Словарь чинит и уже запущенные задания при выдаче: в базе остаётся как было.
OPERATION_TYPE_NAMES: dict[str, str] = {
    "cutWire": "Резка", "cutWireStrip": "Резка с разделкой", "cutWireCrimp": "Резка с обжимом",
    "cutTut": "Резка ТУТ", "prsTerm": "Опрессовка терминалов", "prsTermA": "Опрессовка терминалов (А)",
    "prsTermB": "Опрессовка терминалов (В)", "insTerm": "Монтаж терминалов", "insTermA": "Монтаж терминалов (А)",
    "insTermB": "Монтаж терминалов (В)", "insHousing": "Монтаж в корпус", "insTut": "Надеть ТУТ",
    "heatTut": "Усадка ТУТ", "insWrap": "Обмотка", "insMark": "Маркировка", "tin": "Лужение", "twist": "Свивка",
    "splice": "Сращивание", "seal": "Герметизация", "solderConn": "Пайка разъёма", "stripCable": "Разделка кабеля",
    "foldCore": "Загибка жил", "endCap": "Установка заглушек", "coaxCut": "Резка кабеля", "coaxCutTut": "Резка ТУТ",
    "coaxStrip": "Разделка коаксиала", "coaxShield": "Опрессовка втулки экрана", "coaxSolderShield": "Пайка экрана",
    "coaxPin": "Пайка центрального контакта", "coaxCapCrimp": "Монтаж крышки (обжим)",
    "coaxCapScrew": "Монтаж крышки (винт)", "coaxHousing": "Монтаж корпуса", "coaxInsSleeve": "Монтаж втулки экрана",
    "coaxInsTut": "Монтаж ТУТ", "coaxHeat": "Усадка ТУТ",
}
_NO_CODE = re.compile(r"^\s*без\s+кода\s*$", re.IGNORECASE)


_CODE_PREFIX = re.compile(r"^\s*([^|\s]+)\s*\|\s*(.+)$")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def stage_name(value: str) -> str:
    """Русское название этапа: служебный тип генератора → название; «CUT_WIRE+1crimp | Автомат резка» → название
    без кода (владелец, 30.09: в ячейках часов служебный код вместо названия; код показывается отдельно)."""
    text = str(value or "").strip()
    prefixed = _CODE_PREFIX.match(text)
    if prefixed and not _CYRILLIC.search(prefixed.group(1)):
        text = prefixed.group(2).strip()
    return OPERATION_TYPE_NAMES.get(text, text)


def operation_code(value: str) -> str:
    """Код операции для экрана: заглушка «БЕЗ КОДА» — это отсутствие кода."""
    text = str(value or "").strip()
    return "" if _NO_CODE.match(text) else text


def item_id(project: str, index: str) -> str:
    """tcKitItemId_: the item of a kit position is project + index in upper case."""
    return str(project or "").strip() + str(index or "").strip().upper()


def _seconds(value: object) -> Decimal:
    try:
        number = Decimal(str(value if value is not None else "").replace(" ", "").replace(",", ".") or "0")
    except InvalidOperation:
        return Decimal(0)
    return number if number.is_finite() and number > 0 else Decimal(0)


@dataclass(frozen=True)
class _Block:
    key: str
    name: str  # "" — назвать по этапу шага
    final: bool
    inputs: tuple[BlockInput, ...]


def _per_unit(value: object) -> Decimal:
    """Edge multiplier set by hand on the scheme; anything unreadable or non-positive is the default 1."""
    try:
        number = Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return Decimal(1)
    return number if number.is_finite() and number > 0 else Decimal(1)


def _derived_blocks(count: int) -> list[_Block]:
    """A card without document.route: one block per step in a linear chain, each fed by the previous one."""
    return [
        _Block(f"b{index + 1}", "", index == count - 1, (BlockInput(f"b{index}", Decimal(1)),) if index else ())
        for index in range(count)
    ]


_OP_KEY_FIELDS = ("key", "opKey", "id")


def _keyed_steps(count: int, op_keys: Sequence[Iterable[object]]) -> dict[str, int]:
    """Operation's own key (`key`/`opKey`/`id`) → step index. A key that names two steps is ambiguous (-1) and
    invalidates any block that cites it."""
    result: dict[str, int] = {}
    for index in range(count):
        for name in {str(name).strip() for name in op_keys[index] if name is not None and str(name).strip()}:
            result[name] = -1 if result.get(name, index) != index else index
    return result


def _step_of(value: object, count: int, keyed: Mapping[str, int]) -> int:
    """The step a block's `ops` entry names, -1 — none. The ETK numbers a block's operations by position (the scheme
    editor, the ETK builder and «Ручная сборка» all write 1-based positions), whatever keys the operations carry
    (generator ops keep their `opKey`); an entry that is no position of these steps is looked up as an op key."""
    text = str(value).strip() if value is not None and not isinstance(value, bool) else ""
    try:
        number = Decimal(text)
    except InvalidOperation:
        number = None
    if number is not None and number.is_finite() and number == number.to_integral_value() and 1 <= number <= count:
        return int(number) - 1
    return keyed.get(text, -1)


def _route_blocks(route: object, count: int, op_keys: Sequence[Iterable[object]]) -> tuple[list[_Block] | None, str]:
    """Per-step block data from ETK document.route {blocks:[{key,name,ops,final}], edges:[{from,to,per_unit}]}, or
    None and why (Russian) when the scheme cannot be applied to these steps exactly (unknown or doubled op, step left
    in no block, duplicate block key, edge to an unknown block): production then falls back to the linear chain."""
    if not count:
        return None, "в техкарте нет операций"
    if not isinstance(route, Mapping) or not isinstance(route.get("blocks"), list) or not route["blocks"]:
        return None, "в схеме нет блоков"
    keyed = _keyed_steps(count, op_keys)
    blocks: dict[str, Mapping[str, Any]] = {}
    owner: dict[int, str] = {}
    for number, block in enumerate(route["blocks"], start=1):
        key = str(block.get("key") or "").strip() if isinstance(block, Mapping) else ""
        if not key:
            return None, f"у блока №{number} нет ключа"
        if key in blocks:
            return None, f"ключ блока «{key}» повторяется"
        if not isinstance(block.get("ops"), list):
            return None, f"у блока «{key}» нет списка операций"
        blocks[key] = block
        for op_key in block["ops"]:
            index = _step_of(op_key, count, keyed)
            if index < 0:
                return None, f"операции «{op_key}» нет в техкарте (операций {count})"
            if index in owner:
                return None, f"операция {index + 1} входит в два блока — «{owner[index]}» и «{key}»"
            owner[index] = key
    missing = [str(index + 1) for index in range(count) if index not in owner]
    if missing:
        return None, (f"операция {missing[0]} не входит" if len(missing) == 1 else f"операции {', '.join(missing)} не входят") + " ни в один блок"
    incoming: dict[str, list[BlockInput]] = {key: [] for key in blocks}
    for edge in route.get("edges") or []:
        source, target = (str(edge.get("from") or "").strip(), str(edge.get("to") or "").strip()) if isinstance(edge, Mapping) else ("", "")
        if source not in blocks or target not in blocks:
            return None, f"стрелка «{source or '?'}» → «{target or '?'}» ведёт в блок, которого нет в схеме"
        incoming[target].append(BlockInput(source, _per_unit(edge.get("per_unit"))))
    return [
        _Block(owner[index], str(blocks[owner[index]].get("name") or "").strip(), bool(blocks[owner[index]].get("final")),
               tuple(incoming[owner[index]]))
        for index in range(count)
    ], ""


_MICRO = Decimal("0.000001")  # production_operations.seconds_per_unit is Numeric(18, 6)


def _step_seconds(norm_minutes: Decimal | None, batch: Decimal, op_seconds: Decimal, setup: Decimal) -> tuple[Decimal, Decimal]:
    """(seconds per unit, setup seconds) of a step. With the ETK's norm of the operation (minutes per batch) the plan
    counts exactly what the ETK shows: setup = the card's tPrep, per unit = (norm − setup) / batch, so the task of the
    ETK's batch takes the ETK norm (a setup larger than the whole norm is cut to it). Without one — the card's tOp."""
    if norm_minutes is None:
        return op_seconds, setup
    total = norm_minutes * 60
    setup = min(setup, total)
    return ((total - setup) / batch).quantize(_MICRO, rounding=ROUND_HALF_UP), setup


@dataclass(frozen=True)
class LaunchRoute:
    """The route a task is launched by and the warnings for the master (the scheme did not fit — chain instead)."""

    steps: tuple[RouteStep, ...]
    warnings: tuple[str, ...] = ()


def launch_route(document: Mapping[str, Any] | None) -> LaunchRoute:
    """One route step per ETK operation, as tcRoutingFromConfig_ builds the «Техкарты» rows.

    The generator config (build_config.ops) is the SUPPLYMAP source of stage/code; the built ETK operation fills what
    the config lacks (a card code resolved from the operation library, a title). Times are the ETK's own: its norm of
    the operation (time rows: wires × cores × batch, «на изделие» of «Ручная сборка»…) split into the card's setup and
    seconds per unit — one source of truth with what the ETK and its block scheme show.

    Each step also carries its block of the semi-finished scheme (block, block_name, final, inputs) — from
    a hand-drawn document.route when the card has one, otherwise a linear chain derived from the step order. The block
    data is extra: the steps, their order and positions are the same with or without a scheme. A hand-drawn scheme
    that does not fit the operations does not stop the launch: the chain goes instead, with a warning."""
    if not isinstance(document, Mapping):
        return LaunchRoute(())
    built = [item for item in document.get("operations") or [] if isinstance(item, Mapping)]
    config = document.get("build_config")
    config_ops = [item for item in (config.get("ops") if isinstance(config, Mapping) else None) or [] if isinstance(item, Mapping)]
    count = len(config_ops) or len(built)
    op_keys = [
        [(config_ops[index] if index < len(config_ops) else {}).get(name) for name in _OP_KEY_FIELDS]
        + [(built[index] if index < len(built) else {}).get(name) for name in _OP_KEY_FIELDS]
        for index in range(count)
    ]
    route = document.get("route")
    # The shop follows the block network only once the technologist has drawn it (source «manual»); the scheme the
    # generator derives by itself is a hint on the ETK, and production keeps today's chain for it.
    if isinstance(route, Mapping) and route.get("source") == "derived":
        route = None
    blocks, problem = _route_blocks(route, count, op_keys) if route and count else (None, "")
    warnings: tuple[str, ...] = ()
    if blocks is None and problem:
        warnings = (f"Схема полуфабрикатов техкарты не подошла к её операциям ({problem}) — этапы задания идут "
                    f"цепочкой по порядку операций. Поправьте схему в техкарте и выпустите её заново.",)
    blocks = blocks or _derived_blocks(count)
    batch, norms = operation_norms(document)
    steps: list[RouteStep] = []
    for index in range(count):
        op = config_ops[index] if index < len(config_ops) else {}
        etk = built[index] if index < len(built) else {}
        times = etk.get("times") if isinstance(etk.get("times"), Mapping) else {}
        stage = str(op.get("stage") or op.get("typeLabel") or op.get("label") or etk.get("title") or "").strip()
        stage = stage or stage_name(str(op.get("type") or etk.get("type") or ""))
        stage = stage or f"Этап {index + 1}"
        code = operation_code(str(op.get("opNumber") or op.get("code") or etk.get("code") or ""))
        side = str(op.get("side") or etk.get("side") or "").strip()
        description = str(op.get("descr") or op.get("title") or etk.get("title") or stage).strip()
        seconds_per_unit, setup_seconds = _step_seconds(
            norms[index] if index < len(norms) else None, batch,
            _seconds(op.get("tOp") if op else times.get("operation_seconds")),
            _seconds(op.get("tPrep") if op else times.get("setup_seconds")),
        )
        steps.append(RouteStep(
            position=index + 1,
            code=code,
            stage=stage,
            description=description + (f" (ст. {side})" if side else ""),
            seconds_per_unit=seconds_per_unit,
            setup_seconds=setup_seconds,
            block=blocks[index].key,
            block_name=blocks[index].name or stage,
            final=blocks[index].final,
            inputs=blocks[index].inputs,
        ))
    return LaunchRoute(tuple(steps), warnings)


def route_from_document(document: Mapping[str, Any] | None) -> tuple[RouteStep, ...]:
    """The steps of `launch_route` (screens and planners that do not show its warnings)."""
    return launch_route(document).steps


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


def _step_block(step: RouteStep) -> str:
    # Блок шага схемы полуфабрикатов (ARCH-TRACK-1, A2). Маршрут без этих полей — старая цепочка по номерам.
    return str(getattr(step, "block", "") or "").strip()


def route_inputs(route: Sequence[RouteStep]) -> dict[int, tuple[tuple[int, Decimal], ...]]:
    """Входы шагов маршрута по номерам: {номер шага: ((номер шага-входа, штук на одну), …)}.

    Внутри блока схемы шаги идут цепочкой по номерам; первый шаг блока берёт последний шаг каждого блока-
    предшественника (per_unit ребра схемы). Шаг без блока — маршрут без схемы: предыдущий номер, 1 на 1."""
    ordered = sorted(route, key=lambda step: step.position)
    if any(not _step_block(step) for step in ordered):
        return {step.position: ((earlier.position, Decimal(1)),) for earlier, step in zip(ordered, ordered[1:])}
    members: dict[str, list[int]] = {}
    for step in ordered:
        members.setdefault(_step_block(step), []).append(step.position)
    result: dict[int, tuple[tuple[int, Decimal], ...]] = {}
    for step in ordered:
        own = members[_step_block(step)]
        index = own.index(step.position)
        if index:
            result[step.position] = ((own[index - 1], Decimal(1)),)
            continue
        edges: list[tuple[int, Decimal]] = []
        for item in getattr(step, "inputs", ()) or ():
            block = str(getattr(item, "block", "") or "").strip()
            if block not in members:
                raise ProductionValidationError(f"Схема полуфабрикатов: блок «{block}» не найден в маршруте")
            edges.append((members[block][-1], Decimal(getattr(item, "per_unit", 1) or 1)))
        if edges:
            result[step.position] = tuple(edges)
    return result


def launch_operations(route: Sequence[RouteStep], quantity: int) -> tuple[ProductionOperation, ...]:
    """Операции нового задания по маршруту выданной техкарты: блок схемы и явные входы (сетевой график)."""
    operations = tuple(ProductionOperation.create(
        position=step.position, code=step.code, stage=step.stage, description=step.description,
        seconds_per_unit=step.seconds_per_unit, setup_seconds=step.setup_seconds, quantity=quantity,
        stage_key=_step_block(step) or None,
    ) for step in route)
    return link_inputs(operations, route_inputs(route))
