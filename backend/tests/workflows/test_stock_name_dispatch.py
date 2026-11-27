"""The full stock name (owner 27.11.2026: «где папа где мама»): the builder of the item type, and two different articles
never sharing one name — the maker, the variant tail of the families that need it, else review."""

from __future__ import annotations

import pytest

from app.workflows import stock_name
from app.workflows.stock_name import SAME_DATA, build_stock_name, has_builder, resolve_collisions
from app.workflows.stock_name_types import LotHints, StockName


@pytest.fixture()
def builders(monkeypatch):
    """T1/T2 builders as stubs: each records what it was asked."""
    calls: list[tuple[str, LotHints, tuple]] = []

    def stub(label: str):
        def build(hints, records):
            calls.append((label, hints, records))
            return StockName(f"{label} {hints.article}", True, source=label)
        return build

    loaded = {"stock_name_connectors": stub("connector"), "stock_name_coax": stub("coax")}
    monkeypatch.setattr(stock_name, "_load", lambda module, function: loaded[module])
    return calls


@pytest.mark.parametrize(("item_type", "label"), [
    ("Соединитель", "connector"),
    ("  соединитель ", "connector"),  # the stock's spelling is not the dictionary's
    ("Терминал", "connector"),
    ("Наконечник", "connector"),
    ("Коаксиальный соединитель", "coax"),
])
def test_the_item_type_picks_the_builder(builders, item_type, label):
    found = build_stock_name(LotHints(article="XHP-4", item_type=item_type), ["record"])
    assert found is not None and found.source == label
    assert builders == [(label, LotHints(article="XHP-4", item_type=item_type), ("record",))]
    assert has_builder(item_type)


@pytest.mark.parametrize("item_type", ["Провод Монтажный", "Термоусадочная трубка", "Оплётка", "Кабель", ""])
def test_other_types_have_no_builder(builders, item_type):
    assert build_stock_name(LotHints(article="ПВАМ 0,35", item_type=item_type)) is None
    assert builders == [] and not has_builder(item_type)


def test_a_builder_not_merged_yet_means_no_name():
    assert stock_name._load("stock_name_no_such_builder", "build") is None
    with pytest.raises(AttributeError):  # a module that IS there must have the function
        stock_name._load("stock_name_types", "no_such_function")


# ── collisions ─────────────────────────────────────────────────────────────────────────────────────


def _named(name: str, **attrs) -> StockName:
    return StockName(name, True, attrs=attrs)


def test_the_maker_tells_two_articles_apart():
    angled = "XH вилка (папа) 7pin (однорядный), угловая на плату"
    result = resolve_collisions({
        "S7B-XH-A(LF)(SN)": _named(angled, maker="JST", family="XH"),
        "DS1069-7-MRW6SA": _named(angled, maker="Connfly", family="XH"),
        "XHP-4": _named("XH розетка (мама) 4pin (однорядный)", maker="JST"),
    })
    assert result["S7B-XH-A(LF)(SN)"].name == f"{angled}, JST"
    assert result["DS1069-7-MRW6SA"].name == f"{angled}, Connfly"
    assert result["XHP-4"].name == "XH розетка (мама) 4pin (однорядный)"  # no collision: no maker
    assert all(item.complete for item in result.values())


def test_a_maker_already_in_the_name_is_not_added_twice():
    result = resolve_collisions({
        "A-1": _named("Контакт XH гнездо (мама), JST", maker="JST"),
        "B-1": _named("Контакт XH гнездо (мама), JST", maker="Connfly"),
    })
    assert result["A-1"].name == "Контакт XH гнездо (мама), JST"
    assert result["B-1"].name == "Контакт XH гнездо (мама), JST, Connfly"


@pytest.mark.parametrize(("family", "articles", "tails"), [
    ("Weipu SA8", ("SA810/P4", "SA810/P4IIB"), ("P4", "P4IIB")),
    ("CNLINKO LP-12", ("LP-12-C02PE-02-001", "LP-12-C02PE-03-001"), ("02-001", "03-001")),
    ("Amass XT", ("XT60H-M", "XT60H-M.G.Y"), ("XT60H-M", "G.Y")),
])
def test_the_variant_tail_tells_apart_only_its_families(family, articles, tails):
    name = f"{family} вилка (папа) 4pin"
    result = resolve_collisions({article: _named(name, maker="X", family=family) for article in articles})
    assert [result[article].name for article in articles] == [f"{name}, вариант {tail}" for tail in tails]
    assert all(result[article].complete for article in articles)


def test_same_data_of_other_families_goes_to_review():
    name = "XH розетка (мама) 4pin (однорядный)"
    result = resolve_collisions({
        "XHP-4": _named(name, maker="JST", family="XH"),
        "XHP-4-X": _named(name, maker="jst", family="XH"),  # the same maker in another spelling separates nothing
    })
    for item in result.values():
        assert (item.name, item.complete, item.missing) == (name, False, (SAME_DATA,))


def test_the_maker_separates_who_it_can_and_the_rest_goes_to_review():
    name = "PH розетка (мама) 2pin (однорядный)"
    result = resolve_collisions({
        "PHR-2": _named(name, maker="JST"),
        "PHR-2-B": _named(name, maker="JST"),
        "DS1066-02": _named(name, maker="Connfly"),
    })
    assert result["DS1066-02"] == _named(f"{name}, Connfly", maker="Connfly")
    assert [(result[key].complete, result[key].missing) for key in ("PHR-2", "PHR-2-B")] == [
        (False, (SAME_DATA,)), (False, (SAME_DATA,))]


def test_one_article_spelled_differently_is_no_collision():
    name = "XH розетка (мама) 4pin (однорядный)"
    named = {"XHP-4": _named(name, maker="JST"), "xhp 4": _named(name, maker=""), "XHP.4": _named(name)}
    assert resolve_collisions(named) == named


def test_incomplete_names_are_left_as_they_are():
    named = {"XT30U-M+F": StockName("Amass XT30U 2pin", False, ("пол",)),
             "XT30U-M": StockName("Amass XT30U 2pin", False, ("пол",))}
    assert resolve_collisions(named) == named


def test_a_told_apart_name_meeting_another_one_goes_to_review():
    result = resolve_collisions({
        "A-1": _named("XH вилка (папа) 2pin", maker="JST"),
        "B-1": _named("XH вилка (папа) 2pin", maker="Connfly"),
        "C-1": _named("XH вилка (папа) 2pin, JST"),  # what A-1 becomes
    })
    assert result["B-1"].complete and result["B-1"].name == "XH вилка (папа) 2pin, Connfly"
    assert not result["A-1"].complete and not result["C-1"].complete


# ── «Внести позиции» / «Приход на склад»: article info ─────────────────────────────────────────────


def test_article_info_prefers_the_complete_name_over_a_family_name(monkeypatch):
    from app.modules.warehouse import api
    from app.workflows.warehouse_article import infer_article_identity

    built = StockName("Amass XT60H вилка (папа) 2pin", True, attrs={"gender": "pin"})
    asked: list[LotHints] = []
    monkeypatch.setattr(api, "build_stock_name", lambda hints, records: asked.append(hints) or built)
    identity = infer_article_identity("XT60H-M")
    info = {"name": "Amass XT 2pin (однорядный)", "item_type": "Соединитель", "manufacturer": "AMASS",
            "description": "", "source": "партия G1"}

    found = api._with_full_name(info, "XT60H-M", [], identity)
    assert (found["name"], found["source"]) == ("Amass XT60H вилка (папа) 2pin", "партия G1, полное наименование")
    assert (asked[0].article, asked[0].item_type, asked[0].manufacturer) == ("XT60H-M", "Соединитель", "AMASS")
    # A human name of the last lot with its gender stays; so does everything when the name is not complete.
    kept = {**info, "name": "Корпус Amass XT60H вилка (папа)"}
    assert api._with_full_name(kept, "XT60H-M", [], identity) == kept
    monkeypatch.setattr(api, "build_stock_name", lambda hints, records: StockName("Amass XT60H 2pin", False, ("пол",)))
    assert api._with_full_name(info, "XT60H-M", [], identity) == info
    monkeypatch.setattr(api, "build_stock_name", lambda hints, records: None)
    assert api._with_full_name(info, "XT60H-M", [], identity) == info
    assert api._with_full_name(None, "XT60H-M", [], identity) is None
