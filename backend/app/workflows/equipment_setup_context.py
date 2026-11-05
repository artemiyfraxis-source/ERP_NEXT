"""Server-owned setup context; exact issued-operation resolution is provided by production."""

from sqlalchemy import select

from app.modules.equipment_setup.application import IssuedSetupOperation, SetupMachine, SetupPerson
from app.modules.equipment_setup.recipe_infrastructure import SqlAlchemyRecipes
from app.modules.equipment_setup.queries import ExecutorChoice, MachineChoice
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.references.domain import TechOperationTab
from app.modules.references.equipment import physical_equipment
from app.modules.references.infrastructure import SqlAlchemyTechOperationRepository, TechOperationRecordRow
from app.shared.identity import Actor


class SqlAlchemySetupContext:
    def __init__(self, session, *, resolve_issued_operation):
        self.session = session
        # Must resolve the exact revision, operation and ETK position in this same Session;
        # unknown revision/machine applicability must return None, never latest fallback; an existing
        # operation that can't take setup raises SetupOperationUnavailable with its reason.
        self.resolve_issued_operation = resolve_issued_operation

    def person(self, user_id):
        row = self.session.get(IdentityUserRow, user_id, populate_existing=True)
        if row is None:
            return None
        return SetupPerson(Actor(row.id, row.name, row.department), row.site or "", bool(row.active), bool(row.site_master))

    def machine(self, machine_id):
        machines = self._machines()
        machine = next((item for item in machines if item.id == machine_id), None)
        if machine is None or machine.site not in {"ЦАР", "ЖУК"}:
            return None
        return SetupMachine(machine.id, machine.site)

    def _machines(self):
        rows = self.session.scalars(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == TechOperationTab.EQUIPMENT.value))
        return physical_equipment(SqlAlchemyTechOperationRepository._domain(row) for row in rows)

    def machine_ids_for_site(self, site):
        if site not in {"ЦАР", "ЖУК"}:
            return ()
        return tuple(machine.id for machine in self._machines() if machine.site == site)

    def machine_choices(self, *, site, eligible_ids=None, limit, offset):
        machines = (item for item in self._machines() if item.site in {"ЦАР", "ЖУК"}
            and (site is None or item.site == site) and (eligible_ids is None or item.id in eligible_ids))
        return tuple(MachineChoice(item.id, item.name, item.model, item.site)
            for item in sorted(machines, key=lambda item: item.id)[offset:offset + limit])

    def executor_choices(self, *, site, own_id=None, limit, offset):
        if site not in {"ЦАР", "ЖУК"}:
            return ()
        statement = select(IdentityUserRow.id, IdentityUserRow.name, IdentityUserRow.site).where(
            IdentityUserRow.active.is_(True), IdentityUserRow.site == site)
        if own_id is not None:
            statement = statement.where(IdentityUserRow.id == own_id)
        rows = self.session.execute(statement.order_by(IdentityUserRow.name, IdentityUserRow.id).offset(offset).limit(limit))
        return tuple(ExecutorChoice(row.id, row.name, row.site) for row in rows)

    def issued_operation(self, reference):
        result = self.resolve_issued_operation(reference)
        # "" = the task fixes no site yet (no handed_to, no executors): the chosen machine's site decides.
        if not isinstance(result, IssuedSetupOperation) or result.operation != reference or result.site not in {"", "ЦАР", "ЖУК"}:
            return None
        return result

    def recipe_exists(self, reference, machine_id):
        recipe = SqlAlchemyRecipes(self.session).get(reference.recipe_id, reference.version)
        return recipe is not None and recipe.machine_id == machine_id


def equipment_setup_uow_factory(session_factory, *, integration_ready: bool = False):
    """Explicit composition root hook; importing this module does not activate setup routes."""
    from app.modules.equipment_setup.infrastructure import SqlAlchemySetupUnitOfWork
    from .equipment_setup_resources import SetupResourceReservations
    from .production_resource_context import SqlAlchemyProductionResourceContext

    def context(session):
        source = SqlAlchemyProductionResourceContext(session)
        return SqlAlchemySetupContext(session, resolve_issued_operation=source.issued_operation)

    def reservations(session):
        source = SqlAlchemyProductionResourceContext(session)
        return SetupResourceReservations(session, assert_legacy_available=source.assert_legacy_setup_available,
            integration_ready=integration_ready)

    return lambda: SqlAlchemySetupUnitOfWork(session_factory, context_factory=context, reservations_factory=reservations)
