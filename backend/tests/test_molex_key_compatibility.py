"""M2/M3 preserve stored purchasing identities and the existing issued ETK views."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy.orm import Session

from app.modules.purchasing.items import part_key
from app.modules.purchasing.upd_prices import catalog_key
from app.modules.purchasing.upd_registry import _line_row
from app.modules.techcards import connector_housings as housings
from app.modules.techcards.etk_read import card_view
from app.modules.techcards.project_cards import ProjectTechcard, TechcardStatus
from app.workflows.article_key import code_key, comparison_code_key
from app.workflows.warehouse_article import infer_article_identity
from tests.techcards.test_etk_golden import _cases, _fingerprint, _golden
from tests.workflows.test_molex_match_report import db as report_db, snapshot


@pytest.mark.parametrize("article, stored", [
    ("0510210400", "0510210400"), ("51021-0400", "510210400"),
    ("0901430010", "0901430010"), ("90143-0010", "901430010"),
    ("0511102052", "0511102052"), ("51110-2052", "511102052"),
    ("SHR 02V-s-b", "SHR02VSB"),
])
def test_purchasing_and_price_history_keep_historical_keys(article, stored):
    assert code_key(article) == part_key(article) == stored
    upd = _line_row("document", "manual", seq=1, name="Корпус", article=article)
    assert upd.article == article
    assert upd.article_key == stored
    # Price history already groups purely numeric articles by name, even Molex ones.
    history_key = f"a:{stored}" if article == "SHR 02V-s-b" else f"n:{upd.name_key}"
    assert catalog_key(upd.name_key, upd.article, upd.article_key) == history_key


def test_comparison_port_is_explicit_and_maker_aware():
    assert code_key("0510210400") != code_key("51021-0400")
    assert comparison_code_key("0510210400") == comparison_code_key("51021-0400")
    assert comparison_code_key("0901430010", manufacturer="Molex") == "901430010"
    assert comparison_code_key("0510210400", manufacturer="JST", catalog_molex=True) == "0510210400"
    assert comparison_code_key("0012345678") == "0012345678"


@pytest.mark.parametrize("article", ["90143-0010", "0901430010", "Molex 0901430010"])
def test_comparison_equivalence_does_not_invent_uncatalogued_geometry(article):
    assert comparison_code_key("0901430010", manufacturer="Molex") == comparison_code_key("90143-0010", manufacturer="Molex")
    # The catalogue has 90123 geometry, not 90143. Numeric equivalence alone
    # supplies neither its contact layout nor a checked wire-side orientation.
    assert housings.housing_of(article) is None


def _issued(document):
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    card = ProjectTechcard.create(
        project="720К.", item_index="КБ1", product_name="Жгут", source_calculation="",
        deadline=None, now=now,
    )
    return replace(card, status=TechcardStatus.ISSUED, revision=1, issued_at=now, document=document)


def _identify(article):
    info = infer_article_identity(article, ())
    return info.name if info else ""


@pytest.mark.parametrize("case", sorted(_cases()))
@pytest.mark.parametrize("frozen", [False, True])
def test_existing_golden_etks_build_and_read_unchanged(case, frozen, monkeypatch):
    monkeypatch.setenv("ETK_AUTO_REVISION", "1")
    factory = _cases()[case]
    # Disable only the added candidate to exercise the previous housing behaviour.
    with monkeypatch.context() as legacy:
        legacy.setattr(housings, "is_molex_article", lambda value, **kwargs: False)
        previous = factory()
        card = _issued(previous)
        before = card_view(card, identify=_identify, frozen=frozen)
    document = factory()
    assert _fingerprint(document) == _golden()["cases"][case]
    assert document == previous
    saved = deepcopy(card.document)
    assert card_view(card, identify=_identify, frozen=frozen) == before
    assert card.document == saved


@pytest.mark.parametrize("frozen", [False, True])
def test_newly_resolved_issued_article_only_adds_derived_housings(frozen, monkeypatch):
    monkeypatch.setenv("ETK_AUTO_REVISION", "1")
    document = {
        "operations": [{"key": "mount", "type": "insTerm", "connector_article": "0510210400"}],
        "topology": {"nodes": [{"id": "А1", "conn": "0510210400"}]},
    }
    saved = deepcopy(document)
    card = _issued(document)
    with monkeypatch.context() as legacy:
        legacy.setattr(housings, "is_molex_article", lambda value, **kwargs: False)
        before = card_view(card, frozen=frozen)
    after = card_view(card, frozen=frozen)
    operation_housing = after["document"]["operations"][0].pop("housing")
    node_housing = after["document"]["topology"]["nodes"][0].pop("housing")
    assert operation_housing == node_housing == housings.housing_of("0510210400")
    assert after == before
    assert card.document == document == saved


@pytest.mark.parametrize("frozen", [False, True])
def test_newly_resolved_identity_can_enrich_an_unconverted_component_name(frozen, monkeypatch):
    monkeypatch.setenv("ETK_AUTO_REVISION", "1")
    document = {"operations": [{
        "key": "mount", "type": "insTerm", "components": [{"art": "00510210400", "name": ""}],
    }]}
    saved = deepcopy(document)
    card = _issued(document)
    with monkeypatch.context() as legacy:
        legacy.setattr(housings, "is_molex_article", lambda value, **kwargs: False)
        before = card_view(card, identify=_identify, frozen=frozen)
    after = card_view(card, identify=_identify, frozen=frozen)
    component = after["document"]["operations"][0]["components"][0]
    assert before["document"]["operations"][0]["components"][0]["name"] == ""
    assert component["name"] == "PicoBlade 4pin (однорядный)"
    component["name"] = ""
    assert after == before
    assert card.document == document == saved


def test_report_cli_remains_read_only_on_sqlite(report_db):
    engine, path, _, _ = report_db
    with Session(engine) as session:
        before = snapshot(session)
    environment = {**os.environ, "ERP_DATABASE_URL": f"sqlite:///{path.as_posix()}"}
    result = subprocess.run(
        [sys.executable, "-m", "app.workflows.molex_match_report", "--json"],
        env=environment, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    report = json.loads(result.stdout)
    assert report["read_only"] is True
    # The fixture's padded PicoBlade now resolves directly, so no housing delta remains.
    assert report["changes"]["housing_of"] == []
    with Session(engine) as session:
        assert snapshot(session) == before
