from pathlib import Path

from app.modules.references.load_mirror import BUNDLED_MIRROR
from app.modules.references.technology import (
    normalize_crimp_text as norm,
    normalize_terminal_fields,
    parse_crimp_profiles,
    parse_mirror_rows,
)


def test_normalize_writes_one_canonical_format():
    assert norm("28 AWG: 0,52 - 0,58\n26 AWG: 0,56 - 0,62") == "28 AWG: 0,52–0,58; 26 AWG: 0,56–0,62"
    assert norm("30 AWG: 5.9 28 AWG: 10  26 AWG: 20") == "30 AWG: 5,9; 28 AWG: 10; 26 AWG: 20"
    assert norm("28-24 AWG: 0,55 - 0,70") == "28–24 AWG: 0,55–0,70"
    assert norm("26 AWG: 0,60-0,70 \\n24 AWG: 0,65-0,75 ") == "26 AWG: 0,60–0,70; 24 AWG: 0,65–0,75"
    assert norm("9,8–19,6") == "9,8–19,6" and norm("") == "" and norm(None) == ""
    assert norm("single-wire-seal 964971-1, insulation-Ø 1,2 - 1,6 мм") == "single-wire-seal 964971-1, insulation-Ø 1,2–1,6 мм"


def test_terminal_fields_are_normalized_only_in_the_crimp_columns():
    fields = {"Высота обжима проводника , мм": "22 AWG: 0,66 - 0,72", "Усилие обрыва контакта, N": "30 AWG: 5,9 28 AWG: 10",
              "Источник высоты обжима": "Table 1 - 2", "terCrimpHeight": "0,85 - 0,90"}
    out = normalize_terminal_fields(fields)
    assert out["Высота обжима проводника , мм"] == "22 AWG: 0,66–0,72"
    assert out["Усилие обрыва контакта, N"] == "30 AWG: 5,9; 28 AWG: 10"
    assert out["terCrimpHeight"] == "0,85–0,90" and out["Источник высоты обжима"] == "Table 1 - 2"


def test_every_mirror_value_keeps_its_profiles():
    rows = parse_mirror_rows((Path(BUNDLED_MIRROR) / "БД.ТЕР.tsv").read_text(encoding="utf-8"))
    columns = [i for i, name in enumerate(rows[0]) if name.lower().startswith(("высота обжима", "усилие обрыва"))]
    assert len(columns) == 3
    seen = 0
    for row in rows[1:]:
        for i in columns:
            raw = row[i] if i < len(row) else ""
            text = norm(raw)
            assert norm(text) == text  # idempotent
            before, after = parse_crimp_profiles(raw), parse_crimp_profiles(text)
            # the same AWG ranges in the same order; the values differ only in how their ranges are written
            assert [(p.awg_high, p.awg_low, norm(p.height), p.pull_force) for p in before] == \
                [(p.awg_high, p.awg_low, p.height, p.pull_force) for p in after], raw
            seen += len(before)
    assert seen > 100
