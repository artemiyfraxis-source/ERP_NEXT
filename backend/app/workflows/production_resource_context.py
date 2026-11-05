"""Exact issued ETK and physical-catalog resolution for production and setup commands."""

from sqlalchemy import select

from app.modules.equipment_setup.application import IssuedSetupOperation
from app.modules.production.domain import EtkOperation
from app.modules.production.infrastructure import ProductionTaskRow, ProductionWorkSessionRow, SqlAlchemyOperatorRepository, SqlAlchemyProductionRepository
from app.modules.production.launchable import route_from_document
from app.modules.production.machine_load import inventory_prefix
from app.modules.production.resource_claims import ResourceConflict, ResourceKey, ResourceKind, ResourceWindow
from app.modules.production.resource_assignment_infrastructure import ResourceAllocationMemberRow, ResourceSessionMemberRow, SqlAlchemyProductionResources
from app.shared.clock import MOSCOW
from app.modules.references.domain import TechOperationTab
from app.modules.references.equipment import physical_equipment
from app.modules.references.infrastructure import SqlAlchemyTechOperationRepository, TechOperationRecordRow
from app.modules.techcards.infrastructure import ProjectTechcardRow, TechcardRevisionRow
from app.modules.techcards.project_cards import TechcardStatus


class SqlAlchemyProductionResourceContext:
    def __init__(self, session):
        self.session = session

    def catalog(self):
        rows = self.session.scalars(select(TechOperationRecordRow).where(
            TechOperationRecordRow.tab == TechOperationTab.EQUIPMENT.value,
        ))
        return physical_equipment(SqlAlchemyTechOperationRepository._domain(row) for row in rows)

    @staticmethod
    def matches(machine, family, site):
        return machine.site == site and inventory_prefix(machine.id) == family

    def _document(self, task):
        if task.techcard_id is None or task.techcard_revision is None:
            raise ResourceConflict("Не определена выданная ревизия ЭТК задания")
        card = self.session.get(ProjectTechcardRow, task.techcard_id)
        if card is None or card.status != TechcardStatus.ISSUED.value:
            raise ResourceConflict("Выданная ЭТК задания не найдена")
        revision = self.session.scalar(select(TechcardRevisionRow).where(
            TechcardRevisionRow.project_card_id == task.techcard_id,
            TechcardRevisionRow.revision == task.techcard_revision,
        ))
        document = revision.document if revision is not None else (
            card.document if card.revision == task.techcard_revision else None
        )
        if not isinstance(document, dict):
            raise ResourceConflict("Точная ревизия ЭТК не найдена; текущая ревизия не подставляется")
        return document

    def requirements(self, task, operation):
        document = self._document(task)
        route = route_from_document(document)
        built = document.get("operations")
        if not isinstance(built, list):
            raise ResourceConflict("Не определён состав оборудования ЭТК")
        result = {}
        for part in operation.etk_ops or (EtkOperation(operation.position, operation.code, operation.stage),):
            index = part.position - 1
            if index >= len(route) or route[index].code != part.code or index >= len(built):
                raise ResourceConflict("Операция задания не совпадает с выданной ЭТК")
            card = built[index].get("card") if isinstance(built[index], dict) else None
            tooling = card.get("tooling") if isinstance(card, dict) else None
            if not isinstance(tooling, list):
                raise ResourceConflict("Оборудование операции не определено; ручная работа не предполагается")
            families = set()
            for tool in tooling:
                if not isinstance(tool, dict):
                    raise ResourceConflict("Некорректная строка оборудования ЭТК")
                if str(tool.get("section", "")).strip() != "Оборудование":
                    continue
                family = inventory_prefix(tool.get("inventory_number"))
                if not family:
                    raise ResourceConflict("У оборудования ЭТК не указан инвентарный номер")
                families.add(family)
            if len(families) > 1:
                raise ResourceConflict("У одной операции ЭТК несколько типов станков; назначение требует уточнения")
            result[part.position] = next(iter(families), None)
        return result

    def site(self, task, operator_ids):
        if task.handed_to in {"ЦАР", "ЖУК"}:
            return task.handed_to
        if task.handed_to:
            raise ResourceConflict("Площадка задания не определена")
        people = SqlAlchemyOperatorRepository(self.session)
        sites = {person.site if (person := people.get(identifier)) is not None and person.active else "" for identifier in operator_ids}
        if len(sites) != 1 or not sites.issubset({"ЦАР", "ЖУК"}):
            raise ResourceConflict("Уточните площадку исполнителей операции")
        return next(iter(sites))

    def validate(self, task, allocation):
        operation = task.operation(allocation.operation_id)
        requirements = {position: family for position, family in self.requirements(task, operation).items() if family}
        selected = {item.etk_position: item.machine_id for item in allocation.machines}
        if selected.keys() != requirements.keys():
            raise ResourceConflict("Явно выберите физический станок каждой машинной операции ЭТК")
        site = self.site(task, allocation.operator_ids)
        machines = {item.id: item for item in self.catalog()}
        for position, identifier in selected.items():
            machine = machines.get(identifier)
            if machine is None or machine.site != site or inventory_prefix(identifier) != requirements[position]:
                raise ResourceConflict("Станок не соответствует операции ЭТК или площадке")

    def issued_operation(self, reference):
        task = SqlAlchemyProductionRepository(self.session).get(reference.production_task_id)
        if task is None or task.techcard_id != reference.card_id or task.techcard_revision != reference.card_revision:
            return None
        try:
            operation = task.operation(reference.production_operation_id)
            family = self.requirements(task, operation).get(reference.etk_position)
            site = self.site(task, operation.operator_ids)
        except (KeyError, ValueError):
            return None
        if not family:
            return None
        eligible = tuple(item.id for item in self.catalog() if item.site == site and inventory_prefix(item.id) == family)
        return IssuedSetupOperation(operation=reference, site=site, eligible_machine_ids=eligible)

    def assert_legacy_available(self, resource, window, *, excluding=()):
        """Legacy unresolved plan blocks its interval; actual work blocks until explicitly closed."""
        work = list(self.session.scalars(select(ProductionWorkSessionRow).where(
            ProductionWorkSessionRow.status.in_(("В работе", "Пауза")),
        )))
        tracked = set(self.session.scalars(select(ResourceSessionMemberRow.session_id).where(
            ResourceSessionMemberRow.closed.is_(False),
        )))
        active = {(item.operation_id, item.operator_id) for item in work if item.id not in tracked}
        exact = set(self.session.execute(select(ResourceAllocationMemberRow.operation_id, ResourceAllocationMemberRow.operator_id).where(
            ResourceAllocationMemberRow.current.is_(True),
        )))
        task_ids = set(self.session.scalars(select(ProductionTaskRow.id).where(ProductionTaskRow.status == "Запущено")))
        task_ids.update(item.task_id for item in work)
        repository = SqlAlchemyProductionRepository(self.session)
        for task_id in task_ids:
            task = repository.get(task_id)
            for operation in task.operations:
                members = {share.operator_id: share for share in operation.shares}
                # A master may have removed the old plan while a legacy work session remained open.
                for operation_id, operator_id in active:
                    if operation_id == operation.id:
                        members.setdefault(operator_id, None)
                for operator_id, share in members.items():
                    key = (operation.id, operator_id)
                    if key in excluding or (key in exact and key not in active):
                        continue
                    busy = key in active
                    if not busy and share is not None and task.status.value == "Запущено" and share.finished_at is None and share.planned_start is not None:
                        begin = share.planned_start
                        finish = share.planned_finish
                        begin = begin.replace(tzinfo=MOSCOW) if begin.tzinfo is None else begin
                        finish = finish.replace(tzinfo=MOSCOW) if finish.tzinfo is None else finish
                        busy = ResourceWindow(begin, finish).overlaps(window)
                    if not busy:
                        continue
                    if resource.kind is ResourceKind.PERSON:
                        if str(operator_id) == resource.key:
                            raise ResourceConflict("Сотрудник занят прежним производственным назначением")
                        continue
                    try:
                        families = set(self.requirements(task, operation).values()) - {None}
                    except ResourceConflict:
                        raise ResourceConflict("Есть производственная работа с неуточнённым оборудованием; сначала уточните её")
                    if inventory_prefix(resource.key) in families:
                        raise ResourceConflict("Тип станка занят прежним назначением без конкретного экземпляра")

    def assert_legacy_setup_available(self, task):
        window = ResourceWindow(task.planned_start, task.planned_finish)
        self.assert_legacy_available(ResourceKey(ResourceKind.MACHINE, task.machine_id), window)
        self.assert_legacy_available(ResourceKey(ResourceKind.PERSON, str(task.technician_id)), window)


def production_resources(session):
    """Explicit composition-root factory. Defining it does not enable the runtime."""
    return SqlAlchemyProductionResources(session, SqlAlchemyProductionResourceContext(session))
