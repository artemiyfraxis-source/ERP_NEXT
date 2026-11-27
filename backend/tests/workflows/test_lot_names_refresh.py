"""«Пора сам склад обновить» (owner 26.10.2026): the lots taken in before PR #720 get our short name (only where the name
is the article), the dictionary type and the maker (only where empty), and «Наим. по УПД» = the article (only where
empty) — from the catalog first, the model second; the reviewed CSV is applied exactly, journaled, idempotent."""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database  # noqa: F401 — every module's tables in the metadata (the CLI reads purchasing and references)
import app.main  # noqa: F401 — every table, so a schema made after the CLI import (label jobs) resolves its FKs
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork, StockLotRow
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows import lot_names_refresh as flow
from app.workflows.stock_name import has_builder
from app.workflows.stock_name_types import LotHints, StockName
from app.workflows.lot_names_refresh import (
    Catalog,
    apply_changes,
    name_is_article,
    plan_changes,
    read_csv,
    write_csv,
)
from tests.factories import Actor

NOW = datetime(2026, 10, 26, 18, 0)
ACTOR = Actor(name=flow.ACTOR_NAME, department="")


@pytest.fixture(autouse=True)
def no_full_names(monkeypatch):
    """The rules of 26.10 below are checked without the full-name builders (their own tests cover them); a test of
    the 27.11 wave sets its stub builder with `full_names`."""
    monkeypatch.setattr(flow, "build_stock_name", lambda hints, records=(): None)


def _lot(grn: str, article: str, name: str, **extra) -> StockLot:
    return StockLot.create(
        warehouse=extra.pop("warehouse", "ЦАР"), grn=grn, package="", article=article, name=name,
        item_type=extra.pop("item_type", ""), purpose="Склад", unit="шт", quantity=Decimal("5"),
        storage_location="1F4", source_receiving_ids=(f"test:{grn}",), **extra,
    )


@pytest.mark.parametrize(("name", "article", "maker", "expected"), [
    ("", "XHP-4", "", True),
    ("XHP-4", "XHP-4", "", True),
    ("PLS-4", "PLS-4", "", True),
    ("  PLS-4 ", "PLS-4", "", True),  # spaces trimmed
    ("SMA  Female", "SMA Female", "", True),  # and collapsed
    # Only LITERALLY the article (owner after the prod dry run 26.10): case or separators differ — the house style.
    ("SMA-female RG-58", "SMA-Female RG-58", "", False),
    ("MCX-male R/A RG-316/RG-174", "MCX-Male R/A RG-316/RG-174", "", False),
    ("xhp 4", "XHP-4", "", False),
    ("0430250600", "43025-0600", "", False),
    ("XHP-4@JST", "XHP-4", "", True),  # «article@MAKER» leftover
    ("XHP-4##6000", "XHP-4", "", True),
    ("JST XHP-4", "XHP-4", "JST", True),  # article + maker
    ("XHP-4 (JST)", "XHP-4", "jst", True),
    ("XHP-4 JST", "XHP-4@JST", "", True),  # the maker still glued to the article
    ("JST", "XHP-4", "JST", False),  # the maker alone is not the article
    ("BNC-male RG-174", "BNC-M174", "", False),
    ("Amass XT 2pin (однорядный)", "XT30PW-M", "AMASS", False),
    ("Клемма JST XH 2.54 мм, 4 pin, розетка", "XHP-4", "JST", False),
    ("Корпус XHP-4 JST", "XHP-4", "JST", False),  # a word besides the article and the maker: a human name
])
def test_human_names_are_kept_and_article_names_are_not(name, article, maker, expected):
    assert name_is_article(name, article, maker) is expected


def _by(plan) -> dict[tuple[str, str], tuple[str, str, str]]:
    return {(change.grn, change.field): (change.was, change.now, change.source) for change in plan.changes}


def test_plan_fills_only_what_the_rules_allow():
    lots = [
        _lot("G1", "XHP-4", "XHP-4"),  # article name, no type, no maker, no upd_name
        _lot("G2", "BNC-M174", "BNC-male RG-174", item_type="Коаксиальный соединитель"),  # human name, type kept
        _lot("G3", "TEST-1", "Своё наименование", item_type="Кабель", manufacturer="MOLEX", upd_name="Как в УПД"),
        _lot("G4", "PBS10B", ""),  # the reference type «Кнопка» is not in the dictionary
        _lot("G5", "TEST-2", "TEST-2"),  # known to the purchasing nomenclature only
    ]
    item = {"name": "Датчик холла A1324", "item_type": "микросхема", "manufacturer": "Allegro"}
    plan = plan_changes(lots, Catalog(purchasing=lambda article: item if article == "TEST-2" else None))
    by = _by(plan)
    assert by[("G1", "name")] == ("XHP-4", "XH 4pin (однорядный)", "база: семейство корпуса")
    assert by[("G1", "item_type")] == ("", "Соединитель", "база: семейство корпуса")
    assert by[("G1", "manufacturer")] == ("", "JST", "база: семейство корпуса")  # «JST XH»
    assert by[("G1", "upd_name")] == ("", "XHP-4", "артикул")
    assert ("G2", "name") not in by and ("G2", "item_type") not in by  # a human name and a filled type stay
    assert by[("G2", "upd_name")] == ("", "BNC-M174", "артикул")
    assert not any(grn == "G3" for grn, _ in by)  # everything filled: nothing to do
    assert by[("G4", "name")][1] == "Кнопка PBS-10B без фиксации"
    assert ("G4", "item_type") not in by  # «Кнопка» is no stock type: left for the storekeeper
    assert by[("G5", "name")] == ("TEST-2", "Датчик холла A1324", "база: номенклатура закупок")
    assert by[("G5", "item_type")][1] == "Микросхема"  # spelled as the dictionary has it
    assert by[("G5", "manufacturer")][1] == "ALLEGRO"
    assert "модель не спрашивали" in plan.model_note


def test_another_lot_of_the_article_and_the_glued_maker_are_sources():
    lots = [
        _lot("G1", "SOME-77", "SOME-77"),
        _lot("G2", "some 77", "Датчик тестовый", item_type="Микросхема"),
        _lot("G3", "ABC-9", "ABC-9@RUICHI"),
    ]
    by = _by(plan_changes(lots, Catalog()))
    assert by[("G1", "name")] == ("SOME-77", "Датчик тестовый", "база: партия G2")
    assert by[("G1", "item_type")] == ("", "Микросхема", "база: партия G2")
    assert by[("G3", "manufacturer")] == ("", "RUICHI", "артикул")


class FakeModel:
    def __init__(self, answer: dict):
        self.calls: list[list[dict]] = []
        self.answer = answer

    def __call__(self, rows):
        self.calls.append(rows)
        return [dict(self.answer) for _ in rows]


def test_the_catalog_wins_over_the_model_and_the_model_fills_the_rest():
    model = FakeModel({"short_name": "Колодка ZZ, 6 конт.", "item_type": "Соединитель", "manufacturer": "jst"})
    lots = [
        _lot("G1", "BNC-M174", "BNC-M174"),  # the catalog knows the name and the type; the maker only the model
        _lot("G2", "ZZ-500", "ZZ-500", upd_name="Колодка ZZ-500 6 конт., серия ZZ"),
        _lot("G3", "ok-1", "Человеческое имя", item_type="Кабель", manufacturer="X"),  # nothing missing: not asked
    ]
    by = _by(plan_changes(lots, Catalog(), model=model))
    assert len(model.calls) == 1
    asked = {row["article"]: row for row in model.calls[0]}
    assert set(asked) == {"BNC-M174", "ZZ-500"}
    assert asked["ZZ-500"]["upd_name"] == "Колодка ZZ-500 6 конт., серия ZZ"  # the printed line helps the model
    assert by[("G1", "name")][1:] == ("BNC-male RG-174", "база: разбор кода")
    assert by[("G1", "item_type")][1:] == ("Коаксиальный соединитель", "база: разбор кода")  # not the model's
    assert by[("G1", "manufacturer")][1:] == ("JST", "модель")
    assert by[("G2", "name")][1:] == ("Колодка ZZ, 6 конт.", "модель")
    assert by[("G2", "item_type")][1:] == ("Соединитель", "модель")
    assert ("G2", "upd_name") not in by  # a filled «Наим. по УПД» is never overwritten


def test_the_model_type_must_be_a_dictionary_type_and_its_name_a_human_one():
    model = FakeModel({"short_name": "ZZ-500", "item_type": "Кнопка", "manufacturer": ""})
    by = _by(plan_changes([_lot("G1", "ZZ-500", "")], Catalog(), model=model))
    assert set(by) == {("G1", "upd_name")}


# ── after the prod dry run (owner 26.10.2026) ──────────────────────────────────────────────────────


def test_a_name_differing_from_the_article_by_case_or_separators_is_kept():
    answers = {"PLS-4": "PLS 4pin (однорядный)"}

    def model(rows):
        return [{"short_name": answers.get(row["article"], "SMA гнездовой для RG-58"),
                 "item_type": "Коаксиальный соединитель", "manufacturer": ""} for row in rows]

    lots = [
        _lot("G1", "SMA-Female RG-58", "SMA-female RG-58"),
        _lot("G2", "MCX-Male R/A RG-316/RG-174", "MCX-male R/A RG-316/RG-174", item_type="Коаксиальный соединитель"),
        _lot("G3", "PLS-4", "PLS-4", item_type="Соединитель", manufacturer="CONNFLY"),
    ]
    by = _by(plan_changes(lots, Catalog(), model=model))
    assert ("G1", "name") not in by and ("G2", "name") not in by  # the house style stays
    assert by[("G1", "item_type")][1] == "Коаксиальный соединитель"  # what was empty is still filled
    assert by[("G3", "name")] == ("PLS-4", "PLS 4pin (однорядный)", "модель")  # literally the article: replaced


@pytest.mark.parametrize(("answer", "article", "kept"), [
    ("Соединитель Molex", "5025780600", False),
    ("Электрический соединитель", "QQ-9000", False),
    ("Соединитель, 20 контактов", "QQ-9000", False),
    ("Соединитель CONNFLY", "QQ-9000", False),
    ("Кабель постоянного тока", "QQ-9000", False),
    ("SUR 32pin (однорядный)", "02SUR-32S", False),  # the article says 2 pins
    ("Соединитель JST SUR, 32-контактный", "02SUR-32S", False),
    ("SUR 2pin (однорядный)", "02SUR-32S", True),
    ("BLD 10pin (двухрядный)", "I-DS1071-01-SCB02x10", True),
    ("Колодка I-DS1071-01-SCB02x10 (BLD)", "I-DS1071-01-SCB02x10", False),  # the article inside
    ("Провод ПВАМ 0,35 мм² красный", "ПВАМ 0,35 красный", True),  # a Cyrillic mark is a series word
    ("Трубка ТУТ 6/3 чёрный", "ТУТ 6/3 ч", True),
    ("SMA-female RG-58", "SMA-KWE", True),
])
def test_a_vague_model_name_is_dropped(answer, article, kept):
    assert flow.model_name(answer, article, ["MOLEX", "JST"]) == (answer if kept else "")


def test_a_vague_model_answer_leaves_the_name_as_it_was():
    model = FakeModel({"short_name": "Соединитель Molex", "item_type": "Соединитель", "manufacturer": "Molex"})
    by = _by(plan_changes([_lot("G1", "QQ-9000", "QQ-9000")], Catalog(), model=model))
    assert ("G1", "name") not in by
    assert by[("G1", "item_type")][1:] == ("Соединитель", "модель")  # type and maker answers are kept
    assert by[("G1", "manufacturer")][1:] == ("MOLEX", "модель")


def test_the_family_goes_before_the_nomenclature_and_a_nomenclature_name_with_the_article_is_skipped():
    names = {
        "XHP-4": "Корпус разъёма JST XHP 4 конт.",
        "I-DS1071-01-SCB02x10": "Соединитель I-DS1071-01-SCB02x10 (CONNFLY, BLD2)",
        "I-DS1071-01-SCB02x8": "Соединитель I-DS1071-01-SCB02x8 (CONNFLY, BLD2)",
    }
    catalog = Catalog(purchasing=lambda article: {"name": names.get(article, ""), "manufacturer": "CONNFLY"})
    lots =[_lot("G1", "XHP-4", "XHP-4"), _lot("G2", "I-DS1071-01-SCB02x10", "I-DS1071-01-SCB02x10"),
            _lot("G3", "I-DS1071-01-SCB02x8", "I-DS1071-01-SCB02x8")]
    answers = {"I-DS1071-01-SCB02x10": "BLD 10pin (двухрядный)", "I-DS1071-01-SCB02x8": "BLD 8pin (двухрядный)"}

    def styled(rows):
        return [{"short_name": answers.get(row["article"], ""), "item_type": "Соединитель", "manufacturer": ""}
                for row in rows]

    by = _by(plan_changes(lots, catalog, model=styled))
    assert by[("G1", "name")][1:] == ("XH 4pin (однорядный)", "база: семейство корпуса")
    # Both BLD siblings in ONE style — the model's, told the house style; the nomenclature lines are not names.
    assert by[("G2", "name")][1:] == ("BLD 10pin (двухрядный)", "модель")
    assert by[("G3", "name")][1:] == ("BLD 8pin (двухрядный)", "модель")


def test_style_examples_are_the_stocks_own_complete_names_by_type():
    lots = [
        _lot("G1", "XHP-4", "XH розетка (мама) 4pin (однорядный)", item_type="Соединитель"),
        _lot("G2", "XHP-4", "XH розетка (мама) 4pin (однорядный)", item_type="Соединитель"),
        _lot("G3", "BNC-M174", "BNC-male RG-174", item_type="Коаксиальный соединитель"),
        _lot("G4", "SXH-001T-P0.6", "Контакт XH гнездо (мама)", item_type="Терминал"),
        _lot("G5", "PLS-4", "PLS-4", item_type="Соединитель"),  # the article: no example
        _lot("G6", "ZZ-1000", "Вилка ZZ-1000 на кабель", item_type="Соединитель"),  # the article inside
        _lot("G7", "QQ-2", "Колодка QQ 2 конт. ООО Ромашка", item_type="Соединитель",
             upd_name="Колодка QQ 2 конт. ООО Ромашка"),  # a printed УПД line
        _lot("G8", "QQ-3", "Электрический соединитель", item_type="Соединитель"),  # no series word
        _lot("G9", "QQ-4", "Нечто", item_type="Кнопка"),  # no stock type
        # 27.11: not in the full standard — no example
        _lot("H1", "SYM-001T-P0.6", "Контакт XH Female", item_type="Терминал"),  # no «(мама)»
        _lot("H2", "PHR-2", "PH розетка 2pin (однорядный)", item_type="Соединитель"),
        _lot("H3", "XT60H-M", "Amass XT 2pin (однорядный)", item_type="Соединитель"),
        _lot("H4", "XT90S-F", "Amass XT 2pin (однорядный)", item_type="Соединитель"),
        _lot("H5", "HSDCM-105-P", "HSD female 0 4 pin non waterproof all code", item_type="Коаксиальный соединитель"),
        _lot("H6", "MICRO USB-5BM", "micro-USB B 5pin (однорядный)", item_type="Соединитель"),
        _lot("H7", "НКИ 1,5-4", "Наконечник кольцевой", item_type="Наконечник"),
        _lot("H8", "НВИ 1,5-4", "Наконечник НВИ 1,5-4", item_type="Наконечник"),
        _lot("H9", "ПВАМ 0,35 красный", "Провод ПВАМ 0,35 мм² красный", item_type="Провод Монтажный"),
    ]
    examples = flow.style_examples(lots)
    assert examples == {
        "Коаксиальный соединитель": ["BNC-male RG-174"],
        "Соединитель": ["XH розетка (мама) 4pin (однорядный)"],
        "Терминал": ["Контакт XH гнездо (мама)"],
        "Наконечник": ["Наконечник НВИ 1,5-4"],
        "Провод Монтажный": ["Провод ПВАМ 0,35 мм² красный"],
    }
    prompt = flow.describe_prompt([{"article": "02SUR-32S"}], examples=examples)
    assert "«XH розетка (мама) 4pin (однорядный)»" in prompt and "«Контакт XH гнездо (мама)»" in prompt
    assert "<серия>-<male|female>[ R/A] <кабель>" in prompt and "Провод <марка> <сечение> мм² <цвет>" in prompt
    assert "<вилка (папа)|розетка (мама)>" in prompt and "<штырь (папа)|гнездо (мама)>" in prompt
    assert "«Наконечник НВИ 1,5-4»" in prompt
    assert "02SUR-32S — 2 контакта" in prompt


def test_every_model_call_carries_the_house_style_and_the_examples(monkeypatch):
    sent: list[str] = []

    def complete(config, model, messages, options, timeout):
        sent.append(messages[0]["content"])
        return {"choices": [{"message": {"content": '{"lines": [{"n": 1, "short_name": "SUR 2pin (однорядный)"}]}'}}]}

    monkeypatch.setattr(flow.receipt_llm, "complete", complete)
    config = flow.receipt_llm.LlmConfig(api_key="sk-test-key-0000000000")
    read = flow.model_reader(config, list(flow.ITEM_TYPES_CANON), {"Соединитель": ["XH розетка 4pin (однорядный)"]})
    assert read([{"article": "02SUR-32S"}])[0]["short_name"] == "SUR 2pin (однорядный)"
    assert len(sent) == 1 and "«XH розетка 4pin (однорядный)»" in sent[0] and flow.STYLE_RULES in sent[0]


# ── apply from the reviewed CSV ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def stock():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)  # noqa: E731
    with factory() as uow:
        for lot in (
            _lot("G1", "XHP-4", "XHP-4"),
            _lot("G2", "BNC-M174", "BNC-male RG-174", item_type="Коаксиальный соединитель"),
            _lot("G3", "ZZ-500", "ZZ-500", upd_name="Колодка ZZ-500"),
        ):
            uow.warehouse.add(lot)
        uow.commit()
    yield factory, sessions
    engine.dispose()


def _lots(factory) -> dict[str, StockLot]:
    with factory() as uow:
        return {lot.grn: lot for lot in uow.warehouse.every()}


def _events(sessions) -> list[AuditEventRow]:
    with sessions() as session:
        return list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "warehouse.row_edited")))


def test_apply_writes_exactly_the_reviewed_csv_with_an_audit_event_per_lot(stock, tmp_path):
    factory, sessions = stock
    before = _lots(factory)
    model = FakeModel({"short_name": "Колодка ZZ, 6 конт.", "item_type": "Соединитель", "manufacturer": "ZZCO"})
    plan = plan_changes(list(before.values()), Catalog(), model=model)
    path = tmp_path / "lot-names.csv"
    write_csv(path, plan.changes)
    # The owner corrects one value in Excel and drops one row: the apply writes exactly the file.
    reviewed = [change for change in read_csv(path) if not (change.grn == "G3" and change.field == "manufacturer")]
    reviewed = [flow.replace(change, now="Колодка ZZ 6 конт. (проверено)")
                if change.grn == "G3" and change.field == "name" else change for change in reviewed]
    write_csv(path, reviewed)

    result = apply_changes(read_csv(path), factory, ACTOR, NOW)

    after = _lots(factory)
    assert result.lots == 3 and not result.skipped
    assert after["G3"].name == "Колодка ZZ 6 конт. (проверено)"
    assert after["G3"].manufacturer == ""  # its row was dropped from the file
    assert after["G3"].upd_name == "Колодка ZZ-500"
    assert after["G1"].name == "XH 4pin (однорядный)" and after["G1"].item_type == "Соединитель"
    assert after["G1"].upd_name == "XHP-4" and after["G2"].upd_name == "BNC-M174"
    assert after["G2"].name == "BNC-male RG-174"
    for grn, lot in after.items():  # never the article, quantities, place, ГРН, receipt
        old = before[grn]
        assert (lot.article, lot.inbound, lot.written_off, lot.reserved, lot.storage_location, lot.grn,
                lot.receipt_id) == (old.article, old.inbound, old.written_off, old.reserved, old.storage_location,
                                    old.grn, old.receipt_id)
        assert lot.version == old.version + 1
    events = {UUID(str(event.aggregate_id)): event for event in _events(sessions)}
    assert len(events) == 3
    g3 = events[after["G3"].id]
    assert g3.actor_name == flow.ACTOR_NAME
    assert g3.details["name_from"] == "ZZ-500" and g3.details["name_to"] == "Колодка ZZ 6 конт. (проверено)"
    assert g3.details["name_source"] == "модель"
    assert "НАИМЕНОВАНИЕ: «ZZ-500» → «Колодка ZZ 6 конт. (проверено)»" in g3.details["changes"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(StockLotRow)) == 3


def test_apply_is_idempotent_and_never_overwrites_what_changed_since(stock, tmp_path):
    factory, sessions = stock
    plan = plan_changes(list(_lots(factory).values()), Catalog())
    path = tmp_path / "lot-names.csv"
    write_csv(path, plan.changes)
    # The storekeeper renamed G1 by hand after the dry run: its name row is skipped, the rest of the lot is written.
    with factory() as uow:
        lot = uow.warehouse.by_key("ЦАР", "G1", "")
        uow.warehouse.add(flow.replace(lot, name="Корпус XH на 4", version=lot.version + 1))
        uow.commit()

    first = apply_changes(read_csv(path), factory, ACTOR, NOW)
    assert [(item.change.grn, item.change.field) for item in first.skipped] == [("G1", "name")]
    assert _lots(factory)["G1"].name == "Корпус XH на 4"
    events = len(_events(sessions))

    second = apply_changes(read_csv(path), factory, ACTOR, NOW)
    assert second.lots == 0 and not second.written and second.already == len(first.written)
    assert len(_events(sessions)) == events
    assert not plan_changes(list(_lots(factory).values()), Catalog()).changes  # a fresh dry run: nothing left


def test_apply_refuses_a_type_outside_the_dictionary_and_filling_a_filled_field(stock, tmp_path):
    factory, _ = stock
    lots = _lots(factory)
    changes = [
        flow.Change(lots["G1"].id, "ЦАР", "G1", "", "XHP-4", "item_type", "", "Кнопка", "модель"),
        flow.Change(lots["G2"].id, "ЦАР", "G2", "", "BNC-M174", "item_type", "", "Кабель", "модель"),
    ]
    result = apply_changes(changes, factory, ACTOR, NOW)
    assert {item.change.grn: item.reason for item in result.skipped} == {
        "G1": "тип «Кнопка» не из справочника типов склада",
        "G2": "поле уже заполнено: «Коаксиальный соединитель»",
    }
    assert _lots(factory)["G2"].item_type == "Коаксиальный соединитель"


def test_revert_puts_back_what_was(stock, tmp_path):
    factory, _ = stock
    before = _lots(factory)
    plan = plan_changes(list(before.values()), Catalog())
    apply_changes(plan.changes, factory, ACTOR, NOW)
    apply_changes(plan.changes, factory, ACTOR, NOW, revert=True)
    after = _lots(factory)
    for grn in before:
        assert (after[grn].name, after[grn].item_type, after[grn].manufacturer, after[grn].upd_name) == (
            before[grn].name, before[grn].item_type, before[grn].manufacturer, before[grn].upd_name)


def test_cli_dry_run_writes_the_csv_and_apply_never_calls_the_model(tmp_path, monkeypatch, capsys):
    database = tmp_path / "erp.db"
    engine = create_engine(f"sqlite:///{database}")
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        uow.warehouse.add(_lot("G1", "ZZ-500", "ZZ-500"))
        uow.commit()
    engine.dispose()
    monkeypatch.setenv("ERP_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("ERP_UPD_LLM_API_KEY", "sk-test-key-0000000000")
    model = FakeModel({"short_name": "Колодка ZZ", "item_type": "Соединитель", "manufacturer": ""})
    monkeypatch.setattr(flow, "model_reader", lambda config, types, examples=None: model)
    path = tmp_path / "out.csv"

    assert flow.main(["--csv", str(path)]) == 0
    out = capsys.readouterr().out
    assert "ГРН | артикул | поле | было → стало | источник" in out
    assert "ЦАР G1 | ZZ-500 | наименование | «ZZ-500» → «Колодка ZZ» | модель" in out
    assert len(model.calls) == 1 and len(read_csv(path)) == 3

    def no_model(*_args, **_kwargs):
        raise AssertionError("the model is not called on apply")

    monkeypatch.setattr(flow, "model_reader", no_model)
    monkeypatch.setattr(flow.receipt_llm, "complete", no_model)
    assert flow.main(["--apply", "--from", str(path)]) == 0
    assert "Записано: партий 1, изменений 3" in capsys.readouterr().out
    engine = create_engine(f"sqlite:///{database}")
    with SqlAlchemyWarehouseUnitOfWork(sessionmaker(bind=engine, expire_on_commit=False)) as uow:
        lot = uow.warehouse.every()[0]
    engine.dispose()
    assert (lot.name, lot.item_type, lot.upd_name) == ("Колодка ZZ", "Соединитель", "ZZ-500")
    assert flow.main(["--apply"]) == 2  # no reviewed file — nothing is written


def test_without_a_key_the_model_part_is_skipped_with_a_message(tmp_path, monkeypatch, capsys):
    database = tmp_path / "erp.db"
    engine = create_engine(f"sqlite:///{database}")
    create_schema(engine)
    engine.dispose()
    monkeypatch.setenv("ERP_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.delenv("ERP_UPD_LLM_API_KEY", raising=False)
    assert flow.main([]) == 0
    assert "модельная часть пропущена" in capsys.readouterr().err


# ── lugs are «Наконечник», not «Терминал» (owner 26.10.2026) ───────────────────────────────────────


@pytest.mark.parametrize(("article", "name", "lug"), [
    ("НШВИ 0,5-8", "НШВИ 0,5-8", True),
    ("НШВ 1,5-8", "", True),
    ("НКИ 1,5-4", "НКИ 1,5-4", True),
    ("РПИ-П 1,5-4", "", True),
    ("ПМ 1,5", "", True),
    ("ТМЛ 16-8-6", "", True),
    ("RV1.25-4", "", True),
    ("E0508", "", True),
    ("QQ-1", "Наконечник кольцевой 4 мм", True),
    ("SXH-001T-P0.6", "Контакт XH Female", False),  # a connector's crimp contact stays «Терминал»
    ("SYM-001T-P0.6", "SYM-001T-P0.6", False),
    ("НВ-4 0,2 белый", "Провод НВ-4 0,20 мм² белый", False),  # НВ-4 is a wire, not НВИ
    ("ПМВ 0,5", "", False),
])
def test_lugs_are_told_by_their_mark(article, name, lug):
    assert flow.is_lug(_lot("G1", article, name)) is lug


def test_lugs_get_the_lug_type_even_over_terminal_and_old_spellings():
    lots = [
        _lot("G1", "НШВИ 0,5-8", "НШВИ 0,5-8"),
        _lot("G2", "НКИ 1,5-4", "Наконечник НКИ 1,5-4", item_type="Терминал"),
        _lot("G3", "РПИ-К 1,5-4", "РПИ-К 1,5-4", item_type="наконечник кольцевой"),
        _lot("G4", "НШВ 1,5-8", "НШВ 1,5-8", item_type="Наконечник втулочный"),
        _lot("G5", "SXH-001T-P0.6", "Контакт XH Female", item_type="Терминал"),  # a contact: untouched
        _lot("G6", "НШВИ 1,0-8", "НШВИ 1,0-8", item_type="Наконечник"),  # already right
        _lot("G7", "НШВИ 2,5-8", "НШВИ 2,5-8", item_type="Лента"),  # another filled type is not ours to change
    ]
    model = FakeModel({"short_name": "", "item_type": "Терминал", "manufacturer": ""})
    by = _by(plan_changes(lots, Catalog(), model=model))
    for grn, was in (("G1", ""), ("G2", "Терминал"), ("G3", "наконечник кольцевой"), ("G4", "Наконечник втулочный")):
        assert by[(grn, "item_type")] == (was, "Наконечник", "правило: наконечник")
    assert ("G5", "item_type") not in by and ("G6", "item_type") not in by and ("G7", "item_type") not in by


def test_the_lug_type_is_applied_over_terminal_though_the_dictionary_lacks_it(stock):
    factory, sessions = stock
    with factory() as uow:
        uow.warehouse.add(_lot("L1", "НКИ 1,5-4", "НКИ 1,5-4", item_type="Терминал"))
        uow.warehouse.add(_lot("L2", "SXH-001T-P0.6", "Контакт XH Female", item_type="Терминал"))
        uow.commit()
    lots = _lots(factory)
    plan = plan_changes([lots["L1"], lots["L2"]], Catalog())
    types = [kind for kind in flow.ITEM_TYPES_CANON if kind != "Наконечник"]  # the dictionary before its PR
    forced = flow.Change(lots["L2"].id, "ЦАР", "L2", "", "SXH-001T-P0.6", "item_type", "", "Соединитель", "модель")
    result = apply_changes([*plan.changes, forced], factory, ACTOR, NOW, types)
    after = _lots(factory)
    assert after["L1"].item_type == "Наконечник"
    assert after["L2"].item_type == "Терминал"  # the fill-only guard holds for everything but the lug rule
    assert [item.change.grn for item in result.skipped] == ["L2"]
    apply_changes(plan.changes, factory, ACTOR, NOW, types, revert=True)
    assert _lots(factory)["L1"].item_type == "Терминал"


# ── full names 27.11 (owner: «непонятно где папа где мама, совершенно разные наименования») ─────────


FULL = {
    "XT60H-M": StockName("Amass XT60H вилка (папа) 2pin", True, source="T1",
                         attrs={"family": "Amass XT", "gender": "pin", "maker": "AMASS"}),
    "XT90S-F": StockName("Amass XT90S розетка (мама) 2pin", True, source="T1",
                         attrs={"family": "Amass XT", "gender": "socket", "maker": "AMASS"}),
    "XHP-4": StockName("XH розетка (мама) 4pin (однорядный)", True, source="T1",
                       attrs={"family": "XH", "gender": "socket", "maker": "JST"}),
    "XHP-6": StockName("XH розетка (мама) 6pin (однорядный)", True, source="T1",
                       attrs={"family": "XH", "gender": "socket", "maker": "JST"}),
    "НВИ 1,5-4": StockName("Наконечник НВИ 1,5-4", True, source="T1", attrs={"section": "1,5", "size": "4"}),
    "SMA-KWE": StockName("SMA-female RG-58", True, source="T2", attrs={"gender": "socket"}),
    "XT30U-M+F": StockName("Amass XT30U 2pin", False, ("пол",), source="T1", attrs={"family": "Amass XT"}),
    "PHR-2": StockName("PH 2pin (однорядный)", False, ("пол",), source="T1"),
    "QQ-77": StockName("QQ 2pin", False, ("пол",), source="T1"),
    "SA810/P4": StockName("Weipu SA8 вилка (папа) 4pin", True, attrs={"family": "Weipu SA8", "maker": "WEIPU"}),
    "SA810/P4IIB": StockName("Weipu SA8 вилка (папа) 4pin", True, attrs={"family": "Weipu SA8", "maker": "WEIPU"}),
}


@pytest.fixture()
def full_names(monkeypatch):
    """A stub of T1/T2: the names above for the types with a builder, None for the rest."""
    asked: list[LotHints] = []

    def build(hints, records=()):
        if not has_builder(hints.item_type):
            return None
        asked.append(hints)
        return FULL.get(hints.article)

    monkeypatch.setattr(flow, "build_stock_name", build)
    return asked


SOCKET = FULL["XHP-4"]
LUG = FULL["НВИ 1,5-4"]
COAX = FULL["SMA-KWE"]


@pytest.mark.parametrize(("name", "article", "kwargs", "reason"), [
    ("XHP-4", "XHP-4", {}, flow.NEEDS_ARTICLE),
    ("", "XHP-4", {}, flow.NEEDS_ARTICLE),
    ("Amass XT 2pin (однорядный)", "XT60H-M", {"shared": {"amass xt 2pin (однорядный)"}}, flow.NEEDS_SHARED),
    ("XH 4pin (однорядный)", "XHP-4", {"old_name": "XH 4pin (однорядный)"}, flow.NEEDS_OLD),
    ("XH розетка 4pin (однорядный)", "XHP-4", {"built": SOCKET}, flow.NEEDS_GENDER),  # «розетка» alone is not enough
    ("XH вилка (папа) 4pin (однорядный)", "XHP-4", {"built": SOCKET}, flow.NEEDS_GENDER),  # the wrong gender
    ("SMA-male RG-58", "SMA-KWE", {"built": COAX}, flow.NEEDS_GENDER),  # coax: male/female, «female» ≠ «male»
    ("Наконечник кольцевой 4 мм", "НВИ 1,5-4", {"built": LUG}, flow.NEEDS_SIZE),
    ("Соединитель I-DS1071-01-SCB02x10 (CONNFLY, BLD2)", "I-DS1071-01-SCB02x10", {}, flow.NEEDS_SUPPLIER),
    ("Колодка ZZ 6 конт., серия ZZ", "ZZ-500", {"upd_name": "Колодка ZZ 6 конт., серия ZZ"}, flow.NEEDS_SUPPLIER),
    ("micro-USB B 5pin (однорядный)", "MICRO USB-5BM", {}, flow.NEEDS_LOWER),
    ("наконечник штыревой втулочный", "НШВ 0.5-8", {}, flow.NEEDS_LOWER),
    ("HSD female 0 4 pin non waterproof all code", "HSDCM-105-P", {"built": COAX}, flow.NEEDS_LATIN),
    ("FAKRA HSD female connector", "SHM.400.0002.Z.X.AXX", {}, flow.NEEDS_LATIN),
    # A unique human name with its gender and size stays.
    ("Корпус XH розетка (мама) на 4 контакта", "XHP-4", {"built": SOCKET}, ""),
    ("SMA-female RG-58, обжим", "SMA-KWE", {"built": COAX, "old_name": "SMA-female RG-58"}, ""),
    ("НВИ 1.5-4 синий", "НВИ 1,5-4", {"built": LUG}, ""),
    ("SMA-female bulkhead LMR-100", "SMA-KFB", {}, ""),
    ("Датчик тестовый", "SOME-77", {}, ""),
])
def test_when_the_full_name_replaces_the_current_one(name, article, kwargs, reason):
    assert flow.name_needs_full(name, article, **kwargs) == reason


def test_shared_names_are_those_of_two_or_more_articles():
    lots = [_lot("G1", "XT60H-M", "Amass XT 2pin (однорядный)"), _lot("G2", "XT90S-F", "amass xt 2pin (однорядный)"),
            _lot("G3", "XHP-4", "Корпус XH"), _lot("G4", "xhp 4", "Корпус XH")]  # one article spelled twice
    assert flow.shared_names(lots) == {"amass xt 2pin (однорядный)"}


def test_full_names_replace_family_names_and_the_rest_goes_to_review(full_names):
    lots = [
        _lot("G1", "XT60H-M", "Amass XT 2pin (однорядный)", item_type="Соединитель", manufacturer="AMASS"),
        _lot("G2", "XT90S-F", "Amass XT 2pin (однорядный)", item_type="Соединитель", manufacturer="AMASS"),
        _lot("G3", "XHP-4", "XH розетка 4pin (однорядный)", item_type="Соединитель", manufacturer="JST"),
        _lot("G4", "НВИ 1,5-4", "Наконечник кольцевой", item_type="Наконечник", manufacturer="КВТ"),
        _lot("G5", "SMA-KWE", "SMA-female RG-58", item_type="Коаксиальный соединитель", manufacturer="X"),
        _lot("G6", "XHP-6", "Корпус XH розетка (мама) на 6", item_type="Соединитель", manufacturer="JST"),
        _lot("G7", "XT30U-M+F", "Amass XT 2pin (однорядный)", item_type="Соединитель"),
        _lot("G8", "ПВАМ 0,35 красный", "Провод красный", item_type="Провод Монтажный", manufacturer="X"),
        _lot("G9", "QQ-77", "QQ-77", item_type="Соединитель"),  # nothing in the reference: the model is asked
    ]
    model = FakeModel({"short_name": "Amass XT30U 2pin", "item_type": "Соединитель", "manufacturer": "AMASS"})
    plan = plan_changes(lots, Catalog(), model=model)
    by = _by(plan)
    source = "база: полное наименование (T1)"
    assert by[("G1", "name")] == ("Amass XT 2pin (однорядный)", "Amass XT60H вилка (папа) 2pin", source)
    assert by[("G2", "name")] == ("Amass XT 2pin (однорядный)", "Amass XT90S розетка (мама) 2pin", source)
    assert by[("G3", "name")] == ("XH розетка 4pin (однорядный)", "XH розетка (мама) 4pin (однорядный)", source)
    assert by[("G4", "name")] == ("Наконечник кольцевой", "Наконечник НВИ 1,5-4", source)
    assert ("G5", "name") not in by  # already the full name
    assert ("G6", "name") not in by  # a unique human name with its gender stays
    assert ("G8", "name") not in by  # no builder: the 26.10 rule (only an article name is replaced)
    # G7, G9: no complete name — never the model's guess; the model still fills the maker.
    assert ("G7", "name") not in by and ("G9", "name") not in by
    assert [row["article"] for call in model.calls for row in call] == ["QQ-77"]
    assert by[("G9", "manufacturer")][1:] == ("AMASS", "модель")
    assert [(item.grn, item.reason, item.missing) for item in plan.review] == [
        ("G7", flow.NEEDS_SHARED, ("пол",)), ("G9", flow.NEEDS_ARTICLE, ("пол",))]
    assert "На проверку (полного наименования нет): партий 2; не хватает: пол 2" in flow.format_report(plan)
    assert {hints.article: hints.manufacturer for hints in full_names}["XHP-4"] == "JST"


def test_the_builder_gets_the_lug_type_and_the_glued_maker(full_names):
    plan_changes([_lot("G1", "НВИ 1,5-4", "НВИ 1,5-4", item_type="Терминал"),
                  _lot("G2", "XHP-4@JST", "XHP-4@JST", item_type="Соединитель")], Catalog())
    asked = {hints.article: hints for hints in full_names}
    assert asked["НВИ 1,5-4"].item_type == "Наконечник"  # the lug rule's type, not the old «Терминал»
    assert (asked["XHP-4"].item_type, asked["XHP-4"].manufacturer) == ("Соединитель", "JST")


def test_colliding_full_names_get_the_variant_tail(full_names):
    lots = [_lot("G1", "SA810/P4", "Weipu SA8 4pin", item_type="Соединитель", manufacturer="WEIPU"),
            _lot("G2", "SA810/P4IIB", "Weipu SA8 4pin", item_type="Соединитель", manufacturer="WEIPU")]
    by = _by(plan_changes(lots, Catalog()))
    assert by[("G1", "name")][1] == "Weipu SA8 вилка (папа) 4pin, вариант P4"
    assert by[("G2", "name")][1] == "Weipu SA8 вилка (папа) 4pin, вариант P4IIB"


def test_a_sibling_name_failing_the_rule_is_not_copied(full_names):
    lots = [
        _lot("G1", "PHR-2", "PHR-2", item_type="Соединитель", manufacturer="JST"),
        _lot("G2", "PHR-2", "PH 2pin (однорядный)", item_type="Соединитель", manufacturer="JST"),  # the old name
        _lot("G3", "XT30U-M+F", "XT30U-M+F", item_type="Соединитель", manufacturer="AMASS"),
        _lot("G4", "XT30U-M+F", "Amass XT30U вилка (папа) + розетка (мама) 2pin", item_type="Соединитель",
             manufacturer="AMASS"),  # a complete human name of the article
        _lot("G5", "ZZ-1", "ZZ-1", item_type="Кабель", manufacturer="X"),
        _lot("G6", "ZZ-1", "кабель неэкранированный., бухта 50м.", item_type="Кабель", manufacturer="X"),
    ]
    plan = plan_changes(lots, Catalog())
    by = _by(plan)
    assert ("G1", "name") not in by and ("G2", "name") not in by  # the old family name of G2 does not travel
    assert {item.grn: item.reason for item in plan.review} == {"G1": flow.NEEDS_ARTICLE, "G2": flow.NEEDS_OLD}
    assert by[("G3", "name")] == ("XT30U-M+F", "Amass XT30U вилка (папа) + розетка (мама) 2pin", "база: партия G4")
    assert ("G5", "name") not in by  # a lowercase supplier line is not copied (no builder, no catalog, no model)


def test_the_review_csv_lists_what_is_missing_and_where_to_find_it(full_names, tmp_path):
    plan = plan_changes([flow.replace(_lot("G7", "XT30U-M+F", "XT30U-M+F", item_type="Соединитель"),
                                         package="P1")], Catalog())
    path = tmp_path / "review.csv"
    flow.write_review_csv(path, plan.review)
    with open(path, encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    assert rows[0] == list(flow.REVIEW_COLUMNS)
    assert rows[1][1:] == ["ЦАР", "G7", "P1", "XT30U-M+F", "Соединитель", "XT30U-M+F", flow.NEEDS_ARTICLE, "пол",
                           "контакты: паспорт производителя / БД.ТЕР"]
    assert UUID(rows[1][0]) == plan.review[0].lot_id
    assert flow.needed_source(["одинаковые данные", "что-то ещё"]) == (
        "чем артикулы отличаются: паспорт производителя; паспорт производителя")


def test_full_names_apply_idempotently_and_revert(full_names, stock):
    factory, sessions = stock
    with factory() as uow:
        uow.warehouse.add(_lot("F1", "XT60H-M", "Amass XT 2pin (однорядный)", item_type="Соединитель",
                               manufacturer="AMASS", upd_name="XT60H-M"))
        uow.warehouse.add(_lot("F2", "XT90S-F", "Amass XT 2pin (однорядный)", item_type="Соединитель",
                               manufacturer="AMASS", upd_name="XT90S-F"))
        uow.commit()
    before = _lots(factory)
    plan = plan_changes(list(before.values()), Catalog())
    names = [change for change in plan.changes if change.field == "name" and change.grn.startswith("F")]
    assert {change.grn: change.now for change in names} == {
        "F1": "Amass XT60H вилка (папа) 2pin", "F2": "Amass XT90S розетка (мама) 2pin"}

    first = apply_changes(plan.changes, factory, ACTOR, NOW)
    assert not first.skipped
    after = _lots(factory)
    assert (after["F1"].name, after["F2"].name) == ("Amass XT60H вилка (папа) 2pin", "Amass XT90S розетка (мама) 2pin")
    assert after["F1"].version == before["F1"].version + 1
    event = next(event for event in _events(sessions) if UUID(str(event.aggregate_id)) == after["F1"].id)
    assert event.actor_name == "полные наименования 27.11"
    assert event.details["reason"].startswith("полные наименования 27.11")

    second = apply_changes(plan.changes, factory, ACTOR, NOW)
    assert second.lots == 0 and second.already == len(first.written)
    assert not [change for change in plan_changes(list(_lots(factory).values()), Catalog()).changes
                if change.field == "name"]  # a fresh dry run: the full names stay

    apply_changes(plan.changes, factory, ACTOR, NOW, revert=True)
    reverted = _lots(factory)
    assert (reverted["F1"].name, reverted["F2"].name) == ("Amass XT 2pin (однорядный)", "Amass XT 2pin (однорядный)")


def test_the_cli_writes_the_review_csv(tmp_path, monkeypatch, capsys, full_names):
    database = tmp_path / "erp.db"
    engine = create_engine(f"sqlite:///{database}")
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        uow.warehouse.add(_lot("G7", "XT30U-M+F", "XT30U-M+F", item_type="Соединитель", manufacturer="AMASS"))
        uow.commit()
    engine.dispose()
    monkeypatch.setenv("ERP_DATABASE_URL", f"sqlite:///{database}")
    path = tmp_path / "review.csv"
    assert flow.main(["--no-model", "--review-csv", str(path)]) == 0
    assert f"На проверку: {path} (строк 1)" in capsys.readouterr().out
    with open(path, encoding="utf-8-sig", newline="") as stream:
        assert [row["артикул"] for row in csv.DictReader(stream, delimiter=";")] == ["XT30U-M+F"]
