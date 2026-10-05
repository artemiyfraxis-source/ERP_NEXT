from app.modules.techcards.etk_parts import attach_parts, kit_article, part_row, use_article


def _names(article: str) -> str:
    # Stand-in for the warehouse article rules (stock05ArtInfo_ with empty БД.ТЕР/БД.КОАКС).
    return {"SHF-001T-0.8BS": "Контакт SM Female", "SMP-02V-BC": "SM вилка 2pin (однорядный)"}.get(article, "")


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
        ["SMP-02V-BC", "", "SM вилка 2pin (однорядный)", ""],
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
