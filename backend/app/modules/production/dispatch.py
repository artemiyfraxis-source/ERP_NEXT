"""Раздача операций монтажникам по дням и сдвиг плана по факту (владелец, 30.09).

SUPPLYMAP ставит операцию ровно с выбранного часа и только показывает конфликты (prodPlan_). Экрану мастера этого мало:
«не могу загрузить большую задачу, которая не влезает, чтобы она на следующий день тоже пошла». Здесь операция встаёт
в первое свободное время монтажника с выбранного момента, хвост уходит на следующие рабочие дни, а его работа после
неё сдвигается. Отметки «взял»/«закончил» двигают план по факту: раньше — следующие задания подтягиваются, позже —
сдвигаются, в том числе следующие этапы той же сборки у других монтажников.

Решено агентом (SUPPLYMAP такого не умеет, RUNLOG): операция идёт одним непрерывным куском рабочего времени; позже
закончил — следующая работа сдвигается ровно настолько, насколько мешает; раньше — подтягивается только та, что стояла
вплотную (не дальше часа работы после закончившейся): задание, которое мастер нарочно поставил на другой день, стоит."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from heapq import heapify, heappop, heappush
from math import ceil
from typing import Iterable
from uuid import UUID

from .domain import (
    OperationInput,
    OperationShare,
    OperatorSkill,
    ProductionOperation,
    ProductionStatus,
    ProductionTask,
    ProductionValidationError,
    WorkShift,
    add_work_seconds,
    advance_clock,
    clock_between,
    clock_seconds,
    next_working_moment,
    operation_duration_seconds,
    rewind_clock,
    skill_of,
)
from .scheduling import upstream_ids, work_seconds_between

# План округляется до минуты: работа, начатая в пределах минуты после конца другой, идёт за ней.
SEQUENCE_TOLERANCE = timedelta(seconds=60)
# «Раньше закончил» подтягивает работу, стоявшую не дальше часа работы после закончившейся.
PULL_GAP_SECONDS = 60 * 60

# Ключ узла плана: (операция, монтажник) для доли по штукам, (операция, None) — для бригады с одним окном.
NodeKey = tuple[UUID, UUID | None]


@dataclass(frozen=True)
class DispatchCalendar:
    holidays: frozenset[date]
    shift: WorkShift
    absences: dict[UUID, tuple[tuple[datetime, datetime], ...]]


@dataclass(frozen=True)
class Progress:
    """Факт выработки: сдано по операции всего и каждым монтажником (годные + брак, без исправленных записей) —
    в штуках операции, как сдаёт монтажник (не в изделиях: «2 на изделие» сдаёт вдвое больше тиража)."""
    by_operation: dict[UUID, Decimal]
    by_operator: dict[tuple[UUID, UUID], Decimal]

    @classmethod
    def empty(cls) -> Progress:
        return cls({}, {})


@dataclass(frozen=True)
class Node:
    key: NodeKey
    task: ProductionTask
    operation: ProductionOperation
    operator_ids: tuple[UUID, ...]
    quantity: int | None
    start: datetime
    finish: datetime
    fixed: bool


@dataclass(frozen=True)
class Move:
    task: ProductionTask
    operation: ProductionOperation
    operator_ids: tuple[UUID, ...]
    start: datetime
    finish: datetime


def local(value: datetime | None, reference: datetime) -> datetime | None:
    """Момент в поясе «сейчас»: SQLite отдаёт время без пояса, PostgreSQL — в поясе соединения. Смена считается
    по часам цеха, поэтому всё переводится в пояс reference до расчёта."""
    if value is None:
        return None
    if reference.tzinfo is None:
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    if value.tzinfo is None:
        return value.replace(tzinfo=reference.tzinfo)
    return value.astimezone(reference.tzinfo)


def localize(task: ProductionTask, reference: datetime) -> ProductionTask:
    return replace(task, operations=tuple(
        operation.with_shares(tuple(replace(
            share,
            planned_start=local(share.planned_start, reference),
            planned_finish=local(share.planned_finish, reference),
            started_at=local(share.started_at, reference),
            finished_at=local(share.finished_at, reference),
        ) for share in operation.shares), operation.assigned_by)
        for operation in task.operations
    ))


def rate_of(skills: dict[str, dict[UUID, OperatorSkill]], code: str, operator_ids: Iterable[UUID]) -> Decimal:
    """Сумма коэффициентов исполнителей (prodOpDurationSec_); без допуска или строки компетенций — 1."""
    return sum((skill_of(skills, code, operator_id).coefficient for operator_id in operator_ids), Decimal(0))


def share_seconds(
    task: ProductionTask,
    operation: ProductionOperation,
    quantity: int | None,
    operator_ids: tuple[UUID, ...],
    skills: dict[str, dict[UUID, OperatorSkill]],
) -> int:
    """Норма доли: подготовка + штучное время на штуки операции для своих изделий (бригада — для всего задания)
    / коэффициенты. Ветка «2 на изделие» делает вдвое больше штук — и идёт вдвое дольше."""
    return operation_duration_seconds(
        operation, task.operation_units(operation.id, quantity),
        operator_rate=rate_of(skills, operation.code, operator_ids),
    )


def _live(task: ProductionTask) -> bool:
    return task.status is ProductionStatus.RUNNING and not task.handed_to


def nodes_of(tasks: Iterable[ProductionTask], progress: Progress) -> dict[NodeKey, Node]:
    """Узлы плана: доли с плановым окном в живых заданиях. Бригада — один узел: её окно общее.
    Закреплены (не двигаются) начатые и законченные доли и выполненные операции."""
    result: dict[NodeKey, Node] = {}
    for task in tasks:
        if not _live(task):
            continue
        for operation in task.operations:
            planned = [share for share in operation.shares if share.planned_start and share.planned_finish]
            if not planned:
                continue
            # Сдано в штуках операции: ветка «2 на изделие» готова, когда сдано вдвое больше тиража.
            done = progress.by_operation.get(operation.id, Decimal(0)) >= task.operation_units(operation.id)

            def fixed(share: OperationShare) -> bool:
                return (
                    done or share.started_at is not None or share.finished_at is not None
                    or progress.by_operator.get((operation.id, share.operator_id), Decimal(0)) > 0
                )

            crew = [share for share in planned if share.quantity is None]
            if crew:
                result[(operation.id, None)] = Node(
                    (operation.id, None), task, operation, tuple(share.operator_id for share in crew), None,
                    min(share.planned_start for share in crew), max(share.planned_finish for share in crew),
                    any(fixed(share) for share in crew),
                )
            for share in planned:
                if share.quantity is None:
                    continue
                result[(operation.id, share.operator_id)] = Node(
                    (operation.id, share.operator_id), task, operation, (share.operator_id,), share.quantity,
                    share.planned_start, share.planned_finish, fixed(share),
                )
    return result


def _clear_of_absences(
    start: datetime,
    seconds: int,
    operator_ids: tuple[UUID, ...],
    calendar: DispatchCalendar,
) -> tuple[datetime, datetime]:
    start = next_working_moment(start, calendar.holidays, calendar.shift)
    for _ in range(200):
        finish = add_work_seconds(start, seconds, calendar.holidays, calendar.shift)
        hits = [
            until for operator_id in operator_ids
            for since, until in calendar.absences.get(operator_id, ())
            if since < finish and start < until
        ]
        if not hits:
            return start, finish
        start = next_working_moment(max(hits), calendar.holidays, calendar.shift)
    raise ProductionValidationError("Не нашлось рабочего окна без отсутствий")


def place(
    *,
    tasks: Iterable[ProductionTask],
    task: ProductionTask,
    operation: ProductionOperation,
    operator_id: UUID,
    quantity: int | None,
    requested_start: datetime,
    calendar: DispatchCalendar,
    skills: dict[str, dict[UUID, OperatorSkill]],
    progress: Progress,
) -> tuple[datetime, datetime]:
    """Окно доли монтажника с выбранного момента: первое время, где она не залезает на уже идущую работу
    (начатую раньше этого момента, начатую по факту, или прежние этапы этой же сборки), и не раньше его прежних
    этапов этой сборки. Прежние этапы — те, чей полуфабрикат доходит до этой операции по схеме (в цепочке — все
    меньшие номера); параллельная ветка той же сборки — просто другая работа монтажника. Работа, что стоит позже,
    не мешает — её потом сдвигает reflow. Хвост — на следующие рабочие дни, отсутствия (отпуск, больничный)
    перепрыгиваются."""
    seconds = share_seconds(task, operation, quantity, (operator_id,), skills)
    others = [
        node for node in nodes_of(tasks, progress).values()
        if operator_id in node.operator_ids and node.operation.id != operation.id
    ]
    upstream = upstream_ids(task.operations, operation.id)
    earlier = [node for node in others if node.task.id == task.id and node.operation.id in upstream]
    start = _whole_minute(max([requested_start, *(node.finish for node in earlier)]), calendar)
    for _ in range(500):
        finish = add_work_seconds(start, seconds, calendar.holidays, calendar.shift)
        hits = [
            node.finish for node in others
            if node.start < finish and start < node.finish and (node.start < start or node.fixed or node in earlier)
        ] + [until for since, until in calendar.absences.get(operator_id, ()) if since < finish and start < until]
        if not hits:
            return start, finish
        start = _whole_minute(max(hits), calendar)
    raise ProductionValidationError("Не нашлось окна для операции в пределах календаря")


def _whole_minute(moment: datetime, calendar: DispatchCalendar) -> datetime:
    """Рабочий момент с точностью до минуты (вверх): план показывается в часах и минутах."""
    moment = next_working_moment(moment, calendar.holidays, calendar.shift)
    if moment.second or moment.microsecond:
        moment = next_working_moment(
            moment.replace(second=0, microsecond=0) + timedelta(minutes=1), calendar.holidays, calendar.shift,
        )
    return moment


def _batch(edge: OperationInput, task: ProductionTask) -> int:
    """Передаточная партия стрелки в изделиях задания: своя на ребре, иначе — всё задание (конец → начало).
    Партия, выбранная в авторасчёте, в задании не хранится; нахлёст, который она дала, reflow держит как запас."""
    return max(1, ceil(edge.transfer_batch)) if edge.transfer_batch is not None else task.quantity


def ready_by_input(
    consumer: Node,
    consumer_clock: int,
    edge: OperationInput,
    windows: Iterable[tuple[Node, tuple[datetime, datetime]]],
    calendar: DispatchCalendar,
) -> datetime:
    """Самый ранний старт потребителя i по стрелке j→i при окнах долей входа j (p — партия стрелки, τ — время одного
    изделия по окну на часах): start-lag S_i ≥ S_j + p·τ_j — первая партия входа готова (у раздробленного входа —
    у доли, что даст её раньше всех); finish-lag F_i ≥ F_j + p·τ_i — последняя партия потребителя не обгоняет конец
    входа. Партия целиком — ровно «после конца входа». consumer_clock — длительность потребителя на часах."""
    holidays, shift = calendar.holidays, calendar.shift
    batch = _batch(edge, consumer.task)
    first: datetime | None = None
    last: datetime | None = None
    for node, (start, finish) in windows:
        pieces = node.quantity or node.task.quantity
        ready = finish if batch >= pieces else advance_clock(
            start, ceil(Decimal(batch) * clock_between(start, finish, holidays, shift) / pieces), holidays, shift,
        )
        first = ready if first is None else min(first, ready)
        last = finish if last is None else max(last, finish)
    assert first is not None and last is not None
    pieces = consumer.quantity or consumer.task.quantity
    tail = consumer_clock - ceil(Decimal(min(batch, pieces)) * consumer_clock / pieces)
    return max(first, rewind_clock(last, tail, holidays, shift))


def _network_order(
    nodes: dict[NodeKey, Node],
    by_operation: dict[UUID, list[NodeKey]],
    rank,
) -> list[NodeKey]:
    """Узлы по рангу, но каждый — после долей всех своих входов (план внахлёст может ставить сборку раньше входа)."""
    waiting = {
        key: {other for edge in node.operation.inputs for other in by_operation.get(edge.input_operation_id, ())}
        for key, node in nodes.items()
    }
    consumers: dict[NodeKey, list[NodeKey]] = {}
    for key, inputs in waiting.items():
        for source in inputs:
            consumers.setdefault(source, []).append(key)
    left = {key: len(inputs) for key, inputs in waiting.items()}
    heap = [(rank(key), key) for key, count in left.items() if not count]
    heapify(heap)
    order: list[NodeKey] = []
    while heap:
        _, key = heappop(heap)
        order.append(key)
        for consumer in consumers.get(key, ()):
            left[consumer] -= 1
            if not left[consumer]:
                heappush(heap, (rank(consumer), consumer))
    seen = set(order)
    return order + sorted((key for key in nodes if key not in seen), key=rank)


def reflow(
    *,
    tasks: Iterable[ProductionTask],
    sources: dict[NodeKey, tuple[datetime, datetime] | None],
    pull: bool,
    now: datetime,
    calendar: DispatchCalendar,
    skills: dict[str, dict[UUID, OperatorSkill]],
    progress: Progress,
) -> dict[NodeKey, Move]:
    """Сдвиг плана после изменения окон sources (в tasks они уже новые; значение — окно до изменения, None — доля
    поставлена впервые). Работа того же монтажника, стоявшая после изменённой, сдвигается, если теперь мешает; при
    pull (факт «раньше») подтягивается, если стояла вплотную.

    Связь по схеме входов — по каждой стрелке j→i с запаздываниями (ready_by_input): этап встаёт не раньше готовности
    первой партии входа и так, чтобы его хвост не обогнал конец входа; за ним — его потребители до финальной операции.
    Нахлёст сверх этого, который мастер поставил сам (или дал авторасчёт с меньшей партией), сохраняется как запас:
    опоздал вход на час — сборка уходит на час, а не остаётся на месте и не встаёт «после конца». Новая доля связь по
    схеме не двигает: следующий этап той же сборки мог нарочно идти внахлёст."""
    holidays, shift = calendar.holidays, calendar.shift
    nodes = nodes_of(tasks, progress)
    old = {key: (node.start, node.finish) for key, node in nodes.items()}
    placed: set[NodeKey] = set()
    for key, before in sources.items():
        if key not in nodes:
            continue
        if before is None:
            old[key] = (nodes[key].start, nodes[key].start)
            placed.add(key)
        else:
            old[key] = before
    new = {key: (node.start, node.finish) for key, node in nodes.items()}
    by_operator: dict[UUID, list[NodeKey]] = {}
    by_operation: dict[UUID, list[NodeKey]] = {}
    for key, node in nodes.items():
        for operator_id in node.operator_ids:
            by_operator.setdefault(operator_id, []).append(key)
        by_operation.setdefault(node.operation.id, []).append(key)
    order = _network_order(
        nodes, by_operation, lambda key: (old[key][0], 0 if key in sources else 1, old[key][1], str(key)),
    )
    index = {key: position for position, key in enumerate(order)}

    moves: dict[NodeKey, Move] = {}
    for key in order:
        node = nodes[key]
        if key in sources or node.fixed:
            continue
        start, _ = old[key]
        before = {
            other for operator_id in node.operator_ids for other in by_operator[operator_id]
            if other != key and index[other] < index[key] and old[other][1] <= start + SEQUENCE_TOLERANCE
        }
        edges = [
            (edge, [other for other in by_operation.get(edge.input_operation_id, ()) if other not in placed])
            for edge in node.operation.inputs
        ]
        edges = [(edge, others) for edge, others in edges if others]
        changed = [other for other in before if new[other] != old[other]]
        if not changed and not any(new[other] != old[other] for _, others in edges for other in others):
            continue
        seconds = share_seconds(node.task, node.operation, node.quantity, node.operator_ids, skills)
        lower = max((new[other][1] for other in before), default=None)
        pullable = pull and any(
            new[other][1] < old[other][1]
            and work_seconds_between(old[other][1], start, holidays, shift) <= PULL_GAP_SECONDS
            for other in changed
        )
        if edges:
            span = clock_seconds(seconds, shift)
            was = max(
                ready_by_input(node, span, edge, [(nodes[other], old[other]) for other in others], calendar)
                for edge, others in edges
            )
            ready = max(
                ready_by_input(node, span, edge, [(nodes[other], new[other]) for other in others], calendar)
                for edge, others in edges
            )
            # Запас нахлёста: на сколько рабочего времени этап по плану стоял раньше готовности своих входов.
            ahead = clock_between(start, was, holidays, shift)
            bound = rewind_clock(ready, ahead, holidays, shift) if ahead else ready
            lower = bound if lower is None else max(lower, bound)
            pullable = pullable or (
                pull and ready < was and work_seconds_between(was, start, holidays, shift) <= PULL_GAP_SECONDS
            )
        assert lower is not None
        if lower > start:
            target = lower
        elif pullable:
            target = max(lower, now)
            if target >= start:
                continue
        else:
            continue
        moved = _clear_of_absences(target, seconds, node.operator_ids, calendar)
        if moved == old[key]:
            continue
        new[key] = moved
        moves[key] = Move(node.task, node.operation, node.operator_ids, *moved)
    return moves


def apply_moves(task: ProductionTask, moves: Iterable[Move]) -> ProductionTask:
    """Новые окна долей в задании (версия не меняется — её поднимает вызывающий один раз)."""
    windows: dict[UUID, dict[UUID, tuple[datetime, datetime]]] = {}
    for move in moves:
        for operator_id in move.operator_ids:
            windows.setdefault(move.operation.id, {})[operator_id] = (move.start, move.finish)
    if not windows:
        return task
    return replace(task, operations=tuple(
        operation.with_shares(tuple(
            replace(share, planned_start=windows[operation.id][share.operator_id][0],
                    planned_finish=windows[operation.id][share.operator_id][1])
            if share.operator_id in windows[operation.id] else share
            for share in operation.shares
        ), operation.assigned_by) if operation.id in windows else operation
        for operation in task.operations
    ))


@dataclass(frozen=True)
class Split:
    others: tuple[OperationShare, ...]  # доли остальных после раздела
    resized: frozenset[UUID]  # у кого изменились штуки — окно пересчитать от прежнего начала
    dropped: frozenset[UUID]  # у кого забрали все штуки


def split_shares(
    operation: ProductionOperation,
    task_quantity: int,
    operator_id: UUID,
    quantity: int,
) -> Split:
    """Штуки монтажнику. Бригада превращается в доли поровну на то, что осталось. Не хватает свободных штук —
    забираем у тех, кто заканчивает позже всех (так операция скорее закончится). У начатых и законченных долей
    штуки не забираются: монтажник уже работает, иначе ему не сдать выработку."""
    if quantity <= 0 or quantity > task_quantity:
        raise ProductionValidationError(f"Штук должно быть от 1 до {task_quantity}")
    others = [share for share in operation.shares if share.operator_id != operator_id]
    locked = {share.operator_id for share in others if share.started_at is not None or share.finished_at is not None}
    pieces = {share.operator_id: share.quantity for share in others}
    if others and others[0].quantity is None:
        rest = task_quantity - quantity
        for position, share in enumerate(others):
            pieces[share.operator_id] = rest // len(others) + (1 if position < rest % len(others) else 0)
        if any((pieces[value] or 0) <= 0 for value in locked):
            raise ProductionValidationError("Столько штук не раздать: кто уже взял операцию, остаётся с ней — дай меньше штук")
    excess = sum(value or 0 for value in pieces.values()) + quantity - task_quantity
    latest_first = sorted(
        (share for share in others if share.operator_id not in locked),
        key=lambda share: share.planned_finish.timestamp() if share.planned_finish else float("-inf"),
        reverse=True,
    )
    for share in latest_first:
        if excess <= 0:
            break
        take = min(excess, pieces[share.operator_id] or 0)
        pieces[share.operator_id] = (pieces[share.operator_id] or 0) - take
        excess -= take
    if excess > 0:
        raise ProductionValidationError("Столько штук не раздать: у начатых и законченных долей штуки не забираются — дай меньше штук")
    return Split(
        tuple(replace(share, quantity=pieces[share.operator_id]) for share in others if (pieces[share.operator_id] or 0) > 0),
        frozenset(share.operator_id for share in others if share.quantity != pieces[share.operator_id] and (pieces[share.operator_id] or 0) > 0),
        frozenset(share.operator_id for share in others if (pieces[share.operator_id] or 0) <= 0),
    )


def remainder(operation: ProductionOperation, task_quantity: int) -> int:
    """Нераспределённые штуки операции (бригада и «вся операция» — всё распределено)."""
    if not operation.shares:
        return task_quantity
    if not operation.split:
        return 0
    return max(0, task_quantity - sum(share.quantity or 0 for share in operation.shares))
