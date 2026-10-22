"""Launch production from an issued techcard (ProdTasks.gs prodLaunchable_ / prodLaunch_, TcApi.gs tcRoutingFromConfig_)."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from datetime import date
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from app.modules.techcards.etk_builder import norms_include_setup, operation_norms

from .domain import (
    EtkOperation, ProductionOperation, ProductionStatus, ProductionTask, ProductionValidationError, link_inputs,
    operation_code_key,
)
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
    # Задание по ПФ (13.10): блок нарисован технологом (ручная схема) — номер блока в схеме (номер ПФ, с 1) и место
    # шага в порядке операций блока (порядок `block.ops` = порядок внутри ПФ, с 0). Схемы нет — manual False.
    manual: bool = False
    block_number: int = 0
    block_place: int = 0
    # Шаг-ПФ (semifinished_steps): операции ЭТК этого полуфабриката по порядку; у шага-операции ЭТК пусто.
    etk_ops: tuple[EtkOperation, ...] = ()


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
    "coaxPin": "Пайка центрального контакта", "coaxPinCrimp": "Опрессовка центрального контакта", "coaxCapCrimp": "Монтаж крышки (обжим)",
    "coaxCapScrew": "Монтаж крышки (винт)", "coaxHousing": "Монтаж корпуса", "coaxInsSleeve": "Монтаж втулки экрана",
    "coaxInsTut": "Монтаж ТУТ", "coaxHeat": "Усадка ТУТ",
    "coaxClampNut": "Надеть гайку и шайбу (уплотнитель)", "coaxClampBraid": "Оплётка по зажимному кольцу",
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
    number: int = 0  # номер блока в нарисованной схеме (номер ПФ); 0 — цепочка без схемы
    order: tuple[int, ...] = ()  # шаги блока (индексы) в порядке `block.ops`


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


def _kit_shares(links: list[tuple[str, str, Decimal]], kits: set[str]) -> list[tuple[str, str, Decimal]]:
    """A kit block (route `kit`, «Сложная сборка» 16.10: cut batches, tube cut, braid cut, marking — parts one ПФ
    prepares for several others) is made once per product, not once per consumer: unit_factors sums per_unit over the
    consumers, so a cut feeding 7 ПФ was planned ×7. Its arrows' per_unit become shares of one kit — Σ per_unit × the
    consumer's pieces per product = 1. Shares are cut to the 6 places a task stores; when every consumer is one piece
    per product they add up to exactly 1 (the remainder goes to the first arrows). Other arrows stay as drawn."""
    if not kits or not links:
        return links
    outgoing: dict[str, list[tuple[str, Decimal]]] = {}
    for source, target, per_unit in links:
        outgoing.setdefault(source, []).append((target, per_unit))
    factors: dict[str, Decimal] = {}

    def factor(key: str, path: frozenset[str]) -> Decimal:
        if key in kits:
            return Decimal(1)
        if key not in factors:
            targets = [(target, per_unit) for target, per_unit in outgoing.get(key, ()) if target not in path]
            factors[key] = sum(
                (per_unit * factor(target, path | {key}) for target, per_unit in targets), Decimal(0),
            ) if targets else Decimal(1)
        return factors[key]

    shares: dict[tuple[str, str], Decimal] = {}
    for kit in kits:
        own = [(target, per_unit, factor(target, frozenset({kit}))) for target, per_unit in outgoing.get(kit, ())]
        if not own:
            continue
        total = sum((per_unit * pieces for _, per_unit, pieces in own), Decimal(0))
        exact = [per_unit / total for _, per_unit, _ in own]
        if all(pieces == 1 for *_, pieces in own):
            micro = [(value / _MICRO).to_integral_value(rounding=ROUND_FLOOR) for value in exact]
            left = int(Decimal(1) / _MICRO - sum(micro, Decimal(0)))
            for index in sorted(range(len(own)), key=lambda index: micro[index] - exact[index] / _MICRO)[:left]:
                micro[index] += 1
            values = [value * _MICRO for value in micro]
        else:
            values = [value.quantize(_MICRO, rounding=ROUND_HALF_UP) for value in exact]
        for (target, _, _), value in zip(own, values):
            shares[(kit, target)] = max(value, _MICRO)
    return [(source, target, shares.get((source, target), per_unit)) for source, target, per_unit in links]


def _route_blocks(route: object, count: int, op_keys: Sequence[Iterable[object]]) -> tuple[list[_Block] | None, str]:
    """Per-step block data from ETK document.route {blocks:[{key,name,ops,final}], edges:[{from,to,per_unit}]}, or
    None and why (Russian) when the scheme cannot be applied to these steps exactly (unknown or doubled op, step left
    in no block, duplicate block key, edge to an unknown block): production then falls back to the linear chain.
    The arrows out of a kit block carry its shares (`_kit_shares`), a block may feed several others."""
    if not count:
        return None, "в техкарте нет операций"
    if not isinstance(route, Mapping) or not isinstance(route.get("blocks"), list) or not route["blocks"]:
        return None, "в схеме нет блоков"
    keyed = _keyed_steps(count, op_keys)
    blocks: dict[str, Mapping[str, Any]] = {}
    owner: dict[int, str] = {}
    numbers: dict[str, int] = {}
    orders: dict[str, list[int]] = {}
    for number, block in enumerate(route["blocks"], start=1):
        key = str(block.get("key") or "").strip() if isinstance(block, Mapping) else ""
        if not key:
            return None, f"у блока №{number} нет ключа"
        if key in blocks:
            return None, f"ключ блока «{key}» повторяется"
        if not isinstance(block.get("ops"), list):
            return None, f"у блока «{key}» нет списка операций"
        blocks[key] = block
        numbers[key] = number
        orders[key] = []
        for op_key in block["ops"]:
            index = _step_of(op_key, count, keyed)
            if index < 0:
                return None, f"операции «{op_key}» нет в техкарте (операций {count})"
            if index in owner:
                return None, f"операция {index + 1} входит в два блока — «{owner[index]}» и «{key}»"
            owner[index] = key
            orders[key].append(index)
    missing = [str(index + 1) for index in range(count) if index not in owner]
    if missing:
        return None, (f"операция {missing[0]} не входит" if len(missing) == 1 else f"операции {', '.join(missing)} не входят") + " ни в один блок"
    incoming: dict[str, list[BlockInput]] = {key: [] for key in blocks}
    links: list[tuple[str, str, Decimal]] = []
    for edge in route.get("edges") or []:
        source, target = (str(edge.get("from") or "").strip(), str(edge.get("to") or "").strip()) if isinstance(edge, Mapping) else ("", "")
        if source not in blocks or target not in blocks:
            return None, f"стрелка «{source or '?'}» → «{target or '?'}» ведёт в блок, которого нет в схеме"
        links.append((source, target, _per_unit(edge.get("per_unit"))))
    for source, target, per_unit in _kit_shares(links, {key for key, block in blocks.items() if block.get("kit") is True}):
        incoming[target].append(BlockInput(source, per_unit))
    return [
        _Block(owner[index], str(blocks[owner[index]].get("name") or "").strip(), bool(blocks[owner[index]].get("final")),
               tuple(incoming[owner[index]]), numbers[owner[index]], tuple(orders[owner[index]]))
        for index in range(count)
    ], ""


_MICRO = Decimal("0.000001")  # production_operations.seconds_per_unit is Numeric(18, 6)


def _step_seconds(
    norm_minutes: Decimal | None, batch: Decimal, op_seconds: Decimal, setup: Decimal, *, setup_in_norm: bool,
) -> tuple[Decimal, Decimal]:
    """(seconds per unit, setup seconds) of a step. The plan counts no setup (owner 18.10: the operator starts a task
    only once everything is ready), so the setup is always 0. With the ETK's norm of the operation (minutes per batch)
    per unit = norm / batch; an ETK built before ETK_VERSION 4 has the card's tPrep inside the norm, so it is taken out
    first (a setup larger than the whole norm is cut to it). Without a norm — the card's tOp."""
    if norm_minutes is None:
        return op_seconds, Decimal(0)
    total = norm_minutes * 60
    if setup_in_norm:
        total -= min(setup, total)
    return (total / batch).quantize(_MICRO, rounding=ROUND_HALF_UP), Decimal(0)


@dataclass(frozen=True)
class LaunchRoute:
    """The route a task is launched by and the warnings for the master (the scheme did not fit — chain instead)."""

    steps: tuple[RouteStep, ...]
    warnings: tuple[str, ...] = ()


def launch_route(document: Mapping[str, Any] | None) -> LaunchRoute:
    """One route step per ETK operation, as tcRoutingFromConfig_ builds the «Техкарты» rows.

    The generator config (build_config.ops) is the source of stage/code; the built ETK operation fills what
    the config lacks (a card code resolved from the operation library, a title). Times are the ETK's own: its norm of
    the operation (time rows: wires × cores × batch, «на изделие» of «Ручная сборка»…) per unit, no setup — one
    source of truth with what the ETK and its block scheme show.

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
    setup_in_norm = norms_include_setup(document)
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
            setup_in_norm=setup_in_norm,
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
            manual=blocks[index].number > 0,
            block_number=blocks[index].number,
            block_place=blocks[index].order.index(index) if blocks[index].number else 0,
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


def semifinished_steps(route: Sequence[RouteStep]) -> tuple[RouteStep, ...]:
    """Шаги-ПФ (владелец + Василий, 13.10: задание монтажника = один документ полуфабриката): по шагу на блок
    нарисованной схемы. Номер — номер ПФ (блока в схеме), этап — название ПФ, код — код его первой операции, норма —
    сумма норм его операций (подготовка и штучное время складываются), входы — ПФ-предшественники с per_unit.
    Операции ЭТК — etk_ops в порядке блока. Маршрут без нарисованной схемы — пусто: задание идёт по операциям ЭТК."""
    if not route or not all(getattr(step, "manual", False) and getattr(step, "block_number", 0) > 0 for step in route):
        return ()
    members: dict[str, list[RouteStep]] = {}
    for step in route:
        members.setdefault(_step_block(step), []).append(step)
    numbers: dict[str, int] = {}
    for key, steps in members.items():
        steps.sort(key=lambda step: (step.block_place, step.position))
        if len({step.block_number for step in steps}) != 1:
            raise ProductionValidationError(f"Схема полуфабрикатов: у блока «{key}» два номера")
        numbers[key] = steps[0].block_number
    if len(set(numbers.values())) != len(numbers):
        raise ProductionValidationError("Схема полуфабрикатов: номер полуфабриката повторяется")
    result: list[RouteStep] = []
    for key, steps in sorted(members.items(), key=lambda item: numbers[item[0]]):
        first = steps[0]
        for item in first.inputs:
            if str(getattr(item, "block", "") or "").strip() not in members:
                raise ProductionValidationError(f"Схема полуфабрикатов: блок «{item.block}» не найден в маршруте")
        result.append(RouteStep(
            position=numbers[key], code=first.code, stage=first.block_name or first.stage,
            description="; ".join(step.description for step in steps if step.description),
            seconds_per_unit=sum((step.seconds_per_unit for step in steps), Decimal(0)),
            setup_seconds=sum((step.setup_seconds for step in steps), Decimal(0)),
            block=key, block_name=first.block_name, final=first.final, inputs=first.inputs,
            manual=True, block_number=numbers[key],
            etk_ops=tuple(EtkOperation(step.position, step.code, step.stage) for step in steps),
        ))
    return tuple(result)


def launch_operations(route: Sequence[RouteStep], quantity: int) -> tuple[ProductionOperation, ...]:
    """Операции нового задания по маршруту выданной техкарты: блок схемы и явные входы (сетевой график).

    Нарисованная технологом схема — операция задания на каждый ПФ (semifinished_steps): одна коробка на ПФ, входы —
    коробки предыдущих ПФ. Без схемы — как прежде, операция задания на каждую операцию ЭТК."""
    semis = semifinished_steps(route)
    if semis:
        numbers = {step.block: step.position for step in semis}
        operations = tuple(ProductionOperation.create(
            position=step.position, code=step.code, stage=step.stage, description=step.description,
            seconds_per_unit=step.seconds_per_unit, setup_seconds=step.setup_seconds, quantity=quantity,
            stage_key=step.block, etk_ops=step.etk_ops,
        ) for step in semis)
        return link_inputs(operations, {
            step.position: tuple((numbers[item.block], Decimal(item.per_unit)) for item in step.inputs)
            for step in semis if step.inputs
        })
    operations = tuple(ProductionOperation.create(
        position=step.position, code=step.code, stage=step.stage, description=step.description,
        seconds_per_unit=step.seconds_per_unit, setup_seconds=step.setup_seconds, quantity=quantity,
        stage_key=_step_block(step) or None,
    ) for step in route)
    return link_inputs(operations, route_inputs(route))


def norms_outdated(task: Any, document: Mapping[str, Any] | None) -> bool:
    """«Нормы устарели»: нормы, которые дала бы выданная сейчас ЭТК тому же запуску, отличаются от норм задания.
    Сравниваются действующие нормы операций (секунды на штуку и подготовка), а не номера ревизий: новая ревизия с
    теми же нормами (правка текста, ЭТК до версии 4, у которой подготовку производство и так вычитает) флаг не ставит.
    Допуск — округление ЭТК до 0,01 мин на партию. Другой состав этапов (число, коды) — устарели: нормы не те."""
    steps = launch_route(document).steps
    if not steps:
        return True
    if any(operation.etk_ops for operation in task.operations):
        try:
            steps = semifinished_steps(steps)
        except ProductionValidationError:
            return True
    fresh = {step.position: step for step in steps}
    own = {operation.position: operation for operation in task.operations}
    if set(fresh) != set(own):
        return True
    batch, _ = operation_norms(document)
    unit_tolerance = Decimal("0.6") / max(batch, Decimal(1))  # 0,01 мин на партию — на штуку
    for position, operation in own.items():
        step = fresh[position]
        if operation_code_key(step.code) != operation_code_key(operation.code):
            return True
        if (abs(step.seconds_per_unit - operation.seconds_per_unit) > unit_tolerance
                or abs(step.setup_seconds - operation.setup_seconds) > Decimal("0.6")):
            return True
    return False


@dataclass(frozen=True)
class ReadyCard:
    """КБ с выданной техкартой — то, из чего можно запустить задание (prodLaunchable_): маршрут, тираж, срок."""

    techcard_id: UUID
    version: int
    revision: int
    project: str
    index: str
    name: str
    customer: str
    run: str  # тираж как в карточке набора («» — тиража нет)
    deadline: date | None
    route: LaunchRoute
    document: Mapping[str, Any] | None
    # Сколько штук КБ уже запущено: сумма тиражей её не отменённых заданий (launch_remainders).
    launched: int = 0

    @property
    def key(self) -> str:
        return kit_stage_key(self.project, self.index)

    @property
    def quantity(self) -> int | None:
        """Тираж целым числом больше нуля; иначе None — запускать на автомате нечего."""
        text = str(self.run or "").strip()
        return int(text) if text.isdigit() and int(text) > 0 else None

    @property
    def remainder(self) -> int | None:
        """Остаток тиража к запуску: тираж КБ − уже запущено (не меньше нуля); тиража нет — None."""
        return None if self.quantity is None else max(self.quantity - self.launched, 0)


def launch_remainders(ready: Iterable[ReadyCard], tasks: Iterable[ProductionTask]) -> list[ReadyCard]:
    """КБ «к запуску» — единое правило доски («новое задание»), «Записать готовыми» по КБ без задания, автоплана
    проекта и всего цеха (владелец 22.10: «запущенное — когда его поставили на кого-то; если поставили и убрали, то оно
    уже не запущено»). tasks — задания (отменённые не считаются). Каждая КБ — с `launched` (сумма тиражей её
    не отменённых заданий); в ответе те, у кого остаток тиража больше нуля, даже если задания по ней уже есть
    (запущено 200 из 500 — к запуску 300), и КБ без тиража, пока по ней нет ни одного задания (её покажут с причиной)."""
    launched = launched_quantity(tasks)
    result: list[ReadyCard] = []
    for card in ready:
        card = replace(card, launched=launched.get(card.key, 0))
        if (card.remainder or 0) > 0 or (card.quantity is None and not card.launched):
            result.append(card)
    return result


def ready_cards(cards: Iterable[Any], kitsets: Iterable[Any]) -> list[ReadyCard]:
    """Выданные техкарты с маршрутом, по (проект, индекс). Тираж и заказчик — из карточки набора (prodLaunchable_ читает
    «Наборы»); нет набора — партия ЭТК. Один список и для «Создать задание» на доске мастера, и для автоплана."""
    kit_rows = {
        kit_stage_key(kitset.project, assembly.index): (kitset.customer, assembly.run)
        for kitset in kitsets for assembly in kitset.assemblies
    }
    result: list[ReadyCard] = []
    for card in cards:
        route = launch_route(card.document)
        if not route.steps:
            continue
        kit = (card.document or {}).get("kit") if isinstance(card.document, Mapping) else None
        customer, run = kit_rows.get(kit_stage_key(card.project, card.item_index), ("", None))
        result.append(ReadyCard(
            techcard_id=card.id, version=card.version, revision=card.revision, project=card.project,
            index=card.item_index, name=card.product_name, customer=customer,
            run=format(Decimal(run).normalize(), "f") if run else str((kit if isinstance(kit, Mapping) else {}).get("part_quantity") or ""),
            deadline=card.deadline, route=route, document=card.document,
        ))
    result.sort(key=lambda item: (item.project, item.index))
    return result
