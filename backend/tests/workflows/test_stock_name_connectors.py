"""Full stock names of connector housings, crimp contacts and lugs (owner 27.11.2026: «где папа где мама»).

The fixture holds real articles of the prod stock (27.11) with their current names and the БД.ТЕР rows the builder reads
(export 23.11): before → after for every family, gender by MATING / FAMILY_CONTACTS / БД.ТЕР series / series rule.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.techcards.connector_housings import FAMILIES, MATING, MATING_BY_ARTICLE, housing_of
from app.workflows.stock_name_connectors import (
    ATTR_KEYS, FAMILY_CONTACTS, connector_name, series_token_matches, ter_series_gender,
)
from app.workflows.stock_name_types import LotHints
from app.workflows.warehouse_article import _article_key

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "stock-names-connectors-1127.json").read_text(encoding="utf-8")
)
LOTS = FIXTURE["lots"]
GENDER = re.compile(r"(?<!\w)(вилка|розетка|штырь|гнездо)(?!\w)")


def _record(tab: str, fields: dict[str, str]) -> TechOperationRecord:
    return TechOperationRecord.create(
        tab=TechOperationTab(tab), display_text=fields.get("terArticle") or fields.get("terSeries") or "строка",
        search_text="", source_sheet="БД.ТЕР", sort_key="", fields=fields,
    )


RECORDS = tuple(_record(row["tab"], row["fields"]) for row in FIXTURE["ter"])


def _name(article: str, item_type: str = "Соединитель", records=RECORDS, manufacturer: str = ""):
    return connector_name(LotHints(article=article, item_type=item_type, manufacturer=manufacturer), records)


@pytest.mark.parametrize("lot", LOTS, ids=[f"{lot['item_type']}:{lot['article']}" for lot in LOTS])
def test_real_articles_get_the_full_name(lot) -> None:
    hints = LotHints(article=lot["article"], item_type=lot["item_type"], manufacturer=lot["manufacturer"],
                     name=lot["name"])
    built = connector_name(hints, RECORDS)
    assert (built.name, built.complete, list(built.missing)) == (lot["expected"], lot["complete"], lot["missing"])
    assert set(built.attrs) == set(ATTR_KEYS)
    if built.complete:
        assert built.name[:1].isupper()
        assert lot["item_type"] != "Наконечник" or not re.search(r"\d\.\d", built.name)  # decimal comma


def test_owner_examples() -> None:
    assert _name("XT60H-M").name == "Amass XT60H вилка (папа)"
    assert _name("XT90S-F").name == "Amass XT90S розетка (мама)"
    assert _name("XHP-4").name == "XH розетка (мама) 4pin (однорядный)"
    assert _name("SMR-04V-B").name == "SM вилка (папа) 4pin (однорядный)"
    assert _name("SMP-02V-BC").name == "SM розетка (мама) 2pin (однорядный)"
    assert _name("0510210300").name == "PicoBlade розетка (мама) 3pin (однорядный)"
    assert _name("510470300").name == "PicoBlade вилка (папа) 3pin (однорядный)"
    assert _name("S7B-XH-A(LF)(SN)").name == "XH вилка (папа) 7pin (однорядный), угловая на плату"
    assert _name("SXH-001T-P0.6", "Терминал").name == "Контакт XH гнездо (мама), 0,08–0,35 мм²"
    assert _name("НВИ 1.5-4", "Наконечник").name == "Наконечник НВИ 1,5-4"
    assert _name("ГС 2,5", "Терминал").name == "Гильза соединительная ГС 2,5"


@pytest.mark.parametrize(("article", "source"), [
    ("XT60H-M", "MATING"),
    ("SA812/S4B", "MATING"),
    ("XHP-4", "FAMILY_CONTACTS"),
    ("0022552401", "FAMILY_CONTACTS"),
    ("0901430010", "БД.ТЕР: серия 90143"),
    ("FI-S20S", "БД.ТЕР: серия FI-S"),
    ("DS1069-9-FCW", "правило серии"),
    ("QS8-S-M", "правило серии"),
])
def test_gender_source_order(article: str, source: str) -> None:
    built = _name(article)
    assert built.complete and built.source.startswith(source)
    assert built.attrs["gender"] in {"pin", "socket"}


def test_colour_tail_does_not_hide_the_maker_gender_letter() -> None:
    built = _name("XT60EW-M.G.B")
    assert built.name == "Amass XT60EW вилка (папа), G.B"
    assert built.source.startswith("MATING")


@pytest.mark.parametrize("article", ["GX12M-2A", "8P8C-CH-C6-T", "174047-2", "0511910300", "M12-P5A-GWBM12"])
def test_unknown_contacts_give_no_gender_and_go_to_review(article: str) -> None:
    built = _name(article)
    assert not built.complete
    assert "пол" in built.missing
    assert not GENDER.search(built.name)
    assert built.attrs["gender"] == ""


def test_pairs_and_adapters_go_to_review() -> None:
    assert not _name("XT30U-M+F").complete
    assert not _name("XT60 ШТАНЫ").complete
    assert not _name("MR30F").complete


def test_series_token_is_exact() -> None:
    assert series_token_matches("XHP4", "XHP")
    assert not series_token_matches("XHP4", "XH")
    assert not series_token_matches("SHDR20", "SHD")
    assert series_token_matches("SHDR20", "SHDR")
    assert series_token_matches("430250400", "43025")
    assert series_token_matches("1053071206", "105307")
    assert not series_token_matches("39012025", "3901")  # 4 digits is no Molex series token
    assert not series_token_matches("IDS1071SCB01X3", "I-DS1071-SCB01")  # mixed tokens are not read


def test_shdr_never_takes_the_shd_contacts() -> None:
    shd = (_record("ter", {"terSeries": "SHR, SHD", "terComponent": "SH, SHD", "terType": "Female",
                           "terArticle": "SSH-003GA-P0.2"}),)
    built = _name("SHDR-20", records=shd)
    assert not built.complete and "пол" in built.missing
    shdr = (_record("ter", {"terSeries": "SHDR", "terComponent": "SHD", "terType": "Female",
                            "terArticle": "SSHD-003T-P0.2"}),)
    assert _name("SHDR-20", records=shdr).attrs["gender"] == "socket"


def test_ter_rows_must_agree() -> None:
    rows = (
        _record("ter", {"terSeries": "ABC", "terType": "Female", "terArticle": "ABC-F"}),
        _record("ter", {"terSeries": "ABC", "terType": "Male", "terArticle": "ABC-M"}),
    )
    assert ter_series_gender("ABC12", rows) is None
    assert ter_series_gender("ABC12", rows[:1]) == ("socket", "БД.ТЕР: серия ABC")
    # Lug rows never give a housing its gender.
    assert ter_series_gender("ABC12", (_record("lug", {"terSeries": "ABC", "terType": "Male"}),)) is None


def test_family_contacts_agree_with_mating_and_ter() -> None:
    family_ids = {family["id"] for family in FAMILIES}
    known: dict[str, set[str]] = {key: {value["contacts"]} for key, value in MATING.items()}
    for family_id, _, entry in MATING_BY_ARTICLE:
        known.setdefault(family_id, set()).add(entry["contacts"])
    for family_id, (contacts, source) in FAMILY_CONTACTS.items():
        assert family_id in family_ids and contacts in {"pin", "socket"} and source
        if family_id in known:
            assert known[family_id] == {contacts}, family_id
    checked = 0
    for lot in LOTS:
        housing = housing_of(lot["article"]) if lot["item_type"] == "Соединитель" else None
        if housing and housing["family"] in FAMILY_CONTACTS:
            by_ter = ter_series_gender(_article_key(lot["article"]), RECORDS)
            if by_ter:
                checked += 1
                assert by_ter[0] == FAMILY_CONTACTS[housing["family"]][0], lot["article"]
    assert checked >= 5


def test_distinct_articles_never_share_a_complete_name_and_maker() -> None:
    seen: dict[tuple[str, str], str] = {}
    for lot in LOTS:
        built = connector_name(
            LotHints(article=lot["article"], item_type=lot["item_type"], manufacturer=lot["manufacturer"]), RECORDS,
        )
        if built.complete:
            key = (built.name, built.attrs["maker"])
            assert seen.setdefault(key, lot["article"]) == lot["article"], key


@pytest.mark.parametrize(("article", "expected"), [
    ("НВИ 1.5-4", "Наконечник НВИ 1,5-4"),
    ("РПИ-П 1,5-(4,8)", "Наконечник РПИ-П 1,5-4,8, вилка (папа)"),
    ("РПИ-М 2.5-(4.8)", "Наконечник РПИ-М 2,5-4,8, розетка (мама)"),
    ("НКИ-1.5-6", "Наконечник НКИ 1,5-6"),
    ("НШВ 0.5-8", "Наконечник НШВ 0,5-8"),
    ("НИ 2,5-5", "Наконечник НИ 2,5-5"),
    ("ГС 2,5", "Гильза соединительная ГС 2,5"),
])
def test_lug_spellings(article: str, expected: str) -> None:
    built = _name(article, "Наконечник")
    assert built.complete and built.name == expected


def test_lug_without_size_goes_to_review() -> None:
    built = _name("НВИ", "Наконечник")
    assert not built.complete and "сечение-размер" in built.missing
    assert not _name("FDD2-250", "Наконечник").complete


def test_contact_falls_back_to_the_series_rule() -> None:
    built = _name("SHF-001T-0.8BS", "Терминал", records=())
    assert built.name == "Контакт SM гнездо (мама)" and built.complete


def test_other_types_are_out_of_scope() -> None:
    assert _name("RG-58", "Провод Монтажный") is None
    assert _name("SMA-J", "Коаксиальный соединитель") is None


def test_ra_housings_wait_for_a_sheet() -> None:
    # housing_of reads RA-3011 as 30 contacts while the stock says «RA 3pin»: no pin count is better than a wrong one.
    built = _name("RA-3011")
    assert built is not None and not built.complete
    assert any("число контактов" in item for item in built.missing)


def test_nylon_mark_after_the_size_is_kept():
    after = _name("НВИ 1,5-4 (н)", "Наконечник")
    before = _name("НВИ(н) 1,5-4", "Наконечник")
    plain = _name("НВИ 1,5-4", "Наконечник")
    assert after.name == before.name != plain.name


@pytest.mark.parametrize(("article", "name"), [
    ("I-DS1071-SCB01x5", "BLS розетка (мама) 5pin (однорядный)"),
    ("I-DS1071-SCB02x5", "BLD розетка (мама) 10pin (двухрядный)"),
    ("I-DS1071-01-SCB01x5", "BLS2 розетка (мама) 5pin (однорядный)"),
    ("I-DS1071-01-SCB02x10", "BLD2 розетка (мама) 20pin (двухрядный)"),
    ("I-DS1071-SCB2x4", "BLD розетка (мама) 8pin (двухрядный)"),
])
def test_bls_is_one_row_and_bld_two(article: str, name: str) -> None:
    # Owner 27.11: «BLS, BLD одно и то же, хотя D двухрядные, а S однорядные».
    assert _name(article, manufacturer="CONNFLY").name == name


def test_a_contact_of_a_housing_pair_names_both_with_a_slash() -> None:
    built = _name("SSHL-002T-P0.2", "Терминал")
    assert built.name.startswith(("Контакт GHD/GH ", "Контакт GH/GHD ")), built.name
