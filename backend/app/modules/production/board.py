"""Доска мастера: операции КБ с выданной техкартой — «будущие задания», группы по оборудованию и групповое назначение
(владелец, 19.10.2026: «после того как техкарта готова я хочу тут видеть операции и желательно сгруппированные по
оборудованию, чтобы можно было раскидать сразу резку на одно и т. д.»).

Будущее задание — КБ с выданной техкартой, у которой остаток тиража к запуску больше нуля: тираж КБ минус тиражи её
не отменённых заданий (владелец 22.10: «запущенное — когда его поставили на кого-то; если поставили и убрали, то оно
уже не запущено»; правило — launchable.launch_remainders). Запустили 200 из 500 — остальные 300 снова «новое задание»,
его запуск — следующее задание КБ (следующий номер запуска). То же задание строит автоплан, со стабильными id (от
техкарты, её версии, уже запущенного по КБ и номера этапа): запуск по КБ меняет id — доска, прочитанная до него, устарела. Задание (ПЗ) при выдаче ЭТК не
создаётся — авторевизии ЭТК сразу сделали бы его устаревшим. ПЗ создаётся при первом назначении из выданной в этот момент
ревизии и закрепляет её (techcard_revision), дальше задание по новым ревизиям не перестраивается.

Группа оборудования операции (настоящей или будущей): (1) первое «Оборудование» с инвентарным номером ОБ в оснастке
операции ЭТК (card.tooling — тот же станок, что ключ наладки автоплана и загрузка станков); (2) иначе «Машина» БД.ОП по
коду карточки операции; (3) иначе категория карточки операции из библиотеки карточек, а нет её — название этапа.
У задания по ПФ — первое, что нашлось среди его операций ЭТК по порядку ПФ.

Групповое назначение — одной транзакцией: недостающие задания запускаются тем же путём, что «Создать задание»
(launch_task: номер ПЗ, номер запуска КБ, серийники, аудит), потом каждая операция ставится монтажнику тем же расчётом,
что доля на экране мастера (application.place_share: первое свободное время + сдвиг его следующей работы), по порядку
срок → КБ → этап, каждая — не раньше конца предыдущей: группа идёт одному монтажнику подряд, без нахлёста.

Тираж нового задания (владелец 22.10: отдельный шаг «Запустить задание» убран — задание создаёт первое действие из окна
проекта на доске): по умолчанию весь остаток тиража КБ, мастер может запустить меньше, но не больше остатка. Тот же тираж
принимают назначение, «Уже сделано» по КБ без задания (record_board_output: задание и ввод готовых одной транзакцией)
и автоплан."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4, uuid5

from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.project_number import normalize_project_number

from .application import (
    ConcurrentProductionUpdate,
    LaunchProductionCommand,
    UnitOfWork,
    _dispatch_calendar,
    _live_tasks,
    _progress,
    _skills,
    launch_task,
    place_share,
)
from .autoplan import NEW_TASK_PREFIX
from .dispatch import DispatchCalendar, LoadClock, Progress, _whole_minute, local, localize, nodes_of, share_seconds
from .execution import ManualOutputCommand, enter_manual_output
from .domain import (
    EtkOperation,
    ProductionOperation,
    ProductionStatus,
    ProductionTask,
    ProductionValidationError,
    add_work_seconds,
)
from .launchable import ReadyCard, launch_operations, launch_remainders, operation_code, route_from_document, stage_name
from .machine_load import _machine
from .scheduling import upstream_ids

NO_RUN_REASON = "Нет тиража КБ — задайте тираж в наборе проекта"
# Идентификаторы будущих заданий: стабильные от техкарты и её версии, чтобы назначение узнало ту же операцию.
_NEW_TASK_NAMESPACE = UUID("6f1c2a52-9a43-4f0e-9d6b-5a0e8f6c7b31")
BOARD_KEY_PREFIX = "board:"


class StaleBoard(ConcurrentProductionUpdate):
    """Доска устарела: задание изменилось, по КБ уже создано задание или выдана новая ревизия техкарты."""


@dataclass(frozen=True)
class CardSnapshot:
    """Текущая выданная техкарта позиции: её документ — источник ключей наладки и станков."""
    id: UUID
    version: int
    document: Mapping[str, Any] | None


def card_key(project: str, index: str) -> tuple[str, str]:
    return (" ".join(str(project or "").split()).casefold(), " ".join(str(index or "").split()).casefold())


@dataclass(frozen=True)
class NewTask:
    """Будущее задание КБ: выданная техкарта и задание на остаток тиража, каким его создаст назначение (или автоплан)."""
    card: ReadyCard
    task: ProductionTask


@dataclass(frozen=True)
class Skipped:
    """КБ к запуску, из которой будущее задание не строится (нет тиража, маршрут не собрался)."""
    card: ReadyCard
    reason: str


def future_task(card: ReadyCard, quantity: int, actor: Actor, now: datetime) -> ProductionTask:
    """Задание, каким его создаст запуск по техкарте, со стабильными id (от техкарты, её версии, уже запущенного по КБ
    и номера этапа): новый запуск по КБ даёт другие id — назначение по прочитанной до него доске устарело (409)."""
    task_id = uuid5(_NEW_TASK_NAMESPACE, f"{card.techcard_id}:{card.version}:{card.launched}")
    operations = launch_operations(card.route.steps, quantity)
    ids = {operation.id: uuid5(task_id, str(operation.position)) for operation in operations}
    operations = tuple(replace(
        operation, id=ids[operation.id],
        inputs=tuple(replace(edge, input_operation_id=ids[edge.input_operation_id]) for edge in operation.inputs),
    ) for operation in operations)
    task = ProductionTask.create(
        number=f"{NEW_TASK_PREFIX}{card.project} {card.index}", source_idempotency_key="autoplan",
        source_fingerprint="preview", project=card.project, item=card.index, name=card.name, quantity=quantity,
        fulfillment_request_id=None, deadline=card.deadline, launched_by=actor.name, operations=operations,
        created_at=now, techcard_id=card.techcard_id, techcard_revision=card.revision if card.revision >= 1 else None,
    )
    return replace(task, id=task_id)


def launch_command(
    card: ReadyCard, quantity: int, key: str, actor: Actor, now: datetime, seen_launch_seq: int,
) -> LaunchProductionCommand:
    """Тот же запуск, что «Создать задание» на доске: весь тираж, срок техкарты, без комплекта, ревизия выданной сейчас
    техкарты закрепляется в задании; номер запуска КБ — следующий после того, что видел запускающий до расчёта."""
    return LaunchProductionCommand(
        idempotency_key=key, project=card.project, item=card.index, name=card.name, quantity=quantity,
        fulfillment_request_id=None, deadline=card.deadline, operations=launch_operations(card.route.steps, quantity),
        actor=actor, now=now, techcard_id=card.techcard_id,
        techcard_revision=card.revision if card.revision >= 1 else None, seen_launch_seq=seen_launch_seq,
    )


def run_of(card: ReadyCard, run: int | None) -> int:
    """Тираж нового задания: не задан — весь остаток тиража КБ; меньше можно, больше остатка — нет (запущенное по КБ
    вместе с новым заданием не превышает тираж КБ)."""
    left = card.remainder
    if left is None:
        raise ProductionValidationError(f"{card.project} {card.index}: {NO_RUN_REASON}")
    if left < 1:
        raise StaleBoard(f"{card.project} {card.index}: весь тираж КБ ({card.quantity}) уже запущен — обновите доску")
    if run is None:
        return left
    if run < 1 or run > left:
        limit = "тираж КБ" if not card.launched else f"остаток: тираж КБ {card.quantity}, уже запущено {card.launched}"
        raise ProductionValidationError(f"{card.project} {card.index}: тираж задания — от 1 до {left} ({limit})")
    return run


def unlaunched_cards(
    uow: UnitOfWork, stored: Sequence[ProductionTask], ready: Sequence[ReadyCard],
    cards: Mapping[tuple[str, str], CardSnapshot],
) -> list[ReadyCard]:
    """КБ с выданной техкартой и остатком тиража к запуску (launchable.launch_remainders) — «новое задание» на доске,
    в «Записать готовыми» и в автоплане. stored — задания «Запущено»; выполненные читаются здесь (тоже запущены)."""
    done = uow.production.with_status(ProductionStatus.DONE)
    groups: dict[str, list[ReadyCard]] = {}
    for card in launch_remainders(ready, (*stored, *done)):
        groups.setdefault(card.key, []).append(card)
    result: list[ReadyCard] = []
    for group in groups.values():
        # Две выданные техкарты одной КБ — берём текущую (последнюю выданную), как ключи наладки.
        current = cards.get(card_key(group[0].project, group[0].index))
        result.append(next((card for card in group if current is not None and card.techcard_id == current.id), group[-1]))
    return sorted(result, key=lambda item: (item.project, item.index))


def future_tasks(
    cards: Sequence[ReadyCard], actor: Actor, now: datetime,
) -> tuple[dict[UUID, NewTask], tuple[Skipped, ...]]:
    """Будущие задания КБ на остаток тиража (по id задания) и КБ, из которых его не построить, с причиной."""
    result: dict[UUID, NewTask] = {}
    skipped: list[Skipped] = []
    for card in cards:
        quantity = card.remainder
        if quantity is None:
            skipped.append(Skipped(card, NO_RUN_REASON))
            continue
        try:
            task = future_task(card, quantity, actor, now)
        except ProductionValidationError as error:
            skipped.append(Skipped(card, f"Маршрут техкарты не собрался: {error}"))
            continue
        result[task.id] = NewTask(card, task)
    return result, tuple(skipped)


# --- Группы оборудования -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Equipment:
    """Группа оборудования операции: ключ, подпись по-русски; limited — учётный станок БД.ОБ (единиц ограничено)."""
    key: str
    label: str
    limited: bool = False


def _text(value: object) -> str:
    return " ".join(str(value if value is not None else "").split())


def operation_machines(records: Sequence[Mapping[str, str]]) -> dict[str, str]:
    """«Машина» БД.ОП по коду операции (opNumber / «Номер»; первая строка кода выигрывает)."""
    result: dict[str, str] = {}
    for fields in records:
        code = _text(fields.get("opNumber") or fields.get("Номер"))
        machine = _text(fields.get("Машина"))
        if code and machine and code not in result:
            result[code] = machine
    return result


def _machine_label(value: str) -> str:
    """«EW-05F+M/Автомат мерной резки проводов» → «EW-05F+M Автомат мерной резки проводов»; «/Автомат …» → «Автомат …»."""
    model, _, name = value.partition("/")
    return " ".join(part for part in (_text(model), _text(name)) if part) or _text(value)


def equipment_of(
    task: ProductionTask,
    document: Mapping[str, Any] | None,
    machines: Mapping[str, str],
    categories: Mapping[str, str],
) -> dict[UUID, Equipment]:
    """Группа оборудования каждой операции задания по выданной ЭТК. Операция ЭТК, код которой разошёлся с техкартой,
    станка ЭТК не даёт (как у автоплана и загрузки станков) — дальше «Машина» БД.ОП и категория по коду."""
    route = route_from_document(document) if isinstance(document, Mapping) else ()
    built = [item if isinstance(item, Mapping) else {} for item in (document or {}).get("operations") or []] \
        if isinstance(document, Mapping) else []
    result: dict[UUID, Equipment] = {}
    for operation in task.operations:
        parts = operation.etk_ops or (EtkOperation(operation.position, operation.code, operation.stage),)
        result[operation.id] = _group(parts, route, built, machines, categories)
    return result


def _group(
    parts: Sequence[EtkOperation], route: Sequence[Any], built: Sequence[Mapping[str, Any]],
    machines: Mapping[str, str], categories: Mapping[str, str],
) -> Equipment:
    for part in parts:
        index = part.position - 1
        step = route[index] if 0 <= index < len(route) else None
        if step is None or step.code != part.code or index >= len(built):
            continue
        machine, label = _machine(built[index])
        if machine:
            return Equipment(machine, label or machine, True)
    for part in parts:
        machine = machines.get(_text(part.code))
        if machine:
            label = _machine_label(machine)
            return Equipment(f"machine:{label.casefold()}", label)
    first = parts[0]
    label = _text(categories.get(_text(first.code))) or stage_name(first.stage) or operation_code(first.code) or "Прочее"
    return Equipment(f"category:{label.casefold()}", label)


# --- Групповое назначение -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardItem:
    """Операция группы: настоящего задания (task_id + его версия) или будущего (techcard_id + версия техкарты);
    операция — по id или номеру этапа; штуки — None: вся операция; run — тираж нового задания (None — весь остаток
    тиража КБ); launched — сколько по КБ было запущено, когда мастер читал доску (None — не проверять)."""
    task_id: UUID | None = None
    task_version: int | None = None
    techcard_id: UUID | None = None
    techcard_version: int | None = None
    operation_id: UUID | None = None
    position: int | None = None
    quantity: int | None = None
    run: int | None = None
    launched: int | None = None


@dataclass(frozen=True)
class BoardAssignCommand:
    operator_id: UUID
    requested_start: datetime | None
    items: tuple[BoardItem, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class Placed:
    task_id: UUID
    operation_id: UUID
    position: int
    quantity: int | None
    start: datetime
    finish: datetime


@dataclass(frozen=True)
class Refused:
    item: BoardItem
    reason: str


@dataclass(frozen=True)
class BoardAssigned:
    tasks: tuple[ProductionTask, ...]  # задания, в которые встала работа (после назначения)
    created: tuple[ProductionTask, ...]  # созданные этим назначением
    placed: tuple[Placed, ...]
    refused: tuple[Refused, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Resolved:
    item: BoardItem
    task: ProductionTask  # настоящее или будущее
    operation: ProductionOperation
    new: NewTask | None


def _operation(task: ProductionTask, item: BoardItem) -> ProductionOperation:
    if item.operation_id is not None:
        found = next((operation for operation in task.operations if operation.id == item.operation_id), None)
    elif item.position is not None:
        found = next((operation for operation in task.operations if operation.position == item.position), None)
    else:
        raise ProductionValidationError("Укажите операцию: id или номер этапа")
    if found is None:
        raise StaleBoard(f"{task.number}: операции нет в задании — обновите доску")
    return found


def _order(entry: _Resolved) -> tuple:
    task = entry.task
    return (task.deadline or date.max, normalize_project_number(task.project), task.item.casefold(), entry.operation.position)


def _item_key(item: BoardItem) -> tuple:
    return (item.task_id, item.techcard_id, item.operation_id, item.position)


def assign_board(
    command: BoardAssignCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    ready: Sequence[ReadyCard],
) -> BoardAssigned:
    """Группа операций одному монтажнику одной транзакцией (см. описание модуля). Задание изменилось, по КБ уже есть
    задание, техкарта перевыпущена — StaleBoard (409, доску перечитать). Назначенные и начатые операции не ставятся:
    они в refused с причиной; не осталось ничего — ошибка проверки."""
    items = command.items
    if not items:
        raise ProductionValidationError("Не выбрано ни одной операции")
    if len({_item_key(item) for item in items}) != len(items):
        raise ProductionValidationError("Операция выбрана дважды")
    now = command.now
    with uow:
        for task_id in sorted({item.task_id for item in items if item.task_id is not None}, key=str):
            uow.production.lock(task_id)
        # Номера запусков КБ — до чтения заданий: запуск, прошедший после этого чтения, займёт тот же номер, и наш
        # запуск упрётся в уникальность (techcard_id, launch_seq), а не создаст второе задание по КБ.
        future_ids = sorted({item.techcard_id for item in items if item.task_id is None and item.techcard_id}, key=str)
        seen = uow.production.last_launch_seqs(future_ids)
        stored = tuple(uow.production.with_status(ProductionStatus.RUNNING))
        open_cards = {card.techcard_id: card for card in unlaunched_cards(uow, stored, ready, cards)}
        resolved: list[_Resolved] = []
        news: dict[UUID, NewTask] = {}
        runs: dict[UUID, int | None] = {}
        loaded: dict[UUID, ProductionTask] = {}
        for item in items:
            if item.task_id is not None:
                task = loaded.get(item.task_id) or uow.production.get(item.task_id)
                if task is None:
                    raise StaleBoard("Задание не найдено — обновите доску")
                if item.task_version is not None and task.version != item.task_version:
                    raise StaleBoard(f"Задание {task.number} изменилось — обновите доску")
                loaded[task.id] = task
                resolved.append(_Resolved(item, task, _operation(task, item), None))
                continue
            if item.techcard_id is None or item.techcard_version is None:
                raise ProductionValidationError("Укажите задание (id и версию) или техкарту КБ (id и версию)")
            if runs.setdefault(item.techcard_id, item.run) != item.run:
                raise ProductionValidationError("У одной КБ указаны разные тиражи")
            new = news.get(item.techcard_id)
            if new is None:
                card = _open_card(open_cards, item.techcard_id, item.techcard_version, item.launched)
                new = NewTask(card, future_task(card, run_of(card, item.run), command.actor, now))
                news[card.techcard_id] = new
            resolved.append(_Resolved(item, new.task, _operation(new.task, item), new))
        if len({(entry.task.id, entry.operation.id) for entry in resolved}) != len(resolved):
            raise ProductionValidationError("Операция выбрана дважды")

        progress = _progress(uow, list(loaded))
        accepted: list[_Resolved] = []
        refused: list[Refused] = []
        names = {operator.id: operator.name for operator in uow.operators.list()}
        for entry in resolved:
            reason = _refusal(entry, progress.by_operation.get(entry.operation.id, Decimal(0)), names)
            if reason:
                refused.append(Refused(entry.item, reason))
            else:
                accepted.append(entry)
        if not accepted:
            raise ProductionValidationError("; ".join(dict.fromkeys(item.reason for item in refused)) or "Нечего назначать")
        accepted.sort(key=_order)

        # Задания будущих КБ — тем же запуском, что «Создать задание»; операции сопоставляются по номеру этапа.
        created: dict[UUID, ProductionTask] = {}
        for entry in accepted:
            if entry.new is None or entry.new.card.techcard_id in created:
                continue
            card = entry.new.card
            created[card.techcard_id] = _launch_new(
                card, entry.new.task.quantity, seen.get(card.techcard_id, 0), command.actor, now, uow,
            )

        tasks = _live_tasks(uow, now)
        for task in (*loaded.values(), *created.values()):
            tasks.setdefault(task.id, localize(task, now))
        cursor = local(command.requested_start, now) if command.requested_start is not None else now
        cursor = max(cursor, now)
        calendar = _dispatch_calendar(uow, now)
        skills = _skills(uow)
        progress = _progress(uow, list(tasks))
        placed: list[Placed] = []
        warnings: list[str] = []
        for entry in accepted:
            if entry.new is not None:
                real = created[entry.new.card.techcard_id]
                task_id = real.id
                operation_id = next(item.id for item in real.operations if item.position == entry.operation.position)
            else:
                task_id, operation_id = entry.task.id, entry.operation.id
            task = tasks[task_id]
            operation = task.operation(operation_id)
            # С «курсора» — в первый промежуток, где операция целиком влезает между уже стоящей работой монтажника и
            # его отсутствиями: группа не сдвигает назначенное раньше (одиночная доля сдвигала бы — dispatch.place).
            begin = _free_start(
                tasks, task, operation, command.operator_id, entry.item.quantity or task.quantity, cursor,
                calendar, skills, progress, now,
            )
            updated, plan, lines = place_share(
                uow, tasks, task, operation, command.operator_id, entry.item.quantity, begin, command.actor, now,
            )
            share = updated.operation(operation_id).share_of(command.operator_id)
            placed.append(Placed(task_id, operation_id, entry.operation.position, share.quantity if share else None,
                                 plan.start, plan.finish))
            warnings.extend(plan.warnings)
            warnings.extend(lines)
            # Следующая операция группы — не раньше конца этой: одному монтажнику подряд, без нахлёста.
            cursor = max(cursor, plan.finish)
        touched = list(dict.fromkeys(item.task_id for item in placed))
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=command.operator_id, event_type="production.board_assigned",
            actor=command.actor, occurred_at=now,
            details={
                "operator_id": str(command.operator_id), "operations": len(placed),
                "tasks": [tasks[task_id].number for task_id in touched],
                "created": [task.number for task in created.values()],
                "refused": [item.reason for item in refused],
            },
        ))
        uow.commit()
        return BoardAssigned(
            tasks=tuple(tasks[task_id] for task_id in touched), created=tuple(created.values()),
            placed=tuple(placed), refused=tuple(refused), warnings=tuple(dict.fromkeys(warnings)),
        )


def _open_card(
    open_cards: Mapping[UUID, ReadyCard], techcard_id: UUID, version: int | None, launched: int | None = None,
) -> ReadyCard:
    """КБ к запуску, которую видел мастер: весь тираж уже запущен, по КБ запустили ещё (launched — что видел мастер)
    или техкарта перевыпущена — доска устарела."""
    card = open_cards.get(techcard_id)
    if card is None:
        raise StaleBoard("По этой КБ уже запущен весь тираж или техкарта больше не выдана — обновите доску")
    if card.version != version:
        raise StaleBoard(f"Техкарта {card.project} {card.index} перевыпущена — обновите доску")
    if launched is not None and launched != card.launched:
        raise StaleBoard(f"По {card.project} {card.index} уже запущено {card.launched} шт из {card.quantity} — обновите доску")
    return card


def _launch_new(card: ReadyCard, quantity: int, sequence: int, actor: Actor, now: datetime, uow: UnitOfWork) -> ProductionTask:
    """Задание КБ с доски — тем же запуском, что «Создать задание» (launch_task), без коммита; номер запуска КБ —
    следующий после прочитанного до расчёта, гонку ловит уникальность (techcard_id, launch_seq)."""
    task, fresh = launch_task(launch_command(
        card, quantity, f"{BOARD_KEY_PREFIX}{card.techcard_id}:{sequence + 1}", actor, now, sequence,
    ), uow)
    if not fresh:
        raise StaleBoard(f"Задание по {card.project} {card.index} уже создано — обновите доску")
    return task


# --- «Уже сделано» по КБ без задания --------------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardOutputCommand:
    """Готовые штуки этапа КБ, по которой задания ещё нет (владелец 22.10: «не могу записать готовыми»): задание
    создаётся тем же запуском, что первое назначение, и сразу же — ввод готовых без исполнителя (никто не назначен)."""
    techcard_id: UUID
    techcard_version: int
    position: int
    good_quantity: int
    actor: Actor
    now: datetime
    run: int | None = None
    fill_inputs: bool = False
    launched: int | None = None


@dataclass(frozen=True)
class BoardOutput:
    task: ProductionTask
    box: Any
    output: Any
    filled: tuple[Any, ...]
    closed: tuple[str, ...]


def record_board_output(
    command: BoardOutputCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    ready: Sequence[ReadyCard],
) -> BoardOutput:
    """Задание по КБ и ввод готовых этапа — одной транзакцией: не прошёл ввод (не хватает входов, больше тиража) —
    задание тоже не создаётся."""
    with uow:
        seen = uow.production.last_launch_seqs([command.techcard_id])
        stored = tuple(uow.production.with_status(ProductionStatus.RUNNING))
        open_cards = {card.techcard_id: card for card in unlaunched_cards(uow, stored, ready, cards)}
        card = _open_card(open_cards, command.techcard_id, command.techcard_version, command.launched)
        quantity = run_of(card, command.run)
        task = _launch_new(card, quantity, seen.get(card.techcard_id, 0), command.actor, command.now, uow)
        operation = next((item for item in task.operations if item.position == command.position), None)
        if operation is None:
            raise StaleBoard(f"{card.project} {card.index}: этапа {command.position} нет в маршруте — обновите доску")
        box, output, filled, closed = enter_manual_output(ManualOutputCommand(
            task_id=task.id, operation_id=operation.id, good_quantity=Decimal(command.good_quantity),
            actor=command.actor, now=command.now, fill_inputs=command.fill_inputs,
        ), uow)
        uow.commit()
        return BoardOutput(task, box, output, filled, closed)


def _free_start(
    tasks: Mapping[UUID, ProductionTask],
    task: ProductionTask,
    operation: ProductionOperation,
    operator_id: UUID,
    quantity: int,
    cursor: datetime,
    calendar: DispatchCalendar,
    skills: Mapping[str, Any],
    progress: Progress,
    now: datetime,
) -> datetime:
    """Первый момент не раньше cursor и конца прежних этапов этой сборки, с которого операция монтажника целиком
    встаёт в рабочее время, не задевая его стоящую работу (любую, не только начатую; затянувшуюся — от «сейчас»)
    и отсутствия."""
    seconds = share_seconds(task, operation, quantity, (operator_id,), skills)
    nodes = nodes_of(tasks.values(), progress, LoadClock(now, calendar, dict(skills), progress)).values()
    upstream = upstream_ids(task.operations, operation.id)
    earlier = [node.finish for node in nodes if node.task.id == task.id and node.operation.id in upstream]
    others = [node for node in nodes if operator_id in node.operator_ids and node.operation.id != operation.id]
    start = max([cursor, *earlier])
    for _ in range(500):
        start = _whole_minute(start, calendar)
        finish = add_work_seconds(start, seconds, calendar.holidays, calendar.shift)
        hits = [node.finish for node in others if node.start < finish and start < node.finish] + [
            until for since, until in calendar.absences.get(operator_id, ()) if since < finish and start < until
        ]
        if not hits:
            return start
        start = max(hits)
    raise ProductionValidationError("Не нашлось окна для операции в пределах календаря")


def _refusal(entry: _Resolved, done: Decimal, names: Mapping[UUID, str]) -> str:
    task, operation = entry.task, entry.operation
    where = f"{task.number} · этап {operation.position} «{stage_name(operation.stage)}»"
    if entry.new is None:
        if task.status is not ProductionStatus.RUNNING:
            state = "выполнено" if task.status is ProductionStatus.DONE else "отменено"
            return f"{where}: задание {state}"
        if task.handed_to:
            return f"{where}: задание отдано в {task.handed_to}"
    if done > 0 or any(share.started_at is not None or share.finished_at is not None for share in operation.shares):
        return f"{where}: работа уже начата"
    if operation.shares:
        people = ", ".join(names.get(share.operator_id, "?") for share in operation.shares)
        return f"{where}: уже назначена ({people})"
    return ""
