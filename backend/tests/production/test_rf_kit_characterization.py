"""Publication safety rules independent of storage and bench acquisition."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import struct
from uuid import uuid4

import pytest

from app.modules.production.domain import ProductionValidationError
from app.modules.production.rf_interfaces import RfDetails, propose_details
from app.modules.production.rf_kit_characterization import (
    KitStandard, REQUIRED_PIECES, check_completeness, check_revision, curve_metadata, evidence_snapshot, generation_hash,
)

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)


def piece(kind="OPEN", gender="female"):
    return KitStandard(f"{kind}-{gender}", kind, gender, gender if kind == "THRU" else None,
                       10e6, 3e9 if gender == "female" and kind in ("OPEN", "SHORT") else 6e9)


def curve(format="s1p", value=0.25, maximum=3e9):
    values = f"{value} 0" if format == "s1p" else f"{value} 0 0.9 0 0.9 0 {value} 0"
    return f"# Hz S RI R 50\n10000000 {values}\n{maximum:g} {values}\n".encode()


def provenance():
    return {"planar_instrument_id": str(uuid4()), "planar_serial": "TEST-PLANAR", "session_id": str(uuid4()),
            "command_id": str(uuid4()), "calibration_id": str(uuid4()), "method": "ACM", "plane": "port-1 mating plane",
            "fixtures": [], "operator": "Контролёр", "captured_at": NOW.isoformat(), "if_bandwidth_hz": 1000,
            "power_dbm": -10, "temperatures": {"ambient_c": 23}, "processing_version": "test-v1"}


VALID = {"valid": True, "checks": {"repeatability": True, "grid": True}}


def test_explicit_characterized_definition_does_not_promote_the_legacy_proposal():
    assert RfDetails(role="solt_kit", kit_definition="SMA-MF06-characterized")
    assert propose_details("СИ.004", "SMA-MF06").kit_definition == "SMA-MF06-loads"
    with pytest.raises(ProductionValidationError):
        RfDetails(role="load_kit", kit_definition="SMA-MF06-characterized")


@pytest.mark.parametrize("kind,gender,_", sorted(REQUIRED_PIECES))
def test_all_eight_vendor_pieces(kind, gender, _):
    standard = piece(kind, gender)
    assert standard.format == ("s2p" if kind == "THRU" else "s1p")
    with pytest.raises(ProductionValidationError):
        replace(standard, f_max_hz=standard.f_max_hz + 1)


@pytest.mark.parametrize("changes", [
    {"kind": "SOL"}, {"gender": "unknown"}, {"gender2": "male"}, {"kind": "THRU"},
    {"piece_label": " "}, {"piece_label": " spaced "}, {"f_min_hz": -1}, {"f_min_hz": True},
    {"f_max_hz": float("nan")}, {"f_max_hz": float("inf")}, {"f_min_hz": 3e9},
])
def test_invalid_physical_identities_and_bands(changes):
    with pytest.raises(ProductionValidationError):
        replace(piece(), **changes)


def test_completeness_is_by_kind_and_both_connectors_not_labels():
    standards = [piece(kind, gender) for kind, gender, _ in REQUIRED_PIECES]
    check_completeness(standards)
    for bad in (standards[:-1], standards + [standards[0]], standards[:-1] + [standards[0]]):
        with pytest.raises(ProductionValidationError):
            check_completeness(bad)


@pytest.mark.parametrize("content", [
    b"", pytest.param(b"x" * (2 * 1024 * 1024 + 1), id="oversized"), b"# Hz S RI R 75\n1 0 0\n2 0 0", b"# Hz S RI R 50\n2 0 0\n1 0 0",
    b"# Hz S RI R 50\n1 0 0\n1 0 0", b"# Hz S RI R 50\n1 nan 0\n2 0 0",
    b"# Hz S RI R 50\n1 0 0", b"# Hz S RI R 50\n1 0 0\n2 0 0 1 0",
    b"# Hz S RI R 50\n-1 0 0\n2 0 0", b"\xff",
])
def test_invalid_curves_are_rejected(content):
    with pytest.raises(ProductionValidationError):
        curve_metadata(content, "s1p")


def test_hashes_and_activation_validation():
    content = curve()
    metadata = curve_metadata(content, "s1p")
    assert metadata.sha256 == hashlib.sha256(content).hexdigest()
    assert metadata.grid_hash == hashlib.sha256(struct.pack("!dd", 10e6, 3e9)).hexdigest()
    assert metadata.points == 2
    check_revision(piece(), "s1p", metadata, VALID)
    for format, data, validation in (("s2p", metadata, VALID), ("s1p", replace(metadata, f_max_hz=3e9 + 1), VALID),
                                     ("s1p", replace(metadata, f_min_hz=0), VALID), ("s1p", metadata, {"valid": False})):
        with pytest.raises(ProductionValidationError):
            check_revision(piece(), format, data, validation)
    kit_id = uuid4()
    assert generation_hash(kit_id, 1, []) != generation_hash(kit_id, 2, [])


@pytest.mark.parametrize("change", [
    {"planar_instrument_id": "invented"}, {"captured_at": "2026-11-23T12:00:00"}, {"plane": ""},
    {"if_bandwidth_hz": 0}, {"power_dbm": float("nan")}, {"temperatures": {}},
    {"fixtures": [{"instrument_id": str(uuid4()), "sha256": "0" * 64, "orientation": "unknown"}]},
])
def test_provenance_refuses_missing_traceability(change):
    with pytest.raises(ProductionValidationError):
        evidence_snapshot({**provenance(), **change}, VALID)


def test_evidence_is_snapshotted_and_valid_flag_requires_passed_checks():
    source = provenance()
    saved, _ = evidence_snapshot(source, VALID)
    source["temperatures"]["ambient_c"] = 99
    assert saved["temperatures"]["ambient_c"] == 23
    for bad in ({}, {"valid": "true", "checks": {"grid": True}}, {"valid": True, "checks": {}},
                {"valid": True, "checks": {"grid": False}}):
        with pytest.raises(ProductionValidationError):
            evidence_snapshot(provenance(), bad)
    assert evidence_snapshot(provenance(), {"valid": False, "checks": {"grid": False}})
