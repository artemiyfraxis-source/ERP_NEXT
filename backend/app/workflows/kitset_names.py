"""Фоновый добор наименований набора — SUPPLYMAP TcKitset.gs `tcKitNamesEnqueue_` / `tcKitNamesTick_`.

Загрузка из расчёта ставит проект в очередь: ссылки приехали — значит есть по чему ходить, и дальше это дело
фона, а не человека (десяток страниц поставщиков — десятки секунд крутилки). Тик берёт ПЕРВЫЙ проект очереди
и добирает ему не больше `FETCH_TICK_CAP` ссылок; пока есть что добирать, проект остаётся в голове очереди.
Битый проект очередь не держит: ошибка — в лог, проект — прочь.

Очередь — JSON-список номеров под ключом `tc_name_queue` в `operations_job_marks` (аналог ScriptProperties).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.operations.infrastructure import OperationsJobMarkRow
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.kitset_names import (
    FETCH_TICK_CAP,
    FetchNamesResult,
    HttpPageFetcher,
    PageFetcher,
    apply_names,
    fetch_names,
)
from app.shared.clock import moscow_now


QUEUE_KEY = "tc_name_queue"


def _locked(session: Session) -> OperationsJobMarkRow | None:
    return session.scalar(select(OperationsJobMarkRow).where(OperationsJobMarkRow.key == QUEUE_KEY).with_for_update())


def _queue(row: OperationsJobMarkRow | None) -> list[str] | None:
    """None — очередь битая (не JSON-список)."""
    if row is None or not row.value:
        return []
    try:
        value = json.loads(row.value)
    except ValueError:
        return None
    return [str(item) for item in value] if isinstance(value, list) else None


class KitsetNameQueue:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def enqueue(self, project: str, now: datetime | None = None) -> None:
        """`tcKitNamesEnqueue_`: в конец очереди, если проекта там ещё нет."""
        project = str(project or "").strip()
        if not project:
            return
        now = now or moscow_now()
        with self.sessions() as session:
            row = _locked(session)
            queue = _queue(row) or []
            if project not in queue:
                queue.append(project)
            if row is None:
                session.add(OperationsJobMarkRow(key=QUEUE_KEY, value=json.dumps(queue, ensure_ascii=False), marked_at=now))
            else:
                row.value = json.dumps(queue, ensure_ascii=False)
                row.marked_at = now
            session.commit()

    def head(self) -> str | None:
        """Первый проект очереди; битая или пустая очередь стирается (props.deleteProperty)."""
        with self.sessions() as session:
            row = _locked(session)
            queue = _queue(row)
            if row is not None and not queue:
                session.delete(row)
                session.commit()
            return queue[0] if queue else None

    def drop(self, project: str, now: datetime) -> None:
        """q.shift() — но по свежему чтению: пока тик ходил по сайтам, очередь могла пополниться."""
        with self.sessions() as session:
            row = _locked(session)
            queue = _queue(row)
            if row is None or queue is None:
                return
            if project in queue:
                queue.remove(project)
            row.value = json.dumps(queue, ensure_ascii=False)
            row.marked_at = now
            session.commit()

    def items(self) -> list[str]:
        with self.sessions() as session:
            return _queue(session.get(OperationsJobMarkRow, QUEUE_KEY)) or []


def fill_project_names(
    project: str,
    techcards_uow: Callable[[], SqlAlchemyTechcardsUnitOfWork],
    fetcher: PageFetcher,
    *,
    cap: int = FETCH_TICK_CAP,
) -> FetchNamesResult:
    """`tcKitFetchNames(project, '', cap)`: чтение → сайты (вне транзакции) → запись только в пустые имена."""
    with techcards_uow() as uow:
        kitset = uow.kitsets.get(project)
    if kitset is None:  # карточки нет — добирать нечего
        return FetchNamesResult(project=project)
    result = fetch_names(kitset, fetcher, cap=cap)
    if result.fills:
        with techcards_uow() as uow:
            fresh = uow.kitsets.get(project)
            if fresh is not None:
                updated, written = apply_names(fresh, result.fills)
                if written:
                    uow.kitsets.save(updated)
                    uow.commit()
    return result


def kitset_names_tick(
    now: datetime, sessions: sessionmaker[Session], fetcher: PageFetcher | None = None,
) -> int:
    """`tcKitNamesTick_`: один проект за тик. Возвращает, сколько имён найдено."""
    queue = KitsetNameQueue(sessions)
    project = queue.head()
    if project is None:  # пусто — выходим сразу, шаг дешёвый
        return 0
    try:
        result = fill_project_names(
            project, lambda: SqlAlchemyTechcardsUnitOfWork(sessions), fetcher or HttpPageFetcher(),
        )
    except Exception:  # noqa: BLE001 — битый проект не должен держать очередь
        logging.exception("tcKitNamesTick_:%s", project)
        queue.drop(project, now)
        return 0
    # Добрали всё, что можно, — проект прочь. Тик без единого имени тоже: иначе ссылки, которые сайт не отдаёт
    # (поиск elitan, 403), снова займут весь потолок тика и проект навсегда встанет в голове очереди, закрыв её
    # остальным (баг tcKitNamesTick_ в SUPPLYMAP). Его остаток доберёт следующая постановка в очередь.
    if not result.left or not result.filled:
        queue.drop(project, now)
    return result.filled


def enqueue_missing(sessions: sessionmaker[Session], now: datetime) -> list[str]:
    """Поставить в очередь каждый набор, где есть пустое наименование при ссылке, — для баз, загруженных там,
    где фон не работал (ПК без outbox_worker), и для уже заведённых карточек."""
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        kitsets = list(uow.kitsets.list())
    projects = [
        kitset.project for kitset in kitsets
        if any(component.article.strip() and not component.name.strip() and component.link.strip()
               for assembly in kitset.assemblies for component in assembly.components)
    ]
    queue = KitsetNameQueue(sessions)
    for project in projects:
        queue.enqueue(project, now)
    return projects


def main(argv: list[str] | None = None) -> int:
    """Разовый добор наименований: `python -m app.workflows.kitset_names [--enqueue-only]`.
    Ставит в очередь наборы с пустыми наименованиями и, без --enqueue-only, проходит каждый целиком (на сервере
    очередь проходит outbox_worker сам, тиками)."""
    import argparse
    import os
    import sys

    from app.database import build_engine

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--enqueue-only", action="store_true", help="только поставить в очередь, добор — фоном")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL", file=sys.stderr)
        return 2
    engine = build_engine(url)
    try:
        sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
        projects = enqueue_missing(sessions, moscow_now())
        print(f"Наборов с пустыми наименованиями: {len(projects)}")
        if args.enqueue_only:
            return 0
        # Каждый проект целиком, без потолка тика: ссылки, которые сайт не отдал, иначе держали бы его в голове
        # очереди бесконечно (у фона тик их повторяет).
        filled, queue = 0, KitsetNameQueue(sessions)
        for project in queue.items():
            try:
                filled += fill_project_names(
                    project, lambda: SqlAlchemyTechcardsUnitOfWork(sessions), HttpPageFetcher(), cap=100_000,
                ).filled
            except Exception:  # noqa: BLE001 — битый проект не держит остальные
                logging.exception("kitset names:%s", project)
            queue.drop(project, moscow_now())
        print(f"Наименований найдено на сайтах поставщиков: {filled}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
