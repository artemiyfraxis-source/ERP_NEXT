"""Папка проекта по ссылке на расчёт (владелец 14.10: «папку проекта ты можешь найти по ссылке на расчёт»).

    ERP_DATABASE_URL=... ERP_GOOGLE_SERVICE_ACCOUNT_JSON=... python -m app.workflows.folders_from_calc --dry-run [--project 708К.4]
    ERP_DATABASE_URL=... ERP_GOOGLE_SERVICE_ACCOUNT_JSON=... python -m app.workflows.folders_from_calc --apply   [--project 708К.4]

Файл расчёта лежит внутри папки проекта на Диске, поэтому папка проекта — прямой родитель файла расчёта. Берём каждый
проект без ссылки на папку, но со ссылкой на расчёт, спрашиваем у Диска родителя (только чтение, сервисный аккаунт
«erp-reader») и пишем `folder_url` тем же путём, что ручная правка (версия набора + запись в журнал проекта).
Папку, заданную вручную, не трогает; повторный запуск ничего не меняет.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
import os
import sys
import uuid
from typing import Protocol

from app.modules.production.plan_application import AddPlanEventCommand, add_plan_event
from app.modules.requests.google_drive import DriveError, drive_file_id, drive_reader_from_env  # noqa: F401 — drive_reader_from_env: for the API
from app.modules.techcards.kitsets import KitsetNotFound
from app.shared.clock import moscow_now
from app.shared.identity import Actor

FOLDER_URL = "https://drive.google.com/drive/folders/{id}"
NO_CALC = "Нет ссылки на расчёт"
NO_ACCESS = "Нет доступа к файлу расчёта у сервисного аккаунта ERP"
NO_FOLDER = "У файла расчёта нет папки, которую видит сервисный аккаунт ERP"


class FolderError(Exception):
    """Читаемая по-русски причина, почему папку не нашли."""


class ParentReader(Protocol):
    def parent_folder(self, file_id: str) -> tuple[str, str] | None: ...


@dataclass(frozen=True)
class FolderFound:
    url: str
    name: str


def find_folder(reader: ParentReader, calc_source: str) -> FolderFound:
    file_id = drive_file_id(calc_source)
    if not file_id:
        raise FolderError(NO_CALC)
    try:
        parent = reader.parent_folder(file_id)
    except DriveError as error:
        if error.kind in ("missing", "config"):
            raise FolderError(NO_ACCESS) from error
        raise FolderError(f"Google Диск не ответил: {error}") from error
    if parent is None:
        raise FolderError(NO_FOLDER)
    return FolderFound(url=FOLDER_URL.format(id=parent[0]), name=parent[1])


def save_folder(techcards_factory, production_factory, project: str, url: str, actor: Actor, now: datetime,
                *, found_by_calc: bool = False) -> str:
    """Сохранить `folder_url` набора: версия +1 и запись в журнал проекта. Тот же путь, что у ручной правки."""
    with techcards_factory() as uow:
        current = uow.kitsets.get(project)
        if current is None:
            raise KitsetNotFound(f"Карточка набора «{project}» не найдена")
        before = current.folder_url
        if url != before:
            uow.kitsets.save(replace(
                current, folder_url=url, version=current.version + 1, updated_at=now, updated_by=actor.name,
            ))
            uow.commit()
    if url != before:
        text = f"Папка проекта: {before or 'не задана'} → {url or 'ссылка убрана'}"
        if found_by_calc:
            text += " (папка найдена по расчёту)"
        add_plan_event(AddPlanEventCommand(
            project=current.project, kind="note", blocker="", text=text, deadline=None,
            known_projects=(current.project,), actor=actor, now=now,
        ), production_factory())
    return before


def run(techcards_factory, production_factory, reader_factory: Callable[[], ParentReader], actor: Actor, now: datetime,
        *, apply: bool, project: str | None = None) -> tuple[str, int]:
    with techcards_factory() as uow:
        kitsets = [item for item in uow.kitsets.list() if project is None or item.project == project]
    todo = [item for item in kitsets if not item.folder_url and item.calc_source]
    lines: list[str] = []
    failed = saved = 0
    reader: ParentReader | None = None
    for item in sorted(todo, key=lambda kitset: kitset.project):
        try:
            if reader is None:
                reader = reader_factory()
            found = find_folder(reader, item.calc_source)
        except DriveError as error:  # ключ не прочитан — дальше идти нет смысла
            return f"Google Диск не читается: {error}", 2
        except FolderError as error:
            failed += 1
            lines.append(f"  {item.project} → не найдена: {error}")
            continue
        lines.append(f"  {item.project} → {found.name or '(имя папки не прочитано)'} · {found.url}")
        if apply:
            save_folder(techcards_factory, production_factory, item.project, found.url, actor, now, found_by_calc=True)
            saved += 1
    head = f"Проектов без папки, но с расчётом: {len(todo)} (всего проектов: {len(kitsets)})"
    if apply:
        tail = f"Записано папок: {saved}, не найдено: {failed}."
    else:
        tail = f"Пробный прогон — в базу ничего не записано. Найдено: {len(todo) - failed}, не найдено: {failed}. Запись: --apply."
    return "\n".join([head, *lines, tail]), 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Найти папки проектов по ссылке на расчёт (Диск только читается).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только показать (по умолчанию)")
    mode.add_argument("--apply", action="store_true", help="записать найденные папки")
    parser.add_argument("--project", help="только этот проект")
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
    state = holder.state
    actor = Actor(id=uuid.uuid4(), name="папки по расчёту", department="")
    try:
        text, code = run(state.techcards_uow_factory, state.production_uow_factory, drive_reader_from_env, actor,
                         moscow_now(), apply=args.apply, project=args.project)
    finally:
        engine.dispose()
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
