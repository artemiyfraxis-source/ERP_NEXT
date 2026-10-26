"""The columns of the three cable lists and of БД.КОАКС after the clean-up of 23.10.2026 (owner: «почистил мусорные
столбцы?», «сделай сортировку нормальную, чтобы своя инфа в своём месте», «если что-то заполняется на 3 % — это мусор
для БД») and of 25.10.2026 (owner: «всё, что без галочки, удаляй, они не нужны уже» — монтажные, многожильные,
БД.КОАКС; the coax cable list stays): each list's set, one group order in every list, the removed columns gone from the
lists but not from the records; an existing row still takes a write to a removed column, a new row only to «Источник
данных»."""

from app.modules.references.domain import (
    BASE_COLUMNS, CABLE_COLUMNS, CABLE_DROPPED, CABLE_WRITABLE, COAX_DROPPED_COLUMNS, TechOperationTab, cable_fields,
)
from app.main import app
from app.modules.references.domain import ReferenceEntry, ReferenceKind, custom_column_prefix
from tests.references.test_api import references_api  # noqa: F401  (fixture)

SUPPLY = ("Поставщик",)  # the base supply column; the others are the owner's own columns of every list

# The group order of every list (identification → construction → electrical → mechanics → supply → service).
GROUPS = [
    ["Марка", "Цвет", "Цвета", "Тип провода", "Категория", "Стандарт", "Производители"],
    ["AWG", "Сечение, мм2", "Число жил", "Число пар", "Жила: проволок × диаметр, мм", "Материал жилы",
     "Материал изоляции жилы", "Экран", "Материал оболочки", "Прокладка", "Наружный диаметр, мм"],
    ["Волновое сопротивление, Ом", "Частота до, ГГц", "Напряжение, В"],
    ["Масса, г/м", "Темп. от, °C", "Темп. до, °C"],
    ["Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м", "Цена посл., ₽/м без НДС", "Закупок"],
    ["Ссылка на datasheet", "Источник данных"],
]
GROUP_OF = {name: index for index, names in enumerate(GROUPS) for name in names}


def test_each_list_has_its_cleaned_set_of_columns():
    # 25.10.2026 (owner, per column): nothing hidden — the supply leftovers and «Источник данных» go from every list
    assert CABLE_COLUMNS["wire"] == (
        "Марка", "Цвет", "Тип провода", "Стандарт", "Производители", "AWG", "Сечение, мм2",
        "Жила: проволок × диаметр, мм", "Наружный диаметр, мм", "Напряжение, В", "Масса, г/м", "Темп. от, °C",
        "Темп. до, °C", "Поставщик", "Ссылка на datasheet",
    )
    assert CABLE_COLUMNS["multi"] == (
        "Марка", "Тип провода", "Стандарт", "Производители", "AWG", "Сечение, мм2", "Число жил",
        "Жила: проволок × диаметр, мм", "Экран",
        "Наружный диаметр, мм", "Напряжение, В", "Масса, г/м", "Темп. от, °C", "Темп. до, °C", "Поставщик",
        "Ссылка на datasheet",
    )
    # «Коаксиальные кабели»: «Стандарт» (the passport), construction, bend, temperature, mass, makers, the datasheet stay;
    # one frequency «Частота до, ГГц»
    assert CABLE_COLUMNS["coax"] == (
        "Марка", "Стандарт", "Производители", "Жила: проволок × диаметр, мм",
        "Наружный диаметр, мм", "Волновое сопротивление, Ом", "Частота до, ГГц", "Масса, г/м", "Темп. от, °C",
        "Темп. до, °C", "Поставщик", "Ссылка на datasheet",
    )
    supply = {"Цена посл., ₽/м без НДС", "Закупок", "Дата цены", "Источник данных"}
    assert CABLE_DROPPED["wire"] == supply
    assert CABLE_DROPPED["multi"] == supply | {"Цвета"}  # the core columns stay (owner 25.10: 100 % / 97 % filled)
    assert CABLE_DROPPED["coax"] == supply | {"Цвета", "Напряжение, В", "Тип провода"}
    # a removed column is still a cell an existing row takes a write to
    assert all(supply <= set(CABLE_WRITABLE[kind]) for kind in CABLE_WRITABLE)
    assert {"Цвета", "Жила: проволок × диаметр, мм"} <= set(CABLE_WRITABLE["multi"])


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


# --- 25.10.2026: the removed columns of the монтажные / многожильные and of БД.КОАКС ---

COLUMNS = "/api/references/tech-operations/columns"
PROV_ROWS = [
    {"Марка": "МГТФ 0,35", "Цвет": "белый", "Тип провода": "монтажный", "Число жил": "1", "Стандарт": "ТУ 16-505.185-71",
     "Масса, г/м": "4", "Источник данных": "Склад ЦАР"},
    {"Марка": "КСПВГ 4x0,20", "Тип провода": "многожильный", "Число жил": "4", "Цвета": "белый, синий",
     "Темп. от, °C": "-60", "Экран": "нет"},
    {"Марка": "RG316", "Тип провода": "коаксиальный", "Стандарт": "MIL-C-17", "Масса, г/м": "12"},
]


def _seed_prov(client) -> dict[str, dict]:
    assert client.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [
        {"tab": "prov", "display_text": row["Марка"], "source_sheet": "БД.ПРОВ", "fields": row} for row in PROV_ROWS
    ]}).status_code == 200
    # the owner's own columns as on prod (registered before 25.10, when «+ Столбец» still took them): «Дата цены» in
    # every list (removed), «Материал жилы» of the многожильные, «Материал оболочки» and the coax bend radius (kept)
    _register_own({"wire": ("Дата цены", "Материал оболочки"), "multi": ("Дата цены", "Материал жилы"),
                   "coax": ("Дата цены", "Радиус изгиба неподвижно, мм")})
    return _prov_records(client)


def _register_own(columns: dict[str, tuple[str, ...]], tab: TechOperationTab = TechOperationTab.WIRE) -> None:
    uow = app.state.references_uow_factory()
    with uow:
        entries = list(uow.references.list())
        position = len(entries)
        for kind, names in columns.items():
            for name in names:
                entries.append(ReferenceEntry.create(ReferenceKind.CUSTOM_COLUMN,
                                                     custom_column_prefix(tab, kind or None) + name, name, position))
                position += 1
        uow.references.replace(tuple(entries))
        uow.commit()


def _prov_records(client) -> dict[str, dict]:
    return {record["fields"]["Марка"]: record for record in
            client.get("/api/references/tech-operations", params={"tab": "prov"}).json()["records"]}


def test_the_cable_lists_send_no_removed_column_but_the_rows_keep_the_values(references_api):
    records = _seed_prov(references_api)
    body = references_api.get("/api/references/cables").json()

    assert not CABLE_DROPPED["wire"] & set(body["columns_by_kind"]["wire"])
    assert not CABLE_DROPPED["multi"] & set(body["columns_by_kind"]["multi"])
    assert body["custom_columns_by_kind"]["wire"] == ["Материал оболочки"]
    assert body["custom_columns_by_kind"]["multi"] == ["Материал жилы"]
    assert "Материал оболочки" in body["columns_by_kind"]["wire"]
    assert body["removed_columns_by_kind"]["wire"] == list(CABLE_WRITABLE["wire"][len(CABLE_COLUMNS["wire"]):])
    # the coax cable list: «Стандарт», «Масса», the bend radius, «Частота до» stay; «Дата цены», «Источник данных» go
    assert {"Стандарт", "Масса, г/м", "Частота до, ГГц", "Радиус изгиба неподвижно, мм", "Ссылка на datasheet"} <= set(
        body["columns_by_kind"]["coax"])
    assert not {"Дата цены", "Источник данных", "Напряжение, В", "Закупок", "Цвета"} & set(body["columns_by_kind"]["coax"])
    assert body["custom_columns_by_kind"]["coax"] == ["Радиус изгиба неподвижно, мм"]
    assert "Источник данных" in body["removed_columns_by_kind"]["coax"]
    # the values stay in the rows (the card, the harness catalogue, the kind rule read them)
    rows = {row["cable"]: row for row in body["rows"] if row.get("record")}
    assert rows["МГТФ 0,35"]["record"]["fields"]["Стандарт"] == "ТУ 16-505.185-71"
    assert rows["МГТФ 0,35"]["record"]["fields"]["Источник данных"] == "Склад ЦАР"
    assert rows["КСПВГ 4x0,20"]["record"]["fields"]["Цвета"] == "белый, синий"
    assert records["МГТФ 0,35"]["cable_kind"] == "wire" and records["КСПВГ 4x0,20"]["cable_kind"] == "multi"


def test_an_existing_row_still_takes_a_write_to_a_removed_column(references_api):
    records = _seed_prov(references_api)
    wire, multi = records["МГТФ 0,35"], records["КСПВГ 4x0,20"]

    # a removed cell the row has, one it lacks («Закупок»), the provenance appended as the journals do
    response = references_api.patch(f"/api/references/tech-operations/{wire['id']}", json={"edits": [
        {"field": "Стандарт", "old": "ТУ 16-505.185-71", "value": "ГОСТ 22483"},
        {"field": "Закупок", "old": "", "value": "3"},
        {"field": "Источник данных", "old": "Склад ЦАР", "value": "Склад ЦАР; паспорт 25.10"},
    ]})
    assert response.status_code == 200, response.text
    response = references_api.patch(f"/api/references/tech-operations/{multi['id']}", json={"edits": [
        {"field": "Материал жилы", "old": "", "value": "медь"}, {"field": "Дата цены", "old": "", "value": "25.10.2026"}]})
    assert response.status_code == 200, response.text

    fields = {mark: record["fields"] for mark, record in _prov_records(references_api).items()}
    assert fields["МГТФ 0,35"]["Стандарт"] == "ГОСТ 22483" and fields["МГТФ 0,35"]["Закупок"] == "3"
    assert fields["МГТФ 0,35"]["Источник данных"] == "Склад ЦАР; паспорт 25.10"
    assert fields["КСПВГ 4x0,20"]["Материал жилы"] == "медь" and fields["КСПВГ 4x0,20"]["Дата цены"] == "25.10.2026"


def _load_prov(client, rows: list[dict]) -> dict:
    response = client.post("/api/references/import/prov", params={"dry_run": 0},
                           json={"rows": [{"fields": fields} for fields in rows]})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_load_writes_a_removed_column_into_an_existing_row_and_only_the_provenance_into_a_new_one(references_api):
    _seed_prov(references_api)

    report = _load_prov(references_api, [
        {"Марка": "МГТФ 0,35", "Цвет": "белый", "Масса, г/м": "4,2", "Источник данных": "Склад ЦАР; ТУ"},  # existing
        {"Марка": "КСПВГ 4x0,20", "Ссылка на datasheet": "https://example.ru/kspvg.pdf"},  # existing, a cell it lacks
        {"Марка": "МГТФ 0,5", "Цвет": "белый", "Вид кабеля": "wire", "Источник данных": "паспорт 25.10"},  # new
        {"Марка": "МГТФ 0,12", "Цвет": "белый", "Вид кабеля": "wire", "Закупок": "5"},  # new: refused
        {"Марка": "ПуГВ 1,5", "Тип провода": "монтажный"},  # new, the list by the rule from the type
    ])

    assert len(report["updated"]) == 2 and len(report["refused"]) == 1, report
    reason = report["refused"][0]["reason"]
    assert "«Закупок» — не столбец списка «Монтажные провода»" in reason and "25.10.2026" in reason
    records = _prov_records(references_api)
    assert records["МГТФ 0,35"]["fields"]["Масса, г/м"] == "4,2"
    assert records["МГТФ 0,35"]["fields"]["Источник данных"] == "Склад ЦАР; ТУ"
    assert records["КСПВГ 4x0,20"]["fields"]["Ссылка на datasheet"] == "https://example.ru/kspvg.pdf"
    new = records["МГТФ 0,5"]
    assert new["cable_kind"] == "wire" and new["fields"]["Источник данных"] == "паспорт 25.10"
    assert not {"Закупок", "Дата цены", "Цена посл., ₽/м без НДС"} & set(new["fields"])  # no removed cell padded
    assert new["fields"]["Стандарт"] == ""  # a column of the list is
    assert "МГТФ 0,12" not in records
    assert records["ПуГВ 1,5"]["cable_kind"] == "wire"


def test_a_removed_column_is_not_added_back_by_the_add_column_button(references_api):
    _seed_prov(references_api)
    response = references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "wire", "name": "закупок"})
    assert response.status_code == 422 and "убран из таблицы" in response.text
    response = references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "coax", "name": "Дата цены"})
    assert response.status_code == 422 and "убран из таблицы" in response.text
    # a new own column is added as before
    response = references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "coax", "name": "Полка"})
    assert response.status_code == 201, response.text


def test_the_file_of_a_list_has_no_removed_column_but_keeps_the_key(references_api):
    _seed_prov(references_api)
    for kind in ("wire", "multi"):
        fields = references_api.get("/api/references/import/prov/file/fields", params={"kind": kind}).json()["fields"]
        assert "Марка" in fields and not CABLE_DROPPED[kind] & set(fields), kind
    assert "Цвет" in references_api.get("/api/references/import/prov/file/fields", params={"kind": "wire"}).json()["fields"]
    coax = references_api.get("/api/references/import/prov/file/fields", params={"kind": "coax"}).json()["fields"]
    assert {"Марка", "Стандарт", "Масса, г/м", "Частота до, ГГц"} <= set(coax) and not CABLE_DROPPED["coax"] & set(coax)


CONNECTOR = {"Артикул": "SMA-J-RG316", "Тип/Серия": "SMA-male R/A", "Провод": "RG316", "L1": "3", "Пол": "штекер",
             "Исполнение": "угловой", "Полярность": "прямая", "Монтаж": "на кабель", "Примечание": "второй сайт",
             "Источник данных": "gsm-repiteri.ru, 12.10.2026", "Цена, ₽": "350", "Наименование": "Разъём SMA"}


def test_the_coax_connector_table_drops_the_unchecked_columns_and_the_card_keeps_them(references_api):
    assert references_api.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [
        {"tab": "coax", "display_text": "SMA-J-RG316", "source_sheet": "БД.КОАКС", "fields": CONNECTOR}]}).status_code == 200
    assert references_api.post(COLUMNS, json={"tab": "coax", "name": "HEX обжима, мм"}).status_code == 201
    # «Дата цены» — the owner's own column on prod, registered before 25.10 — is removed with the others
    _register_own({"": ("Дата цены",)}, TechOperationTab.COAX)
    response = references_api.post(COLUMNS, json={"tab": "coax", "name": "Примечание"})
    assert response.status_code == 422 and "убран из таблицы" in response.text

    listed = references_api.get("/api/references/tech-operations", params={"tab": "coax"}).json()
    removed = {"Пол", "Исполнение", "Полярность", "Монтаж", "Дата цены", "Примечание", "Источник данных"}
    assert not removed & set(listed["columns"]) and not removed & set(listed["custom_columns"])
    assert {"Артикул", "Провод", "Наименование", "Цена, ₽", "HEX обжима, мм"} <= set(listed["columns"])
    record = listed["records"][0]
    assert all(record["fields"][name] == CONNECTOR[name] for name in removed - {"Дата цены"})

    # an existing row still takes a write to them (the card's «Править запись», the loads)
    response = references_api.patch(f"/api/references/tech-operations/{record['id']}", json={"edits": [
        {"field": "Примечание", "old": "второй сайт", "value": "второй сайт; АМЭЛ"},
        {"field": "Источник данных", "old": "gsm-repiteri.ru, 12.10.2026", "value": "gsm-repiteri.ru, 25.10.2026"},
        {"field": "Дата цены", "old": "", "value": "25.10.2026"}]})
    assert response.status_code == 200, response.text
    assert response.json()["fields"]["Примечание"] == "второй сайт; АМЭЛ"
    # the file of «Скачать для правки» keeps the key, without the removed columns
    fields = references_api.get("/api/references/import/coax/file/fields").json()["fields"]
    assert {"Артикул", "Провод"} <= set(fields) and not removed & set(fields)
