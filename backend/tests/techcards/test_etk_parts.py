from app.modules.techcards.etk_parts import EtkLot, EtkLots, attach_parts, kit_article, lots_text, part_row, use_article


def _names(article: str) -> str:
    # Stand-in for the warehouse article rules (stock05ArtInfo_ with empty БД.ТЕР/БД.КОАКС).
    return {"SHF-001T-0.8BS": "Контакт SM Female", "SMP-02V-BC": "SM розетка 2pin (однорядный)"}.get(article, "")


def test_kit_name_code_other_than_article_is_the_article_to_apply():
    # SUPPLYMAP run.mjs: «набор: код в НАИМЕНОВАНИИ ≠ артикулу — артикул к применению, артикул — по КД».
    assert [
        use_article("BL2018TS", "SHF-001T-0.8BS"),
        use_article("GHR-04V-S", "GHR-04V-S"),
        use_article("GHR-04V-S", ""),
        use_article("МК26-13 0,35 черный", "Провод МК 26-13 (цветной МГТФ). Продажа от 5 метров!"),
        use_article("", "SMP-02V-BC"),
    ] == [
        {"art": "SHF-001T-0.8BS", "kd": "BL2018TS"},
        {"art": "GHR-04V-S", "kd": ""},
        {"art": "GHR-04V-S", "kd": ""},
        {"art": "МК26-13 0,35 черный", "kd": ""},
        {"art": "SMP-02V-BC", "kd": ""},
    ]


def test_component_row_is_article_kd_name_by_article_and_grn_only_from_a_lot():
    def row(**fields):
        converted = part_row(fields, _names)
        return [converted["art"], converted["kd"], converted["name"], converted["grn"]]

    assert [
        row(art="BL2018TS", name="SHF-001T-0.8BS", norm="4000"),
        row(art="SMP-02V-BC", name="SMP-02V-BC"),
        row(art="UL1061 30AWG белый", name="UL1061 30AWG белый"),
        row(art="МК26-13 0,35 черный", name="Провод МК 26-13 (цветной МГТФ)"),
    ] == [
        ["SHF-001T-0.8BS", "BL2018TS", "Контакт SM Female", ""],
        ["SMP-02V-BC", "", "SM розетка 2pin (однорядный)", ""],
        ["UL1061 30AWG белый", "", "", ""],
        ["МК26-13 0,35 черный", "", "Провод МК 26-13 (цветной МГТФ)", ""],
    ]


def test_kd_analogue_is_replaced_in_operation_and_harness_and_a_second_pass_changes_nothing():
    document = {
        "operations": [{"connector": "BL2018P-04", "connector_article": "BL2018P-04",
                        "components": [{"art": "BL2018P-04", "name": "SMP-04V-BC", "norm": "1"}]}],
        "topology": {"nodes": [{"id": "n1", "conn": "BL2018P-04"}]},
    }
    once = attach_parts(document)
    operation = once["operations"][0]
    assert [operation["connector"], operation["connector_article"], once["topology"]["nodes"][0]["conn"],
            operation["components"][0]["kd"]] == ["SMP-04V-BC", "SMP-04V-BC", "SMP-04V-BC", "BL2018P-04"]
    assert attach_parts(once) == once
    assert document["operations"][0]["connector"] == "BL2018P-04"  # the stored document is not mutated
    assert attach_parts(None) is None


def test_kit_row_uses_the_wire_from_the_name_when_the_calc_replaced_it():
    """Owner, 05.10: the wire was changed in the calc — «Наименование» is the item used, the КД article stays as kd."""
    assert [
        kit_article("Силиконовый 28AWG черный", "Силиконовый 30AWG черный"),
        kit_article("МК26-13 0,35 черный", "МГТФ 0,35 белый"),
        kit_article("BL2018TS", "SHF-001T-0.8BS"),
    ] == [
        {"art": "Силиконовый 30AWG черный", "kd": "Силиконовый 28AWG черный"},
        {"art": "МГТФ 0,35 белый", "kd": "МК26-13 0,35 черный"},
        {"art": "SHF-001T-0.8BS", "kd": "BL2018TS"},
    ]


def test_kit_row_name_that_describes_the_article_is_not_a_replacement():
    article = "МК26-13 0,35 черный"
    assert [
        kit_article(article, "Провод МК 26-13 (цветной МГТФ). Продажа от 5 метров!"),  # supplier page title
        kit_article(article, "Провод МК26-13 0,35 черный"),  # repeats the article
        kit_article(article, "мк26-13 0.35 чёрный"),  # the same article, spelled otherwise
        kit_article(article, "Провод монтажный МК 26-13 сечением 0,35 цвет черный"),  # too long for a designation
        kit_article(article, ""),
        kit_article("GHR-04V-S", "Колодка 4pin 1.25мм"),  # a part number keeps its descriptive name
        kit_article("", "Силиконовый 30AWG черный"),
    ] == [
        {"art": article, "kd": ""}, {"art": article, "kd": ""}, {"art": article, "kd": ""},
        {"art": article, "kd": ""}, {"art": article, "kd": ""},
        {"art": "GHR-04V-S", "kd": ""}, {"art": "Силиконовый 30AWG черный", "kd": ""},
    ]


def test_issued_etk_rows_keep_the_article_they_were_built_with():
    # Read-time ETK conversion stays on kitUseArt_: an ETK built with the КД wire is not relabelled as the new one.
    assert use_article("Силиконовый 28AWG черный", "Силиконовый 30AWG черный") == {
        "art": "Силиконовый 28AWG черный", "kd": "",
    }


# ── ГРН by the current bindings (owner, 12.10.2026: «после того как Алексей привяжет к позиции ГРН, нужно чтобы это
# синкалось в техкарте») ──


def _etk(*rows: dict) -> dict:
    return {"operations": [{"code": "CRIMP", "connector": "XS1", "components": [dict(row) for row in rows]}]}


def _grn(document: dict) -> list[str]:
    return [row["grn"] for row in document["operations"][0]["components"]]


def test_grn_bound_in_the_kitset_after_the_etk_was_built_shows_on_read():
    etk = _etk({"art": "43025-0400", "name": "Корпус Molex", "norm": "2"}, {"art": "UL1061 30AWG белый"})
    lots = EtkLots(kitset=(EtkLot(article="43025-0400", name="Корпус Molex", grn="Г-101", package="1"),))

    assert _grn(attach_parts(etk, None, lots)) == ["Г-101", ""]
    assert _grn(attach_parts(etk)) == ["", ""]


def test_issued_lots_beat_the_kitset_binding_and_the_snapshot():
    etk = _etk({"art": "43025-0400", "grn": "Г-СНИМОК"}, {"art": "GHR-04V-S", "grn": "Г-СНИМОК"})
    lots = EtkLots(
        issued=(EtkLot(article="43025-0400", name="43025-0400", grn="Г-202"),),
        kitset=(
            EtkLot(article="43025-0400", name="", grn="Г-101"),
            EtkLot(article="GHR-04V-S", name="Колодка 4pin", grn="Г-103"),
        ),
    )

    assert _grn(attach_parts(etk, None, lots)) == ["Г-202", "Г-103"]


def test_no_binding_for_the_article_keeps_the_snapshot():
    etk = _etk({"art": "43025-0400", "grn": "Г-СНИМОК"}, {"name": "Термоусадка"})
    lots = EtkLots(issued=(EtkLot(article="GHR-04V-S", name="", grn="Г-202"),))

    assert _grn(attach_parts(etk, None, lots)) == ["Г-СНИМОК", ""]


def test_several_lots_of_a_position_are_listed_once_each_in_binding_order():
    etk = _etk({"art": "43025-0400"})
    lots = EtkLots(issued=(
        EtkLot(article="43025-0400", name="", grn="Г-7", package="1"),
        EtkLot(article="43025-0400", name="", grn="Г-3", package="1"),
        EtkLot(article="43025-0400", name="", grn="Г-7", package="1"),
    ))

    assert _grn(attach_parts(etk, None, lots)) == ["Г-7, Г-3"]


def test_package_is_named_only_when_one_grn_came_in_different_packages():
    lot = lambda grn, package: EtkLot(article="A", name="", grn=grn, package=package)  # noqa: E731

    assert lots_text([lot("Г-7", "1"), lot("Г-7", "2"), lot("Г-3", "5")]) == "Г-7 · 1, Г-7 · 2, Г-3"
    assert lots_text([lot("Г-7", ""), lot("Г-7", "2")]) == "Г-7, Г-7 · 2"
    assert lots_text([lot("Г-7", "4"), lot("", "1")]) == "Г-7"


def test_binding_matches_the_kd_article_and_the_article_to_apply_either_way_round():
    """533К.11: the kit line is КД BL2018TS with the name SHF-001T-0.8BS (the article to apply); the ETK row shows the
    applied one with «по КД» under it — both sides spaced and cased differently still meet."""
    etk = _etk({"art": "BL2018TS", "name": "SHF-001T-0.8BS"}, {"art": "shf-001t-0.8bs"})
    lots = EtkLots(kitset=(EtkLot(article="BL2018TS ", name="SHF-001T-0.8BS", grn="Г-55"),))

    assert _grn(attach_parts(etk, None, lots)) == ["Г-55", "Г-55"]


def test_kit_request_numbers_of_issued_lots_are_unique_and_joined_none_for_kitset_only():
    etk = _etk({"art": "43025-0400"}, {"art": "GHR-04V-S"}, {"name": "Термоусадка"})
    lots = EtkLots(
        issued=(
            EtkLot(article="43025-0400", name="", grn="Г-7", request="REQ.26.0000001"),
            EtkLot(article="43025-0400", name="", grn="Г-3", request="REQ.26.0000002"),
            EtkLot(article="43025-0400", name="", grn="Г-4", request="REQ.26.0000001"),
        ),
        kitset=(EtkLot(article="GHR-04V-S", name="", grn="Г-103"),),
    )

    rows = attach_parts(etk, None, lots)["operations"][0]["components"]

    assert [row.get("kit_request") for row in rows] == ["REQ.26.0000001, REQ.26.0000002", None, None]



def test_etk_input_names_wires_and_tubes_by_the_references_not_by_the_supplier_title():
    """Owner 22.10: ETK «Вход» showed site titles under the article; the live display path now names by the references."""
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
    from app.modules.references.load_mirror import BUNDLED_MIRROR, load_mirror
    from app.modules.techcards.api import etk_part_identifier
    from app.shared.persistence import create_schema

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    def factory():
        return SqlAlchemyReferencesUnitOfWork(sessions)

    load_mirror(BUNDLED_MIRROR, factory(), dry_run=False, now=datetime(2026, 10, 6, 12, 0))
    identify = etk_part_identifier(factory)
    assert identify("ПВАМ 0,35 красный") == "Провод ПВАМ 0,35 мм² красный"
    assert identify("ТУТ 3,0/1,5мм, чёрная") == "Трубка термоусадочная ТУТ 3/1,5 мм, чёрная (Rexant)"
    assert identify("ZZ-9") == ""
    row = part_row({"art": "ПВАМ 0,35 красный", "name": "Автомобильный провод ПВАМ (медный). Продажа от 5 м!"}, identify)
    assert (row["art"], row["name"]) == ("ПВАМ 0,35 красный", "Провод ПВАМ 0,35 мм² красный")
    engine.dispose()
