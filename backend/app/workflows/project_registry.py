"""Requests' ProjectRegistry port over the Projects snapshot imported for the production plan (+ open internal projects)."""

from __future__ import annotations

from collections.abc import Callable

from app.modules.production.plan import normalize_project_number, project_terminal, summarize_journal
from app.modules.production.plan_application import PlanUnitOfWork


CLOSED_PROJECT_STATUS = "Закрыт"


def _closed(item, journal) -> bool:
    if journal is not None and journal.status is not None:
        return bool(journal.status)
    return item.terminal or bool(project_terminal(item, None))


class PlanSnapshotProjectRegistry:
    """Open projects = not terminal and not «Закрыт» (SUPPLYMAP Forms.gs purposeProjects_).

    An empty snapshot means the registry has not been imported yet: ``None``, the check is skipped.
    """

    def __init__(self, production_uow_factory: Callable[[], PlanUnitOfWork]):
        self.production_uow_factory = production_uow_factory

    def open_projects(self) -> list[str] | None:
        with self.production_uow_factory() as uow:
            projects = list(uow.plan_projects.list())
            events = list(uow.plan_events.list()) if projects else []
        if not projects:
            return None
        # «Статус проекта» из журнала ERP (закрыт / возвращён в работу) сильнее снимка реестра.
        journal = summarize_journal(events)
        return [
            item.project
            for item in projects
            if not _closed(item, journal.get(normalize_project_number(item.project)))
            and item.status.strip() != CLOSED_PROJECT_STATUS
        ]


class InternalAwareProjectRegistry:
    """The plan registry plus the open internal projects «ВП-…» while the option projects.internal is on.

    Off, or the plan registry unavailable (None = «skip the check»), the answer is the plan registry's own."""

    def __init__(self, base, purchasing_uow_factory: Callable[[], object]):
        self.base = base
        self.purchasing_uow_factory = purchasing_uow_factory

    def open_projects(self) -> list[str] | None:
        projects = self.base.open_projects()
        if projects is None:
            return projects
        return [*projects, *(item["number"] for item in self.internal_projects())]

    def purposes(self) -> list[str] | None:
        """The purposes directory («Настройки закупки»)."""
        from app.workflows.purchasing_internal_projects import directory_purposes

        return directory_purposes(self.purchasing_uow_factory)

    def internal_projects(self) -> list[dict[str, str]]:
        from app.workflows.purchasing_internal_projects import open_summaries

        return open_summaries(self.purchasing_uow_factory)
