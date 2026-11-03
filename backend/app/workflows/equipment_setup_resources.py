"""Connect setup commands to the production-owned resource transaction.

Unregistered until production execution and planning use the same resource registry.
"""

from app.modules.equipment_setup.domain import SetupStatus, SetupTask
from app.modules.production.resource_claims import ResourceConflict, ResourceKey, ResourceKind, ResourceOwner, ResourceWindow
from app.modules.production.resource_infrastructure import SqlAlchemyResourceClaims


class SetupResourceReservations:
    def __init__(self, session, *, assert_legacy_available, integration_ready: bool = False):
        self.claims = SqlAlchemyResourceClaims(session)
        self.assert_legacy_available = assert_legacy_available
        self.integration_ready = integration_ready

    def acquire(self):
        self._ready()
        self.claims.acquire()

    def _ready(self):
        if not self.integration_ready:
            raise ResourceConflict("Общий учёт занятости производства и наладки ещё не подключён")

    @staticmethod
    def _owner(task):
        return ResourceOwner("setup", task.id)

    @staticmethod
    def _resources(task):
        return (ResourceKey(ResourceKind.MACHINE, task.machine_id), ResourceKey(ResourceKind.PERSON, str(task.technician_id)))

    def reserve(self, task: SetupTask):
        self._ready()
        # The callback must use the same Session and reject unresolved legacy machine groups.
        # Acquire the shared generation lock before its production-read checks.
        self.claims.generation()
        self.assert_legacy_available(task)
        self.claims.replace_plan(self._owner(task), self._resources(task), ResourceWindow(task.planned_start, task.planned_finish))
        if task.status is SetupStatus.IN_PROGRESS:
            self.claims.activate(self._owner(task), task.id, self._resources(task), task.updated_at)

    def block(self, task: SetupTask):
        self._ready()
        if task.status is not SetupStatus.BLOCKED:
            raise ValueError("Ожидается заблокированное задание наладки")
        owner = self._owner(task)
        self.claims.release_active(owner, task.id, kinds=(ResourceKind.PERSON,))
        self.claims.release_plan(owner, kinds=(ResourceKind.PERSON,))
        if task.blocked_machine_available:
            self.claims.release_active(owner, task.id, kinds=(ResourceKind.MACHINE,))
            self.claims.release_plan(owner, kinds=(ResourceKind.MACHINE,))

    def release(self, task: SetupTask):
        self._ready()
        if task.status not in {SetupStatus.READY, SetupStatus.CANCELLED}:
            raise ValueError("Освобождение допускается после завершения или отмены наладки")
        owner = self._owner(task)
        self.claims.release_active(owner, task.id, kinds=(ResourceKind.PERSON, ResourceKind.MACHINE))
        self.claims.release_plan(owner)
