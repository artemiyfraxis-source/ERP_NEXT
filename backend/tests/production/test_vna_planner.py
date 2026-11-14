from dataclasses import replace
from uuid import uuid4

import pytest

from app.modules.production.rf_interfaces import RfDetails, RfInterface
from app.modules.production.vna_planner import RfAsset, plan_connections


def port(series, male, ohms=50, maximum=18):
    return RfInterface(series, "вилка" if male else "розетка", "штырь" if male else "гнездо",
                       ohms, maximum, True, "Паспорт")


def asset(role, *ports, quantity=1, label="Оснастка", **changes):
    return RfAsset(uuid4(), label, RfDetails(tuple(ports), role, True, "Паспорт", 0,
                    kit_definition="6650F27-full" if role == "solt_kit" else ""), quantity, **changes)


def fixture():
    vna = asset("vna", port("N", False), port("N", False), label="ВАЦ")
    cables = asset("test_cable", port("N", True), port("3.5mm", False), quantity=2, label="Тестовый кабель")
    kit = asset("solt_kit", port("3.5mm", True, maximum=26.5), port("3.5mm", False, maximum=26.5), label="6650F27")
    return vna, cables, kit


def plan(vna, dut, *items, **kwargs):
    return plan_connections(vna, dut, list(items), (0.001, 6), **kwargs)


def test_sma_both_ends_uses_six_sol_and_defined_same_gender_thru():
    vna, cable, kit = fixture()
    result = plan(vna, (port("SMA", True),) * 2, cable, kit)
    assert result["status"] == "ready" and result["method"] == "SOLT 6650F27"
    standards = result["calibration_connections"]["standards"]
    assert len(standards) == 7 and standards[-1]["standard"] == "MA6666A-11"
    assert standards[-1]["directions"] == ["1 → 2", "2 → 1"]
    assert [p["steps"][0]["unit_slot"] for p in result["measurement_connections"]] == [1, 2]


def test_mixed_genders_and_scarce_adapter_joint_allocation():
    vna, cable, kit = fixture()
    adapter = asset("adapter", port("3.5mm", True), port("3.5mm", True))
    result = plan(vna, (port("SMA", True), port("SMA", False)), cable, kit, adapter)
    assert result["status"] == "ready"
    assert result["calibration_connections"]["standards"][-1]["standard"] == "MA6666C-11"
    assert plan(vna, (port("SMA", False),) * 2, cable, kit, adapter)["status"] == "blocked"
    assert plan(vna, (port("SMA", False),) * 2, cable, kit, replace(adapter, quantity=2))["status"] == "ready"


def test_n_dut_requires_n_acm_or_qualified_post_calibration_adapters():
    vna, cable, kit = fixture()
    n_cable = asset("test_cable", port("N", True), port("N", False), quantity=2)
    dut = (port("N", True),) * 2
    assert plan(vna, dut, n_cable, kit)["status"] == "blocked"
    acm = asset("auto_calibration", port("N", True), port("N", True))
    result = plan(vna, dut, n_cable, kit, acm)
    assert result["method"] == "АКМ" and result["status"] == "ready"
    transition = asset("adapter", port("3.5mm", True), port("N", False), quantity=2)
    result = plan(vna, dut, cable, kit, transition)
    assert result["status"] == "qualified" and not result["automatic_acceptance"]
    assert result["method"] == "через переходник после калибровки"
    assert all(len(p["after_calibration"]) == 1 for p in result["measurement_connections"])


@pytest.mark.parametrize("failure", ["75", "band", "unconfirmed", "unavailable", "quantity"])
def test_unusable_assets_are_excluded(failure):
    vna, cable, kit = fixture()
    if failure == "75":
        cable = replace(cable, rf=replace(cable.rf, interfaces=tuple(replace(p, impedance_ohm=75) for p in cable.rf.interfaces)))
    if failure == "band":
        kit = replace(kit, rf=replace(kit.rf, interfaces=tuple(replace(p, f_max_ghz=3) for p in kit.rf.interfaces)))
    if failure == "unconfirmed":
        cable = replace(cable, rf=replace(cable.rf, confirmed=False))
    if failure == "unavailable":
        cable = replace(cable, available=False)
    if failure == "quantity":
        cable = replace(cable, quantity=1)
    assert plan(vna, (port("SMA", True),) * 2, cable, kit)["status"] == "blocked"


def test_acm_preferred_when_complete_confirmed_and_fitting():
    vna, cable, kit = fixture()
    acm = asset("auto_calibration", port("3.5mm", True), port("3.5mm", True))
    assert plan(vna, (port("SMA", True),) * 2, cable, kit, acm)["method"] == "АКМ"
    unknown = replace(acm, rf=RfDetails(role="auto_calibration"))
    assert plan(vna, (port("SMA", True),) * 2, cable, kit, unknown)["method"] == "SOLT 6650F27"


def test_search_limit_is_reported_and_does_not_claim_impossibility():
    vna, cable, kit = fixture()
    result = plan(vna, (port("SMA", True),) * 2, cable, kit, max_states=1)
    assert result["status"] == "blocked" and result["search_limited"]


def one_port_fixture(series, cables=1, adapters=2):
    vna, _, kit = fixture()
    cable = asset("test_cable", port("N", True), port("3.5mm", True), quantity=cables)
    adapter = asset("adapter", port("SMA", False), port(series, False), quantity=adapters)
    load = asset("load", port("SMA", True), label="Нагрузка SMA 50 Ом")
    acm = asset("auto_calibration", port("3.5mm", False), port("3.5mm", False), label="АКМ")
    return vna, cable, adapter, load, kit, acm


@pytest.mark.parametrize("series", ["BNC", "TNC"])
def test_one_port_composite_termination_two_orientations_one_attempt(series):
    vna, cable, adapter, load, kit, acm = one_port_fixture(series)
    result = plan(vna, (port(series, True),) * 2, cable, adapter, load, kit, acm)
    assert result["status"] == "qualified" and result["mode"] == "one_port_reversal"
    assert result["one_attempt"] and not result["automatic_acceptance"]
    assert len(result["orientations"]) == 2
    assert [o["side"] for o in result["orientations"]] == ["А", "Б"]
    for orientation in result["orientations"]:
        assert orientation["calibration"]["method"] == "АКМ, один порт"
        assert orientation["termination"]["load_id"] == str(load.id)
        assert orientation["termination"]["label"] == "Составная нагрузка 50 Ом"
        # The two ends use different physical adapters simultaneously; flipping reuses those units.
        assert orientation["measurement"]["steps"][-1]["unit_slot"] == 1
        assert orientation["termination"]["steps"][0]["unit_slot"] == 2
    assert next(a["quantity"] for a in result["allocations"] if a["instrument_id"] == str(adapter.id)) == 2


@pytest.mark.parametrize("series", ["BNC", "TNC"])
def test_two_port_remains_default_with_composite_load_also_available(series):
    vna, cable, adapter, load, kit, acm = one_port_fixture(series, cables=2)
    result = plan(vna, (port(series, True),) * 2, cable, adapter, load, kit, acm)
    assert result["mode"] == "two_port" and result["status"] == "qualified"
    assert result["calibration_method"] == "АКМ"


@pytest.mark.parametrize("series", ["BNC", "TNC"])
def test_one_port_sol_and_explicit_load_kit_are_allowed_but_reference_kit_is_not_load(series):
    vna, cable, adapter, load, kit, _ = one_port_fixture(series)
    missing = plan(vna, (port(series, True),) * 2, cable, adapter, kit)
    assert missing["status"] == "blocked" and any("нужна нагрузка SMA 50 Ом" in p for p in missing["problems"])
    load_kit = replace(load, rf=replace(load.rf, role="load_kit", kit_definition="SMA-MF06-loads"))
    result = plan(vna, (port(series, True),) * 2, cable, adapter, load_kit, kit)
    assert result["mode"] == "one_port_reversal"
    assert all(len(o["calibration"]["standards"]) == 3 for o in result["orientations"])
    assert all(o["calibration"]["method"] == "SOL 6650F27" for o in result["orientations"])
    assert all(o["termination"]["load_id"] != str(kit.id) for o in result["orientations"])


def test_one_port_cannot_reuse_scarce_adapter_for_far_end():
    vna, cable, adapter, load, kit, acm = one_port_fixture("BNC", adapters=1)
    result = plan(vna, (port("BNC", True),) * 2, cable, adapter, load, kit, acm)
    assert result["status"] == "blocked"
    assert any("одновременно" in problem for problem in result["problems"])
    direct_load = replace(load, rf=replace(load.rf, interfaces=(port("BNC", False),)))
    result = plan(vna, (port("BNC", True),) * 2, cable, adapter, direct_load, kit, acm)
    assert result["mode"] == "one_port_reversal"
    assert all(not o["termination"]["steps"] for o in result["orientations"])


@pytest.mark.parametrize("cables", [1, 2])
def test_characterization_changes_planned_method_without_claiming_applied_correction(cables):
    vna, cable, adapter, load, kit, acm = one_port_fixture("TNC", cables=cables)
    file_id = uuid4()
    characterized = replace(adapter, quantity=1, characterization_file_id=file_id)
    second = replace(characterized, id=uuid4(), characterization_file_id=uuid4())
    result = plan(vna, (port("TNC", True),) * 2, cable, characterized, second, load, kit, acm)
    assert result["method"] == "с исключением переходника (де-эмбеддинг)"
    assert not result["automatic_acceptance"] and result["status"] == "qualified"
    assert all(c["file_id"] in (str(file_id), str(second.characterization_file_id)) and c["requires_orientation_confirmation"] for c in result["characterizations"])


def test_one_characterization_does_not_describe_two_physical_units():
    vna, cable, adapter, load, kit, acm = one_port_fixture("TNC", cables=2)
    result = plan(vna, (port("TNC", True),) * 2, cable, replace(adapter, characterization_file_id=uuid4()), load, kit, acm)
    assert "де-эмбеддинг" not in result["method"]
    assert not result["characterizations"]


def test_direct_one_port_and_mixed_dut_have_accurate_method_labels():
    vna, cable, adapter, load, kit, acm = one_port_fixture("BNC")
    result = plan(vna, (port("SMA", False),) * 2, cable, load, kit, acm)
    assert result["method"] == "Однопортовое измерение с переворотом"
    assert not result["characterizations"]
    characterized = replace(adapter, quantity=1, characterization_file_id=uuid4())
    mixed = plan(vna, (port("SMA", False), port("BNC", True)), cable, characterized, load, kit, acm)
    assert mixed["mode"] == "one_port_reversal"
    assert mixed["method"] == "с исключением переходника (де-эмбеддинг)"


def test_owner_confirmed_acm_geometry_without_band_cannot_authorize_calibration():
    from app.modules.production.rf_interfaces import propose_details
    vna, cable, _, _, kit, acm = one_port_fixture("BNC")
    acm = replace(acm, rf=propose_details("СИ.104", "ACM2520-111"))
    # Geometry is confirmed, but no frequency rating was supplied in the owner's answer.
    assert acm.rf.confirmed
    result = plan(vna, (port("SMA", False),) * 2, replace(cable, quantity=2), kit, acm)
    assert result["calibration_method"] == "SOLT 6650F27"
