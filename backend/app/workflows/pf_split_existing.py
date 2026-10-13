"""Перенос готовых ЭТК на ПФ (владелец 13.10: «старые сам переделай»).

    ERP_DATABASE_URL=... python -m app.workflows.pf_split_existing --dry-run [--card <id>] [--include-complex]
    ERP_DATABASE_URL=... python -m app.workflows.pf_split_existing --apply   [--card <id>] [--include-complex]

Берёт каждую ЭТК, у которой схема полуфабрикатов ещё автоматическая (`derived`, по ПФ на операцию) или её нет, и
режет её на ПФ тем же правилом, что кнопка «Предложить разбивку на ПФ» (`etk_builder.propose_route`): ЭТК генератора —
по этапам генератора (domain/pf_split.py), «Ручная сборка» — по её блокам. Запись — тем же путём, что «Сохранить ПФ»
(PUT …/route): проверка версии карточки, выданная ЭТК получает ревизию «Правка ПФ (перенос на ПФ)», журнал.

Пропускает с причиной: «Сложную сборку» (её ПФ настраивает владелец; `--include-complex` — тоже переносить), ЭТК уже
по ПФ, ЭТК с идущим заданием по ПФ этой ревизии, у которого состав ПФ стал бы другим. Задания не трогает: они идут по
своему снимку. Повторный `--apply` ничего не пишет.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import os
import sys
from typing import Any
import uuid

from app.modules.techcards.application import SaveTechcardRouteCommand, save_project_route
from app.modules.techcards.etk_builder import COMPLEX_TAB, document_route, generator_tab, propose_route
from app.modules.techcards.project_cards import ProjectTechcard, TechcardValidationError
from app.shared.clock import moscow_now
from app.shared.identity import Actor

REASON = "Правка ПФ (перенос на ПФ)"
MOVE, SKIP, DONE = "move", "skip", "done"


@dataclass(frozen=True)
class PfMove:
    card: ProjectTechcard
    verdict: str  # move — перенести, skip — пропуск с причиной, done — уже по ПФ
    reason: str
    route: Mapping[str, Any] | None = None
    generated: bool = False
    note: str = ""


def pf_line(route: Mapping[str, Any] | None) -> str:
    """«ПФ-1 · Нарезанные провода (01) · ПФ-2 · Обжатые провода (02,04) · …» — ПФ по порядку схемы."""
    blocks = [block for block in (route or {}).get("blocks") or [] if isinstance(block, Mapping)]
    return " · ".join(
        f"ПФ-{number} · {str(block.get('name') or '').strip() or '—'} ({','.join(f'{int(op):02d}' for op in block.get('ops') or [])})"
        for number, block in enumerate(blocks, start=1)
    )


def _composition(route: Mapping[str, Any] | None) -> list[tuple[int, ...]]:
    return [tuple(int(op) for op in block.get("ops") or []) for block in (route or {}).get("blocks") or []
            if isinstance(block, Mapping)]


def _tasks_of(card: ProjectTechcard, tasks: Sequence[Any]) -> list[Any]:
    """Идущие задания с текущей ревизии карточки (задание до 0089 без id техкарты — по проекту и КБ)."""
    return [
        task for task in tasks
        if (task.techcard_id == card.id and task.techcard_revision == card.revision)
        or (task.techcard_id is None and task.project == card.project and task.item == card.item_index)
    ]


def plan_move(card: ProjectTechcard, running: Sequence[Any] = (), *, include_complex: bool = False) -> PfMove:
    """Что сделать с одной ЭТК — без записи."""
    document = card.document
    current = document_route(document) if isinstance(document, Mapping) else None
    if not current:
        return PfMove(card, SKIP, "ЭТК не собрана")
    if current.get("source") in ("manual", "generated"):
        return PfMove(card, DONE, "уже по ПФ")
    config = document.get("build_config") if isinstance(document.get("build_config"), Mapping) else None
    if generator_tab(config) == COMPLEX_TAB and not include_complex:
        return PfMove(card, SKIP, "Сложная сборка — ПФ настраивает владелец")
    proposal = propose_route(document)
    if proposal is None:
        return PfMove(card, SKIP, "в ЭТК нет операций")
    if proposal["same"]:
        return PfMove(card, DONE, "разбивка уже по ПФ")
    tasks = _tasks_of(card, running)
    wanted = _composition(proposal["route"])
    for task in tasks:
        own = [tuple(item.position for item in operation.etk_ops) for operation in task.operations if operation.etk_ops]
        if own and own != wanted:
            return PfMove(card, SKIP, f"идёт задание {task.number} по ПФ этой ревизии — состав ПФ стал бы другим; "
                                      "перенесите после его закрытия")
    note = ", ".join(task.number for task in tasks)
    return PfMove(card, MOVE, "", proposal["route"], proposal["rule"] == "generator",
                  f"идут задания {note} по операциям ЭТК — остаются по своему снимку" if note else "")


def describe(move: PfMove, applied: bool) -> str:
    card = move.card
    operations = len((card.document or {}).get("operations") or [])
    status = getattr(card.status, "value", card.status)
    head = f"{card.project} · {card.item_index} · {status} ред. {card.revision} · {operations} оп."
    if move.verdict != MOVE:
        return f"{head} — {move.reason}"
    mark = "перенесено" if applied else "→"
    return f"{head} {mark} {pf_line(move.route)}" + (f"\n    {move.note}" if move.note else "")


def run(
    techcards_uow_factory: Callable[[], Any], running_tasks: Callable[[], Sequence[Any]], actor: Actor, now: datetime,
    *, apply: bool, card_id: uuid.UUID | None = None, include_complex: bool = False,
) -> tuple[str, int]:
    """Отчёт и код выхода: 0 — всё сделано (или проверка), 1 — запись какой-то карточки не прошла, 2 — нет карточки."""
    with techcards_uow_factory() as uow:
        cards = [uow.techcards.get(card_id)] if card_id else list(uow.techcards.list())
    if card_id and cards[0] is None:
        return f"Карточки {card_id} нет", 2
    cards = sorted((card for card in cards if card is not None), key=lambda card: (card.project, card.item_index))
    running = list(running_tasks())
    out: list[str] = []
    counts = {MOVE: 0, SKIP: 0, DONE: 0}
    code = 0
    for card in cards:
        if card.document is None and not card_id:
            continue
        move = plan_move(card, running, include_complex=include_complex)
        if move.verdict == MOVE and apply:
            try:
                save_project_route(SaveTechcardRouteCommand(
                    card_id=card.id, expected_version=card.version, actor=actor, now=now,
                    route=dict(move.route or {}), generated=move.generated, reason=REASON,
                ), techcards_uow_factory())
            except (TechcardValidationError, KeyError) as error:
                out.append(f"{card.project} · {card.item_index} — не записано: {error}")
                code = 1
                continue
        counts[move.verdict] += 1
        out.append(describe(move, apply))
    verb = "Перенесено" if apply else "Перенести"
    out.append(f"{verb}: {counts[MOVE]} · уже по ПФ: {counts[DONE]} · пропущено: {counts[SKIP]}")
    if not apply:
        out.append("Проверка без записи. Записать — та же команда с --apply.")
    return "\n".join(out), code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Перенести готовые ЭТК на ПФ (по правилу генератора или блокам ручной сборки).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="только показать, что будет перенесено")
    mode.add_argument("--apply", action="store_true", help="записать новые ревизии «Правка ПФ (перенос на ПФ)»")
    parser.add_argument("--card", type=uuid.UUID, help="только эта карточка (id техкарты проекта)")
    parser.add_argument("--include-complex", action="store_true", help="переносить и «Сложную сборку»")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from types import SimpleNamespace

    from app.database import configure_database
    from app.modules.production.domain import ProductionStatus

    holder = SimpleNamespace(state=SimpleNamespace())
    engine = configure_database(holder, url)
    state = holder.state

    def running() -> Sequence[Any]:
        with state.production_uow_factory() as uow:
            return list(uow.production.with_status(ProductionStatus.RUNNING))

    actor = Actor(id=uuid.uuid4(), name="перенос ЭТК на ПФ", department="")
    try:
        text, code = run(state.techcards_uow_factory, running, actor, moscow_now(), apply=args.apply,
                         card_id=args.card, include_complex=args.include_complex)
    finally:
        engine.dispose()
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
