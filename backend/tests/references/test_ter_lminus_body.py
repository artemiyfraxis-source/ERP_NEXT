"""The first approximate L− of БД.ТЕР terminals as a body for `POST /api/references/import/ter`
(backend/data/terminals/ter-lminus.json, built by data/terminals/build_ter_lminus.py from ter-lminus-sources.json;
owner 13.10.2026: «попробуй на первое время заполнить сам L-»). L− = housing length − the terminal's L+, so the
generator's «от краёв разъёмов» cut equals its «между разъёмами» one. The committed body is what the builder makes from
the sources and the bundled mirror; each row writes only an empty L− and its note («Источник L-», a column no row has
yet) and carries `old`, so on the production base it creates nothing, fills only empty cells, and a row whose L+ or L−
changed meanwhile is refused instead of overwritten."""

import importlib.util
import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.references.domain import TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import default_source, load_mirror
from app.shared.bulk_import import STALE_REASON
from tests.identity.test_api_tokens import tokens_api  # noqa: F401  (fixture)
from tests.identity.test_refs_tokens import refs_api  # noqa: F401  (fixture)
from tests.references.test_import_api import fresh_limiter  # noqa: F401  (fixture)

TERMINALS = Path(__file__).resolve().parents[2] / "data" / "terminals"
NOW = datetime(2026, 10, 13, 18, 0)


def _builder():
    spec = importlib.util.spec_from_file_location("build_ter_lminus", TERMINALS / "build_ter_lminus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _builder()


def _rows() -> list[dict]:
    return json.loads(BUILDER.BODY.read_text(encoding="utf-8"))["rows"]


def _sources() -> dict:
    return json.loads(BUILDER.SOURCES.read_text(encoding="utf-8"))


def _estimates() -> dict[str, dict]:
    rows = json.loads(BUILDER.ESTIMATES_BODY.read_text(encoding="utf-8"))["rows"]
    return {row["fields"]["terArticle"]: row for row in rows}


def _by_article() -> dict[str, dict]:
    return {row["fields"]["terArticle"]: row for row in _rows()}


def test_the_committed_body_is_what_the_builder_makes_from_the_sources_and_the_bundled_mirror():
    # regenerate: cd backend && python data/terminals/build_ter_lminus.py
    assert _rows() == BUILDER.build(_sources(), BUILDER.mirror_terminals(default_source()))


def test_l_minus_is_the_housing_length_less_the_terminals_l_plus():
    rows = _by_article()
    terminals = BUILDER.mirror_terminals(default_source())
    for housing in _sources()["housings"]:
        for article in housing["terminals"]:
            l_plus = Decimal(terminals[article]["terLPlus"].replace(",", "."))
            l_minus = Decimal(rows[article]["fields"]["terLMinus"].replace(",", "."))
            assert l_minus == Decimal(housing["length_mm"]) - l_plus > 0, article
    assert rows["SPH-002T-P0.5S"]["fields"]["terLMinus"] == "0,85"  # JST PHR 6,85 − L+ 6
    assert rows["39000038"]["fields"]["terLMinus"] == "12,6"  # Molex Mini-Fit Jr. 5557 19,6 − L+ 7
    assert rows["T-DS1071-SC600"]["fields"]["terLMinus"] == "9"  # Connfly DS1071 14 − L+ 5


def test_each_row_writes_only_an_empty_l_minus_and_its_approximate_source():
    for row in _rows():
        fields, old = row["fields"], row["old"]
        assert set(fields) == {"terArticle", "terLMinus", BUILDER.NOTE_FIELD}
        assert fields[BUILDER.NOTE_FIELD].startswith("оценка по чертежу корпуса: L− = ")
        assert "http" in fields[BUILDER.NOTE_FIELD]  # the maker's document the housing length was read from
        assert old["terArticle"] == fields["terArticle"] and old["terLPlus"]
        assert set(old) == {"terArticle", "terLPlus", "terLMinus", BUILDER.NOTE_FIELD}
        assert old["terLMinus"] == old[BUILDER.NOTE_FIELD] == ""


def test_the_sources_name_each_terminal_once_and_keep_the_ones_left_empty_out_of_the_body():
    filled = [article for housing in _sources()["housings"] for article in housing["terminals"]]
    left = [article for item in _sources()["not_filled"] for article in item["terminals"]]
    assert len(set(filled)) == len(filled) == len(_rows()) and not set(filled) & set(left)
    for article in ("8500063", "SPND-001T-C0.5"):  # no housing length / two housings: not guessed
        assert article in left
    for article in ("SZH-002T-P0.5", "500798001", "DF13-2630SCF", "2147205000"):  # housing shorter than the stored L+
        assert article in left and article not in _estimates()  # no drawing shows where the wire ends: not guessed


def test_a_terminal_whose_housing_is_not_longer_than_its_l_plus_is_refused_by_the_builder():
    terminals = {"SZH-002T-P0.5": {"terArticle": "SZH-002T-P0.5", "terLPlus": "5", "terLMinus": ""}}
    sources = {"housings": [{"maker": "JST", "housing": "ZHR", "length_mm": "4.8", "doc": "JST eZH", "url": "u",
                             "terminals": ["SZH-002T-P0.5"]}]}
    with pytest.raises(ValueError, match="не длиннее"):
        BUILDER.build(sources, terminals)
    terminals["SZH-002T-P0.5"]["terLMinus"] = "1"
    with pytest.raises(ValueError, match="уже заполнен"):
        BUILDER.build(sources, terminals)


def test_on_the_production_base_the_body_fills_only_empty_l_minus_and_never_overwrites(refs_api):
    from fastapi.testclient import TestClient

    from app.main import app

    sessions, tokens = refs_api
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    load_mirror(default_source(), uow, dry_run=False, now=NOW)
    with uow:  # the generator's «Записать в БД» filled one L− after the body was built
        record = next(item for item in uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000)
                      if item.fields.get("terArticle") == "SXH-001T-P0.6")
        uow.tech_operations.save(replace(record, fields={**record.fields, "terLMinus": "3", "L-": "3"}))
        uow.commit()
    client = TestClient(app)

    def load(dry_run: int) -> dict:
        response = client.post("/api/references/import/ter", params={"dry_run": dry_run, "source": "api"},
                               json={"rows": _rows()}, headers=tokens["refs:write"])
        assert response.status_code == 200, response.text
        return response.json()

    total = len(_rows())
    check = load(1)
    assert check["dry_run"] and check["created"] == [] and check["unchanged"] == 0
    assert check["refused"] == [{"key": "SXH-001T-P0.6", "reason": STALE_REASON}]
    assert len(check["updated"]) == total - 1
    for item in check["updated"]:
        assert set(item["changes"]) == {"terLMinus", BUILDER.NOTE_FIELD}
        assert all(old in (None, "") for old, _new in item["changes"].values())  # fills only

    applied = load(0)
    assert len(applied["updated"]) == total - 1 and applied["created"] == []
    with uow:
        stored = {item.fields.get("terArticle"): item.fields
                  for item in uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000)}
    assert stored["SPH-002T-P0.5S"]["terLMinus"] == stored["SPH-002T-P0.5S"]["L-"] == "0,85"
    assert stored["SPH-002T-P0.5S"][BUILDER.NOTE_FIELD].startswith("оценка по чертежу корпуса")
    assert stored["SXH-001T-P0.6"]["terLMinus"] == "3"  # the value entered on screen stays

    again = load(1)  # every L− is filled now: the same body refuses all rows and writes nothing
    assert again["created"] == [] and again["updated"] == [] and len(again["refused"]) == total
    assert {item["reason"] for item in again["refused"]} == {STALE_REASON}


def test_the_committed_estimates_body_is_what_the_builder_makes_from_the_sources():
    # regenerate: cd backend && python data/terminals/build_ter_lminus.py
    rows = json.loads(BUILDER.ESTIMATES_BODY.read_text(encoding="utf-8"))["rows"]
    assert rows == BUILDER.build_estimates(_sources(), BUILDER.catalogue_terminals())


def test_estimates_take_l_plus_only_from_a_drawing_of_the_wire_in_the_housing_or_keep_the_bases_own():
    # L+ is the depth of the conductor end from the housing's wire-side face (owner 16.10.2026), not the contact length
    rows = _estimates()
    assert set(rows) == {"SSHL-002T-P0.2", "8500113"}
    for article, row in rows.items():
        fields, old = row["fields"], row["old"]
        note = fields[BUILDER.NOTE_FIELD]
        assert note.startswith(("примерно: ", "по чертежу: ")) and "http" in note, article
        l_minus = Decimal(fields["terLMinus"].replace(",", "."))
        l_plus = Decimal(fields.get("terLPlus", old["terLPlus"]).replace(",", "."))
        assert l_minus > 0 and l_plus > 0 and l_plus + l_minus > l_plus, article
        assert old["terArticle"] == article and old[BUILDER.NOTE_FIELD] == ""
    gh = rows["SSHL-002T-P0.2"]
    assert gh["old"]["terLMinus"] == "10" and gh["fields"]["terLMinus"] == "0,7" and "terLPlus" not in gh["fields"]
    kk = rows["8500113"]  # Molex SD-2695: «WIRE INTO HOUSING 6.73 REF» from the rear face of the 12,7 mm housing
    assert kk["fields"]["terLPlus"] == "6,73" and kk["fields"]["terLMinus"] == "5,97" and kk["old"]["terLPlus"] == ""
    left = {article for item in _sources()["not_filled"] for article in item["terminals"]}
    assert {"DF1B-2022SCF", "DF1B-2022PCF", "1053002200", "2014472300", "L-KLS1-2.50-T"} <= left


def test_an_estimate_from_the_contact_length_or_a_share_of_the_housing_is_refused():
    terminals = {"X": {"terArticle": "X", "terLPlus": "", "terLMinus": ""}}
    group = {"maker": "M", "housing": "H", "length_mm": "10", "l_plus": "8.3", "basis": "proportion", "doc": "d", "url": "u",
             "terminals": ["X"]}
    for basis in ("proportion", "contact"):
        group.update(basis=basis, contact_mm="8.3")
        with pytest.raises(ValueError, match="только с чертежа"):
            BUILDER.build_estimates({"estimates": [group]}, terminals)
    group.update(basis="drawing", wire_depth_mm="6")
    with pytest.raises(ValueError, match="глубине провода"):
        BUILDER.build_estimates({"estimates": [group]}, terminals)
    group.update(l_plus="6")
    assert BUILDER.build_estimates({"estimates": [group]}, terminals)[0]["fields"]["terLMinus"] == "4"
    group.update(l_plus="12", wire_depth_mm="12")
    with pytest.raises(ValueError, match="вне корпуса"):
        BUILDER.build_estimates({"estimates": [group]}, terminals)


def test_on_the_production_base_the_estimates_fill_l_plus_and_refuse_a_cell_changed_meanwhile(refs_api):
    from fastapi.testclient import TestClient

    from app.main import app

    sessions, tokens = refs_api
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    load_mirror(default_source(), uow, dry_run=False, now=NOW)
    client = TestClient(app)

    def load(rows: list[dict], dry_run: int) -> dict:
        response = client.post("/api/references/import/ter", params={"dry_run": dry_run, "source": "api"},
                               json={"rows": rows}, headers=tokens["refs:write"])
        assert response.status_code == 200, response.text
        return response.json()

    with uow:  # somebody typed another L− for one terminal after the body was built
        record = next(item for item in uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000)
                      if item.fields.get("terArticle") == "SSHL-002T-P0.2")
        uow.tech_operations.save(replace(record, fields={**record.fields, "terLMinus": "1", "L-": "1"}))
        uow.commit()
    rows = list(_estimates().values())
    check = load(rows, 1)
    assert check["created"] == [] and check["refused"] == [{"key": "SSHL-002T-P0.2", "reason": STALE_REASON}]
    assert len(check["updated"]) == len(rows) - 1
    applied = load(rows, 0)
    assert len(applied["updated"]) == len(rows) - 1 and applied["created"] == []
    with uow:
        stored = {item.fields.get("terArticle"): item.fields
                  for item in uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000)}
    assert stored["8500113"]["terLPlus"] == "6,73" and stored["8500113"]["terLMinus"] == "5,97"
    assert stored["8500113"][BUILDER.NOTE_FIELD].startswith("по чертежу: ")
    assert stored["SSHL-002T-P0.2"]["terLMinus"] == "1"  # the value entered meanwhile stays
    assert stored["SZH-002T-P0.5"]["terLPlus"] == "5"  # no drawing of the wire end: left as it was
