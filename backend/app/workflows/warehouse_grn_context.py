"""Resolve stock origin references against the production registry in the acceptance transaction."""

from dataclasses import replace

from sqlalchemy import select

from app.modules.procurement.domain import project_key
from app.modules.production.infrastructure import ProductionPlanProjectRow
from app.modules.warehouse.domain import WarehouseValidationError
from app.modules.warehouse.grn import GrnCategory


def validate_warehouse_grn_context(uow, category, context):
    if category not in {GrnCategory.CSM, GrnCategory.RD}:
        return context
    value = context.customer_order if category == GrnCategory.CSM else context.project
    key = project_key(value)
    projects = uow.session.scalars(select(ProductionPlanProjectRow.project)).all()
    matched = [project for project in projects if project_key(project) == key]
    if len(matched) != 1:
        raise WarehouseValidationError("Укажите существующий однозначный проект производства")
    project = matched[0]
    if category == GrnCategory.CSM:
        if context.project is not None and project_key(context.project) != key:
            raise WarehouseValidationError("Давальческий материал: заказ клиента и проект не совпадают")
        return replace(context, customer_order=project, project=project)
    return replace(context, project=project)
