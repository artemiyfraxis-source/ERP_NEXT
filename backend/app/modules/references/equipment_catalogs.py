"""Equipment catalog membership and each catalog's independently owned columns."""

from .domain import ReferenceValidationError, TechOperationTab

CATALOG_TABLES = {
    "ob": "equipment_machines",
    "ob_measuring": "equipment_measuring_units",
    "ob_support": "equipment_support_units",
    "ob_applicators": "equipment_applicators",
    "ob_dies": "equipment_dies",
    "ob_tools": "equipment_tools",
}
CATALOG_TYPES = {
    "ob": "Оборудование", "ob_measuring": "Средства измерений",
    "ob_support": "Обеспечивающее оборудование", "ob_applicators": "Аппликаторы",
    "ob_dies": "Матрицы", "ob_tools": "Инструмент",
}
TYPE_CATALOG = {value: key for key, value in CATALOG_TYPES.items()}
TYPE_CATALOG.update({"Обжимной инструмент": "ob_tools", "Оборудование (ПР)": "ob"})


def equipment_tab(fields: dict, *, role: str = "") -> TechOperationTab:
    """Specific current types win; only a confirmed supporting role splits an ordinary machine.

    Neither names nor inventory prefixes establish a measuring-instrument classification.
    """
    kind = str(fields.get("Тип") or fields.get("obType") or "").strip()
    catalog = TYPE_CATALOG.get(kind)
    if catalog is None:
        raise ReferenceValidationError(f"Неизвестный тип оборудования «{kind}»")
    if catalog == "ob" and role == "supporting":
        catalog = "ob_support"
    return TechOperationTab(catalog)


_COMMON = ("Инв. номер", "Наименование", "Артикул", "Производитель", "Расположение", "Примечание")
CATALOG_COLUMNS = {
    "ob": (*_COMMON, "Мощность, кВт", "Стоимость, руб."),
    "ob_measuring": (*_COMMON, "Заводской номер", "Категория измерений"),
    "ob_support": (*_COMMON, "Обслуживает"),
    "ob_applicators": ("Код в базе терминалов", *_COMMON, "Обжимает (серии разъёмов)"),
    "ob_dies": (*_COMMON, "Для инструмента", "Код в базе терминалов", "Обжимает (серии разъёмов)"),
    "ob_tools": (*_COMMON, "Подтип", "Код в базе терминалов", "Обжимает (серии разъёмов)"),
}
