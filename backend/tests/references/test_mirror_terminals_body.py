"""The mirror's БД.ТЕР as a body for `POST /api/references/import/ter` (backend/data/terminals/mirror-ter.json, built by
`python -m app.modules.references.load_mirror --export-ter`): the committed file is exactly what the converter makes
from the bundled mirror, one row per REEL article with its BAG article in the same row, articles as the maker writes
them (Molex hyphens kept), no technologist field. On a base loaded from the mirror before the contact card existed —
production's state — it creates nothing, refuses nothing and only fills the card; a second apply changes nothing, and
it shares no terminal with the Hirose DF1B body, so both load side by side."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from app.modules.references.domain import TERMINAL_TABS, TechOperationTab
from app.modules.references.importers import _normalized_article, technologist_field
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import TER_EXPORT_KEYS, default_source, load_mirror, ter_import_rows
from app.modules.references.technology import TER_CARD_KEYS
from tests.identity.test_api_tokens import tokens_api  # noqa: F401  (fixture)
from tests.identity.test_refs_tokens import refs_api  # noqa: F401  (fixture)
from tests.references.test_import_api import fresh_limiter  # noqa: F401  (fixture)

TERMINALS = Path(__file__).resolve().parents[2] / "data" / "terminals"
BODY = TERMINALS / "mirror-ter.json"
DF1B = TERMINALS / "hirose-df1b.json"
NOW = datetime(2026, 10, 13, 12, 0)


def _rows(path: Path = BODY) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["rows"]


def _articles(rows: list[dict]) -> list[str]:
    return [row["fields"][key] for row in rows for key in ("terArticle", "terArticleBag") if row["fields"].get(key)]


def test_the_committed_body_is_what_the_converter_makes_from_the_bundled_mirror():
    # regenerate: python -m app.modules.references.load_mirror --export-ter data/terminals/mirror-ter.json
    assert _rows() == ter_import_rows(default_source()).rows


def test_one_row_per_terminal_with_its_bag_article_and_articles_as_the_maker_writes_them():
    rows = _rows()
    assert len(rows) == 455 and sum(1 for row in rows if row["fields"].get("terArticleBag")) == 89
    articles = _articles(rows)
    # no REEL or BAG article repeats, even ignoring hyphens, spaces and case — the import's match key
    assert len({_normalized_article(article) for article in articles}) == len(articles)
    by_reel = {row["fields"]["terArticle"]: row["fields"] for row in rows}
    assert by_reel["39000038"]["terArticleBag"] == "39000039"  # Mini-Fit Jr.: REEL and BAG in one row
    for molex in ("560023-0721", "08-52-0071", "19003-0129"):  # hyphens kept as in the datasheet and the warehouse
        assert by_reel[molex]["terManufacturer"] == "Molex"
    for row in rows:
        fields = row["fields"]
        assert set(fields) <= set(TER_EXPORT_KEYS) and all(fields.values())  # empty cells left out (null-spec)
        assert not any(technologist_field(name) for name in fields), fields["terArticle"]


def test_the_body_shares_no_terminal_with_the_hirose_df1b_body():
    mirror = {_normalized_article(article) for article in _articles(_rows())}
    assert not mirror & {_normalized_article(article) for article in _articles(_rows(DF1B))}


def test_on_the_production_base_the_body_only_fills_the_card_and_a_second_apply_changes_nothing(refs_api):
    from fastapi.testclient import TestClient

    from app.main import app

    sessions, tokens = refs_api
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    load_mirror(default_source(), uow, dry_run=False, now=NOW)
    with uow:  # loaded from the mirror before the card keys existed, as production holds it
        for record in uow.tech_operations.list(tab=TERMINAL_TABS, query="", limit=100_000):
            uow.tech_operations.save(replace(record, fields={
                key: value for key, value in record.fields.items() if key not in TER_CARD_KEYS}))
        uow.commit()
    client = TestClient(app)

    def load(rows: list[dict], dry_run: int) -> dict:
        response = client.post("/api/references/import/ter", params={"dry_run": dry_run, "source": "api"},
                               json={"rows": rows}, headers=tokens["refs:write"])
        assert response.status_code == 200, response.text
        return response.json()

    rows = _rows()
    check = load(rows, 1)
    assert check["dry_run"] and check["created"] == [] and check["refused"] == []
    assert len(check["updated"]) == 450 and check["unchanged"] == 5
    assert {name for item in check["updated"] for name in item["changes"]} <= set(TER_CARD_KEYS)
    assert all(old in (None, "") for item in check["updated"] for old, _new in item["changes"].values())  # fills only
    applied = load(rows, 0)
    assert len(applied["updated"]) == 450 and applied["refused"] == []
    again = load(rows, 1)
    assert (again["created"], again["updated"], again["refused"], again["unchanged"]) == ([], [], [], 455)

    df1b = load(_rows(DF1B), 0)
    assert len(df1b["created"]) == 12 and df1b["refused"] == []
    assert load(rows, 1)["unchanged"] == 455
