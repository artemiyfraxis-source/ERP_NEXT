"""Планирование цеха по ID модели оборудования: ключ и единицы станка — модель БД.ОБ, не текст; обеспечивающее
оборудование единиц не даёт; переименование модели не меняет ни единиц, ни плана."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

from app.main import app
from app.modules.production.autoplan import setup_key
from app.modules.production.equipment_catalog import EMPTY, build_catalog
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from tests.production.test_autoplan_api import (  # noqa: F401 — фикстура client
    ANNA, NOW, apply, client, launch, preview,
)

PRESS = UUID("70000000-0000-0000-0000-000000000001")
COMPRESSOR = UUID("70000000-0000-0000-0000-000000000002")
OTHER = UUID("70000000-0000-0000-0000-000000000003")


def unit(number: str, model: UUID, name: str, article: str = "", site: str = "ЦАР"):
    return SimpleNamespace(equipment_model_id=model, display_text=name, fields={
        "Тип": "Оборудование", "Инв. номер": number, "Наименование": name, "Артикул": article, "Расположение": site,
    })


def catalog(press_name: str = "Пресс автоматический"):
    return build_catalog(
        [unit("ОБ.070.1", PRESS, press_name, "AP-1"), unit("ОБ.070.2", PRESS, press_name, "AP-1"),
         unit("ОБ.070.X", PRESS, press_name, "AP-1"), unit("ОБ.070.3", COMPRESSOR, "Компрессор", "C-1")],
        [{"id": str(PRESS), "name": press_name, "article": "AP-1", "role": "operational", "display": f"AP-1/{press_name}"},
         {"id": str(COMPRESSOR), "name": "Компрессор", "article": "C-1", "role": "supporting", "display": "C-1/Компрессор"}],
    )


def tool(number: str, name: str = "Пресс", **extra) -> dict:
    return {"section": "Оборудование", "inventory_number": number, "name": name, **extra}


def test_machine_is_the_model_and_units_are_its_physical_operational_units():
    equipment = catalog()

    for reference in ("ОБ.070.X", "ОБ.070", "ОБ.070.2"):
        machine = equipment.machine(tool(reference))
        assert (machine.key, machine.code, machine.label) == (str(PRESS), "ОБ.070", "ОБ.070 Пресс автоматический")
    # Расчётная «.X» — не единица; компрессор того же номера модели — обеспечивающий: станком не бывает, единиц не даёт.
    assert dict(equipment) == {str(PRESS): 2}
    assert equipment.machine(tool("ОБ.070.3", "Компрессор")) is None
    sites, labels = equipment.site_units()
    assert sites == {("ЦАР", str(PRESS)): 2} and labels == {("ЦАР", str(PRESS)): "ОБ.070 Пресс автоматический"}


def test_supporting_row_is_skipped_and_explicit_model_id_wins():
    equipment = catalog()
    built = {"type": "insHousing", "card": {"tooling": [tool("ОБ.070.3", "Компрессор"), tool("ОБ.070.X")]}}

    key = setup_key(code="ASM", stage="Сборка", built=built, config_op={}, config={}, equipment=equipment)

    assert (key.machine, key.machine_label, key.limited) == (str(PRESS), "ОБ.070 Пресс автоматический", True)
    assert equipment.machine(tool("ОБ.099", equipment_model_id=str(PRESS))).key == str(PRESS)
    assert equipment.machine(tool("ОБ.070", equipment_model_id=str(COMPRESSOR))) is None


def test_renaming_the_model_keeps_the_machine_key_units_and_setup_family():
    before, after = catalog(), catalog("Пресс новый")
    built = {"type": "prsTerm", "fields": {"applicator": "A19"}, "card": {"tooling": [tool("ОБ.070.X", "Старое название")]}}

    old = setup_key(code="PRS", stage="Опрессовка", built=built, config_op={}, config={}, equipment=before)
    new = setup_key(code="PRS", stage="Опрессовка", built=built, config_op={}, config={}, equipment=after)

    assert (old.machine, old.family, old.exact) == (new.machine, new.family, new.exact)
    assert dict(before) == dict(after) == {str(PRESS): 2}
    assert new.machine_label == "ОБ.070 Пресс новый"


def test_several_models_under_one_number_without_a_choice_are_counted_together():
    equipment = build_catalog(
        [unit("ОБ.027.1", PRESS, "Станок", "EW-50A"), unit("ОБ.027.2", OTHER, "Станок", "WL-2.0")],
        [{"id": str(PRESS), "name": "Станок", "article": "EW-50A", "role": "operational"},
         {"id": str(OTHER), "name": "Станок", "article": "WL-2.0", "role": "operational"}],
    )

    assert equipment.machine(tool("ОБ.027.X", "Станок")).key == "ОБ.027"
    assert equipment["ОБ.027"] == 2
    assert equipment.machine(tool("ОБ.027.X", "EW-50A | Станок")).key == str(PRESS)
    assert equipment.machine(tool("ОБ.027.2")).key == str(OTHER)
    # Номера нет в БД.ОБ — станок по номеру модели, как раньше (единица одна — по умолчанию у читателя).
    unknown = EMPTY.machine(tool("ОБ.050.1"))
    assert (unknown.key, unknown.label) == ("ОБ.050", "ОБ.050 Пресс")


# --- через HTTP: справочник, автоплан, загрузка станков, доска -------------------------------------------------------


def _equipment_rows(name: str) -> None:
    """Пресс ОБ.070 (две единицы) и компрессор ОБ.070.3; «Машина» БД.ОП сборки ASM — тот же пресс."""
    with app.state.references_uow_factory() as references:
        existing = {record.fields.get("Инв. номер"): record for record in references.tech_operations.list(
            tab=TechOperationTab.EQUIPMENT, query="", limit=100)}
        for number, title, article in (("ОБ.070.1", name, "AP-1"), ("ОБ.070.2", name, "AP-1"), ("ОБ.070.3", "Компрессор", "C-1")):
            fields = {"Тип": "Оборудование", "Инв. номер": number, "Наименование": title, "Артикул": article,
                      "Для базы": f"{article}/{title}", "Расположение": "ЦАР"}
            record = existing.get(number)
            references.tech_operations.save(replace(record, fields=fields) if record is not None else TechOperationRecord.create(
                tab=TechOperationTab.EQUIPMENT, display_text=title, search_text=number, source_sheet="БД.ОБ",
                sort_key=number, fields=fields,
            ))
        if not references.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=10):
            references.tech_operations.save(TechOperationRecord.create(
                tab=TechOperationTab.OPERATION, display_text="Сборка ASM", search_text="ASM", source_sheet="БД.ОП",
                sort_key="ASM", fields={"opNumber": "ASM", "Номер": "ASM", "Машина": "AP-1/Пресс автоматический"},
            ))
        references.commit()


def _press_card(http, index: str) -> dict:
    task = launch(http, index)
    with app.state.techcards_uow_factory() as uow:
        card = next(item for item in uow.techcards.list() if item.item_index == index)
        document = dict(card.document)
        operations = [dict(item) for item in document["operations"]]
        operations[0]["card"] = {"tooling": [
            {"section": "Оборудование", "inventory_number": "ОБ.070.3", "name": "Компрессор"},
            {"section": "Оборудование", "inventory_number": "ОБ.070.X", "name": "Пресс"},
        ]}
        uow.techcards.save(replace(card, document={**document, "operations": operations}))
        uow.commit()
    return task


def _state(http) -> tuple[dict, dict, dict]:
    planned = preview(http)
    params = {"date_from": NOW.date().isoformat(), "date_to": NOW.date().isoformat()}
    load = http.get("/api/production/machine-load", params=params).json()
    board = http.get("/api/production/board").json()
    return planned, load, board


def test_renamed_model_keeps_capacity_and_plan_and_supporting_unit_adds_nothing(client):
    http, _ = client
    _equipment_rows("Пресс автоматический")
    tasks = [_press_card(http, index) for index in ("КБ1", "КБ2", "КБ3")]
    applied = apply(http, preview(http))
    assert applied.status_code == 200, applied.text

    planned, load, board = _state(http)
    [press] = [row for row in load["machines"] if row["machine"] == "ОБ.070"]
    model_id = press["model_id"]
    assert UUID(model_id) and press["units"] == 2 and press["known"]  # компрессор ОБ.070.3 единиц не добавил
    assert press["label"] == "ОБ.070 Пресс автоматический" and load["warnings"] == []
    crimps = [item for item in planned["proposals"] if item["code"] == "PRS"]
    assert len(crimps) == len(tasks) and {item["machine"] for item in crimps} == {"ОБ.070 Пресс автоматический"}
    groups = {operation["code"]: operation["equipment"] for row in board["tasks"] for operation in row["operations"]}
    assert groups["PRS"] == {"key": model_id, "label": "ОБ.070 Пресс автоматический", "limited": True}
    # «Машина» БД.ОП сборки — по связи с моделью: та же модель, что станок опрессовки.
    assert groups["ASM"] == {"key": model_id, "label": "ОБ.070 Пресс автоматический", "limited": False}

    _equipment_rows("Пресс новый")
    with app.state.references_uow_factory() as references:
        [operation] = references.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=10)
    assert operation.fields["Машина"] == "AP-1/Пресс новый"  # текст ячейки — копия, связь по ID осталась

    renamed, load_after, board_after = _state(http)
    [press_after] = [row for row in load_after["machines"] if row["machine"] == "ОБ.070"]
    assert (press_after["model_id"], press_after["units"]) == (model_id, 2)
    assert press_after["label"] == "ОБ.070 Пресс новый"
    assert press_after["days"] == press["days"]
    assert renamed["fingerprint"] == planned["fingerprint"]
    plan_of = lambda body: [(item["operation_id"], item["operator_id"], item["start"], item["finish"]) for item in body["proposals"]]
    assert plan_of(renamed) == plan_of(planned)
    groups = {operation["code"]: operation["equipment"] for row in board_after["tasks"] for operation in row["operations"]}
    assert groups["PRS"]["key"] == groups["ASM"]["key"] == model_id
    assert groups["ASM"]["label"] == "ОБ.070 Пресс новый"
