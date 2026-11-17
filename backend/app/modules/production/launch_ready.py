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
  задание, а получает отказ «уже запущено».
  Compatible cutting rows launch atomically as one group; independent groups and ineligible standalone
  rows use separate transactions. A stale child refuses its entire proposed cutting group.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.identity import Actor

from .application import ConcurrentProductionUpdate, UnitOfWork, launch_task
from .board import BOARD_KEY_PREFIX, CardSnapshot, launch_command, _open_card, run_of, unlaunched_cards
from .cutting import RECIPE_VERSION, cutting_recipe, digest
from .cutting_application import describe_group
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
    issue_separately: bool = False


@dataclass(frozen=True)
class LaunchReadyCommand:
    items: tuple[LaunchItem, ...]
    actor: Actor
    now: datetime
    fingerprint: str | None = None


@dataclass(frozen=True)
class Refused:
    item: LaunchItem
    reason: str


@dataclass(frozen=True)
class LaunchReadyResult:
    created: tuple[ProductionTask, ...]
    refused: tuple[Refused, ...]
    groups: tuple[dict, ...] = ()
    grouping_refused: tuple[dict, ...] = ()


def preview_cutting(command: LaunchReadyCommand, uow, ready: Sequence[ReadyCard]) -> dict:
    cards = {card.techcard_id: card for card in ready}
    operator = uow.operators.get(command.actor.id)
    site = operator.site if operator else ""
    groups, refused = {}, []
    for item in command.items:
        card = cards.get(item.techcard_id)
        if card is None:
            refused.append({"techcard_id": str(item.techcard_id), "reason": "КБ недоступна к запуску"})
            continue
        try:
            quantity = run_of(card, item.quantity)
            operations = launch_operations(card.route.steps, quantity)
            recipe, reason = cutting_recipe(card.project, site, card.document, operations)
        except ProductionValidationError as error:
            recipe, reason = None, str(error)
        if recipe is None:
            refused.append({"techcard_id": str(item.techcard_id), "reason": reason})
            continue
        key = recipe.key if not item.issue_separately else f"separate:{item.techcard_id}"
        group = groups.setdefault(key, {"key": key, "recipe_key": recipe.key, "recipe_version": RECIPE_VERSION,
                                       "site": site, "members": []})
        group["members"].append({"techcard_id": str(item.techcard_id), "index": card.index,
                                  "quantity": quantity, **recipe.row})
    current = uow.cutting.fingerprint() if getattr(uow, "cutting", None) else []
    result = {"groups": list(groups.values()), "grouping_refused": refused}
    result["fingerprint"] = digest({**result, "membership": current,
        "items": [[str(i.techcard_id), i.techcard_version, i.launched, i.quantity, i.issue_separately] for i in command.items],
        "cards": [[str(c.techcard_id), c.version, c.revision, c.launched] for c in ready]})
    return result


def launch_ready(
    command: LaunchReadyCommand,
    uow_factory: Callable[[], UnitOfWork],
    cards: Mapping[tuple[str, str], CardSnapshot],
    ready: Sequence[ReadyCard],
) -> LaunchReadyResult:
    """Launch each compatible cutting group atomically through the ordinary task launch service."""
    items = command.items
    if not items:
        raise ProductionValidationError("Не выбрано ни одной КБ")
    if len(items) > MAX_BATCH:
        raise ProductionValidationError(f"За один раз — не больше {MAX_BATCH} КБ")
    if len({item.techcard_id for item in items}) != len(items):
        raise ProductionValidationError("КБ выбрана дважды")
    with uow_factory() as uow:
        stored = tuple(uow.production.with_status(ProductionStatus.RUNNING))
        current_ready = unlaunched_cards(uow, stored, ready, cards)
        preview = preview_cutting(command, uow, current_ready)
        if command.fingerprint is not None and preview["fingerprint"] != command.fingerprint:
            raise ConcurrentProductionUpdate("Группы изменились после предпросмотра — обновите список")
        membership = uow.cutting.fingerprint() if getattr(uow, "cutting", None) else []
    buckets = {}
    key_of = {member["techcard_id"]: group["key"] for group in preview["groups"] for member in group["members"]}
    for item in items:
        buckets.setdefault(key_of.get(str(item.techcard_id), str(item.techcard_id)), []).append(item)
    created: list[ProductionTask] = []
    refused: list[Refused] = []
    group_ids = set()
    for bucket in buckets.values():
        try:
            with uow_factory() as uow:
                selected = {i.techcard_id for i in bucket}
                uow.production.lock_projects(projects=tuple(c.project for c in ready if c.techcard_id in selected))
                if command.fingerprint and getattr(uow, "cutting", None):
                    # Changes to a group this bucket could join invalidate its preview under the project lock.
                    recipe_keys = {g["recipe_key"] for g in preview["groups"]
                                   if any(UUID(m["techcard_id"]) in selected for m in g["members"])}
                    before = sorted(r for r in membership if r[2] in recipe_keys)
                    after = sorted(r for r in uow.cutting.fingerprint() if r[2] in recipe_keys)
                    if before != after:
                        raise ConcurrentProductionUpdate("Состав группы изменился — пересчитайте предпросмотр")
                seen = uow.production.last_launch_seqs(tuple(selected))
                stored = tuple(uow.production.with_status(ProductionStatus.RUNNING))
                opened = {c.techcard_id: c for c in unlaunched_cards(uow, stored, ready, cards)}
                pending = []
                new_group_ids = set()
                for item in bucket:
                    card = _open_card(opened, item.techcard_id, item.techcard_version, item.launched)
                    sequence = seen.get(card.techcard_id, 0)
                    launch = launch_command(card, run_of(card, item.quantity),
                        f"{BOARD_KEY_PREFIX}{card.techcard_id}:{sequence + 1}", command.actor, command.now, sequence)
                    task, fresh = launch_task(replace(launch, issue_separately=item.issue_separately), uow)
                    if not fresh:
                        raise ConcurrentProductionUpdate("Задание уже создано — обновите список")
                    pending.append(task)
                    if getattr(uow, "cutting", None):
                        for operation in task.operations:
                            member = uow.cutting.for_operation(operation.id)
                            if member:
                                new_group_ids.add(member.group_id)
                changed_groups = [r for r in uow.cutting.fingerprint() if UUID(r[0]) in new_group_ids] if new_group_ids else []
                uow.commit()
                created.extend(pending)
                group_ids.update(new_group_ids)
                membership = [r for r in membership if UUID(r[0]) not in new_group_ids] + changed_groups
        except (ConcurrentProductionUpdate, ProductionValidationError) as error:
            refused.extend(Refused(item, str(error)) for item in bucket)
        except (IntegrityError, StaleDataError):
            refused.extend(Refused(item, "Эту КБ одновременно запустили — обновите список") for item in bucket)
    with uow_factory() as uow:
        groups = tuple(describe_group(uow.cutting.get(identifier), uow) for identifier in sorted(group_ids, key=str))
    return LaunchReadyResult(tuple(created), tuple(refused), groups, tuple(preview["grouping_refused"]))
