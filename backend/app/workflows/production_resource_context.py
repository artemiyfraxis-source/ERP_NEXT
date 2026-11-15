from app.modules.references.domain import EQUIPMENT_TABS
"""Exact issued ETK and physical-catalog resolution for production and setup commands.

A machine is a БД.ОБ unit, named by its record id (owner 05.11.2026: «связка не через название, а через
идентификатор»; assignments link to the physical unit). An ETK operation needs a MODEL of equipment: the tooling row's
`equipment_model_id`; an ETK built before that field names its machine only by an inventory number, which counts only
when its family («ОБ.027») is the units of exactly one model — otherwise the ETK is rebuilt. A unit is eligible when
its model is the operation's one and the model is «операционное»: «обеспечивающее» equipment (compressor, chiller…)
is never the machine of an operation.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.modules.equipment_setup.application import SITE_UNKNOWN, SITES, IssuedSetupOperation, SetupOperationUnavailable
from app.modules.production.domain import EtkOperation, ProductionValidationError
from app.modules.production.infrastructure import ProductionTaskRow, ProductionWorkSessionRow, SqlAlchemyOperatorRepository, SqlAlchemyProductionRepository
from app.modules.production.launchable import route_from_document
from app.modules.production.machine_load import inventory_prefix
from app.modules.production.resource_claims import ResourceConflict, ResourceKey, ResourceKind, ResourceWindow
from app.modules.production.resource_assignment_infrastructure import ResourceAllocationMemberRow, ResourceSessionMemberRow, SqlAlchemyProductionResources
from app.shared.clock import MOSCOW
from app.modules.references.domain import TechOperationTab
from app.modules.references.equipment import physical_equipment
from app.modules.references.infrastructure import EquipmentModelRow, SqlAlchemyTechOperationRepository, EquipmentRecordRow
from app.modules.techcards.infrastructure import ProjectTechcardRow, TechcardRevisionRow
from app.modules.techcards.project_cards import TechcardStatus


SUPPORTING = "supporting"
REBUILD = "пересоберите ЭТК"


@dataclass(frozen=True)
class MachineUnit:
    """A numbered physical БД.ОБ unit: its record id is the identity, the inventory number only a display copy."""

    id: UUID
    inventory_number: str
    name: str
    model: str  # the unit's article
    site: str  # ЦАР | ЖУК | "" (unknown)
    model_id: UUID | None
    role: str = "operational"

    @property
    def operational(self) -> bool:
        return self.role != SUPPORTING


def machine_units(session) -> tuple[MachineUnit, ...]:
    """The numbered units of БД.ОБ (no calculation rows «.X» / «(R)», no model counts), by inventory number."""
    rows = session.execute(select(EquipmentRecordRow, EquipmentModelRow.role).outerjoin(
        EquipmentModelRow, EquipmentModelRow.id == EquipmentRecordRow.equipment_model_id,
    ))
    units = []
    for row, role in rows:
        found = physical_equipment((SqlAlchemyTechOperationRepository._domain(row),))
        if found:
            item = found[0]
            units.append(MachineUnit(row.id, item.inventory_number, item.name, item.model, item.site,
                                     row.equipment_model_id, role or "operational"))
    return tuple(sorted(units, key=lambda unit: (unit.inventory_number, str(unit.id))))


class SqlAlchemyProductionResourceContext:
    def __init__(self, session):
        self.session = session

    def catalog(self):
        cached = getattr(self, "_units", None)
        if cached is None:
            cached = self._units = machine_units(self.session)
        return cached

    @staticmethod
    def matches(machine, model, site):
        return machine.site == site and machine.model_id == model and machine.operational

    @staticmethod
    def family_label(model, catalog):
        """The inventory families of a model's units («ОБ.027»), for display only."""
        return ", ".join(sorted({inventory_prefix(unit.inventory_number) for unit in catalog if unit.model_id == model} - {""}))

    @staticmethod
    def _tool_model(tool, catalog):
        """The model a tooling row of the ETK needs; None for «обеспечивающее» equipment (not the machine)."""
        raw = tool.get("equipment_model_id")
        if raw not in (None, ""):
            try:
                model = UUID(str(raw))
            except ValueError:
                raise ResourceConflict(f"Некорректная модель оборудования в ЭТК — {REBUILD}") from None
            roles = {unit.role for unit in catalog if unit.model_id == model}
            if not roles:
                raise ResourceConflict(f"Модели оборудования из ЭТК нет среди единиц БД.ОБ — {REBUILD}")
            return None if roles == {SUPPORTING} else model
        family = inventory_prefix(tool.get("inventory_number"))
        if not family:
            raise ResourceConflict("У оборудования ЭТК не указан инвентарный номер")
        models = {unit.model_id: unit.role for unit in catalog
                  if unit.model_id is not None and inventory_prefix(unit.inventory_number) == family}
        if not models:
            raise ResourceConflict(f"Оборудования {family} из ЭТК нет среди единиц БД.ОБ")
        if len(models) > 1:
            raise ResourceConflict(f"ЭТК собрана без модели оборудования, а {family} — несколько моделей БД.ОБ: {REBUILD}")
        model, role = next(iter(models.items()))
        return None if role == SUPPORTING else model

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
            models = set()
            for tool in tooling:
                if not isinstance(tool, dict):
                    raise ResourceConflict("Некорректная строка оборудования ЭТК")
                if str(tool.get("section", "")).strip() != "Оборудование":
                    continue
                model = self._tool_model(tool, self.catalog())
                if model is not None:
                    models.add(model)
            if len(models) > 1:
                raise ResourceConflict("У одной операции ЭТК несколько типов станков; назначение требует уточнения")
            result[part.position] = next(iter(models), None)
        return result

    def _fixed_site(self, task, operator_ids):
        """Site fixed by the task itself: handed_to, else its executors'. "" = not fixed yet (nobody assigned)."""
        if task.handed_to in SITES:
            return task.handed_to
        if task.handed_to:
            raise ResourceConflict("Площадка задания не определена")
        if not operator_ids:
            return ""
        people = SqlAlchemyOperatorRepository(self.session)
        sites = {person.site if (person := people.get(identifier)) is not None and person.active else "" for identifier in operator_ids}
        if len(sites) != 1 or not sites.issubset(SITES):
            raise ResourceConflict("Уточните площадку исполнителей операции")
        return next(iter(sites))

    def site(self, task, operator_ids, machine_sites=()):
        """handed_to → executors' site → the chosen machines' one site."""
        fixed = self._fixed_site(task, operator_ids)
        if fixed:
            return fixed
        sites = set(machine_sites)
        if len(sites) != 1 or not sites.issubset(SITES):
            raise ResourceConflict(SITE_UNKNOWN)
        return next(iter(sites))

    def validate(self, task, allocation):
        operation = task.operation(allocation.operation_id)
        requirements = {position: model for position, model in self.requirements(task, operation).items() if model}
        selected = {item.etk_position: item.machine_id for item in allocation.machines}
        if selected.keys() != requirements.keys():
            raise ResourceConflict("Явно выберите физический станок каждой машинной операции ЭТК")
        machines = {str(item.id): item for item in self.catalog()}
        site = self.site(task, allocation.operator_ids, {machines[item].site for item in selected.values() if item in machines})
        for position, identifier in selected.items():
            machine = machines.get(identifier)
            if machine is None:
                raise ResourceConflict("Единица оборудования не найдена в БД.ОБ")
            if not machine.operational:
                raise ResourceConflict("Обеспечивающее оборудование не бывает станком операции")
            if machine.site != site or machine.model_id != requirements[position]:
                raise ResourceConflict("Станок не соответствует операции ЭТК или площадке")

    def _task(self, task_id):
        return SqlAlchemyProductionRepository(self.session).get(task_id)

    def issued_operation(self, reference):
        """None only when the task, its operation/ETK position or the issued revision really doesn't exist."""
        task = self._task(reference.production_task_id)
        if task is None or task.techcard_id != reference.card_id:
            return None
        if task.techcard_revision != reference.card_revision:
            raise SetupOperationUnavailable("Ревизия ЭТК задания изменилась — обновите список операций")
        try:
            operation = task.operation(reference.production_operation_id)
            self._document(task)
        except (KeyError, ProductionValidationError, ResourceConflict):
            return None
        try:
            requirements = self.requirements(task, operation)
            if reference.etk_position not in requirements:
                return None
            model = requirements[reference.etk_position]
            if not model:
                raise SetupOperationUnavailable("В ЭТК нет оборудования для операции")
            site = self._fixed_site(task, operation.operator_ids)
        except ValueError as error:  # ResourceConflict and route/ETK validation: the operation exists, say why
            raise SetupOperationUnavailable(str(error)) from error
        eligible = tuple(item.id for item in self.catalog()
            if (item.site == site if site else item.site in SITES) and self.matches(item, model, item.site))
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
                        models = set(self.requirements(task, operation).values()) - {None}
                    except ResourceConflict:
                        raise ResourceConflict("Есть производственная работа с неуточнённым оборудованием; сначала уточните её")
                    unit = next((item for item in self.catalog() if str(item.id) == resource.key), None)
                    if unit is not None and unit.model_id in models:
                        raise ResourceConflict("Тип станка занят прежним назначением без конкретного экземпляра")

    def assert_legacy_setup_available(self, task):
        window = ResourceWindow(task.planned_start, task.planned_finish)
        self.assert_legacy_available(ResourceKey(ResourceKind.MACHINE, str(task.machine_id)), window)
        self.assert_legacy_available(ResourceKey(ResourceKind.PERSON, str(task.technician_id)), window)


def production_resources(session):
    """Explicit composition-root factory. Defining it does not enable the runtime."""
    return SqlAlchemyProductionResources(session, SqlAlchemyProductionResourceContext(session))
