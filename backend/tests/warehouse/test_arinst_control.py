"""Exact per-device capabilities, mixed heartbeat generations and verified Arinst identity."""

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.warehouse import devices, vna_jobs
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.modules.warehouse.scan_jobs import ScanJobClosed
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, tokens_api  # noqa: F401
from tests.warehouse.test_vna_jobs import ARINST_INSTRUMENT, HEARTBEAT, JOBS, NAME, capture  # noqa: F401

SERIAL = "arinst:3932198-827215893-540422231"
UID = SERIAL[7:]


def reports(clock):
    return [dict(source="s2vna", name=NAME, serial="26030095", version=2, methods=["acm"], ops=["device_status", "release"],
                 identity_verified=True, control_ready=True, observed_at=clock.now.isoformat()),
            dict(source="arinst", serial=SERIAL, version=3, methods=[], ops=["connect", "read_profile", "close"],
                 model="VNA-DL", firmware="2.0.0", hardware_uid=UID, identity_verified=True, license_state="not_required",
                 trial_remaining_seconds=None, control_ready=True, observed_at=clock.now.isoformat())]


def beat(capture, items=None, **extra):
    _, agent, clock, _ = capture
    agent = extra.pop("_agent", agent)
    data = dict(machine_id="pc-1", pc_name="ПК", version="2.13.0", vna=dict(name=NAME, ready=True),
                vna_controls=dict(version=1, devices=reports(clock) if items is None else items))
    data.update(extra)
    return TestClient(app).post(HEARTBEAT, json=data, headers=agent)


def test_two_devices_on_one_pc_have_exact_independent_control(capture):
    assert beat(capture).status_code == 200
    with capture[3]() as session:
        assert len(list(session.scalars(select(devices.VnaControlRow)))) == 2
        assert devices.control_ops(session, "pc-1", capture[2].now, serial=SERIAL, source="arinst") == {"connect", "read_profile", "close"}
        assert devices.control_capability(session, "pc-1", capture[2].now, serial="26030095") == ["acm"]
        assert vna_jobs.control_station(session, SERIAL, capture[2].now, source="arinst", ops={"connect"}) == "pc-1"
        with pytest.raises(ScanJobClosed):
            vna_jobs.control_station(session, SERIAL, capture[2].now)  # no source confusion
        with pytest.raises(ScanJobClosed):
            vna_jobs.control_station(session, SERIAL, capture[2].now, source="arinst", ops={"apply_profile"})


def test_legacy_singleton_does_not_erase_arinst_control(capture):
    assert beat(capture).status_code == 200
    assert beat(capture, vna_controls=None, vna_control=dict(version=1, methods=["solt1"])).status_code == 200
    with capture[3]() as session:
        assert session.get(devices.VnaControlRow, ("pc-1", SERIAL)) is not None
        assert devices.control_capability(session, "pc-1", capture[2].now) == ["solt1"]
        assert vna_jobs.control_station(session, SERIAL, capture[2].now, source="arinst") == "pc-1"
    assert beat(capture, vna_controls=None).status_code == 200
    with capture[3]() as session:
        assert session.get(devices.VnaControlRow, ("pc-1", SERIAL)) is not None
        assert session.get(devices.VnaControlRow, ("pc-1", "26030095")) is None


def test_empty_inventory_revokes_routes_and_keeps_identity_history(capture):
    assert beat(capture).status_code == 200
    assert beat(capture, items=[]).status_code == 200
    with capture[3]() as session:
        assert not list(session.scalars(select(devices.VnaControlRow)))
        assert session.get(devices.VnaBindingRow, SERIAL).machine_id == "pc-1"
        assert not session.get(devices.DeviceRow, ("pc-1", "arinst", SERIAL)).present


def test_inventory_only_planar_retains_full_capture_identity(capture):
    assert beat(capture, vna=None).status_code == 200
    with capture[3]() as session:
        assert session.get(devices.DeviceRow, ("pc-1", "s2vna", "26030095")).name == NAME
    choices = devices.vna_agents(SqlAlchemyWarehouseUnitOfWork(capture[3]), capture[2].now, "s2vna", "26030095")
    assert devices.idn_serial(choices[0]["vna"]["name"]) == "26030095"


def test_cached_observation_and_trial_cannot_be_refreshed_by_poll_or_heartbeat(capture):
    initial = reports(capture[2])
    initial[1]["license_state"] = "TRIAL"  # Old agent observations remain conservative.
    initial[1]["trial_remaining_seconds"] = 5
    assert beat(capture, initial).status_code == 200
    capture[2].now += timedelta(seconds=6)
    assert beat(capture, initial).status_code == 200
    with capture[3]() as session:
        with pytest.raises(ScanJobClosed, match="Время пробного"):
            devices.control_capability(session, "pc-1", capture[2].now, serial=SERIAL, source="arinst")
    capture[2].now += timedelta(minutes=4)
    assert beat(capture, initial).status_code == 200
    with capture[3]() as session:
        with pytest.raises(ScanJobClosed):
            vna_jobs.control_station(session, SERIAL, capture[2].now, source="arinst")


def test_native_license_never_counts_down_but_identity_still_expires(capture):
    initial = reports(capture[2])
    assert beat(capture, initial).status_code == 200
    capture[2].now += timedelta(seconds=61)
    assert beat(capture, initial).status_code == 200
    with capture[3]() as session:
        control = session.get(devices.VnaControlRow, ("pc-1", SERIAL))
        assert control.license_state == "not_required" and control.trial_remaining_seconds is None
        assert devices.control_capability(session, "pc-1", capture[2].now, serial=SERIAL, source="arinst") == []
    capture[2].now += timedelta(minutes=4)
    assert beat(capture, initial).status_code == 200
    with capture[3]() as session:
        with pytest.raises(ScanJobClosed):
            devices.control_capability(session, "pc-1", capture[2].now, serial=SERIAL, source="arinst")


@pytest.mark.parametrize("patch", [
    {"serial": "arinst"}, {"hardware_uid": "01-2-3"}, {"hardware_uid": "4294967296-2-3"},
    {"identity_verified": False}, {"model": "S50180"}, {"version": 2}, {"ops": ["*RST"]},
    {"methods": ["acm"]}, {"license_state": "LOCKED", "trial_remaining_seconds": None},
    {"trial_remaining_seconds": 0}, {"trial_remaining_seconds": 601}, {"observed_at": "2026-11-23T12:00:00"},
])
def test_invalid_arinst_claims_are_rejected_before_inventory_write(capture, patch):
    items = reports(capture[2]); items[1].update(patch)
    assert beat(capture, items).status_code == 422
    with capture[3]() as session:
        assert not list(session.scalars(select(devices.VnaControlRow)))


def test_namespace_duplicates_future_clocks_and_usb_candidates_cannot_route(capture):
    items = reports(capture[2]); items[0]["serial"] = SERIAL
    assert beat(capture, items).status_code == 422
    assert beat(capture, [reports(capture[2])[1]] * 2).status_code == 422
    future = reports(capture[2])[1]; future["observed_at"] = (capture[2].now + timedelta(hours=1)).isoformat()
    assert beat(capture, [future], arinst_candidates=[dict(usb_path=r"USB\VID_0483&PID_5740\OTHER", port="COM3")]).status_code == 200
    with capture[3]() as session:
        assert session.get(devices.VnaControlRow, ("pc-1", SERIAL)) is None
    assert devices.vna_agents(SqlAlchemyWarehouseUnitOfWork(capture[3]), capture[2].now, "arinst") == []


def test_operator_confirmation_preserves_printed_serial_and_is_idempotent(capture):
    assert beat(capture).status_code == 200
    admin, _, clock, sessions = capture
    payload = dict(instrument_id=ARINST_INSTRUMENT, serial=SERIAL, agent_id="pc-1")
    assert admin.post(JOBS + "/identity", json=payload, headers=_csrf(admin)).status_code == 200
    assert admin.post(JOBS + "/identity", json=payload, headers=_csrf(admin)).status_code == 200
    with sessions() as session:
        identity = devices.instrument_identity(session, UUID(ARINST_INSTRUMENT))
        assert identity.serial == SERIAL and identity.hardware_uid == UID and identity.firmware == "2.0.0"
        assert len(list(session.scalars(select(devices.VnaIdentityRow)))) == 1
    clock.now += timedelta(minutes=4)
    assert admin.post(JOBS + "/identity", json=payload, headers=_csrf(admin)).status_code == 409


def test_locked_identity_can_be_associated_but_cannot_control(capture):
    items = reports(capture[2]); items[1].update(license_state="LOCKED", trial_remaining_seconds=None, control_ready=False)
    assert beat(capture, items).status_code == 200
    with capture[3]() as session:
        with pytest.raises(ScanJobClosed, match="Время пробного"):
            vna_jobs.control_station(session, SERIAL, capture[2].now, source="arinst")
    assert capture[0].post(JOBS + "/identity", json=dict(instrument_id=ARINST_INSTRUMENT, serial=SERIAL, agent_id="pc-1"),
                           headers=_csrf(capture[0])).status_code == 200


def test_arinst_transfer_preserves_guard_and_cancels_old_capture(capture):
    assert beat(capture).status_code == 200
    second_agent = _bearer(_issue(capture[0], scopes=("scan:upload",))["token"])
    assert beat(capture, machine_id="pc-2", _agent=second_agent).status_code == 200
    from app.shared.identity import Actor
    from tests.identity.test_api_tokens import ADMIN_ID

    actor = Actor(ADMIN_ID, "Админ", "")
    uow = SqlAlchemyWarehouseUnitOfWork(capture[3])
    from app.modules.warehouse.scan_jobs import create_job, claim, get_job

    job = create_job(uow, requested_by=str(ADMIN_ID), now=capture[2].now, target="pc-1", kind="vna",
                     source="arinst", instrument_id=UUID(ARINST_INSTRUMENT), requester_id=ADMIN_ID)
    assert claim(uow, now=capture[2].now, machine_id="pc-1", vna=True, scan=False).id == job.id
    def blocked(session):
        raise ScanJobClosed("Сначала завершите сеанс")
    arguments = dict(serial=SERIAL, owner="pc-1", target="pc-2", source="arinst",
                     instrument_id=UUID(ARINST_INSTRUMENT), actor=actor, now=capture[2].now)
    with pytest.raises(ScanJobClosed, match="Сначала завершите"):
        devices.transfer_vna(uow, **arguments, check_sessions=blocked)
    with capture[3]() as session:
        assert session.get(devices.VnaBindingRow, SERIAL).machine_id == "pc-1"
    devices.transfer_vna(uow, **arguments, check_sessions=lambda session: None)
    assert get_job(uow, job.id, now=capture[2].now).status == "failed"
    with pytest.raises(ScanJobClosed):
        vna_jobs.complete(uow, job.id, b"# Hz S RI R 50\n1000000 1 0\n", "test.s1p", capture[2].now, "pc-1")
    with capture[3]() as session:
        assert session.get(devices.VnaBindingRow, SERIAL).machine_id == "pc-2"
        with pytest.raises(ScanJobClosed):
            vna_jobs.control_station(session, SERIAL, capture[2].now, machine_id="pc-1", source="arinst")
