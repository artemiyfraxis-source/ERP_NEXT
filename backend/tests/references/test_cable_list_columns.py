"""The columns of the three cable lists and of БД.КОАКС after the clean-up of 23.10.2026 (owner: «почистил мусорные
столбцы?», «сделай сортировку нормальную, чтобы своя инфа в своём месте», «если что-то заполняется на 3 % — это мусор
для БД»): each list's set, one group order in every list, the removed columns gone from the lists but not from the
records."""

from app.modules.references.domain import (
    BASE_COLUMNS, CABLE_COLUMNS, COAX_DROPPED_COLUMNS, TechOperationTab, cable_fields,
)
from tests.references.test_api import references_api  # noqa: F401  (fixture)

SUPPLY = ("Поставщик",)  # the base supply column; the others are the owner's own columns of every list

# The group order of every list (identification → construction → electrical → mechanics → supply → service).
GROUPS = [
    ["Марка", "Цвет", "Цвета", "Тип провода", "Стандарт", "Производители"],
    ["AWG", "Сечение, мм2", "Число жил", "Жила: проволок × диаметр, мм", "Экран", "Наружный диаметр, мм"],
    ["Волновое сопротивление, Ом", "Напряжение, В"],
    ["Масса, г/м", "Темп. от, °C", "Темп. до, °C"],
    ["Поставщик", "Цена посл., ₽/м без НДС", "Закупок"],
    ["Ссылка на datasheet", "Источник данных"],
]
GROUP_OF = {name: index for index, names in enumerate(GROUPS) for name in names}


def test_each_list_has_its_cleaned_set_of_columns():
    assert CABLE_COLUMNS["wire"] == (
        "Марка", "Цвет", "Тип провода", "Стандарт", "Производители", "AWG", "Сечение, мм2",
        "Жила: проволок × диаметр, мм", "Наружный диаметр, мм", "Напряжение, В", "Масса, г/м", "Темп. от, °C",
        "Темп. до, °C", "Поставщик", "Цена посл., ₽/м без НДС", "Закупок", "Ссылка на datasheet", "Источник данных",
    )
    assert CABLE_COLUMNS["multi"] == (
        "Марка", "Цвета", "Тип провода", "Стандарт", "Производители", "AWG", "Сечение, мм2", "Число жил",
        "Жила: проволок × диаметр, мм", "Экран", "Наружный диаметр, мм", "Напряжение, В", "Масса, г/м",
        "Темп. от, °C", "Темп. до, °C", "Поставщик", "Цена посл., ₽/м без НДС", "Закупок", "Ссылка на datasheet",
        "Источник данных",
    )
    assert CABLE_COLUMNS["coax"] == (
        "Марка", "Цвета", "Тип провода", "Стандарт", "Производители", "Жила: проволок × диаметр, мм",
        "Наружный диаметр, мм", "Волновое сопротивление, Ом", "Напряжение, В", "Масса, г/м", "Темп. от, °C",
        "Темп. до, °C", "Поставщик", "Цена посл., ₽/м без НДС", "Закупок", "Ссылка на datasheet", "Источник данных",
    )


def test_the_removed_columns_are_no_column_of_their_list():
    # монтажные: one value for every row («Экран» = нет, «Число жил» = 1), «Изоляция» = «Материал оболочки», «Ток» 4 %
    assert not {"Экран", "Число жил", "Изоляция", "Ток, А"} & set(CABLE_COLUMNS["wire"])
    # коаксиальные: «Сечение» 0,5 %, «Число жил» = 1, the old «Изоляция» / «Экран» texts the materials replaced
    assert not {"Сечение, мм2", "Число жил", "Изоляция", "Экран"} & set(CABLE_COLUMNS["coax"])
    # многожильные: «Категория» and «Волновое сопротивление» 1 %, the old «Изоляция»; «Экран» (the screen type) stays
    assert not {"Категория", "Изоляция", "Волновое сопротивление, Ом"} & set(CABLE_COLUMNS["multi"])
    assert "Экран" in CABLE_COLUMNS["multi"]
    # «Изоляция», «Категория», «Ток» are a column of no list, but stay in the БД.ПРОВ sheet header (the mirror)
    assert {"Изоляция", "Категория", "Ток, А"} <= set(BASE_COLUMNS[TechOperationTab.WIRE])
    # the supply columns stay in every list however sparse (owner 19.10.2026)
    assert all(set(SUPPLY) <= set(columns) for columns in CABLE_COLUMNS.values())


def test_every_list_keeps_one_group_order():
    for kind, columns in CABLE_COLUMNS.items():
        groups = [GROUP_OF[name] for name in columns]
        assert groups == sorted(groups), kind


def test_a_filled_value_of_a_removed_column_stays_in_the_record():
    fields = {"Марка": "RG316", "Изоляция": "ПТФЭ (оболочка ФЭП)", "Экран": "", "Число жил": "1"}
    shaped = cable_fields(CABLE_COLUMNS["coax"], fields)
    assert shaped == {"Марка": "RG316", "Изоляция": "ПТФЭ (оболочка ФЭП)", "Число жил": "1"}  # the empty cell goes


def test_the_coax_connector_table_has_no_dropped_columns_but_the_records_keep_them(references_api):
    fields = {"Артикул": "SMA-J-RG316", "Тип/Серия": "SMA-male", "Провод": "RG316", "L1": "3", "L+": "", "L-": "",
              "Программа": "", "Габариты": "10×8", "Материал корпуса": "латунь", "КСВ": "≤1,3"}
    assert references_api.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [
        {"tab": "coax", "display_text": "SMA-J-RG316", "source_sheet": "БД.КОАКС", "fields": fields}]}).status_code == 200

    listed = references_api.get("/api/references/tech-operations", params={"tab": "coax"}).json()

    assert not COAX_DROPPED_COLUMNS & set(listed["columns"])
    assert {"Артикул", "Тип/Серия", "Провод", "L1", "КСВ"} <= set(listed["columns"])
    record = listed["records"][0]["fields"]
    assert record["Габариты"] == "10×8" and record["Материал корпуса"] == "латунь" and "L+" in record
