"""Resolve the complete order catalog for intake and transactional packing."""

from collections.abc import Iterable, Sequence
from decimal import Decimal

from sqlalchemy import or_, select

from app.modules.production.domain import ProductionStatus
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.shipping.domain import FinishedGood
from app.modules.shipping.infrastructure import FinishedGoodRow
from app.modules.techcards.infrastructure import ProjectTechcardRow
from app.modules.techcards.kitset_infrastructure import SqlAlchemyKitsetRepository
from app.modules.techcards.kitsets import KitAssembly
from app.shared.project_number import normalize_project_number


def order_quantities(
    goods: Sequence[FinishedGood], assemblies: Sequence[KitAssembly], indexes: Iterable[str],
) -> dict[str, Decimal]:
    """Order run comes from the kitset, not a partial task; manual/legacy goods use their ordered quantity.

    A card/task without an order quantity is deliberately unknown and prevents automatic permission.
    """
    planned = {index.strip().upper(): Decimal("0") for index in indexes if index.strip()}
    for good in goods:
        key = good.item_index.strip().upper()
        planned[key] = max(planned.get(key, Decimal("0")), good.ordered)
    for assembly in assemblies:
        if assembly.index.strip():
            planned[assembly.index.strip().upper()] = assembly.run
    return planned


def read_packing_order(uow, project: str) -> dict[str, Decimal]:
    """Read in the packing transaction after its project lock, including KBs without goods."""
    key = normalize_project_number(project)
    # The UoW already holds the shared project-run lock, including when no kitset exists yet.
    # Lock siblings against intake corrections too, but do not lock unrelated legacy NULL-key rows.
    good_ids = [good.id for good in uow.goods.for_project(project)]
    uow.session.scalars(select(FinishedGoodRow).where(FinishedGoodRow.id.in_(good_ids))
                        .order_by(FinishedGoodRow.id).with_for_update()
                        .execution_options(populate_existing=True)).all()
    kitset = SqlAlchemyKitsetRepository(uow.session).get(project)
    indexes = []
    for model, item_column in ((ProjectTechcardRow, ProjectTechcardRow.item_index), (ProductionTaskRow, ProductionTaskRow.item)):
        query = select(model.project, item_column).where(or_(model.project_key == key, model.project_key.is_(None)))
        if model is ProductionTaskRow:
            query = query.where(model.status.in_((ProductionStatus.RUNNING.value, ProductionStatus.DONE.value)))
        indexes.extend(index for name, index in uow.session.execute(query) if normalize_project_number(name) == key)
    return order_quantities(uow.goods.for_project(project), kitset.assemblies if kitset else (), indexes)
