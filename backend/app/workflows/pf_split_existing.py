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

Перестройка по новому правилу ПФ (владелец 14.10: резка → обжим А → монтаж А → обжим В → монтаж В, новые названия ПФ):

    ERP_DATABASE_URL=... python -m app.workflows.pf_split_existing --regenerate [--dry-run | --apply] [--project <№>] [--card <id>]

Выданные ЭТК типовых вкладок генератора («Межплатная», «КОАКС»), чья схема ПФ сделана генератором (`generated` или
`derived`), пересобираются из своего `build_config` тем же путём, что «Пересобрать» (`auto_build_project_techcard`:
нормы терминалов из справочников, правленные вручную операции остаются), но без сохранённой схемы — её режет текущий
`generator_split`. Новая ревизия «перестроено по новому правилу ПФ (владелец 14.10)», событие в журнале. Без
`--apply` — только проверка (по умолчанию). Пропускает с причиной: «Ручную» и «Сложную сборку», ЭТК не из генератора,
схему ПФ, правленную вручную, свой порядок операций, ЭТК с идущим заданием по ПФ. ЭТК,
которая уже собрана так же, — «без изменений», повторный `--apply` ничего не пишет.
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

from app.modules.techcards.application import (
    AutoBuildProjectTechcardCommand,
    SaveTechcardRouteCommand,
    auto_build_project_techcard,
    save_project_route,
)
from app.modules.techcards.etk_builder import (
    COMPLEX_TAB,
    MANUAL_MODE,
    TYPICAL_TABS,
    build_etk,
    document_route,
    generator_tab,
    propose_route,
)
from app.modules.techcards.operation_cards import OperationCardTemplate
from app.modules.techcards.project_cards import ProjectTechcard, TechcardStatus, TechcardValidationError
from app.shared.clock import moscow_now
from app.shared.identity import Actor

REASON = "Правка ПФ (перенос на ПФ)"
MOVE, SKIP, DONE = "move", "skip", "done"
REGEN_REASON = "перестроено по новому правилу ПФ (владелец 14.10)"
REGEN, SAME = "regen", "same"


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


# ── --regenerate: выданные ЭТК генератора — на текущее правило ПФ и новые тексты (владелец 14.10) ──

Prepare = Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, Any] | None]]


@dataclass(frozen=True)
class Regen:
    card: ProjectTechcard
    verdict: str  # regen — перестроить, same — без изменений, skip — пропуск с причиной
    reason: str = ""
    config: dict[str, Any] | None = None
    technology_source: dict[str, Any] | None = None
    old_route: Mapping[str, Any] | None = None
    new_route: Mapping[str, Any] | None = None
    texts: int = 0
    note: str = ""


def _texts(document: Mapping[str, Any] | None) -> list[tuple[str, ...]]:
    """What every operation hands on and takes, as the fitter reads it: result and semi-finished rows, the label."""
    rows: list[tuple[str, ...]] = []
    for operation in (document or {}).get("operations") or []:
        if not isinstance(operation, Mapping):
            continue
        names = [str(row.get("name") or "") for key in ("result", "semis")
                 for row in operation.get(key) or [] if isinstance(row, Mapping)]
        rows.append((*names, str(operation.get("result_label") or "")))
    return rows


def _changed_texts(old: Mapping[str, Any] | None, new: Mapping[str, Any] | None) -> int:
    before, after = _texts(old), _texts(new)
    return sum(1 for first, second in zip(before, after) if first != second) + abs(len(before) - len(after))


def _shape(route: Mapping[str, Any] | None) -> tuple[Any, ...]:
    """The scheme as the shop sees it: ПФ (name, operations, final) in order and the links between them."""
    blocks = [block for block in (route or {}).get("blocks") or [] if isinstance(block, Mapping)]
    ops = {block.get("key"): tuple(int(op) for op in block.get("ops") or []) for block in blocks}
    return (
        (route or {}).get("source"),
        tuple((str(block.get("name") or ""), ops[block.get("key")], bool(block.get("final"))) for block in blocks),
        frozenset((ops.get(edge.get("from")), ops.get(edge.get("to")), str(edge.get("per_unit")))
                  for edge in (route or {}).get("edges") or [] if isinstance(edge, Mapping)),
    )


def _card_tasks(card: ProjectTechcard, tasks: Sequence[Any]) -> list[Any]:
    """Running tasks of the card of any revision (a task before 0089 has no techcard id — by project and КБ)."""
    return [task for task in tasks if task.techcard_id == card.id
            or (task.techcard_id is None and task.project == card.project and task.item == card.item_index)]


def regen_config(document: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """The generator config to rebuild from — the stored one without a stored scheme and with its tab named (an old
    config is told by its operations), so the build cuts it by the current rule — or why the ETK is left alone."""
    config = document.get("build_config") if isinstance(document.get("build_config"), Mapping) else {}
    if not config:
        return None, "ЭТК не из генератора"
    tab = generator_tab(config)
    if tab == MANUAL_MODE:
        return None, "Ручная сборка — ПФ по её блокам"
    if tab == COMPLEX_TAB:
        return None, "Сложная сборка — ПФ настраивает владелец"
    if tab not in TYPICAL_TABS:
        return None, "ЭТК не из типовой вкладки генератора"
    stored = document.get("route") if isinstance(document.get("route"), Mapping) else {}
    if config.get("route") or stored.get("source") == "manual":
        return None, "схема ПФ правлена вручную — не трогаю"
    if config.get("opOrder"):
        return None, "свой порядок операций — не трогаю"
    return {**{key: value for key, value in config.items() if key != "route"}, "tab": tab}, ""


def plan_regen(
    card: ProjectTechcard, library: Sequence[OperationCardTemplate], now: datetime,
    running: Sequence[Any] = (), prepare: Prepare | None = None,
) -> Regen:
    """What the rebuild would make of one ETK — built in memory exactly as `auto_build_project_techcard` builds it
    (manually edited operations kept), nothing written."""
    document = card.document if isinstance(card.document, Mapping) else None
    if document is None:
        return Regen(card, SKIP, "ЭТК не собрана")
    if card.status is not TechcardStatus.ISSUED:
        return Regen(card, SKIP, "не выдана — соберите в генераторе")
    config, why = regen_config(document)
    if config is None:
        return Regen(card, SKIP, why)
    stamp = (document.get("source") or {}).get("technology_snapshot") if isinstance(document.get("source"), Mapping) else None
    config, technology_source = prepare(config) if prepare else (config, stamp)
    try:
        built = build_etk(config, library, technology_source=technology_source)
        planned = card.rebuild(built, now).document
    except (TechcardValidationError, ValueError, KeyError, TypeError) as error:
        return Regen(card, SKIP, f"не собирается заново: {error}")
    old_route, new_route = document_route(document), (planned or {}).get("route")
    if (new_route or {}).get("source") != "generated":
        return Regen(card, SKIP, "правило ПФ не режет эту ЭТК — оставляю как есть", old_route=old_route)
    texts = _changed_texts(document, planned)
    if _shape(old_route) == _shape(new_route) and not texts:
        return Regen(card, SAME, "без изменений")
    tasks = _card_tasks(card, running)
    by_pf = [task for task in tasks if any(operation.etk_ops for operation in task.operations)]
    if by_pf:
        numbers = ", ".join(task.number for task in by_pf)
        return Regen(card, SKIP, f"идёт задание {numbers} по ПФ — перестроить после его закрытия")
    note = ", ".join(task.number for task in tasks)
    return Regen(card, REGEN, "", config, technology_source, old_route, new_route, texts,
                 f"идут задания {note} по операциям ЭТК — остаются по своему снимку" if note else "")


def describe_regen(item: Regen, applied: bool) -> str:
    card = item.card
    operations = len((card.document or {}).get("operations") or [])
    status = getattr(card.status, "value", card.status)
    head = f"{card.project} · {card.item_index} · {status} ред. {card.revision} · {operations} оп."
    if item.verdict != REGEN:
        return f"{head} — {item.reason}"
    texts = f"тексты меняются у {item.texts} из {operations} оп." if item.texts else "тексты прежние"
    lines = [f"{head} {'перестроено' if applied else '→ перестроить'}",
             f"    было:   {pf_line(item.old_route)}",
             f"    станет: {pf_line(item.new_route)}",
             f"    {texts}"]
    return "\n".join(lines + ([f"    {item.note}"] if item.note else []))


def run_regenerate(
    techcards_uow_factory: Callable[[], Any], running_tasks: Callable[[], Sequence[Any]], actor: Actor, now: datetime,
    *, apply: bool, project: str | None = None, card_id: uuid.UUID | None = None, prepare: Prepare | None = None,
) -> tuple[str, int]:
    """Отчёт и код выхода: 0 — всё сделано (или проверка), 1 — запись какой-то ЭТК не прошла, 2 — нечего читать."""
    from app.modules.techcards.kitsets import project_key

    with techcards_uow_factory() as uow:
        cards = [uow.techcards.get(card_id)] if card_id else list(uow.techcards.list())
        library = list(uow.operation_cards.list())
    if card_id and cards[0] is None:
        return f"Карточки {card_id} нет", 2
    if not library:
        return "Библиотека операционных карт не загружена — пересобрать нечем", 2
    wanted = project_key(project) if project else None
    cards = sorted((card for card in cards if card is not None and (wanted is None or project_key(card.project) == wanted)),
                   key=lambda card: (card.project, card.item_index))
    running = list(running_tasks())
    out: list[str] = []
    counts = {REGEN: 0, SAME: 0, SKIP: 0}
    code = 0
    for card in cards:
        if card.document is None and not card_id:
            continue
        item = plan_regen(card, library, now, running, prepare)
        if item.verdict == REGEN and apply:
            try:
                built = auto_build_project_techcard(AutoBuildProjectTechcardCommand(
                    card_id=card.id, expected_version=card.version, actor=actor, now=now,
                    config=dict(item.config or {}), technology_source=item.technology_source, reason=REGEN_REASON,
                ), techcards_uow_factory())
            except (TechcardValidationError, KeyError) as error:
                out.append(f"{card.project} · {card.item_index} — не записано: {error}")
                code = 1
                continue
            out.append(describe_regen(item, True) + f"\n    новая ревизия {built.revision}")
        else:
            out.append(describe_regen(item, False))
        counts[item.verdict] += 1
    verb = "Перестроено" if apply else "Перестроить"
    out.append(f"{verb}: {counts[REGEN]} · без изменений: {counts[SAME]} · пропущено: {counts[SKIP]}")
    if not apply:
        out.append("Проверка без записи. Записать — та же команда с --apply.")
    return "\n".join(out), code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Перенести готовые ЭТК на ПФ (по правилу генератора или блокам ручной сборки).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только показать, что будет перенесено")
    mode.add_argument("--apply", action="store_true", help="записать новые ревизии «Правка ПФ (перенос на ПФ)»")
    parser.add_argument("--card", type=uuid.UUID, help="только эта карточка (id техкарты проекта)")
    parser.add_argument("--include-complex", action="store_true", help="переносить и «Сложную сборку»")
    parser.add_argument("--regenerate", action="store_true",
                        help="пересобрать выданные ЭТК генератора по новому правилу ПФ и текстам (по умолчанию — проверка)")
    parser.add_argument("--project", help="с --regenerate: только этот № проекта")
    args = parser.parse_args(argv)
    if not args.regenerate and not (args.dry_run or args.apply):
        parser.error("нужен --dry-run или --apply")
    if args.project and not args.regenerate:
        parser.error("--project — только с --regenerate")
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

    try:
        if args.regenerate:
            from app.workflows.techcard_build import with_technology

            references = getattr(state, "references_uow_factory", None)
            actor = Actor(id=uuid.uuid4(), name="перестройка ЭТК по новому правилу ПФ", department="")
            text, code = run_regenerate(
                state.techcards_uow_factory, running, actor, moscow_now(), apply=args.apply, project=args.project,
                card_id=args.card, prepare=lambda config: with_technology(config, references),
            )
        else:
            actor = Actor(id=uuid.uuid4(), name="перенос ЭТК на ПФ", department="")
            text, code = run(state.techcards_uow_factory, running, actor, moscow_now(), apply=args.apply,
                             card_id=args.card, include_complex=args.include_complex)
    finally:
        engine.dispose()
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
