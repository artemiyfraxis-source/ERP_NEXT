"""«Оболочки» (owner 12.10.2026): classifier, size parsing, sorting, the manual group and the endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.references import coverings as cv
from app.modules.references.application import SetCoveringGroupCommand, coverings_catalog, set_covering_group
from app.modules.references.domain import ReferenceValidationError, TechOperationTab
from app.modules.references.load_mirror import ACTOR, default_source, load_mirror
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from tests.references.test_load_mirror import NOW, sessions, uow  # noqa: F401  (fixtures)


def row(name, group="", **cells):
    return {"Наименование": name, "Группа": group, **cells}


@pytest.mark.parametrize(("fields", "expected"), [
    (row("ТУТнг 4/2 черная", "ТУТ", **{"Клеевой слой": "нет"}), (cv.TUT, "по группе")),
    (row("ТТК 6/2 черная", "ТТК", **{"Клеевой слой": "да"}), (cv.TTK, "по группе")),
    (row("Трубка 12/4", "ТУТ", **{"Клеевой слой": "да"}), (cv.TTK, "по группе")),
    (row("Трубка 12/4 с клеевым слоем", "ТУТ"), (cv.TTK, "по группе")),
    (row("Оплётка GTRPA-10", "оплётка"), (cv.BRAID, "по группе")),
    (row("Плетёнка ПМЛ 3х6 У3", "плетёнка"), (cv.BRAID, "по группе")),
    (row("Гофра 6,8мм", "гофра"), (cv.CORRUGATED, "по группе")),
    (row("Изолента ПВХ 15х0,13х20м", "лента"), (cv.TAPE, "по группе")),
    (row("Трубка ТВ-40 ПВХ 4мм (кембрик)", "кембрик"), (cv.CAMBRIC, "по группе")),
    (row("Стяжка 3x60", "стяжка"), (None, "")),
    (row("Тройник 9,8x9,8x9,8", "тройник"), (None, "")),
    (row("Бирка TMS-SCE-1/4-2.0-4", "бирка"), (None, "")),
    # no sheet group: the text decides
    (row("Heat shrink tube 3:1 adhesive lined"), (cv.TTK, "по названию")),
    (row("Dual wall heat-shrink 6/2"), (cv.TTK, "по названию")),
    (row("Термоусадочная трубка 6/3"), (cv.TUT, "по названию")),
    (row("Expandable braided sleeving PET 10 mm"), (cv.BRAID, "по названию")),
    (row("Оплётка нейлон 6-12 мм"), (cv.BRAID, "по названию")),
    (row("Corrugated split tube 10"), (cv.CORRUGATED, "по названию")),
    (row("Гофротрубка 12"), (cv.CORRUGATED, "по названию")),
    (row("Трубка ПВХ 4мм (кембрик)"), (cv.CAMBRIC, "по названию")),
    (row("Изолента ПВХ 19х0,13х20м"), (cv.TAPE, "по названию")),
    (row("Лента тканевая 20мм"), (cv.TAPE, "по названию")),
    (row("Что-то непонятное"), (None, "")),
])
def test_classifier_table(fields, expected):
    assert cv.classify(fields) == expected


def test_manual_group_wins_and_is_marked():
    fields = row("Гофра 6,8мм", "гофра", **{cv.OVERRIDE_FIELD: "Прочее"})
    assert cv.classify(fields) == (cv.OTHER, "вручную")
    assert cv.classify(row("Стяжка 3x60", "стяжка", **{cv.OVERRIDE_FIELD: "ТУТ"})) == (cv.TUT, "вручную")
    # an unknown override value is ignored, not guessed
    assert cv.classify(row("Гофра 6,8мм", "гофра", **{cv.OVERRIDE_FIELD: "что-то"})) == (cv.CORRUGATED, "по группе")


@pytest.mark.parametrize(("text", "value"), [("6,4", 6.4), ("Ø6,4", 6.4), ("6.4 мм", 6.4), ("3", 3.0), ("-55", -55.0), ("", None), ("6/3", None), ("нет", None)])
def test_number(text, value):
    assert cv.number(text) == value


def test_shrink_sizes_from_columns_and_ratio():
    parsed = cv.parse(row("ТТК 4,8/1,6 черная", "ТТК", **{"Размер до, мм": "4,8", "Размер после, мм": "1,6", "Коэф. усадки": "3:1"}), cv.TTK)
    assert (parsed.before_mm, parsed.after_mm, parsed.ratio, parsed.adhesive) == (4.8, 1.6, "3:1", "да")
    assert parsed.notes == []


def test_shrink_sizes_and_ratio_from_the_name_when_columns_are_empty():
    parsed = cv.parse(row("ТУТ 6,4/3,2мм 2:1 черная", "ТУТ"), cv.TUT)
    assert (parsed.before_mm, parsed.after_mm, parsed.ratio, parsed.color) == (6.4, 3.2, "2:1", "чёрный")
    assert "Ø из названия" in parsed.notes and "усадка из названия" in parsed.notes
    # nothing in the name → nothing invented
    empty = cv.parse(row("Трубка термоусадочная", "ТУТ"), cv.TUT)
    assert (empty.before_mm, empty.after_mm, empty.ratio) == (None, None, "")


def test_shrink_temperature_from_columns_and_from_text():
    assert cv.parse(row("ТУТ 6/3", "ТУТ", **{"Темп. от, °C": "-55", "Темп. до, °C": "125"}), cv.TUT).temp_min_c == -55
    parsed = cv.parse(row("ТУТ 6/3 (-55...125 °C)", "ТУТ"), cv.TUT)
    assert (parsed.temp_min_c, parsed.temp_max_c) == (-55, 125)


def test_braid_range_nominal_and_name_range():
    gtrpa = cv.parse(row("Оплётка GTRPA-10", "оплётка", **{"Серия": "GTRPA-10", "Размер до, мм": "10", "Размер после, мм": "16", "Материал": "полиамид (нейлон)"}), cv.BRAID)
    assert (gtrpa.nominal_mm, gtrpa.min_mm, gtrpa.max_mm, gtrpa.material_short) == (10, 10, 16, "ПА")
    reversed_columns = cv.parse(row("Оплётка XT-20/10 чёрная", "оплётка", **{"Серия": "XT (85371)", "Размер до, мм": "20", "Размер после, мм": "10"}), cv.BRAID)
    assert (reversed_columns.min_mm, reversed_columns.max_mm) == (10, 20)
    from_name = cv.parse(row("Оплётка ПЭТ 6–12 мм", "оплётка"), cv.BRAID)
    assert (from_name.min_mm, from_name.max_mm, from_name.nominal_mm) == (6, 12, None)
    single = cv.parse(row("Оплётка стекловолоконная FSHT(C) 2,0мм белая", "оплётка", **{"Размер до, мм": "2"}), cv.BRAID)
    assert (single.nominal_mm, single.min_mm, single.max_mm) == (2, None, None)


def test_braid_density_only_when_written():
    assert cv.parse(row("Оплётка ПЭТ плотность 5,5 г/м", "оплётка"), cv.BRAID).density == "5,5 г/м"
    assert cv.parse(row("Оплётка ПЭТ 10", "оплётка"), cv.BRAID).density == ""


@pytest.mark.parametrize(("fields", "inner", "outer", "split"), [
    (row("Трубка гофрированная 8,5мм", "гофра", **{"Серия": "Тип B, NW8.5, разрезная негорючая", "Размер до, мм": "8,5", "Размер после, мм": "12"}), 8.5, 12, True),
    (row("Трубка гофрированная неразрезная 4,6мм", "гофра", **{"Серия": "Тип B неразрезная", "Размер до, мм": "4,6", "Размер после, мм": "7,1"}), 4.6, 7.1, False),
    (row("Трубка гофрированная 9,8мм", "гофра", **{"Серия": "Тип B, NW10, разрезная негорючая"}), 9.8, None, True),
    (row("Гофра 6,8мм", "гофра", **{"Размер до, мм": "6,8"}), 6.8, None, None),
])
def test_corrugated_fields(fields, inner, outer, split):
    parsed = cv.parse(fields, cv.CORRUGATED)
    assert (parsed.inner_mm, parsed.outer_mm, parsed.split) == (inner, outer, split)


def test_sorting_by_diameter_as_number_then_name():
    records = [(str(i), r) for i, r in enumerate([
        row("ТУТ 12/4", "ТУТ", **{"Серия": "Б", "Размер до, мм": "12"}),
        row("ТУТ 4/2", "ТУТ", **{"Серия": "Б", "Размер до, мм": "4"}),
        row("ТУТ 9/3", "ТУТ", **{"Серия": "Б", "Размер до, мм": "9"}),
        row("ТУТ без серии", "ТУТ", **{"Размер до, мм": "1"}),
        row("ТУТ А 6", "ТУТ", **{"Серия": "А", "Размер до, мм": "6"}),
        row("ТУТ Б без Ø", "ТУТ", **{"Серия": "Б"}),
    ])]
    names = [item["name"] for item in cv.build(records)["rows"]]
    # owner 15.10.2026: inside a kind — Ø ascending (the series is a column), unknown Ø last
    assert names == ["ТУТ без серии", "ТУТ 4/2", "ТУТ А 6", "ТУТ 9/3", "ТУТ 12/4", "ТУТ Б без Ø"]


@pytest.mark.parametrize(("name", "width"), [
    ("Изолента ПВХ 15х0,13х20м", 15.0), ("Изолента ПВХ 3M Temflex 1300 чёрная 15х10м", 15.0),
    ("Лента 3M PT1100 (акриловая двусторонняя VHB, 6мм×33м)", 6.0), ("Лента нетканая 20мм", 20.0),
    ("Медная лента Bonkeel Prof 10мм x20м", 10.0), ("Токопроводящая экранирующая лента 0,1х50мм х20м", 50.0),
    ("Тканевая лента T150PT", None),
])
def test_tape_width_from_the_name(name, width):
    assert cv.parse(row(name, "лента"), cv.TAPE).width_mm == width


def test_tapes_sort_by_width_and_cambric_by_diameter():
    records = [(str(i), r) for i, r in enumerate([
        row("Лента тканевая 20мм", "лента"), row("Тканевая лента T150PT", "лента"), row("Изолента ПВХ 15х0,13х20м", "лента"),
        row("Трубка ТВ-40 ПВХ 6мм (кембрик)", "кембрик", **{"Размер до, мм": "6"}),
        row("Трубка ТВ-40 ПВХ 4мм (кембрик)", "кембрик", **{"Размер до, мм": "4"}),
    ])]
    names = [(item["group"], item["name"]) for item in cv.build(records)["rows"]]
    assert names == [("cambric", "Трубка ТВ-40 ПВХ 4мм (кембрик)"), ("cambric", "Трубка ТВ-40 ПВХ 6мм (кембрик)"),
                     ("tape", "Изолента ПВХ 15х0,13х20м"), ("tape", "Лента тканевая 20мм"), ("tape", "Тканевая лента T150PT")]


def test_series_family_drops_the_catalogue_number():
    parsed = cv.parse(row("ТУТнг 4/2 красная", "ТУТ", **{"Серия": "ТУТнг (65994)"}), cv.TUT)
    assert (parsed.series, parsed.series_family) == ("ТУТнг (65994)", "ТУТнг")
    assert cv.parse(row("ТТК Flex", "ТТК", **{"Серия": 'ТТК "Flex" (85208)'}), cv.TTK).series_family == 'ТТК "Flex"'
    assert cv.parse(row("Оплётка XT", "оплётка", **{"Серия": "XT (85371)"}), cv.BRAID).series_family == "XT"
    assert cv.parse(row("Оплётка GTRPA-10", "оплётка", **{"Серия": "GTRPA-10"}), cv.BRAID).series_family == "GTRPA"
    assert cv.parse(row("ТУТ", "ТУТ", **{"Серия": "HTS-14"}), cv.TUT).series_family == "HTS-14"
    assert cv.parse(row("ТУТ", "ТУТ", **{"Серия": "ТУТ(3:1)нг-LS"}), cv.TUT).series_family == "ТУТ(3:1)нг-LS"


def test_groups_come_in_the_owner_order_and_non_coverings_are_left_out():
    result = cv.build([("1", row("Стяжка", "стяжка")), ("2", row("Лента", "лента")), ("3", row("Гофра", "гофра")),
                       ("4", row("Оплётка", "оплётка")), ("5", row("ТТК", "ТТК")), ("6", row("ТУТ", "ТУТ")),
                       ("7", row("Кембрик", "кембрик")), ("8", row("Гофра", "гофра", **{cv.OVERRIDE_FIELD: "Прочее"}))])
    # owner 15.10.2026: термоусадка, кембрик, гофра, оплётки, ленты
    assert [item["group"] for item in result["rows"]] == ["tut", "ttk", "cambric", "corrugated", "braid", "tape", "other"]
    assert result["counts"] == {"tut": 1, "ttk": 1, "cambric": 1, "corrugated": 1, "braid": 1, "tape": 1, "other": 1}


def test_bundled_mirror_splits_into_the_four_groups_and_loses_nothing(uow, tmp_path):  # noqa: F811
    load_mirror(default_source(), uow, dry_run=False, now=NOW)
    with uow:
        material = uow.tech_operations.list(tab=TechOperationTab.MATERIAL, query="", limit=10_000)
    result = coverings_catalog(uow)
    counts = result["counts"]
    assert counts["tut"] > 0 and counts["ttk"] > 0 and counts["braid"] > 0 and counts["corrugated"] > 0
    assert len(result["rows"]) == sum(counts.values())
    covering_groups = {"тут", "ттк", "оплётка", "оплетка", "плетёнка", "плетенка", "гофра", "лента", "кембрик"}
    assert len(result["rows"]) == sum(1 for item in material if item.fields.get("Группа", "").casefold() in covering_groups)
    # every shrink row of the mirror has a Ø and a ratio parsed
    shrink = [item for item in result["rows"] if item["group"] in ("tut", "ttk")]
    assert all(item["before_mm"] is not None for item in shrink)


def test_manual_group_roundtrip_through_the_api(uow, sessions):  # noqa: F811
    load_mirror(default_source(), uow, dry_run=False, now=NOW)
    app.state.references_uow_factory = lambda: type(uow)(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="admin@example.test", roles=frozenset({"admin"}), permissions={"erpref": "edit"})
    app.dependency_overrides[moscow_now] = lambda: NOW
    try:
        client = TestClient(app)
        before = client.get("/api/references/coverings").json()
        gofra = next(item for item in before["rows"] if item["group"] == "corrugated")
        assert gofra["group_source"] == "по группе"
        assert client.put(f"/api/references/coverings/{gofra['id']}/group", json={"group": "Прочее"}).status_code == 200
        after = client.get("/api/references/coverings").json()
        moved = next(item for item in after["rows"] if item["id"] == gofra["id"])
        assert (moved["group"], moved["group_source"], moved["auto_group"]) == ("other", "вручную", "corrugated")
        assert after["counts"]["other"] == before["counts"]["other"] + 1
        assert len(after["rows"]) == len(before["rows"])  # nothing is deleted
        assert client.put(f"/api/references/coverings/{gofra['id']}/group", json={"group": ""}).status_code == 200
        assert next(item for item in client.get("/api/references/coverings").json()["rows"] if item["id"] == gofra["id"])["group"] == "corrugated"
        assert client.put(f"/api/references/coverings/{gofra['id']}/group", json={"group": "ерунда"}).status_code == 422
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
        app.dependency_overrides.pop(moscow_now, None)
        del app.state.references_uow_factory


def test_manual_group_refuses_a_non_material(uow):  # noqa: F811
    load_mirror(default_source(), uow, dry_run=False, now=NOW)
    with uow:
        [terminal] = uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=1)
    with pytest.raises(ReferenceValidationError):
        set_covering_group(SetCoveringGroupCommand(record_id=terminal.id, group="ТУТ", actor=ACTOR, now=NOW), uow)


@pytest.mark.parametrize(("name", "series", "material", "ratio", "kind"), [
    # the owner's screenshot of ТУТ (25.10.2026)
    ("ТУТ 3,0/1,5 черная", "20-3006", "полиолефин", "2:1", "plain"),
    ("ТУТ 10/5 черная", "", "", "2:1", "plain"),
    ("ТУТнг 2,5/1,25 черная", "ТУТнг", "полиолефин", "2:1", "ng"),
    ("ТУТнг 4/2 красная", "ТУТнг (65994)", "полиолефин нг", "2:1", "ng"),
    ("ТУТнг 4/2 черная", "ТУТнг-LS", "полиолефин нг-LS (низкое дымо- и газовыделение)", "2:1", "ngls"),
    ("ТУТнг-LS 8/4 черная", "ТУТнг-LS (60089)", "полиолефин нг, низкое дымо- и газовыделение", "2:1", "ngls"),
    ("ТУТнг 1,0/0,5 черная", "ТУТнг-LS(2:1) 20-1006", "полиолефин", "2:1", "ngls"),
    ("ТУТ(HF) 4/2 белая", "ТУТ(HF)", "ПЭВД, безгалогенная (HF)", "2:1", "hf"),
    ("ТУТ(HF) 4/2 прозрачная", "ТУТ-HF (83266)", "полиолефин HF (безгалогенный)", "2:1", "hf"),
    ("ТУТ(3:1)нг-LS 6/2 черная", "ТУТ(3:1)нг-LS", "полиолефин нг-LS (низкое дымо- и газовыделение)", "3:1", "r3"),
    ("ТСТ Raychman 0,8/0,4 черная", "ТСТ", "сшитый полиолефин", "2:1", "brand"),
    ("ТУТ HTS-16 16/8 черная", "HTS-16", "полиолефин", "2:1", "brand"),
    # ТТК
    ("ТТК 6/2 черная", "ТТК(3:1)", "полиолефин нг", "3:1", "r3"),
    ("ТТК 8/2 черная", "ТТК(4:1)", "полиолефин нг", "4:1", "r4"),
    ("ТТК CFM 8/2 чёрная", "CFM", "полиолефин", "4:1", "r4"),
    ("ТТК 19/6,5мм, черная", "", "", "", "plain"),
])
def test_shrink_kind_of_every_name_in_the_owner_screenshot(name, series, material, ratio, kind):
    assert cv.shrink_kind(name, series, material, ratio) == kind


def test_shrink_tubes_are_ordered_by_kind_then_size_then_name():
    def tube(name, series, material, ratio="2:1"):
        return (name, row(name, "ТУТ", **{"Серия": series, "Материал": material, "Коэф. усадки": ratio}))
    names = [("1", tube("ТУТ(3:1)нг-LS 6/2", "ТУТ(3:1)нг-LS", "полиолефин нг-LS", "3:1")), ("2", tube("ТСТ 0,8/0,4", "ТСТ", "полиолефин")),
             ("3", tube("ТУТ(HF) 4/2", "ТУТ(HF)", "безгалогенная (HF)")), ("4", tube("ТУТнг 12/6", "ТУТнг", "полиолефин нг")),
             ("5", tube("ТУТ 10/5", "", "")), ("6", tube("ТУТнг 2/1", "ТУТнг", "полиолефин нг")),
             ("7", tube("ТУТнг 4/2", "ТУТнг-LS", "полиолефин нг-LS")), ("8", tube("ТУТ 2/1", "", ""))]
    built = cv.build([(record_id, fields) for record_id, (_, fields) in names])
    assert [item["id"] for item in built["rows"]] == ["8", "5", "6", "4", "7", "3", "1", "2"]
    assert [kind["id"] for kind in built["kinds"][cv.TUT]] == ["plain", "ng", "ngls", "hf", "r3", "brand"]
    assert built["kinds"][cv.TTK] == []


def test_kind_uses_the_ratio_found_in_the_series_text():
    parsed = cv.parse(row("ТУТ(3:1)нг-LS 20/6 черная", "ТУТ", **{"Серия": "ТУТ(3:1)нг-LS (85089)", "Материал": "полиолефин нг-LS"}), cv.TUT)
    assert parsed.kind == "r3"
