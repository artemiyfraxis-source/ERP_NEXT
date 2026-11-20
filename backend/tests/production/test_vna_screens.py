"""Stored VNA bench screens (S5b): a definition holds line SOURCES; the six presets draw exactly what the code drew."""

import pytest
from pydantic import ValidationError

from app.modules.production.domain import ProductionValidationError
from app.modules.production.vna_bench_protocol import ApplyScreenArgs, screen_hash
from app.modules.production.vna_screens import SCREENS, ItemLimits, Line, ScreenDef, definition, presets, screen

ITEM = ItemLimits(swr=(Line("upper", 5e8, 18e9, 1.0525, 1.14), Line("upper", 1e7, 18e9, 1.3, 1.3)),
                  loss=(Line("lower", 5e8, 18e9, -0.0212, -0.1273),), title="СИ.035 · КСВН ≤ 1,05 + 0,005·F")
# screen_hash of every preset as the code drew it BEFORE the screens moved to the database (computed on 2c6061da):
# (with the item lines, an ETK band 1–6000 МГц and norm 1,32) and (bare, the same band).
GOLDEN = {
    "adapter": ("6ba4ed289fbe158761f57859be7ec0c96222919c6fbec89a064289522b420ea5",
                "78df508edd35b2ca33fd94b43190417321dbc08de17d2bf9ff1c8a1efc46976c"),
    "assembly": ("315a718fdd748d288b97514101e42b8298937c55dc83da4da70aaec56a7eb915",
                 "7b1e8a44db0b31f3c6756592abd90be78f744e4fda6947c678db779a8daca436"),
    "one_port": ("6a41756303ade223b385e4a09b95e93bc4e8399384768e92ff8fdaea34244113",
                 "5659b2588ef04e16868357d622bd1bf5a0fafc86b890399e22f38ddf512c073a"),
    "load_check": ("f12f70837197878840d2caa3ce1bb9083a78d1cc33107d75f81456247f6e2a3e",
                   "f12f70837197878840d2caa3ce1bb9083a78d1cc33107d75f81456247f6e2a3e"),
    "flex": ("1e1cafe66354ed6c6b92f6e82c924dd31421c9958f826de19dab694e23e22359",
             "1e1cafe66354ed6c6b92f6e82c924dd31421c9958f826de19dab694e23e22359"),
    "engineer": ("5aa7d0ccc5cf1dfac55aeb3a186ad1184e1d5ea8ba991082ab3adeb67e34e813",
                 "c889359ab72a6c75f4697759f1326206c64d97e4b1728d3ac16e10858bc4004c"),
}


def drawn(spec, key):
    """The two golden contexts of a preset key, drawn from `spec` (a stored definition or the key itself)."""
    etk = SCREENS[key].full_band is False
    band = (1e6, 6e9) if etk else None
    return (screen_hash(screen(spec, item=ITEM, band_hz=band, assembly_vswr=1.32 if etk else None)),
            screen_hash(screen(spec, band_hz=band)))


@pytest.mark.parametrize("key", list(SCREENS))
def test_stored_presets_draw_exactly_the_screens_of_the_code(key):
    assert drawn(key, key) == GOLDEN[key]
    stored = ScreenDef.model_validate(definition(key).model_dump(mode="json"))   # through JSON, as the table holds it
    assert drawn(stored, key) == GOLDEN[key]


def test_presets_are_one_per_purpose_with_line_sources():
    by_purpose = presets()
    assert list(by_purpose) == ["adapter", "assembly", "one_port", "load", "flex", "engineer"]
    assert [t.limit for t in by_purpose["adapter"].traces] == ["norm"] * 4
    assert [t.scale for t in by_purpose["adapter"].traces][0] == "norm"
    assert [t.limit for t in by_purpose["assembly"].traces] == ["etk", "none", "etk"]
    load = by_purpose["load"].traces[0]
    assert load.limit == "fixed" and (load.fixed.kind, load.fixed.value) == ("upper", 1.06)


def edited(**trace):
    data = definition("adapter").model_dump(mode="json")
    data["traces"][0] = {**data["traces"][0], **trace}
    return data


@pytest.mark.parametrize("trace,message", [
    (dict(format="MLOG", scale="norm"), "по норме"),
    (dict(format="PHAS", scale={"pdiv": 36.0, "rlev": 0.0, "rpos": 5}, limit="norm"), "нормы СИ"),
    (dict(format="MLOG", scale={"pdiv": 0.1, "rlev": 0.0, "rpos": 10}, limit="etk"), "ЭТК"),
    (dict(limit="fixed"), "Своё число"),
    (dict(limit="none", fixed={"kind": "upper", "value": 1.2}), "Своё число"),
    (dict(format="SMIT", scale={"pdiv": 1.0, "rlev": None, "rpos": None}, limit="fixed",
          fixed={"kind": "upper", "value": 1.2}), "Вольперта-Смита"),
    (dict(format="SMIT", scale={"pdiv": 1.0, "rlev": 1.0, "rpos": 0}, limit="none"), "Вольперта-Смита"),
    (dict(scale={"pdiv": 0.0, "rlev": 1.0, "rpos": 0}), "greater than 0"),
    (dict(scale={"pdiv": 0.05, "rlev": 1.0, "rpos": 11}), "less than or equal to 10"),
    (dict(param="S33"), "S11"),
])
def test_a_definition_out_of_the_v2_contract_is_refused(trace, message):
    with pytest.raises(ValidationError, match=message):
        ScreenDef.model_validate(edited(**trace))


def test_screen_level_bounds():
    base = definition("adapter").model_dump(mode="json")
    for change in (dict(split=17), dict(split=0), dict(traces=[]), dict(traces=base["traces"] * 5),
                   dict(markers_hz=[3e9, 1e9]), dict(markers_hz=[1e9] * 2), dict(name="  "), dict(purpose="other"),
                   dict(markers_hz=[float(i + 1) * 1e9 for i in range(16)])):
        with pytest.raises(ValidationError):
            ScreenDef.model_validate({**base, **change})


def test_own_number_line_title_and_the_norm_scale():
    data = definition("engineer").model_dump(mode="json")
    data["traces"][9] = {**data["traces"][9], "limit": "fixed", "fixed": {"kind": "lower", "value": -20}}
    data["traces"][0] = {**data["traces"][0], "scale": {"pdiv": 0.02, "rlev": 1.0, "rpos": 0}}
    spec = ScreenDef.model_validate(data)
    built = screen(spec, item=ITEM)
    assert [(s.kind, s.start_hz, s.stop_hz, s.start_value) for s in built.traces[9].limits] == [("lower", 1e7, 18e9, -20)]
    assert built.traces[0].scale.pdiv == 0.02 and built.title == ITEM.title
    # No norm source on any graph — no item title; own lines only.
    for trace in data["traces"]:
        if trace["limit"] == "norm":
            trace["limit"] = "none"
    assert screen(ScreenDef.model_validate(data), item=ITEM).title == ""
    ApplyScreenArgs.model_validate({"screen": built.model_dump(mode="json"), "screen_hash": screen_hash(built)})


def test_etk_purpose_needs_a_band_and_markers_follow_the_definition():
    data = definition("assembly").model_dump(mode="json")
    data["markers_hz"] = [2e9, 4e9]
    spec = ScreenDef.model_validate(data)
    with pytest.raises(ProductionValidationError, match="полоса по ЭТК"):
        screen(spec)
    assert screen(spec, band_hz=(1e6, 3e9)).markers_hz == [2e9]
