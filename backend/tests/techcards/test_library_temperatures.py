"""Owner, 17.10.2026: «на пайке 240, на усадке везде 150» — the bundled cards carry no other temperature."""

import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
RANGE = re.compile(r"t°\s*\d{3}\s*[–-]\s*\d{3}")


def _cards():
    for name in ("techcard-library.json", "techcard-library-erp.json"):
        for template in json.loads((DATA / name).read_text(encoding="utf-8"))["templates"]:
            yield template["title"].split("|")[0].strip(), [cell for row in template["rows"] for cell in row]


def test_soldering_cards_say_240_and_no_range():
    soldering = [(code, cells) for code, cells in _cards() if code.startswith("SDR_")]
    assert len(soldering) > 20
    for code, cells in soldering:
        assert not any(RANGE.search(cell) or "330–360" in cell for cell in cells), code
    assert any("t° 240 °C" in cell for code, cells in soldering if code == "SDR_W_W" for cell in cells)
    assert any(cell == "240 °C" for code, cells in soldering if code == "SDR_tin" for cell in cells)


def test_shrink_cards_say_150():
    shrink = {code: cells for code, cells in _cards() if code.startswith("HEAT_")}
    assert sorted(shrink) == ["HEAT_TTK_long", "HEAT_TTK_short", "HEAT_TYT_long", "HEAT_TYT_short"]
    for code, cells in shrink.items():
        assert "150 °C" in cells and "160 °C" not in cells, code
