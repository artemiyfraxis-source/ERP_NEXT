"""Structured cable reference (owner 10.10.2026): «Сечения AWG ↔ мм²», «Монтажные провода», coax cards."""

import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.references.application import (
    EditRecordCommand, FieldEdit, SaveGaugeCommand, SaveHookupCommand, StaleRecordError, cable_catalog, delete_gauge,
    edit_record, import_coax_cards, save_gauge, save_hookup,
)
from app.modules.references.cable_structure import (
    WireGauge, attenuation_point, card_for, cards_from_seed, match_gauge, parse_hookup, wire_series,
)
from app.modules.references.domain import ReferenceKind, ReferenceValidationError, gost_awg_map
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_cables import DEFAULT_FILE, read_seed
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from tests.references.test_cables import _mirror
from tests.references.test_load_mirror import NOW, sessions, uow  # noqa: F401  (fixtures)

SIZES = [("30", "0,05", "0,26"), ("28", "0,08", "0,32"), ("26", "0,12", "0,41"), ("", "0,07", ""),
         ("22", "0,35", "0,64"), ("20", "0,5", "0,81"), ("17", "1", "1,15")]


def _gauges() -> list[WireGauge]:
    return [WireGauge.create(awg=awg, section_mm2=section, conductor_d_mm=diameter, position=index)
            for index, (awg, section, diameter) in enumerate(SIZES)]


def _seed_gauges(uow) -> dict[str, WireGauge]:
    gauges = {}
    for awg, section, diameter in SIZES:
        gauge = save_gauge(SaveGaugeCommand(None, awg, section, diameter, ACTOR, NOW), uow)
        gauges[awg or section] = gauge
    return gauges


# --- pure rules ---

@pytest.mark.parametrize(("mark", "series"), [
    ("UL1061 28AWG", "UL1061"), ("НВ-4 0,20", "НВ-4"), ("Силиконовый провод 28AWG", "Силиконовый провод"),
    ("ПуГВнг(А)-LS 1х1,0", "ПуГВнг(А)-LS"), ("МС 15-11 0,12", "МС 15-11"), ("AGR2 Core 0,3", "AGR2 Core"),
    ("Кабель высоковольтный 150kV 0,75мм2 8мм", "Кабель высоковольтный 150kV"), ("ПВАМ", "ПВАМ"), ("0,5", "0,5"),
])
def test_series_is_the_mark_without_its_size(mark, series):
    assert wire_series(mark) == series


def test_gauge_match_prefers_awg_then_section_then_the_mark():
    gauges = _gauges()
    label = lambda gauge_problem: gauge_problem[0].label if gauge_problem[0] else gauge_problem[1]  # noqa: E731
    assert label(match_gauge("UL1015 26AWG", {"AWG": "26", "Сечение, мм2": "0,13"}, gauges)) == "26 AWG · 0,12 мм²"
    assert label(match_gauge("Силиконовый провод 28AWG", {}, gauges)) == "28 AWG · 0,08 мм²"
    assert label(match_gauge("ПВАМ 0,50", {"Сечение, мм2": "0,5"}, gauges)) == "20 AWG · 0,5 мм²"
    assert label(match_gauge("ПуГВ 1,0", {}, gauges)) == "17 AWG · 1 мм²"
    assert label(match_gauge("МГТФ 0,07", {"Сечение, мм2": "0,07"}, gauges)) == "0,07 мм²"
    assert label(match_gauge("AGR2 Core 0,3", {"Сечение, мм2": "0,3"}, gauges)) == "сечения 0,3 мм² нет в таблице сечений"
    assert label(match_gauge("UL758 10AWG", {"AWG": "10"}, gauges)) == "10 AWG нет в таблице сечений"
    assert label(match_gauge("Провод", {}, gauges)) == "не указаны ни AWG, ни сечение"


def test_parse_reads_the_numbers_a_wire_row_carries():
    parsed = parse_hookup("ПВАМ 0,50", {
        "Сечение, мм2": "0,5", "Наружный диаметр, мм": "1,6", "Изоляция": "ПВХ-пластикат  маслобензостойкий",
        "Напряжение, В": "48 (ном.) / 1000 (испыт.)", "Темп. от, °C": "-40", "Темп. до, °C": "105",
    }, _gauges())
    assert (parsed.series, parsed.gauge.awg, parsed.outer_d_mm, parsed.insulation, parsed.voltage_v,
            parsed.temp_min_c, parsed.temp_max_c) == ("ПВАМ", "20", Decimal("1.6"), "ПВХ-пластикат маслобензостойкий", 48, -40, 105)
    odd = parse_hookup("ШВВП 2x0,5", {"Сечение, мм2": "0,5", "Наружный диаметр, мм": "6×4 (овал)", "Темп. от, °C": "90",
                                      "Темп. до, °C": "70"}, _gauges())
    assert odd.outer_d_mm is None and (odd.temp_min_c, odd.temp_max_c) == (None, None)  # never a guess


def test_gauge_rows_are_validated():
    with pytest.raises(ReferenceValidationError):
        WireGauge.create(awg="28", section_mm2="0")
    with pytest.raises(ReferenceValidationError):
        WireGauge.create(awg="двадцать", section_mm2="0,5")
    with pytest.raises(ReferenceValidationError):
        WireGauge.create(awg="41", section_mm2="0,5")
    assert WireGauge.create(awg="1/0", section_mm2="50").awg == "1/0"
    assert WireGauge.create(awg="28awg", section_mm2="0.08").awg == "28"


def test_attenuation_keeps_the_published_value_and_derives_db_per_metre():
    per_ft = attenuation_point("LMR-240", Decimal(2400), "nominal", "12.9", "dB/100ft", temp_c=20, source="https://x")
    per_m = attenuation_point("RG-316", Decimal(1000), "max", "0,9", "дБ/м")
    assert (per_ft.value, per_ft.unit, per_ft.ref_length_m, per_ft.loss_db_m, per_ft.derived) == (
        Decimal("12.9"), "dB/100ft", Decimal("30.48"), Decimal("0.4232"), True)
    assert (per_m.loss_db_m, per_m.derived, per_m.kind) == (Decimal("0.9000"), False, "max")
    with pytest.raises(ReferenceValidationError):
        attenuation_point("X", Decimal(1), "nominal", "1", "Np/m")


def _cable(name="RG-316", **extra):
    return {"name": name, "construction": {"core": {"material": "SCCS", "d_mm": 0.51}, "jacket": {"material": "FEP"}},
            "connectors": [{"family": "SMA", "max_ghz": 3}, {"family": "BNC", "max_ghz": 2}],
            "vna_span": {"from_mhz": 10, "to_ghz": 3, "narrowing": [{"connector": "BNC", "to_ghz": 2}, "до 6 ГГц при L < 30 см"]},
            "markers": [{"freq_mhz": 433, "vswr_max": 1.1, "attenuation_db_m": 0.62}, {"freq_mhz": "peak"}],
            "fc_ghz": 9, **extra}


def test_seed_card_one_cable_per_entry_with_narrowing_and_peak():
    [card] = cards_from_seed({"verified": True, "cables": [_cable(attenuation=[
        {"freq_mhz": 1000, "nominal": 0.8, "max": 0.95, "unit": "dB/m", "interpolated": True}],
        impedance_ohm=50, temp_min_c=-55)]})
    assert [(c.family, c.span_to_ghz) for c in card.connectors] == [("SMA", None), ("BNC", Decimal(2))]
    assert card.span_note == "до 6 ГГц при L < 30 см"
    assert [m.freq_mhz for m in card.markers] == [Decimal(433), None]
    assert [(a.freq_mhz, a.kind, a.interpolated) for a in card.attenuation] == [
        (Decimal(433), "nominal", False), (Decimal(1000), "max", True), (Decimal(1000), "nominal", True)]
    assert card.verified and card.params == {"impedance_ohm": Decimal(50), "temp_min_c": Decimal(-55)}
    with pytest.raises(ReferenceValidationError, match="одна строка — один кабель"):
        cards_from_seed([_cable("RG-142 / RG-400")])
    with pytest.raises(ReferenceValidationError, match="повторяется"):
        cards_from_seed([_cable("RG-316"), _cable("RG 316")])
    with pytest.raises(ReferenceValidationError, match="КСВН"):
        cards_from_seed([_cable(markers=[{"freq_mhz": 433, "vswr_max": 0.9}])])


def test_committed_seed_file_is_valid_and_splits_every_combined_row():
    cards = cards_from_seed(read_seed(DEFAULT_FILE))
    names = {card.name for card in cards}
    assert {"RG-316", "RG-316D", "RG-142", "RG-400", "LMR-195", "LMR-240", "5D-FB", "8D-FB", "10D-FB", "RF 0.81",
            "RF 1.13", "RF 1.37"} <= names
    assert len(cards) == 19 and all(" / " not in card.name for card in cards)
    by_name = {card.name: card for card in cards}
    # verified against datasheets: every card says so, with its sources and how sure the check is
    assert all(card.verified and card.sources and card.confidence in {"высокая", "средняя", "низкая"} for card in cards)
    # the cut-off of the first higher mode and the rated operating limit are two different numbers
    assert (by_name["RG-316"].fc_ghz, by_name["RG-316"].max_operating_ghz) == (Decimal("65.3"), Decimal(3))
    assert by_name["RG-405"].span_to_ghz == Decimal(60)
    # 0,81 мм: no maker's table — one distributor point, nothing computed on the markers
    micro = by_name["RF 0.81"]
    assert micro.confidence == "низкая" and len(micro.attenuation) == 1 and not any(a.interpolated for a in micro.attenuation)
    # published points keep their unit; points between them are marked interpolated with the basis
    rg316 = by_name["RG-316"]
    assert any(a.unit == "dB/100ft" and a.derived for a in rg316.attenuation)
    assert any(a.interpolated and a.basis.startswith("интерполировано") for a in rg316.attenuation)
    assert rg316.markers[0].vswr_ref == Decimal("1.4")  # the reference assembly's datasheet VSWR


def test_card_for_a_row_by_exact_name_or_family():
    cards = {card.name_key: card for card in cards_from_seed([_cable("RG-58"), _cable("LMR-400")])}
    assert card_for("rg58c/u", cards)[0].name == "RG-58" and not card_for("rg58c/u", cards)[1]
    assert card_for("lmr400", cards)[1]
    assert card_for("rg581", cards)[0] is None


# --- application ---

def test_size_rows_keep_the_gost_awg_map_in_step(uow):
    gauges = _seed_gauges(uow)
    with uow:
        entries = uow.references.list()
    assert gost_awg_map(entries) == {"0.05": 30, "0.08": 28, "0.12": 26, "0.35": 22, "0.5": 20, "1": 17}

    save_gauge(SaveGaugeCommand(gauges["26"].id, "26", "0,13", "0,41", ACTOR, NOW), uow)
    with uow:
        mapped = gost_awg_map(uow.references.list())
    assert "0.12" not in mapped and mapped["0.13"] == 26

    with pytest.raises(ReferenceValidationError, match="Такое сечение"):
        save_gauge(SaveGaugeCommand(None, "", "0,5", "", ACTOR, NOW), uow)
    with pytest.raises(ReferenceValidationError, match="Такой AWG"):
        save_gauge(SaveGaugeCommand(None, "28", "0,09", "", ACTOR, NOW), uow)
    delete_gauge(gauges["30"].id, ACTOR, NOW, uow)
    with uow:
        assert "0.05" not in gost_awg_map(uow.references.list())


def test_an_admins_own_gost_awg_entry_is_not_dropped_by_a_size_edit(uow):
    gauges = _seed_gauges(uow)
    with uow:
        entries = [entry for entry in uow.references.list() if not (entry.kind == ReferenceKind.GOST_AWG and entry.normalized_key == "0.35")]
        from app.modules.references.domain import ReferenceEntry
        uow.references.replace(tuple(entries) + (ReferenceEntry.create(ReferenceKind.GOST_AWG, "0,35", "23", 99),))
        uow.commit()
    save_gauge(SaveGaugeCommand(gauges["22"].id, "22", "0,34", "0,64", ACTOR, NOW), uow)
    with uow:
        mapped = gost_awg_map(uow.references.list())
    assert mapped["0.35"] == 23 and mapped["0.34"] == 22


def _wire_row(uow, mark):
    [row] = [item for item in cable_catalog(uow).rows if item.cable == mark]
    return row


def test_hookup_save_writes_only_changed_cells_and_a_used_size_cannot_go(tmp_path, uow):
    load_mirror(_mirror(tmp_path), uow, dry_run=False, now=NOW)
    gauges = _seed_gauges(uow)
    row = _wire_row(uow, "ПВАМ 0,50")
    command = dict(record_id=row.record.id, series="ПВАМ", gauge_id=gauges["20"].id, insulation="", outer_d_mm="1,6",
                   voltage_v="", temp_min_c="", temp_max_c="", old=None, actor=ACTOR, now=NOW)

    wire, record = save_hookup(SaveHookupCommand(**command), uow)
    assert record.fields["Сечение, мм2"] == "0,5" and record.fields["Наружный диаметр, мм"] == "1,6"
    assert not record.edited_in_erp  # nothing changed in the row: «0,5» is 20 AWG, «1,6» is 1,6

    with pytest.raises(StaleRecordError):
        save_hookup(SaveHookupCommand(**{**command, "gauge_id": gauges["22"].id}), uow)
    wire2, record2 = save_hookup(SaveHookupCommand(**{**command, "gauge_id": gauges["22"].id, "outer_d_mm": "1,4", "old": wire}), uow)
    assert (record2.fields["Сечение, мм2"], record2.fields["AWG"], record2.fields["Наружный диаметр, мм"]) == ("0,35", "22", "1,4")
    assert record2.edited_in_erp and wire2.id == wire.id

    with pytest.raises(ReferenceValidationError, match="монтажных проводов"):
        delete_gauge(gauges["22"].id, ACTOR, NOW, uow)

    # a БД.ПРОВ edit of a mirrored cell moves the structure; a mirror reload keeps both
    edit_record(EditRecordCommand(record2.id, (FieldEdit("Наружный диаметр, мм", "1,4", "1,45"), FieldEdit("Сечение, мм2", "0,35", "1")), ACTOR, NOW), uow)
    load_mirror(_mirror(tmp_path / "again"), uow, dry_run=False, now=NOW)
    catalog = cable_catalog(uow)
    stored = catalog.hookups["пвам0,50"]
    assert stored.outer_d_mm == Decimal("1.45") and stored.gauge_id == gauges["17"].id


def test_hookup_refuses_an_unknown_size_and_bad_numbers(tmp_path, uow):
    load_mirror(_mirror(tmp_path), uow, dry_run=False, now=NOW)
    gauges = _seed_gauges(uow)
    row = _wire_row(uow, "ПВАМ 0,50")
    base = dict(record_id=row.record.id, series="ПВАМ", gauge_id=uuid4(), insulation="", outer_d_mm="", voltage_v="",
                temp_min_c="", temp_max_c="", old=None, actor=ACTOR, now=NOW)
    with pytest.raises(ReferenceValidationError, match="сечение"):
        save_hookup(SaveHookupCommand(**base), uow)
    for bad in ({"outer_d_mm": "-1"}, {"voltage_v": "0"}, {"temp_min_c": "100", "temp_max_c": "80"}, {"series": " "}):
        with pytest.raises(ReferenceValidationError):
            save_hookup(SaveHookupCommand(**{**base, "gauge_id": gauges["20"].id, **bad}), uow)


def test_coax_import_is_idempotent_and_drops_cards_the_file_no_longer_names(uow):
    first = import_coax_cards({"cables": [_cable("RG-316"), _cable("RG-58")]}, ACTOR, NOW, uow)
    again = import_coax_cards({"cables": [_cable("RG-316", fc_ghz=10)]}, ACTOR, NOW, uow)
    with uow:
        cards = uow.cable_structure.cards()
    assert first == {"created": 2, "updated": 0, "removed": 0, "cards": 2}
    assert again == {"created": 0, "updated": 1, "removed": 1, "cards": 1}
    assert [(card.name, card.fc_ghz, len(card.markers), len(card.attenuation)) for card in cards] == [("RG-316", Decimal(10), 2, 1)]
    dry = import_coax_cards({"cables": []}, ACTOR, NOW, SqlAlchemyReferencesUnitOfWork(uow.session_factory), dry_run=True)
    assert dry["removed"] == 1
    with uow:
        assert len(uow.cable_structure.cards()) == 1  # a dry run writes nothing


# --- HTTP ---

@pytest.fixture()
def structure_api(sessions):  # noqa: F811
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="admin@example.test", roles=frozenset({"admin"}), permissions={"erpref": "edit"})
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 10, 10, 10, 0)
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.references_uow_factory


def test_api_cables_carry_structure_sizes_and_cards(tmp_path, sessions, structure_api):  # noqa: F811
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    load_mirror(_mirror(tmp_path), uow, dry_run=False, now=NOW)
    import_coax_cards({"cables": [_cable("RG-316"), _cable("UT-047")]}, ACTOR, NOW, uow)
    created = structure_api.post("/api/references/wire-gauges", json={"awg": "20", "section_mm2": "0,5", "conductor_d_mm": "0,81"})
    assert created.status_code == 201 and created.json()["label"] == "20 AWG · 0,5 мм²"
    assert structure_api.post("/api/references/wire-gauges", json={"awg": "20", "section_mm2": "0,6"}).status_code == 422

    listed = structure_api.get("/api/references/cables").json()
    pvam = next(row for row in listed["rows"] if row["cable"] == "ПВАМ 0,50")
    assert pvam["hookup"] is None and pvam["hookup_suggestion"]["gauge_id"] == created.json()["id"]
    agr = next(row for row in listed["rows"] if row["cable"] == "FLRY-B 0,35")
    # «прочие» closed 20.10.2026: монтажный, but no series of the база — it gets no structure
    assert (agr["kind"], agr["reason"], agr["hookup"]) == ("wire", "марка не из базы монтажных", None)
    rg316 = next(row for row in listed["rows"] if row["cable"] == "RG316")
    assert rg316["card"]["name"] == "RG-316" and rg316["card"]["markers"][-1]["peak"]
    assert rg316["card"]["attenuation"][0] == {
        "freq_mhz": 433, "kind": "nominal", "value": 0.62, "unit": "dB/m", "ref_length_m": 1, "temp_c": 20,
        "loss_db_m": 0.62, "derived": False, "interpolated": False, "source": "", "basis": ""}
    only_card = next(row for row in listed["rows"] if row["cable"] == "UT-047")
    assert only_card["id"].startswith("card:") and only_card["record"] is None and only_card["kind"] == "coax"
    assert listed["gauges"][0]["wires"] == 0 and listed["counts"]["multi"] == 2

    saved = structure_api.put(f"/api/references/cables/{pvam['id']}/hookup", json={
        "series": "ПВАМ", "gauge_id": created.json()["id"], "outer_d_mm": "1,7"})
    stale = structure_api.put(f"/api/references/cables/{pvam['id']}/hookup", json={
        "series": "ПВАМ", "gauge_id": created.json()["id"], "outer_d_mm": "1,8"})
    assert saved.status_code == 200 and saved.json()["record"]["fields"]["Наружный диаметр, мм"] == "1,7"
    assert stale.status_code == 409
    assert structure_api.delete(f"/api/references/wire-gauges/{created.json()['id']}").status_code == 422
    assert structure_api.get("/api/references/bases").json()["gauges"] == 1
    # the import is the administrator's; a technologist with the reference edit right cannot run it
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="tech@example.test", roles=frozenset({"technologist"}), permissions={"erpref": "edit"})
    assert structure_api.post("/api/references/coax-cards/import").status_code == 403


def test_api_import_reloads_the_committed_file(sessions, structure_api):  # noqa: F811
    response = structure_api.post("/api/references/coax-cards/import")
    assert response.status_code == 200
    assert response.json()["cards"] == len(json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))["cables"])


def test_structure_hookups_is_idempotent_and_lists_what_it_left(tmp_path, uow):
    from app.modules.references.application import structure_hookups

    load_mirror(_mirror(tmp_path), uow, dry_run=False, now=NOW)
    _seed_gauges(uow)
    factory = lambda: SqlAlchemyReferencesUnitOfWork(uow.session_factory)  # noqa: E731

    dry = structure_hookups(ACTOR, NOW, factory, dry_run=True)
    first = structure_hookups(ACTOR, NOW, factory)
    again = structure_hookups(ACTOR, NOW, factory)

    assert dry["created"] == first["created"] == 2
    assert first["left"] == ["FLRY-B 0,35: марка не из базы монтажных"]  # монтажный of no series: no structure
    assert (again["created"], again["structured_before"]) == (0, 2)
    assert sorted(cable_catalog(factory()).hookups) == ["ul106128awg", "пвам0,50"]
