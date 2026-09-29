from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.workflows.warehouse_article import infer_article_identity


def record(tab: TechOperationTab, **fields: str) -> TechOperationRecord:
    return TechOperationRecord.create(
        tab=tab,
        display_text=next(iter(fields.values())),
        search_text="",
        source_sheet="БД.Технология",
        sort_key="",
        fields=fields,
    )


def test_article_identity_uses_terminal_series_and_connector_family():
    records = (
        record(
            TechOperationTab.TERMINAL,
            terArticle="430300001",
            terSeries="43025",
            terComponent="Micro-Fit 3.0",
            terType="Female",
        ),
        record(
            TechOperationTab.TERMINAL,
            terArticle="430310001",
            terSeries="43020",
            terComponent="Micro-Fit 3.0",
            terType="Male",
        ),
    )

    contact = infer_article_identity("430300001", records)
    housing = infer_article_identity("0430250600", records)
    generic = infer_article_identity("0430200201", records)

    assert (contact.series, contact.name, contact.item_type) == (
        "Micro-Fit 3.0", "Контакт Micro-Fit 3.0 Female", "Терминал",
    )
    assert (housing.series, housing.name, housing.item_type) == (
        "Micro-Fit 3.0", "Micro-Fit 3.0 6pin (двухрядный)", "Соединитель",
    )
    assert generic.name == "Micro-Fit 3.0 2pin"


def test_article_identity_normalizes_coax_database_and_supplier_codes():
    records = (
        record(
            TechOperationTab.COAX,
            coaxArticle="BNC-C58P",
            coaxType="BNC-male",
            coaxWire="RG58",
        ),
    )

    from_database = infer_article_identity("BNC-C58P", records)
    inferred = {
        article: infer_article_identity(article)
        for article in (
            "SMA-JW2CG",
            "SMA-C58P gold",
            "SMB-KB2",
            "SMA-LC174P@RUICHI, SMA-Male R/A RG174/RG316",
        )
    }

    assert from_database.name == "BNC-male RG-58"
    assert inferred["SMA-JW2CG"].name == "SMA-male R/A"
    assert inferred["SMA-C58P gold"].name == "SMA-male RG-58"
    assert inferred["SMB-KB2"].name == "SMB-female"
    assert inferred["SMA-LC174P@RUICHI, SMA-Male R/A RG174/RG316"].name == "SMA-male R/A RG-174/RG-316"


def test_article_identity_handles_latest_housing_aliases():
    lp12 = infer_article_identity("LP-12")
    usb = infer_article_identity("USB-A Male 2.0")

    assert (lp12.series, lp12.name) == ("CNLINKO LP-12", "CNLINKO LP-12 2pin")
    assert (usb.series, usb.name) == ("USB-A 2.0", "USB-A 2.0 4pin (однорядный)")


# SUPPLYMAP 39a5a97 (Stock05.gs stock05ArtInfo_): cases from _local/tooling/test/run.mjs.
def _names(*articles: str, records=()) -> list[str | None]:
    return [
        (identity.name if (identity := infer_article_identity(article, records)) else None)
        for article in articles
    ]


def test_article_identity_cuts_supplier_tail_and_leading_zeros_for_any_article():
    micro_fit = (
        record(TechOperationTab.TERMINAL, terArticle="430310001", terSeries="43025", terComponent="Micro-Fit 3.0"),
    )
    plain = infer_article_identity("430250600", micro_fit)
    tailed = infer_article_identity("0430250600@MOLEX SH12501-4Y china (на тесты)", micro_fit)
    mini_fit = infer_article_identity("0039012141@MOLEX")

    assert tailed == plain
    assert (mini_fit.series, mini_fit.name) == ("Mini-Fit Jr.", "Mini-Fit Jr. 14pin (двухрядный)")
    assert infer_article_identity("0039012025@MOLEX").name == "Mini-Fit Jr. 2pin (двухрядный)"
    assert infer_article_identity("174047-2@TYCO").series == "Multilock 040"
    assert infer_article_identity("@MOLEX") is None


def test_article_identity_series_rules_come_before_housing_recognition():
    assert _names(
        "0039014051@MOLEX", "0050375043@MOLEX", "0470541000@MOLEX", "0510050200@MOLEX",
        "0511102052@MOLEX", "0511910600@MOLEX", "0901430010@MOLEX", "1053071206@MOLEX",
    ) == [
        "Mini-Fit Jr. 5pin (однорядный)", "Mini-SPOX 4pin (однорядный)", "KK 254 4pin (однорядный)",
        "MX2.0 (51005) 2pin (однорядный)", "Milli-Grid 20pin (двухрядный)", "Mini-Latch 6pin (однорядный)",
        "C-Grid III 10pin (двухрядный)", "Nano-Fit 6pin (однорядный)",
    ]
    # I-DS1069 has a housing drawing (Connfly DS1069), but the series rule names it first.
    housing = infer_article_identity("I-DS1069-SCW004")
    assert (housing.series, housing.name, housing.source) == ("XH", "XH розетка 4pin (однорядный)", "серия по артикулу")


def test_article_identity_series_rules_for_jst_analogues_and_contacts():
    assert _names(
        "S7B-XH-A(LF)(SN)@JST", "A2501-07AW (S7B-XH-A, WK-07R, DS1069-07MRW, PW10-07-M-R)",
        "DS1069-7-MRW6SA@CONNFLY", "DS1069-9-FCW@CONNFLY", "SCT20004H-2X3P (PHDR-06VS)",
        "SCT2004H-203BWT182@SCONDAR", "SHF-001T-0.8BS", "SYM-001T-P0.6(N)", "SXH-001T-P0/6N##5000@JST",
        "DS1107-WN0@CONNFLY", "T-DS1069-SC600",
    ) == [
        "XH вилка 7pin (однорядный), угловая на плату", "XH вилка 7pin (однорядный), угловая на плату",
        "XH вилка 7pin (однорядный), угловая на плату", "XH розетка 9pin (однорядный)", "PHD 6pin (двухрядный)",
        "PHD 6pin (двухрядный)", "Контакт SM Female", "Контакт SM Male", "Контакт XH Female",
        "USB-A вилка под пайку", "Контакт XH Female",
    ]
    contact = infer_article_identity("SHF-001T-0.8BS")
    assert (contact.series, contact.item_type) == ("SM", "Терминал")


def test_article_identity_series_rules_for_circular_and_other_connectors():
    assert _names(
        "Т4140012051-000, ТЕ Connectivity", "FS 4.6-0.5/18.25, Turck, 6-Контактный штекерный разъём",
        "M12-P5A-GWBM12, CAZN", "GX16M-2A", "Разъём QS8-S-M male безыскровый (вилка, 120А, черный)",
        "6136.0138.0310 Разъем питания SCHURTER", "Штекер питания SZC-0026a/2.5*0.7 mm(7-0026a)",
    ) == [
        "M12 вилка 5pin (A-код), на плату", "M12 вилка 6pin, панельная, провода 0,5 м", "M12 5pin (A-код)",
        "GX16 2pin", "QS8-S вилка (безыскровый)", "Ввод питания IEC C20 с автоматом (Schurter 6136)",
        "DC-штекер 2,5×0,7 мм",
    ]
    button = infer_article_identity("PBS-10B-RED@RUICHI")
    assert (button.series, button.name, button.item_type) == ("PBS-10B", "Кнопка PBS-10B без фиксации", "Кнопка")


def test_article_identity_finds_nine_digit_molex_number_inside_description():
    picoblade = infer_article_identity("MOLEX PicoBlade 510470300 корпус вилки на кабель; 3-конт.")

    assert (picoblade.series, picoblade.name) == ("PicoBlade", "PicoBlade 3pin (однорядный)")
    # Ten digits are not a nine-digit number with a tail.
    assert infer_article_identity("описание 5104703001") is None


def test_article_identity_takes_plug_or_socket_from_housing_title():
    assert _names("SMR-02V-B@JST", "SMP-02V-BC@JST", "BL2018P-04") == [
        "SM розетка 2pin (однорядный)", "SM вилка 2pin (однорядный)", "SM вилка 4pin (однорядный)",
    ]
    assert infer_article_identity("SMR-02V-B").series == "SM"


def test_article_identity_decodes_supplier_coax_codes():
    assert _names(
        "S-211F", "N-121F male R/A RG58", "T-111B", "BNC B-111L-1", "S-111L male RG316 RG174", "N-231F RG58",
        "S-M111C SMA male 5D, LMR240, RG8X", "S-M015R male",
    ) == [
        "SMA-female RG-58", "N-male R/A RG-58", "TNC-male RG-213", "BNC-male RG-174", "SMA-male RG-316/RG-174",
        "N-female RG-58", "SMA-male", "SMA-male",
    ]
    vendor = infer_article_identity("S-211F")
    assert (vendor.series, vendor.item_type, vendor.source) == ("SMA", "Коаксиальный соединитель", "код поставщика")
    # 3xx / 4xx (panel, flange) are not decoded; a lower-case supplier letter still names the family.
    assert infer_article_identity("S-311F") is None
    assert infer_article_identity("s-M015R male").name == "SMA-male"


def test_article_identity_lists_every_rg_and_reads_086_as_rg405():
    assert _names(
        "MCX-female R/A RG316 RG174", "RHT-612-0646, SMA-Female RG316/RG174", "N male RG58C/U",
        "TNC male RG316 RG 174 T-111L", "SMA-Male RG1.37", "TNC(female)- RG174, RG316",
        "SMA-LC174P@RUICHI (SMA Female R/A RG174/RG316)", "SMA-58P@china",
    ) == [
        "MCX-female R/A RG-316/RG-174", "SMA-female RG-316/RG-174", "N-male RG-58", "TNC-male RG-316/RG-174",
        "SMA-male", "TNC-female RG-174/RG-316", "SMA-male R/A RG-174/RG-316", "SMA-male RG-58",
    ]
    assert _names("SMA-JWB2-F", "SMA-JWB3-F-1", "SMA-F", "2.92mm male 086", "SMA-C58J GOLD") == [
        "SMA-male R/A RG-405", "SMA-male R/A RG-402", "SMA-female", "2.92mm-male RG-405", "SMA-male RG-58",
    ]


def test_article_identity_knows_isma_and_precision_coax_families():
    assert _names(
        "2.92-JB2G-F", "3.5-JB2AG", "ISMA-KB2-F", "MCX KWHT", "SMP-KW2 R/A RG178", "SMB-K1.5 RG174 RG316",
        "SMA-JR угловой", "F male RG316 RG174", "NGD N-1128D female 8D-FB",
    ) == [
        "2.92mm-male RG-405", "3.5mm-male RG-405", "SMA-female RG-405", "MCX-female R/A", "SMP-female R/A RG-178",
        "SMB-female RG-174/RG-316", "SMA-male R/A", "F-male RG-316/RG-174", "N-female",
    ]
    isma = infer_article_identity("ISMA-KB2-F")
    assert (isma.series, isma.source) == ("SMA", "разбор кода")
    described = infer_article_identity("2.92mm male 086")
    assert (described.series, described.source) == ("2.92mm", "разбор описания")


def test_article_identity_keeps_unknown_and_database_answers():
    records = (record(TechOperationTab.COAX, coaxArticle="SMA-JWB2", coaxType="SMA-male R/A", coaxWire="LMR-100"),)

    assert _names("SMA-JWB2", "N-C58P", "SMA-KE174", "RHT-612-0646", "", records=records) == [
        "SMA-male R/A LMR-100", "N-male RG-58", "SMA-female RG-174", None, None,
    ]
