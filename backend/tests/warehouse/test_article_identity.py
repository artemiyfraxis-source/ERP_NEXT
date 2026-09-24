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
    assert inferred["SMA-LC174P@RUICHI, SMA-Male R/A RG174/RG316"].name == "SMA-male R/A RG-174"


def test_article_identity_handles_latest_housing_aliases():
    lp12 = infer_article_identity("LP-12")
    usb = infer_article_identity("USB-A Male 2.0")

    assert (lp12.series, lp12.name) == ("CNLINKO LP-12", "CNLINKO LP-12 2pin")
    assert (usb.series, usb.name) == ("USB-A 2.0", "USB-A 2.0 4pin (однорядный)")
