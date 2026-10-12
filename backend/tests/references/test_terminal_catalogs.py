"""Terminal bodies for `POST /api/references/import/ter` read from the makers' catalogues (backend/data/terminals):
Hirose DF1B crimp contacts (owner, 12.10.2026: the generator showed «нет в БД.ТЕР» for DF1B-2428SCFA and
DF1-PD2428SCFB). The body loads through the normal import path, writes no technologist field, and a second apply
changes nothing."""

import json
from pathlib import Path

from app.modules.references.importers import technologist_field
from tests.identity.test_api_tokens import tokens_api  # noqa: F401  (fixture)
from tests.identity.test_refs_tokens import refs_api  # noqa: F401  (fixture)
from tests.references.test_import_api import fresh_limiter, loader  # noqa: F401  (fixtures)

DF1B = Path(__file__).resolve().parents[2] / "data" / "terminals" / "hirose-df1b.json"


def _rows() -> list[dict]:
    return json.loads(DF1B.read_text(encoding="utf-8"))["rows"]


def test_df1b_body_lists_every_crimp_contact_of_the_catalogue_once():
    rows = _rows()
    reels = [row["fields"]["terArticle"] for row in rows]
    bags = [row["fields"]["terArticleBag"] for row in rows]
    assert len(reels) == len(set(reels)) == 12 and len(set(bags)) == 12
    assert {"DF1B-2428SCFA", "DF1-PD2428SCFB", "DF1B-2022SCF", "DF1B-2022SCFA"} <= set(reels)
    assert {"DF1B-2428SCA", "DF1-PD2428SCB"} <= set(bags)
    for row in rows:
        fields = row["fields"]
        assert not any(technologist_field(key) for key in fields), fields["terArticle"]
        assert fields["terDatasheet"] == f"https://www.hirose.com/product/en/products/DF1B/{fields['terArticle']}/"
        assert fields["terType"] in {"Female", "Male"} and fields["terStep"] == "2,5"
        # sockets (SC…) strip 2.0–2.5 mm, plugs 3.0–3.7 mm — the catalogue's «Strip Length»
        assert fields["terStrip"] == ("2 - 2,5" if fields["terType"] == "Female" else "3 - 3,7")
        assert "terCrimpHeight" not in fields and "terLPlus" not in fields  # not in the catalogue: not guessed
    by_reel = {row["fields"]["terArticle"]: row["fields"] for row in rows}
    assert (by_reel["DF1B-2428SCFA"]["awgFrom"], by_reel["DF1B-2428SCFA"]["awgTo"]) == ("28", "24")
    assert (by_reel["DF1B-2022SCF"]["terInsOdFrom"], by_reel["DF1B-2022SCF"]["terInsOdTo"]) == ("1,2", "1,9")
    assert by_reel["DF1-PD2428SCFB"]["terType"] == "Male" and by_reel["DF1-PD2428SCFB"]["terMaterial"].endswith("/ Gold")


def test_df1b_body_loads_through_the_import_and_a_second_apply_changes_nothing(loader):
    _sessions, _tokens, _client, load = loader
    rows = _rows()

    check = load("ter", rows, dry_run=1).json()
    assert check["dry_run"] and len(check["created"]) == 12 and check["refused"] == []
    applied = load("ter", rows).json()
    assert len(applied["created"]) == 12
    again = load("ter", rows, dry_run=1).json()
    assert again["created"] == [] and again["updated"] == [] and again["unchanged"] == 12
