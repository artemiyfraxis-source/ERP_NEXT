from dataclasses import replace

import pytest

from app.modules.production.domain import ProductionValidationError
from app.modules.production.rf_interfaces import RfDetails, RfInterface, mates, mating_rating, parse_interfaces, propose_details


@pytest.mark.parametrize("series", ["SMA", "N", "TNC", "BNC", "F", "RP-SMA", "3.5mm"])
def test_explicit_pairs_keep_body_and_contact_separate(series):
    ports = parse_interfaces(f"Переходник {series} female–{series} male 50 Ом до 6 ГГц")
    assert len(ports) == 2
    female, male = ports
    assert female.body == "розетка" and male.body == "вилка"
    assert male.center == ("гнездо" if series == "RP-SMA" else "штырь")
    assert female.center != male.center
    assert mates(female, male) and mating_rating(female, male) == 6
    assert not female.f_confirmed and female.f_source


@pytest.mark.parametrize("name", ["SN-321", "Переходник SMA–N male", "SMA male N", "SMA", "SMA-MF06", "N male N female N male"])
def test_ambiguous_names_never_get_guessed_interfaces(name):
    assert parse_interfaces(name) == ()


def test_cross_series_lower_rating_and_unknown_impedance():
    male = RfInterface("SMA", "вилка", "штырь", 50, 6)
    female = RfInterface("3.5mm", "розетка", "гнездо", 50, 26.5)
    assert mates(male, female) and mating_rating(male, female) == 6
    for bad in (replace(female, impedance_ohm=75), replace(female, impedance_ohm=None),
                replace(female, center="штырь"), replace(female, body="вилка"), replace(female, series="тип III")):
        assert not mates(male, bad)


def test_known_assets_are_unconfirmed_and_identity_checked():
    planar = propose_details("СИ.002", "Planar S50180 №26030095")
    assert planar.role == "vna" and planar.f_min_ghz == .0001
    assert len(planar.interfaces) == 2 and all(p.series == "N" and p.body == "розетка" for p in planar.interfaces)
    assert not planar.confirmed and not any(p.f_confirmed for p in planar.interfaces)
    assert not propose_details("СИ.002", "Другой прибор").interfaces
    acm = propose_details("СИ.104", "АКМ ACM2520-111")
    assert acm.confirmed and len(acm.interfaces) == 2
    assert all(p.series == "3.5mm" and p.body == "розетка" and p.center == "гнездо" and p.impedance_ohm == 50 for p in acm.interfaces)
    assert all(p.f_max_ghz is None and not p.f_confirmed for p in acm.interfaces)
    assert propose_details("СИ.004", "SMA-MF06").role == "load_kit"
    assert propose_details("СИ.003", "6650F27").kit_definition == "6650F27-full"
    cable = propose_details("СИ.098", "C50NMIIIM.2")
    assert cable.interfaces[1].series == "тип III"
    assert propose_details("СИ.100", "C50SMNM.2").interfaces[0].f_max_ghz is None


def test_known_75_ohm_models_are_not_inferred_as_50():
    proposal = propose_details("СИ.044", "Переходник BNC female–F male B-311")
    assert all(p.impedance_ohm == 75 for p in proposal.interfaces)


@pytest.mark.parametrize("changes", [{"impedance_ohm": float('nan')}, {"f_max_ghz": float('inf')}, {"f_max_ghz": -1}, {"f_confirmed": True}])
def test_interface_validation(changes):
    with pytest.raises(ProductionValidationError):
        RfInterface("SMA", "вилка", "штырь", **changes)


def test_cannot_confirm_unknown_or_partial_rf_data():
    with pytest.raises(ProductionValidationError):
        RfDetails(confirmed=True)
