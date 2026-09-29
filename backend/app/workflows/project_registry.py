"""Requests' ProjectRegistry port over the Projects snapshot imported for the production plan."""

from __future__ import annotations

from collections.abc import Callable

from app.modules.production.plan import normalize_project_number, project_terminal, summarize_journal
from app.modules.production.plan_application import PlanUnitOfWork


CLOSED_PROJECT_STATUS = "Закрыт"


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
            if not project_terminal(item, journal.get(normalize_project_number(item.project)))
            and item.status.strip() != CLOSED_PROJECT_STATUS
        ]
