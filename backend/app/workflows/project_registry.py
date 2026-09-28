"""Requests' ProjectRegistry port over the Projects snapshot imported for the production plan."""

from __future__ import annotations

from collections.abc import Callable

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
        if not projects:
            return None
        return [
            item.project
            for item in projects
            if not item.terminal and item.status.strip() != CLOSED_PROJECT_STATUS
        ]
