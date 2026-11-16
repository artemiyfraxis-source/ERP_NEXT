"""Server-owned session evidence, atomic recording and safe local cancellation."""

import base64
from copy import deepcopy
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.production.infrastructure import ProductionInstrumentRow, ProductionVswrMeasurementRow
from app.modules.production.vna_session_infrastructure import VnaArtifactRow, VnaCommandRow, VnaSessionRepository
from app.modules.production.vna_sessions import SessionConflict
from app.shared.persistence import AuditEventRow
from app.workflows import vna_measurement_api as api
from tests.identity.test_api_tokens import ADMIN_ID, OPERATOR_ID, _csrf, _login, tokens_api  # noqa: F401
from tests.warehouse.test_vna_jobs import capture  # noqa: F401
from tests.workflows.test_vna_measurement_api import (
    URL, action, advance_until, answer, create, env, get_view, poll, send, start,  # noqa: F401
)


@pytest.fixture
def documents(env, monkeypatch):
    pinned = {"coaxVswrNorm": deepcopy(env[5]["profile"]["norm"])}
    current = {}
    monkeypatch.setattr(api, "_task_etk", lambda *_: (
        pinned, {"techcard_id": "test", "current_revision": 4}, current or None,
    ))
    return pinned, current


def review(env, *, fail_b=False, short=False, split=False):
    view = start(env)
    for _ in range(45):
        view = get_view(env, view)
        if view["state"] == "review":
            return view
        if view["state"] in ("awaiting_standard", "awaiting_dut"):
            assert action(env, view, "confirm").status_code == 200
        command = poll(env).json()["job"]
        payload = answer(command)
        if command["op"] == "capture":
            args = command["args"]
            low, high = args["profile"]["start_hz"], args["profile"]["stop_hz"]
            if short:
                high /= 2
            traces = args["profile"]["traces"]
            gamma_b = .4 if fail_b else .1
            if len(traces) == 1:
                gamma = gamma_b if args["orientation"] == "Б" else .1
                artifacts = [("trace.s1p", traces, f"{gamma} 0")]
            elif split:
                artifacts = [(f"{trace}.s1p", [trace], f"{gamma_b if trace == 'S22' else .1} 0") for trace in traces]
            else:
                artifacts = [("trace.s2p", traces, f".1 0 .9 0 .9 0 {gamma_b} 0")]
            payload["result"]["artifacts"] = [dict(file_name=name, traces=names, content_base64=base64.b64encode(
                f"# Hz S RI R 50\n{low} {values}\n{high} {values}\n".encode()).decode())
                for name, names, values in artifacts]
        response = send(env, command, payload)
        assert response.status_code == 200, response.text
    pytest.fail("Session did not reach review")


def record(env, view, **extra):
    return env[0].post(f"{URL}/{view['id']}/record", json={"expected_version": view["version"], **extra},
                       headers=_csrf(env[0]))


def counts(env):
    with env[3]() as session:
        return (
            session.scalar(select(func.count()).select_from(ProductionVswrMeasurementRow)),
            session.scalar(select(func.count()).select_from(AuditEventRow).where(
                AuditEventRow.event_type == "production.vswr_recorded")),
        )


@pytest.mark.parametrize("env", ["solt2", "acm", "solt1"], indirect=True)
def test_records_all_session_ends_and_retry_returns_exact_attempt(env, documents):
    view = review(env, fail_b=True)
    response = record(env, view)
    assert response.status_code == 200, response.text
    data = response.json()
    measured = data["measurement"]
    assert data["session"]["state"] == "recorded"
    assert data["session"]["recorded_reference"] == measured["id"]
    assert measured["attempt"] == 1 and measured["complete"] and measured["verdict"] == "fail"
    assert [(end["end"], end["verdict"]) for end in measured["end_results"]] == [("A", "pass"), ("B", "fail")]
    assert measured["serial_number"] == "VNA-1-0001"
    assert measured["norm"] == env[5]["profile"]["norm"]
    assert {f["file_meta"]["evidence_id"] for f in measured["files"]} == {a["id"] for a in view["artifacts"]}
    for file in measured["files"]:
        assert file["file_meta"]["capture_verified"]
        settings = file["file_meta"]["capture_settings"]
        assert settings["session_id"] == view["id"] and settings["correction_status"] == "COR"
        assert settings["qualification_reason"] == view["snapshot"]["qualification_reason"]
        source = next(a for a in view["artifacts"] if a["id"] == file["file_meta"]["evidence_id"])
        original = env[0].get(f"{URL}/{view['id']}/artifacts/{source['id']}")
        assert env[0].get(file["download_url"]).content == original.content
    documents[0]["coaxVswrNorm"]["vswrMax"] = 1.1
    assert record(env, view).json() == data  # Retry does not re-evaluate a newer norm.
    assert record(env, data["session"]).json() == data
    assert counts(env) == (1, 1)
    assert create(env).status_code == 200


def test_unsupported_split_transmission_files_are_not_silently_dropped(env, documents):
    view = review(env, split=True)
    response = record(env, view)
    assert response.status_code == 422, response.text
    assert "S11 или S22" in response.json()["detail"]
    assert counts(env) == (0, 0) and get_view(env, view)["state"] == "review"


@pytest.mark.parametrize("env", ["solt1", "solt2"], indirect=True)
def test_qualified_good_measurement_cannot_become_automatic_pass(env, documents):
    if env[5]["status"] == "ready":
        env[5]["status"] = "qualified"  # A verification warning can qualify an otherwise ready chain.
        env[4]["qualification_reason"] = "Проверка метода без автоматической приёмки"
    view = review(env)
    response = record(env, view)
    assert response.status_code == 409, response.text
    assert "автоматическую приёмку" in response.json()["detail"]
    assert counts(env) == (0, 0)
    assert get_view(env, view)["state"] == "review" and create(env).status_code == 409


def test_ready_good_measurement_records_pass(env, documents):
    response = record(env, review(env))
    assert response.status_code == 200, response.text
    measured = response.json()["measurement"]
    assert measured["verdict"] == "pass" and measured["complete"]
    assert counts(env) == (1, 1)


def test_full_norm_band_still_required_for_session_evidence(env, documents):
    response = record(env, review(env, short=True))
    assert response.status_code == 200, response.text
    measured = response.json()["measurement"]
    assert measured["verdict"] == "fail" and not measured["complete"]


@pytest.mark.parametrize("change", ["limit", "band", "customer", "ends"])
def test_changed_applicable_norm_requires_remeasurement(env, documents, change):
    view = review(env)
    norm = documents[0]["coaxVswrNorm"]
    if change == "customer":
        documents[1]["coaxVswrNorm"] = {"source": "customer", "customer": {
            "vswrMax": 1.3, "fromMhz": 1, "toMhz": 6000, "basis": "Новая норма заказчика"}}
    else:
        norm.update({"vswrMax": 1.3} if change == "limit" else
                    {"bandMhz": [1, 7000]} if change == "band" else {"requiredEnds": ["A"]})
    response = record(env, view)
    assert response.status_code == 409, response.text
    assert "повторный замер" in response.json()["detail"]
    assert counts(env) == (0, 0)
    assert get_view(env, view)["state"] == "review" and create(env).status_code == 409


def test_record_rejects_stale_version_and_unfinished_session(env, documents):
    view = start(env)
    assert record(env, view).status_code == 409
    view = advance_until(env, view, "review")
    assert record(env, view, expected_version=view["version"] - 1).status_code == 409
    assert counts(env) == (0, 0)


@pytest.mark.parametrize("field,value", [
    ("content", "abc"), ("file_base64", "YWJj"), ("calibration", {"status": "COR"}),
    ("capture_settings", {}), ("norm", {}), ("product_position", 2), ("port_mapping", {"1": "B", "2": "A"}),
])
def test_browser_cannot_supply_session_evidence(env, field, value):
    assert record(env, create(env).json(), **{field: value}).status_code == 422


def test_record_requires_qc_permission_and_browser_csrf(env):
    view = create(env).json()
    url = f"{URL}/{view['id']}/record"
    payload = {"expected_version": view["version"]}
    assert TestClient(app).post(url, json=payload, headers=env[1]).status_code == 403
    operator = _login("operator")
    assert operator.post(url, json=payload, headers=_csrf(operator)).status_code == 403
    assert env[0].post(url, json=payload).status_code == 403


def test_qc_editor_can_record_another_operators_completed_session(env, documents):
    view = review(env)
    with env[3]() as session:
        VnaSessionRepository(session).get(UUID(view["id"])).operator_id = OPERATOR_ID
        session.commit()
    response = record(env, view)
    assert response.status_code == 200, response.text
    assert response.json()["measurement"]["inspector"]["id"] == str(ADMIN_ID)
    assert response.json()["session"]["operator_id"] == str(OPERATOR_ID)


def test_new_session_with_identical_bytes_is_a_new_attempt(env, documents):
    first = record(env, review(env))
    second = record(env, review(env))
    assert first.status_code == second.status_code == 200
    assert first.json()["measurement"]["id"] != second.json()["measurement"]["id"]
    assert second.json()["measurement"]["attempt"] == 2 and counts(env) == (2, 2)


def test_registry_instrument_swap_cannot_relabel_session_evidence(env, documents):
    view = review(env)
    with env[3]() as session:
        session.get(ProductionInstrumentRow, UUID(view["instrument_id"])).serial_number = "other-instrument"
        session.commit()
    response = record(env, view)
    assert response.status_code == 409, response.text
    assert "Прибор сессии изменился" in response.json()["detail"]
    assert counts(env) == (0, 0) and get_view(env, view)["state"] == "review"


@pytest.mark.parametrize("failure", ["mark", "commit", "checksum"])
def test_attempt_audit_and_session_transition_roll_back_together(env, documents, monkeypatch, failure):
    view = review(env)
    if failure == "checksum":
        with env[3]() as session:
            session.get(VnaArtifactRow, UUID(view["artifacts"][0]["id"])).content = b"corrupted"
            session.commit()
    else:
        def fail(*args, **kwargs):
            raise ValueError("Запись намеренно прервана")
        if failure == "mark":
            original = VnaSessionRepository.mark_recorded

            def mark_then_fail(*args, **kwargs):
                original(*args, **kwargs)
                fail()
            monkeypatch.setattr(VnaSessionRepository, "mark_recorded", mark_then_fail)
        else:
            from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
            monkeypatch.setattr(SqlAlchemyProductionUnitOfWork, "commit", fail)
    response = record(env, view)
    assert response.status_code == 422, response.text
    assert counts(env) == (0, 0)
    # Read without lazy-watchdog commits (the commit itself may be the injected failure).
    with env[3]() as session:
        row = VnaSessionRepository(session).get(UUID(view["id"]))
        assert row.state == "review" and not row.recorded_reference and row.version == view["version"]


def test_review_discard_releases_lease_without_agent_commands(env):
    view = review(env)
    response = action(env, view, "abort")
    assert response.status_code == 200, response.text
    discarded = response.json()
    assert discarded["state"] == "aborted" and discarded["commands"] == view["commands"]
    assert poll(env).json()["job"] is None
    assert record(env, discarded).status_code == 409
    assert counts(env) == (0, 0) and create(env).status_code == 200


def test_unclaimed_prepare_abort_cancels_locally_and_rejects_late_claim(env):
    view = start(env)
    with env[3]() as session:
        repository = VnaSessionRepository(session)
        pending = repository.pending("pc-1")
        response = action(env, view, "abort")
        assert response.status_code == 200, response.text
        discarded = response.json()
        assert discarded["state"] == "aborted"
        assert [(c["op"], c["status"]) for c in discarded["commands"]] == [("prepare", "cancelled")]
        with pytest.raises(SessionConflict):
            repository.claim(pending, env[2].now)
    assert send(env, view["commands"][0]).status_code == 409
    assert poll(env).json()["job"] is None and create(env).status_code == 200


def test_claim_between_operator_read_and_abort_retains_recovery(env):
    view = start(env)
    with env[3]() as session:
        repository = VnaSessionRepository(session)
        stale = repository.get(UUID(view["id"]))
        prepare = poll(env).json()["job"]
        repository.act(stale, "abort", view["version"], view["step"], env[2].now)
        assert stale.state == "aborting"
        session.commit()
    assert send(env, prepare).status_code == 409
    assert create(env).status_code == 409
    assert poll(env).json()["job"]["op"] == "abort"


def test_pending_prepare_with_claim_history_is_not_treated_as_untouched(env):
    view = start(env)
    with env[3]() as session:
        session.get(VnaCommandRow, UUID(view["commands"][0]["id"])).claimed_at = env[2].now - timedelta(seconds=1)
        session.commit()
    assert action(env, view, "abort").json()["state"] == "aborting"
    assert create(env).status_code == 409
