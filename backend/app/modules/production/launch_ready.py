"""«Готово к запуску» — запуск задания в один клик (владелец 29.10.2026, идея №3 Ресурсного плана).

Мастер видит КБ, которые можно запускать прямо сейчас, и запускает каждую одной кнопкой (или несколько сразу). Новых
обязательных шагов нет, само ничего не запускается: список только подсказывает, запуск — то же действие, что «Создать
задание» с доски мастера.

Правила (чтение из других модулей — `app/workflows/production_launch_ready_api.py`):

* **Готова к запуску** — КБ к запуску по единому правилу доски и автоплана (`board.unlaunched_cards` →
  `launchable.launch_remainders`: выданная ЭТК с маршрутом, тираж КБ больше уже запущенного, КБ не принята складом и
  проект не закрыт), у которой **комплект набран** — тот же факт «kit», что ставит этап КБ «Набрана» на лестнице
  (`workflows.production_plan.kit_stage_facts`: заявка на комплект «Укомплектован» и дальше). Частично запущенная
  КБ (200 из 500) тоже здесь — на остаток.
* **Тираж задания** = тираж КБ − max(уже запущено, сделано) — «сделано» как в «Загрузке» (`workload.done_of`: сдано
  цехом, принято складом, отгружено — наибольшее). Готовое мимо заданий (склад принял без ПЗ) к запуску не идёт.
  Тираж ≤ 0 — КБ в списке нет. Мастер может запустить меньше (не больше остатка: `board.run_of`).
* **Площадка** — подсказка, в задание не пишется (ручной запуск её тоже не пишет): как «Загрузка» решает
  (`workload.kb_site`) по заданиям этой КБ; их нет — по заданиям проекта; нет и их — «не распределено».
* **Запуск** — `board`: `_open_card` (доска устарела: техкарта перевыпущена, по КБ запустили ещё — отказ),
  `run_of`, `_launch_new` → `application.launch_task` (номер ПЗ, номер запуска КБ, серийники, аудит
  `production.launched`). Ключ идемпотентности — от техкарты и номера запуска КБ: повторный клик не создаёт второе
  задание, а получает отказ «уже запущено». Каждая КБ пакета — своей транзакцией: устаревшая строка не мешает
  остальным, её причина возвращается мастеру.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.identity import Actor

from .application import ConcurrentProductionUpdate, UnitOfWork
from .board import CardSnapshot, _launch_new, _open_card, run_of, unlaunched_cards
from .domain import ProductionOperation, ProductionStatus, ProductionTask, ProductionValidationError, unit_factors
from .launchable import ReadyCard, launch_operations
from .workload import UNASSIGNED, ZERO, Done, kb_site

MAX_BATCH = 100


@dataclass(frozen=True)
class ReadyRow:
    """КБ, готовая к запуску: техкарта, предложенный тираж задания, что уже сделано, площадка, комплект, срок."""

    card: ReadyCard
    quantity: int
    done: Done
    site: str
    site_note: str
    kit: str
    deadline: date | None  # срок проекта (как в «Загрузке» и плане); у задания — срок техкарты, как при ручном запуске


@dataclass(frozen=True)
class ReadySummary:
    rows: tuple[ReadyRow, ...]
    waiting_kit: int  # КБ к запуску, у которых комплект ещё не набран
    no_run: tuple[str, ...]  # «проект КБ» с набранным комплектом, но без тиража


def suggested_quantity(card: ReadyCard, done: Decimal) -> int:
    """Тираж задания: тираж КБ − max(уже запущено, сделано), не меньше нуля; тиража нет — 0."""
    if card.quantity is None:
        return 0
    made = max(Decimal(card.launched), done).to_integral_value(rounding=ROUND_FLOOR)
    return max(card.quantity - int(made), 0)


def suggest_site(
    own: Sequence[ProductionTask], project: Sequence[ProductionTask], site_of: Mapping[UUID, str],
) -> tuple[str, str]:
    """Площадка КБ по `workload.kb_site`: её задания → задания проекта → «не распределено»."""

    def decide(tasks: Sequence[ProductionTask]) -> tuple[str, str]:
        live = [task for task in tasks if task.status is not ProductionStatus.CANCELLED]
        return kb_site(
            (task.handed_to for task in live),
            (site_of.get(operator_id, "") for task in live if not task.handed_to for operation in task.operations
             for operator_id in {*operation.operator_ids, *(share.operator_id for share in operation.shares)}),
        )

    site, note = decide(own)
    if site != UNASSIGNED:
        return site, note
    site, note = decide(project)
    if site != UNASSIGNED:
        return site, f"по другим КБ проекта: {note}"
    return UNASSIGNED, "по проекту нет заданий с исполнителями"


def ready_rows(
    open_cards: Iterable[ReadyCard],
    kits: Mapping[str, str],
    done: Mapping[str, Done],
    sites: Mapping[str, tuple[str, str]],
    deadlines: Mapping[str, date | None],
) -> ReadySummary:
    """КБ к запуску (`unlaunched_cards`) с набранным комплектом (kits: kit_stage_key → статус заявки) и тиражом > 0.
    Порядок — срок проекта (без срока — в конце), проект, КБ."""
    rows: list[ReadyRow] = []
    waiting = 0
    no_run: list[str] = []
    for card in open_cards:
        kit = kits.get(card.key)
        if kit is None:
            if card.quantity is not None:
                waiting += 1
            continue
        if card.quantity is None:
            no_run.append(f"{card.project} {card.index}")
            continue
        made = done.get(card.key, Done(ZERO, ""))
        quantity = suggested_quantity(card, made.quantity)
        if quantity < 1:
            continue
        site, note = sites.get(card.key, (UNASSIGNED, ""))
        rows.append(ReadyRow(card, quantity, made, site, note, kit, deadlines.get(card.key)))
    rows.sort(key=lambda row: (row.deadline is None, row.deadline or date.max, row.card.project, row.card.index))
    return ReadySummary(tuple(rows), waiting, tuple(no_run))


def planned_operations(card: ReadyCard, quantity: int) -> tuple[tuple[ProductionOperation, Decimal], ...]:
    """Операции, которые создаст запуск (тот же `launch_operations`), со штуками полуфабриката на изделие."""
    try:
        operations = launch_operations(card.route.steps, quantity)
    except ProductionValidationError:
        return ()
    factors = unit_factors(operations)
    return tuple((operation, factors.get(operation.id, Decimal(1))) for operation in operations)


# ── Запуск ──────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LaunchItem:
    """Строка, которую видел мастер: техкарта и её версия, сколько было запущено, тираж (None — весь остаток)."""

    techcard_id: UUID
    techcard_version: int
    launched: int
    quantity: int | None = None


@dataclass(frozen=True)
class LaunchReadyCommand:
    items: tuple[LaunchItem, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class Refused:
    item: LaunchItem
    reason: str


@dataclass(frozen=True)
class LaunchReadyResult:
    created: tuple[ProductionTask, ...]
    refused: tuple[Refused, ...]


def launch_ready(
    command: LaunchReadyCommand,
    uow_factory: Callable[[], UnitOfWork],
    cards: Mapping[tuple[str, str], CardSnapshot],
    ready: Sequence[ReadyCard],
) -> LaunchReadyResult:
    """Запуск выбранных КБ тем же путём, что «Создать задание» с доски: каждая — своей транзакцией (см. модуль)."""
    items = command.items
    if not items:
        raise ProductionValidationError("Не выбрано ни одной КБ")
    if len(items) > MAX_BATCH:
        raise ProductionValidationError(f"За один раз — не больше {MAX_BATCH} КБ")
    if len({item.techcard_id for item in items}) != len(items):
        raise ProductionValidationError("КБ выбрана дважды")
    created: list[ProductionTask] = []
    refused: list[Refused] = []
    for item in items:
        try:
            created.append(_launch_one(item, command.actor, command.now, uow_factory(), cards, ready))
        except (ConcurrentProductionUpdate, ProductionValidationError) as error:
            refused.append(Refused(item, str(error)))
        except (IntegrityError, StaleDataError):
            refused.append(Refused(item, "Эту КБ одновременно запустили — обновите список"))
    return LaunchReadyResult(tuple(created), tuple(refused))


def _launch_one(
    item: LaunchItem, actor: Actor, now: datetime, uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot], ready: Sequence[ReadyCard],
) -> ProductionTask:
    with uow:
        # Номер запуска КБ — до чтения заданий: параллельный запуск займёт тот же номер и упрётся в уникальность.
        seen = uow.production.last_launch_seqs([item.techcard_id])
        stored = tuple(uow.production.with_status(ProductionStatus.RUNNING))
        open_cards = {card.techcard_id: card for card in unlaunched_cards(uow, stored, ready, cards)}
        card = _open_card(open_cards, item.techcard_id, item.techcard_version, item.launched)
        quantity = run_of(card, item.quantity)
        task = _launch_new(card, quantity, seen.get(card.techcard_id, 0), actor, now, uow)
        uow.commit()
        return task
