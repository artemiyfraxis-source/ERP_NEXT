"""Read the exact project KB catalog for Shipping without cross-module ORM imports in its domain."""

from sqlalchemy import select

from app.modules.techcards.kitset_infrastructure import KitsetRow
from app.shared.project_number import normalize_project_number


def known_pack_items(uow, project: str) -> set[str]:
    return set(pack_item_details(uow, project))


def pack_item_details(uow, project: str) -> dict[str, dict[str, str]]:
    items = {}
    row = uow.session.scalar(select(KitsetRow).where(KitsetRow.project_key == normalize_project_number(project)))
    if row is not None:
        items.update({str(item["index"]): {"name": str(item.get("name") or item["index"]), "unit": "шт"}
                      for item in row.assemblies})
    for good in uow.goods.for_project(project):
        items[good.item_index] = {"name": good.name, "unit": good.unit}
    return items
