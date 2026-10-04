"""Хвосты уже удалённых проектов: `python -m app.workflows.purge_project_tails 719К.2 П2510-1.2 [--yes]`.

Удаление проекта на экране «Проекты» само сносит его хвосты без движения (`delete_kitset` →
`purge_project_tails`). Проект, удалённый раньше этого правила, каскад уже не запустит — эта команда делает то же
самое тем же кодом: без `--yes` только печатает, что будет удалено и что останется (и почему), с `--yes` пишет.
Проект, у которого ещё есть карточка, она не трогает: его удаляют с экрана целиком.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime
from typing import Any

from app.shared.clock import moscow_now
from app.shared.identity import Actor
from app.workflows.project_kitset import TAIL_SECTIONS, ProjectTails, purge_project_tails

SECTION_TITLES = {
    "fulfillment": "Запросы на комплектацию",
    "production": "Производственные задания",
    "requests": "Заявки на закупку",
}


def format_tails(tails: ProjectTails) -> str:
    lines = [f"Проект {tails.project}: {tails.message}"]
    for section in TAIL_SECTIONS:
        deleted = [entry.label for entry in tails.deleted if entry.section == section]
        kept = [entry for entry in tails.kept if entry.section == section]
        if not deleted and not kept:
            continue
        lines.append(f"  {SECTION_TITLES[section]}:")
        mark = "удалено" if tails.applied else "удалить"
        lines.extend(f"    {mark}: {label}" for label in deleted)
        lines.extend(f"    осталось: {entry.label} — {entry.reason}" for entry in kept)
    return "\n".join(lines)


def run(projects: list[str], state: Any, actor: Actor, now: datetime, *, apply: bool) -> tuple[str, int]:
    """Report text and exit code: 0 — done, 1 — some project refused (it still has a card)."""
    out: list[str] = []
    code = 0
    for project in projects:
        project = str(project or "").strip()
        if not project:
            continue
        with state.techcards_uow_factory() as uow:
            card = uow.kitsets.get(project)
        if card is not None:
            out.append(f"Проект {project}: карточка ещё есть — удалите проект на экране «Проекты», хвосты уйдут с ним.")
            code = 1
            continue
        tails = purge_project_tails(
            project, actor, now,
            fulfillment_uow=getattr(state, "fulfillment_uow_factory", None),
            production_uow=getattr(state, "production_uow_factory", None),
            requests_uow=getattr(state, "uow_factory", None),
            procurement_stages=getattr(state, "procurement_stage_lookup", None),
            apply=apply,
        )
        out.append(format_tails(tails))
    if not apply:
        out.append("Проверка без записи. Удалить — та же команда с --yes.")
    return "\n".join(out), code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Удалить хвосты уже удалённых проектов: задания без работы, запросы на комплектацию без выдачи, "
                    "заявки, не взятые в закупку. Без --yes только показывает.",
    )
    parser.add_argument("projects", nargs="+", help="№ проекта (можно несколько)")
    parser.add_argument("--yes", action="store_true", help="удалить по-настоящему (без флага — проверка без записи)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from types import SimpleNamespace

    from app.database import configure_database

    holder = SimpleNamespace(state=SimpleNamespace())
    engine = configure_database(holder, url)
    actor = Actor(id=uuid.uuid4(), name="удаление хвостов проекта", department="")
    try:
        text, code = run(args.projects, holder.state, actor, moscow_now(), apply=args.yes)
    finally:
        engine.dispose()
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
