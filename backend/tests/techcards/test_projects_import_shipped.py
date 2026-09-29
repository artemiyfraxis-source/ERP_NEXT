"""scripts/projects_import.py: отгруженное по листу цеха «ЗАГРУЗ» — в уже загруженную базу."""

import importlib.util
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.main import app
from app.modules.shipping.application import RegisterGoodCommand, register_good
from tests.factories import Actor
from tests.techcards.test_kitset_api import _load, kitset_api  # noqa: F401 — фикстура

_spec = importlib.util.spec_from_file_location("projects_import", Path(__file__).parents[3] / "scripts" / "projects_import.py")
projects_import = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(projects_import)

HEAD = ["Комментарий", "Статус", "% Готовн", "В плане", "№ Проекта", "Наименование организции"]


def _grid(*rows: tuple[str, str, str]) -> list[list[str]]:
    return [["", "", "", "", "", ""], HEAD, *[[comment, status, "100%", "TRUE", no, "ООО"] for comment, status, no in rows]]


# Строки как в «ЗАГРУЗ» 30.09.2026.
SHEET = _grid(
    ("ОТГРУЗКА  ", "ОТГРУЖЕН", "714К. 1"),
    ("Отгрузили 154 шт ", "ОТГРУЖЕН", "70К.26.13.2"),
    ("Привезут 600 камер для анализа", "ГОТОВО", "70К.26.13.2"),
    ("ОТГРУЗКА  60000", "ОТГРУЖЕН", "35К.М.34"),
    ("Проверка 3 партии", "ОЖИД КОМПЛ., К ЗАПУС", "35К.М.34"),
    ("Отгрузили 50 шт. Придут разъемы и донабить еще 50 шт. ", "ГОТОВО, НА ОТГР", "720К.1.2"),
    ("Вторнику утром Степа отдаст еще 600 шт.", "В РАБОТЕ", "506К.6"),
    ("ОТГРУЗКА 1 200", "В РАБОТЕ", "591К.4"),
    ("", "ОТГРУЖЕН", "713К.3"),
    ("Делаем", "В РАБОТЕ", "713К.3"),
)


def test_partial_shipments_take_the_count_from_the_comment_of_a_shipped_row_or_a_past_tense_note():
    rows = projects_import.shop_rows(SHEET)
    partial = projects_import.partial_shipments(rows)

    assert projects_import.shipped_projects(rows) == {"714К.1"}
    assert {k: v["total"] for k, v in partial.items()} == {
        "70К.26.13.2": Decimal("154"), "35К.М.34": Decimal("60000"), "720К.1.2": Decimal("50"), "713К.3": None,
    }


def test_sync_removes_fully_shipped_and_records_partial_shipment_once(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    assert _load(client, number="714К.1").status_code == 200
    actor = Actor(id=uuid4(), name="перенос", department="")
    now = datetime(2026, 9, 30, 9, 0)
    register_good(RegisterGoodCommand(
        project="70К.26.13.2", item_index="КБ1", customer="СПТ", article="", name="Жгут", ordered=Decimal("10270"),
        unit="шт", note="", actor=actor, now=now,
    ), app.state.shipping_uow_factory())
    for index in ("КБ1", "КБ2"):
        register_good(RegisterGoodCommand(
            project="35К.М.34", item_index=index, customer="", article="", name="Жгут", ordered=Decimal("1000000"),
            unit="шт", note="", actor=actor, now=now,
        ), app.state.shipping_uow_factory())

    done, skipped = projects_import.sync_shipped(SHEET, app.state, actor, now)
    again = projects_import.sync_shipped(SHEET, app.state, actor, now)

    with app.state.shipping_uow_factory() as uow:
        goods = {(g.project, g.item_index): g for g in uow.goods.list()}
    assert client.get("/api/techcards/kitsets/714К.1").status_code == 404
    assert not [key for key in goods if key[0] == "714К.1"]
    legacy = goods[("70К.26.13.2", "КБ1")]
    assert (legacy.accepted, legacy.shipped, legacy.sent, legacy.before_erp) == (Decimal("154"),) * 4
    assert goods[("35К.М.34", "КБ1")].before_erp == 0
    assert any("714К.1" in line for line in done) and any("70К.26.13.2" in line for line in done)
    assert any("35К.М.34" in line and "КБ 2" in line for line in skipped)
    assert any("713К.3" in line and "не написано" in line for line in skipped)
    assert again[0] == []


def test_sync_keeps_a_shipped_project_that_already_moved_in_erp(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    assert _load(client, number="714К.1").status_code == 200
    actor = Actor(id=uuid4(), name="перенос", department="")
    with app.state.shipping_uow_factory() as uow:
        good = next(g for g in uow.goods.list() if g.project == "714К.1")
        uow.goods.add(good.accept(Decimal("1"), datetime(2026, 9, 30).date(), "ГП.26.0000001"))
        uow.commit()

    done, skipped = projects_import.sync_shipped(SHEET, app.state, actor, datetime(2026, 9, 30, 9, 0))

    assert client.get("/api/techcards/kitsets/714К.1").status_code == 200
    assert not [line for line in done if "714К.1" in line]
    assert any("714К.1" in line and "не удаляю" in line for line in skipped)
