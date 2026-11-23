"""Resolve stock origin references against the production registry in the acceptance transaction."""

from dataclasses import replace

from sqlalchemy import select
from app.shared.project_identity import current_project_name, stable_project_key, project_alias_keys, ProjectIdentityConflict

from app.modules.procurement.domain import project_key
from app.modules.production.infrastructure import ProductionPlanProjectRow
from app.modules.warehouse.domain import WarehouseValidationError
from app.modules.warehouse.grn import GrnCategory


def validate_warehouse_grn_context(uow, category, context):
    try:
        return _validate(uow, category, context)
    except ProjectIdentityConflict as error:
        raise WarehouseValidationError("Неоднозначная история проекта — проверьте реестр проектов") from error


def _validate(uow, category, context):
    if category not in {GrnCategory.CSM, GrnCategory.RD}:
        return replace(context, project=stable_project_key(uow.session, context.project)) if context.project else context
    value = context.customer_order if category == GrnCategory.CSM else context.project
    key = project_key(current_project_name(uow.session, value))
    projects = uow.session.scalars(select(ProductionPlanProjectRow.project)).all()
    matched = [project for project in projects if project_key(project) == key]
    if len(matched) != 1:
        raise WarehouseValidationError("Укажите существующий однозначный проект производства")
    project = stable_project_key(uow.session, matched[0])
    historical_keys = project_alias_keys(uow.session, project) - {key}
    if any(project_key(item) in historical_keys for item in projects):
        raise WarehouseValidationError("Прежнее имя проекта используется повторно — проверьте реестр проектов")
    if category == GrnCategory.CSM:
        if context.project is not None and project_key(current_project_name(uow.session, context.project)) != key:
            raise WarehouseValidationError("Давальческий материал: заказ клиента и проект не совпадают")
        return replace(context, customer_order=project, project=project)
    return replace(context, project=project)
