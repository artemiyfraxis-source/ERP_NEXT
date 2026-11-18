"""ОТК: замер КСВН и паспорт коаксиальной сборки по HTTP. Норма — из закреплённой ревизии ЭТК задания, не от клиента."""

import base64
from datetime import datetime
import pytest
from uuid import UUID, uuid4

from app.main import app
from app.modules.references.domain import CABLE_TABS
from app.modules.techcards.project_cards import ProjectTechcard, TechcardRevision
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.production.test_api import MASTER_ID, production_api  # noqa: F401
from tests.production.test_job_flow_api import OPS

NOW = datetime(2026, 9, 22, 9, 0)
NORM = {
    "status": "ok", "reason": "", "vswrMax": 1.35, "bandMhz": [1000, 3000], "s21Db": 0.9, "lengthM": 0.5,
    "cable": {"name": "RG-316", "lossDbM": 1.2},
    "ends": [
        {"side": "A", "article": "SMA-J-1", "vswrText": "1,2 до 6 ГГц", "vswr": 1.2, "maxGhz": 6.0},
        {"side": "B", "article": "SMA-P-2", "vswrText": "1,25 до 6 ГГц", "vswr": 1.25, "maxGhz": 6.0},
    ],
}
UNDEFINED = {"status": "undefined", "reason": "Паспортной нормы для этого класса нет", "vswrMax": None}


def _csv(points):
    lines = ["Частота, МГц;КСВН"] + [f"{mhz:g};{vswr:.4f}".replace(".", ",") for mhz, vswr in points]
    return "\n".join(lines).encode("utf-8")


def _both(points):
    return ("# MHz S RI R 50\n" + "\n".join(
        f"{mhz} {(vswr-1)/(vswr+1)} 0 .8 0 .8 0 {(vswr-1)/(vswr+1)} 0" for mhz, vswr in points
    )).encode()


GOOD = _both([(900, 2.5), (1000, 1.10), (2400, 1.30), (3000, 1.20)])
BAD = _both([(1000, 1.10), (1800, 1.42), (3000, 1.20)])
WORSE = _both([(1000, 1.10), (1800, 1.62), (3000, 1.20)])


def _as(permissions, name="Контролёр"):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=MASTER_ID, name=name, department="ОТК"),
        email="qc@example.test", roles=frozenset({"qc"}), permissions=permissions,
    )


def _card(extra: dict | None, index="КБ1", config: dict | None = None):
    card = ProjectTechcard.create(
        project="591K.3", item_index=index, product_name="Кабель ВЧ", source_calculation="", deadline=None, now=NOW,
    ).rebuild({
        "operations": [{"code": f"OP-{i}", "connector": str(i), "side": ""} for i, *_ in enumerate(OPS, 1)],
        "build_config": {"ops": [
            {"type": "assembly", "stage": stage, "opNumber": code, "tOp": t_op, "tPrep": t_prep}
            for code, stage, t_op, t_prep in OPS
        ], **(config or {})},
        "kit": {"part_quantity": "10"}, **(extra or {}),
    }, NOW).issue(NOW)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    return card


def _launch(client, card, key="vswr-1", quantity=3):
    response = client.post("/api/production/tasks", headers={"Idempotency-Key": key},
                           json={"techcard_id": str(card.id), "quantity": quantity})
    assert response.status_code == 200, response.text
    return response.json()["task"]


def _instrument(client):
    response = client.post("/api/production/instruments", json={
        "inventory_number": "ВАЦ-01", "name": "Arinst VNA-DL", "instrument_type": "ВАЦ",
        "verification_number": "ПВ-2026-17", "verified_at": "2026-09-01T00:00:00",
        "verification_due": "2027-09-01T00:00:00",
    })
    assert response.status_code == 200, response.text
    return response.json()["instrument"]


def _body(task, instrument, content, position=1, name="sn1.csv", **extra):
    return {"task_id": task["id"], "position": position, "instrument_id": instrument["id"],
            "file_name": name, "file_base64": base64.b64encode(content).decode(), **extra}


def _setup(client, norm=NORM):
    _as({"quality": "edit", "production": "edit"})
    card = _card({"coaxVswrNorm": norm} if norm is not None else None)
    return card, _launch(client, card), _instrument(client)


def test_capture_settings_are_kept_in_existing_measurement_metadata(production_api):
    client, _ = production_api
    _, task, instrument = _setup(client)
    settings = {"points": 4, "smoothing_enabled": False, "correction_enabled": True,
                "correction_status": "COR", "s_parameter": "S11", "unavailable": ["averaging_factor"]}
    response = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, capture_settings=settings))
    assert response.status_code == 200, response.text
    measurement = response.json()["measurement"]
    assert measurement["file_meta"]["capture_settings"] == settings
    serials = client.get(f"/api/production/vswr/tasks/{task['id']}").json()["serials"]
    assert serials[0]["latest"]["file_meta"]["capture_settings"] == settings


@pytest.mark.parametrize(("settings", "reason"), [
    ({"correction_status": "C?"}, "«К?»"), ({"correction_status": "RC?"}, "«К?»"),
    ({"correction_status": "C!"}, "«К!»"), ({"correction_status": "RC!"}, "«К!»"),
    ({"correction_status": "COR", "smoothing_enabled": True}, "сглаживание"),
])
def test_interpolated_calibration_or_smoothing_is_refused_not_warned(production_api, settings, reason):
    """СТО ФРАК-002-2026 п. 11.1, 11.5: «К?», «К!» и сглаживание — замер не принимается, попытка не создаётся."""
    client, _ = production_api
    _, task, instrument = _setup(client)
    response = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, capture_settings={**settings, "s_parameter": "S11"}))
    assert response.status_code == 422, response.text
    assert response.json()["detail"].startswith("Замер не принят: ") and reason in response.json()["detail"]
    assert client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1").json()["measurements"] == []


def test_verified_capture_with_smoothing_is_refused_whatever_the_client_sends(production_api):
    client, _ = production_api
    _, task, instrument = _setup(client)
    job_id = _capture_job(instrument, GOOD, capture_settings={"smoothing_enabled": True, "s_parameter": "S11"})
    response = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, capture_job_id=job_id,
                                                              capture_settings={"smoothing_enabled": False}))
    assert response.status_code == 422 and "сглаживание" in response.json()["detail"]


def test_two_end_api_result_mapping_replay_and_passport(production_api):
    client, _ = production_api
    _, task, instrument = _setup(client)
    content = b"# MHz S RI R 50\n1000 .05 0 .8 0 .8 0 .3 0\n3000 .05 0 .8 0 .8 0 .3 0"
    payload = _body(task, instrument, content, name="both.s2p", port_mapping={"1": "B", "2": "A"})
    response = client.post("/api/production/vswr", json=payload)
    assert response.status_code == 200, response.text
    measured = response.json()["measurement"]
    assert measured["verdict"] == "fail" and measured["complete"]
    assert measured["port_mapping"] == {"1": "B", "2": "A"}
    assert [(r["end"], r["trace_name"], r["verdict"]) for r in measured["end_results"]] == [
        ("A", "S22", "fail"), ("B", "S11", "pass"),
    ]
    assert {r["name"] for r in measured["traces"]} == {"S11", "S22", "S21", "S12"}
    assert client.post("/api/production/vswr", json=payload).json()["measurement"] == measured
    passport = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport").json()
    assert passport["checks"][0]["attempts"] == 1
    assert passport["checks"][0]["measurement"] == measured


def _end_file(end, vswr=1.2, **extra):
    gamma = (vswr - 1) / (vswr + 1)
    content = f"! Arinst VNA-DL\n# MHz S RI R 50\n1000 {gamma} 0\n3000 {gamma} 0\n".encode()
    return {"end": end, "file_name": f"{end}.s1p", "file_base64": base64.b64encode(content).decode(), **extra}


def _pair_body(task, instrument, files):
    return {"task_id": task["id"], "position": 1, "instrument_id": instrument["id"], "files": files}


@pytest.mark.parametrize("b_vswr,verdict", [(1.2, "pass"), (1.5, "fail")])
def test_one_port_pair_api_preserves_both_files_and_replays_one_attempt(production_api, b_vswr, verdict):
    client, _ = production_api
    _, task, instrument = _setup(client)
    files = [_end_file("A"), _end_file("B", b_vswr)]
    payload = _pair_body(task, instrument, files)
    response = client.post("/api/production/vswr", json=payload)
    assert response.status_code == 200, response.text
    measured = response.json()["measurement"]
    assert measured["verdict"] == verdict and measured["complete"]
    assert [(r["end"], r["trace_name"], r["verdict"]) for r in measured["end_results"]] == [
        ("A", "A:S11", "pass"), ("B", "B:S11", verdict),
    ]
    assert [r["name"] for r in measured["traces"]] == ["A:S11", "B:S11"]
    assert client.post("/api/production/vswr", json={**payload, "files": files[::-1]}).json()["measurement"] == measured
    for uploaded, stored in zip(files, measured["files"]):
        download = client.get(stored["download_url"])
        assert download.status_code == 200 and download.content == base64.b64decode(uploaded["file_base64"])
        assert uploaded["file_name"] in download.headers["content-disposition"]
    passport = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport").json()
    assert passport["checks"][0]["measurement"] == measured and passport["checks"][0]["attempts"] == 1
    # Old download remains usable and matches the file of the governing reflection end.
    primary = client.get(f"/api/production/vswr/measurements/{measured['id']}/file")
    expected = files[1] if verdict == "fail" else files[0]
    assert primary.content == base64.b64decode(expected["file_base64"])
    _as({"myops": "edit"})
    assert client.get(measured["files"][0]["download_url"]).status_code == 403


@pytest.mark.parametrize("case", ["duplicate", "mixed", "legacy_and_files", "missing_end", "three_files", "invalid_base64"])
def test_invalid_one_port_sets_are_rejected_before_recording(production_api, case):
    client, _ = production_api
    _, task, instrument = _setup(client)
    files = [_end_file("A"), _end_file("B")]
    if case == "duplicate":
        files[1]["end"] = "A"
    elif case == "mixed":
        files[0] = {"file_name": "both.s2p", "file_base64": base64.b64encode(GOOD).decode()}
    elif case == "missing_end":
        del files[1]["end"]
    elif case == "three_files":
        files.append(_end_file("A"))
    elif case == "invalid_base64":
        files[1]["file_base64"] = "not_base64"
    payload = _pair_body(task, instrument, files)
    if case == "legacy_and_files":
        payload.update(file_name="also.csv", file_base64=base64.b64encode(GOOD).decode())
    response = client.post("/api/production/vswr", json=payload)
    assert response.status_code == 422, response.text
    assert client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1").json()["measurements"] == []


def test_single_explicit_end_is_incomplete_and_missing_download_is_not_invented(production_api):
    client, _ = production_api
    _, task, instrument = _setup(client)
    response = client.post("/api/production/vswr", json=_pair_body(task, instrument, [_end_file("B")]))
    assert response.status_code == 200, response.text
    measured = response.json()["measurement"]
    assert measured["verdict"] == "fail" and not measured["complete"]
    assert "Не измерен конец А" in measured["verdict_reason"]
    assert client.get(f"/api/production/vswr/measurements/{measured['id']}/files/A").status_code == 404
    assert client.get(f"/api/production/vswr/measurements/{measured['id']}/files/C").status_code == 422


def _capture_job(instrument, content, **changes):
    from app.modules.warehouse.scan_agents import ScanAgentRow
    from app.modules.warehouse.scan_jobs import ScanJobRow

    job_id = uuid4()
    with app.state.production_uow_factory() as uow:
        if uow.session.get(ScanAgentRow, "capture-pc") is None:
            uow.session.add(ScanAgentRow(machine_id="capture-pc", first_seen=NOW, last_seen=NOW, legacy=False))
        uow.session.flush()
        values = dict(id=job_id, status="done", kind="vna", source="s2vna", requested_by=str(MASTER_ID),
            created_at=NOW, claimed_at=NOW, finished_at=NOW, target_machine_id="capture-pc",
            instrument_id=UUID(instrument["id"]), capture_content=content,
            capture_settings={"smoothing_enabled": False, "correction_status": "COR", "s_parameter": "S11"})
        values.update(changes)
        uow.session.add(ScanJobRow(**values))
        uow.commit()
    return str(job_id)


@pytest.mark.parametrize("case", ["valid", "same_capture", "foreign_owner"])
def test_each_one_port_capture_is_independently_authorized_and_counted_once(production_api, case):
    client, _ = production_api
    _, task, instrument = _setup(client)
    a, b = _end_file("A"), _end_file("B")
    a["capture_job_id"] = _capture_job(instrument, base64.b64decode(a["file_base64"]))
    b["capture_job_id"] = a["capture_job_id"] if case == "same_capture" else _capture_job(
        instrument, base64.b64decode(b["file_base64"]), **({"requested_by": str(uuid4())} if case == "foreign_owner" else {}),
    )
    payload = _pair_body(task, instrument, [a, b])
    response = client.post("/api/production/vswr", json=payload)
    if case == "valid":
        assert response.status_code == 200, response.text
        measured = response.json()["measurement"]
        assert measured["complete"] and measured["verdict"] == "pass"
        assert [f["file_meta"]["evidence_id"] for f in measured["files"]] == [a["capture_job_id"], b["capture_job_id"]]
        assert client.post("/api/production/vswr", json=payload).json()["measurement"] == measured
    else:
        assert response.status_code == 422
        assert client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1").json()["measurements"] == []


def test_neither_client_settings_nor_verified_capture_bypass_customer_coverage(production_api):
    client, _ = production_api
    _, task, instrument = _setup(client, {**NORM, "source": "customer"})
    content = _csv([(1500, 1.1), (2500, 1.2)])
    for settings in ({}, {"s_parameter": "S11"}):
        response = client.post("/api/production/vswr", json=_body(task, instrument, content, capture_settings=settings))
        assert response.status_code == 200
        assert response.json()["measurement"]["verdict"] == "fail"
        assert "не покрывает" in response.json()["measurement"]["verdict_reason"]
    job_id = _capture_job(instrument, content)
    response = client.post("/api/production/vswr", json=_body(task, instrument, content, position=2,
        capture_job_id=job_id, capture_settings={"smoothing_enabled": False}))
    assert response.status_code == 200, response.text
    measurement = response.json()["measurement"]
    assert measurement["band_mhz"] == [1000, 3000]
    assert measurement["verdict"] == "fail" and not measurement["complete"]
    assert "не покрывает" in measurement["file_meta"]["capture_warnings"][0]
    assert measurement["file_meta"]["capture_settings"]["correction_status"] == "COR"  # the job's, not the client's


@pytest.mark.parametrize("case", ["missing", "owner", "instrument", "bytes", "pending"])
def test_capture_job_linkage_is_checked_before_upload(production_api, case):
    client, _ = production_api
    _, task, instrument = _setup(client)
    changes = {}
    if case == "owner":
        changes["requested_by"] = str(uuid4())
    if case == "instrument":
        changes["instrument_id"] = None
    if case == "bytes":
        changes["capture_content"] = BAD
    if case == "pending":
        changes.update(status="pending", claimed_at=None, finished_at=None, capture_content=None)
    job_id = str(uuid4()) if case == "missing" else _capture_job(instrument, GOOD, **changes)
    response = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, capture_job_id=job_id))
    assert response.status_code == 422
    assert "завершённому заданию ВАЦ" in response.json()["detail"]


def test_unverified_instrument_cannot_record_vswr(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, _active = _setup(client)
    with app.state.production_uow_factory() as uow:
        serials_before = uow.serials.list_for_task(UUID(task["id"]))
    response = client.post("/api/production/instruments", json={
        "inventory_number": "TEST-CABLE.002", "name": "Тестовый прибор", "instrument_type": "Тестер",
        "status": "Поверка не подтверждена",
    })
    assert response.status_code == 200, response.text
    pending = response.json()["instrument"]
    refused = client.post("/api/production/vswr", json=_body(task, pending, GOOD))
    assert refused.status_code == 422 and "не подтверждена" in refused.json()["detail"]
    with app.state.production_uow_factory() as uow:
        assert uow.serials.list_for_task(UUID(task["id"])) == serials_before


def test_passport_instrument_serializer_handles_unknown_verification():
    from app.modules.production.traceability import InstrumentStatus, MeasurementInstrument
    from app.workflows.coax_vswr_api import _instrument_result

    pending = MeasurementInstrument(
        id=uuid4(), version=1, inventory_number="TEST-CABLE.003", name="Тестовый прибор", instrument_type="Тестер",
        verification_number=None, verified_at=None, verification_due=None, status=InstrumentStatus.UNVERIFIED,
    )
    result = _instrument_result(pending)
    assert result["verification_number"] is None and result["verified_at"] is None and result["verification_due"] is None
    assert result["inventory_number"] == pending.inventory_number


def test_norm_comes_from_the_pinned_etk_revision_and_sets_the_verdict(production_api):  # noqa: F811
    client, _ = production_api
    card, task, instrument = _setup(client)
    # ЭТК получает новую ревизию с другой нормой — задание остаётся на своей (1.35).
    with app.state.techcards_uow_factory() as uow:
        current = uow.techcards.get(card.id)
        uow.techcards.add_revision(TechcardRevision(
            id=uuid4(), project_card_id=card.id, revision=current.revision, reason="Тест", actor_name="Технолог",
            occurred_at=NOW, document=current.document,
        ))
        updated = current.rebuild({**current.document, "coaxVswrNorm": {**NORM, "vswrMax": 1.1}}, NOW)
        uow.techcards.save(updated)
        uow.commit()

    good = client.post("/api/production/vswr", json=_body(task, instrument, GOOD))
    bad = client.post("/api/production/vswr", json=_body(task, instrument, BAD, position=2, name="sn2.csv"))

    assert good.status_code == 200, good.text
    measurement = good.json()["measurement"]
    assert measurement["verdict"] == "pass" and measurement["norm_vswr_max"] == 1.35
    assert measurement["max_vswr"] == 1.3 and measurement["max_vswr_mhz"] == 2400.0
    assert measurement["band_mhz"] == [1000.0, 3000.0] and measurement["attempt"] == 1
    assert measurement["inspector"]["name"] == "Контролёр" and measurement["trace"]
    assert bad.json()["measurement"]["verdict"] == "fail"


def test_client_cannot_pass_a_norm_or_override_the_verdict(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client)

    forged = client.post("/api/production/vswr", json=_body(task, instrument, BAD, norm={"vswrMax": 9}))
    override = client.post("/api/production/vswr", json=_body(task, instrument, BAD, verdict="pass", reason="надо"))

    assert forged.status_code == 422
    assert override.status_code == 422 and "задана" in override.json()["detail"]
    assert client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1").json()["measurements"] == []


def test_undefined_norm_needs_a_manual_verdict_with_a_reason(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client, UNDEFINED)

    band = {"band_from_mhz": 1000, "band_to_mhz": 3000}
    none = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, **band))
    no_reason = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, verdict="pass", **band))
    manual = client.post("/api/production/vswr", json=_body(
        task, instrument, GOOD, verdict="pass", reason="Норма по ТЗ заказчика 1,5", band_from_mhz=1000, band_to_mhz=3000))

    assert none.status_code == 422 and "вручную" in none.json()["detail"]
    assert no_reason.status_code == 422
    assert manual.status_code == 200, manual.text
    result = manual.json()["measurement"]
    assert result["verdict_manual"] is True and result["verdict_reason"] == "Норма по ТЗ заказчика 1,5"
    assert result["norm_vswr_max"] is None


COAX_CONFIG = {"coax": {
    "cableLength": 500, "cableArt": "RG-316", "cableName": "RG-316",
    "sideA": {"article": "SMA-J-1", "type": "SMA-male"}, "sideB": {"article": "SMA-P-2", "type": "SMA-female"},
}}
ROWS = (
    {"coaxArticle": "SMA-J-1", "КСВ": "≤1,2", "Частота до, ГГц": "6"},
    {"coaxArticle": "SMA-P-2", "КСВ": "≤1,25", "Частота до, ГГц": "6"},
)


class _Records:
    def __init__(self, rows):
        self.rows = rows

    def list(self, **_kwargs):
        return [type("Record", (), {"fields": row})() for row in self.rows]


class _FakeRefs:
    tech_operations = _Records(ROWS)
    cable_structure = type("Cables", (), {"cards": staticmethod(lambda: [])})()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _old_coax_setup(client, config=COAX_CONFIG, key="vswr-old"):
    _as({"quality": "edit", "production": "edit"})
    app.state.references_uow_factory = lambda: _FakeRefs()
    card = _card(None, config=config)
    return card, _launch(client, card, key=key), _instrument(client)


def _drop_refs():
    if hasattr(app.state, "references_uow_factory"):
        del app.state.references_uow_factory


def test_old_etk_with_a_coax_config_gets_a_computed_norm_and_an_automatic_verdict(production_api):  # noqa: F811
    client, _ = production_api
    try:
        _card_, task, instrument = _old_coax_setup(client)
        band = {"band_from_mhz": 1000, "band_to_mhz": 3000}
        full = _both([(1, 1.1), (2400, 1.3), (6000, 1.2)])
        good = client.post("/api/production/vswr", json=_body(task, instrument, full, **band))
        bad = client.post("/api/production/vswr", json=_body(task, instrument, WORSE, position=2, name="sn2.csv", **band))
        listed = client.get(f"/api/production/vswr/tasks/{task['id']}").json()
        passport = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport").json()
    finally:
        _drop_refs()

    assert good.status_code == 200, good.text
    measurement = good.json()["measurement"]
    assert measurement["norm"]["source"] == "computed" and measurement["norm_vswr_max"] == 1.51
    assert measurement["norm"]["basis"] == "рассчитана по справочникам на 22.09.2026 — ЭТК выпущена до нормы КСВН"
    assert measurement["verdict"] == "pass" and not measurement["verdict_manual"]
    assert bad.json()["measurement"]["verdict"] == "fail"
    assert listed["norm"]["status"] == "ok" and listed["norm"]["source"] == "computed"
    assert listed["control"] == {"mode": "all", "every": None} and listed["required"] == {"total": 3, "measured": 2}
    assert passport["norm"]["basis"].endswith("рассчитана по справочникам на 22.09.2026 — ЭТК выпущена до нормы КСВН")


class _CableRefs(_FakeRefs):
    """БД.КОАКС rows and the cable's row of the cable bases (БД.ПРОВ, read as a family since migration 0165)."""

    class _ByTab:
        def list(self, *, tab, **_kwargs):
            rows = ({"Марка": "RG316", "Стандарт": "MIL-DTL-17/113", "Частота до, ГГц": "3"},)                 if tab == CABLE_TABS else ROWS
            return [type("Record", (), {"fields": row})() for row in rows]

    tech_operations = _ByTab()


def test_the_cable_frequency_and_standard_come_from_the_coax_cable_list(production_api):  # noqa: F811
    """Owner 25.10.2026: «Частота до, ГГц» of the cable — the one the table shows — bounds the norm band; the passport
    prints the cable's «Стандарт»."""
    client, _ = production_api
    try:
        _card_, task, _instrument_ = _old_coax_setup(client)
        app.state.references_uow_factory = lambda: _CableRefs()
        listed = client.get(f"/api/production/vswr/tasks/{task['id']}").json()
        passport = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport").json()
    finally:
        _drop_refs()
    assert listed["norm"]["band_mhz"] == [1.0, 3000.0]  # the cable's 3 GHz, below the connectors' 6
    assert passport["product"]["cable"]["standard"] == "MIL-DTL-17/113"


def test_old_etk_computed_norm_takes_the_control_mode_from_the_config(production_api):  # noqa: F811
    client, _ = production_api
    config = {"coax": {**COAX_CONFIG["coax"], "vswrControl": {"mode": "sample", "every": 2}}}
    try:
        _card_, task, _instrument_ = _old_coax_setup(client, config)
        listed = client.get(f"/api/production/vswr/tasks/{task['id']}").json()
    finally:
        _drop_refs()
    assert listed["control"] == {"mode": "sample", "every": 2} and listed["required"]["total"] == 2


def test_old_etk_without_a_coax_config_is_not_a_coax_assembly(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client, None)

    refused = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, band_from_mhz=1000, band_to_mhz=3000))
    manual = client.post("/api/production/vswr", json=_body(
        task, instrument, GOOD, verdict="fail", reason="Норма не известна", band_from_mhz=1000, band_to_mhz=3000))
    listed = client.get(f"/api/production/vswr/tasks/{task['id']}").json()

    assert refused.status_code == 422
    assert manual.status_code == 200, manual.text
    assert manual.json()["measurement"]["norm"]["reason"] == "не коаксиальная сборка"
    assert listed["norm"] == {**listed["norm"], "status": "undefined", "reason": "не коаксиальная сборка"}


def test_task_list_has_only_coax_assemblies_with_counts(production_api):  # noqa: F811
    client, _ = production_api
    try:
        _card_, coax_task, instrument = _old_coax_setup(client)
        client.post("/api/production/vswr", json=_body(coax_task, instrument, GOOD))
        _launch(client, _card(None, index="КБ2"), key="plain-1", quantity=2)  # ЭТК без коаксиальной сборки — в список не попадает
        new_card = _card({"coaxVswrNorm": NORM}, index="КБ3", config=COAX_CONFIG)
        new_task = _launch(client, new_card, key="vswr-new", quantity=4)
        listed = client.get("/api/production/vswr/tasks")
    finally:
        _drop_refs()

    assert listed.status_code == 200, listed.text
    rows = {row["id"]: row for row in listed.json()["tasks"]}
    assert set(rows) == {coax_task["id"], new_task["id"]}
    old = rows[coax_task["id"]]
    assert old["number"] == coax_task["number"] and old["quantity"] == 3 and old["status"]
    assert old["norm"] == {"status": "ok", "vswr_max": 1.51, "source": "computed"}
    assert old["required"] == {"total": 3, "measured": 1}
    assert rows[new_task["id"]]["norm"]["vswr_max"] == 1.35 and rows[new_task["id"]]["required"] == {"total": 4, "measured": 0}


def test_task_list_hides_tasks_of_a_fully_shipped_kb(production_api):  # noqa: F811
    """Владелец 18.11: КБ уехала целиком (отгружено ≥ заказано) — её задание из «Замеров КСВН» уходит; уехала часть — остаётся."""
    from tests.production.test_otk_shipped import finished_good

    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    shipped = _launch(client, _card({"coaxVswrNorm": NORM}, index="КБ1", config=COAX_CONFIG), key="vswr-shipped")
    partly = _launch(client, _card({"coaxVswrNorm": NORM}, index="КБ2", config=COAX_CONFIG), key="vswr-partly")
    finished_good("591K.3", "КБ1", "3", "3")
    finished_good("591K.3", "КБ2", "4", "2")

    listed = client.get("/api/production/vswr/tasks")

    assert listed.status_code == 200, listed.text
    ids = {row["id"] for row in listed.json()["tasks"]}
    assert shipped["id"] not in ids and partly["id"] in ids


def test_parser_and_domain_errors_are_russian_422(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client)

    garbage = client.post("/api/production/vswr", json=_body(task, instrument, b"abc"))
    not_base64 = client.post("/api/production/vswr", json={**_body(task, instrument, GOOD), "file_base64": "!!!"})
    position = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, position=9))
    unknown = client.post("/api/production/vswr", json={**_body(task, instrument, GOOD), "task_id": str(uuid4())})

    assert garbage.status_code == 422 and "файл" in garbage.json()["detail"].lower()
    assert not_base64.status_code == 422 and "base64" in not_base64.json()["detail"]
    assert position.status_code == 422 and "изделия с 1 по 3" in position.json()["detail"]
    assert unknown.status_code == 404


def test_list_per_task_and_per_serial_and_file_download(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client)
    first = client.post("/api/production/vswr", json=_body(task, instrument, BAD)).json()["measurement"]
    second = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, name="повтор.csv")).json()["measurement"]

    listed = client.get(f"/api/production/vswr/tasks/{task['id']}")
    attempts = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1")
    stored = client.get(f"/api/production/vswr/measurements/{second['id']}/file")

    assert listed.status_code == 200, listed.text
    row = listed.json()["serials"][0]
    assert row["position"] == 1 and row["attempts"] == 2 and row["latest"]["id"] == second["id"]
    assert row["latest"]["verdict"] == "pass" and listed.json()["norm"]["vswr_max"] == 1.35
    assert [item["attempt"] for item in attempts.json()["measurements"]] == [1, 2]
    assert attempts.json()["measurements"][0]["id"] == first["id"]
    assert stored.status_code == 200 and stored.content == GOOD
    assert "attachment" in stored.headers["content-disposition"] and "%D0%BF%D0%BE%D0%B2%D1%82%D0%BE%D1%80" in stored.headers["content-disposition"]
    assert client.get(f"/api/production/vswr/measurements/{uuid4()}/file").status_code == 404


def test_passport_collects_the_product_etk_norm_measurement_instrument_and_inspector(production_api):  # noqa: F811
    client, _ = production_api
    card, task, instrument = _setup(client)
    client.post("/api/production/vswr", json=_body(task, instrument, GOOD))

    passport = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport")
    empty = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/2/passport")

    assert passport.status_code == 200, passport.text
    body = passport.json()
    assert body["product"]["serial_number"] == f"{task['number']}-0001"
    # no «Стандарт» of the cable in «Коаксиальные кабели»: none — the print says «—»
    assert body["product"]["cable"] == {"name": "RG-316", "loss_db_m": 1.2, "standard": None}
    assert body["product"]["length_m"] == 0.5
    assert [(end["side"], end["article"], end["vswr"], end["max_ghz"]) for end in body["product"]["ends"]] == [
        ("A", "SMA-J-1", 1.2, 6.0), ("B", "SMA-P-2", 1.25, 6.0)]
    assert body["etk"]["techcard_id"] == str(card.id) and body["etk"]["revision"] == 1
    assert body["norm"]["vswr_max"] == 1.35 and body["norm"]["band_mhz"] == [1000, 3000]
    assert body["norm"]["basis"] == "ЭТК КБ1, ревизия 1" and body["norm"]["status"] == "ok"
    [check] = body["checks"]
    assert check["kind"] == "vswr" and check["verdict_name"] == "Годно"
    assert check["measurement"]["max_vswr"] == 1.3 and check["measurement"]["file_name"] == "sn1.csv"
    assert check["measurement"]["trace"][0] == {"mhz": 900.0, "vswr": 2.5}
    assert check["instrument"]["inventory_number"] == "ВАЦ-01" and check["instrument"]["verification_number"] == "ПВ-2026-17"
    assert check["instrument"]["verification_due"].startswith("2027-09-01")
    assert body["inspector"]["name"] == "Контролёр" and body["inspected_at"].startswith("2026-09-22T09:00")
    # Изделие без замера: паспорт есть, проверок нет, норма — из ЭТК.
    assert empty.status_code == 200 and empty.json()["checks"] == [] and empty.json()["norm"]["vswr_max"] == 1.35
    assert client.get(f"/api/production/vswr/tasks/{task['id']}/serials/7/passport").status_code == 404


def test_rights_follow_the_existing_qc_endpoints(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client)
    body = _body(task, instrument, GOOD)
    recorded = client.post("/api/production/vswr", json=body).json()["measurement"]

    _as({"production": "edit"}, name="Мастер")  # правки «ОТК» нет — замер не пишет, читает
    refused = client.post("/api/production/vswr", json=body)
    read = client.get(f"/api/production/vswr/tasks/{task['id']}")
    download = client.get(f"/api/production/vswr/measurements/{recorded['id']}/file")
    _as({"myops": "edit"}, name="Монтажник")
    nobody = client.get(f"/api/production/vswr/tasks/{task['id']}")
    nobody_file = client.get(f"/api/production/vswr/measurements/{recorded['id']}/file")
    nobody_post = client.post("/api/production/vswr", json=body)
    _as({"production": "view"}, name="Наблюдатель")
    viewer = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport")
    viewer_post = client.post("/api/production/vswr", json=body)

    assert refused.status_code == 403 and "ОТК" in refused.json()["detail"]
    assert read.status_code == 200 and download.status_code == 200
    assert nobody.status_code == 403 and nobody_file.status_code == 403 and nobody_post.status_code == 403
    assert viewer.status_code == 200 and viewer_post.status_code == 403


def test_sample_control_marks_the_required_positions_and_counts_them(production_api):  # noqa: F811
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    card = _card({"coaxVswrNorm": {**NORM, "control": {"mode": "sample", "every": 2}}})
    task, instrument = _launch(client, card, key="vswr-sample", quantity=3), _instrument(client)
    client.post("/api/production/vswr", json=_body(task, instrument, GOOD))
    outside = client.post("/api/production/vswr", json=_body(task, instrument, BAD, position=2, name="sn2.csv"))

    listed = client.get(f"/api/production/vswr/tasks/{task['id']}").json()

    assert outside.status_code == 200, outside.text  # вне выборки мерить можно
    assert listed["control"] == {"mode": "sample", "every": 2}
    assert [(row["position"], row["in_sample"]) for row in listed["serials"]] == [(1, True), (2, False), (3, True)]
    assert listed["required"] == {"total": 2, "measured": 1}  # №1 и №3 в выборке, замерено №1 (№2 — вне выборки)


def test_without_the_setting_every_unit_is_required(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, _instrument_ = _setup(client)
    listed = client.get(f"/api/production/vswr/tasks/{task['id']}").json()
    assert listed["control"] == {"mode": "all", "every": None}
    assert listed["required"] == {"total": 3, "measured": 0} and all(row["in_sample"] for row in listed["serials"])


def test_passport_of_a_unit_outside_the_sample_refers_to_the_batch_sample(production_api):  # noqa: F811
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    card = _card({"build_config": {"ops": [], "coax": {"vswrControl": {"mode": "sample", "every": 2}}},
                  "coaxVswrNorm": NORM})
    task, instrument = _launch(client, card, key="vswr-passport", quantity=3), _instrument(client)
    client.post("/api/production/vswr", json=_body(task, instrument, BAD))
    client.post("/api/production/vswr", json=_body(task, instrument, GOOD, name="повтор.csv"))

    outside = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/2/passport").json()
    sampled = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport").json()
    waiting = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/3/passport").json()

    assert outside["control"] == {"mode": "sample", "every": 2} and outside["checks"] == []
    sample = outside["vswr_sample"]
    assert sample["every"] == 2 and [unit["position"] for unit in sample["units"]] == [1, 3]
    assert sample["units"][0]["verdict"] == "pass" and sample["units"][0]["serial_number"] == f"{task['number']}-0001"
    assert sample["units"][1]["verdict"] is None
    assert sampled["vswr_sample"] is None and sampled["checks"][0]["verdict"] == "pass"  # замерено — как раньше
    assert waiting["vswr_sample"] is None  # в выборке, но ещё не замерено — ссылки на выборку нет


def test_accessory_cannot_record_vswr_or_accept_qc(production_api):
    from decimal import Decimal
    from app.modules.production.traceability import ProductionBox, format_box_number

    client, operator_id = production_api
    _card_, task, parent = _setup(client)
    response = client.post("/api/production/instruments", json={
        "inventory_number": "СИ.002", "name": "Переходник", "instrument_type": "Переходник",
        "kind": "Принадлежность", "parent_id": parent["id"],
    })
    assert response.status_code == 200, response.text
    accessory = response.json()["instrument"]
    with app.state.production_uow_factory() as uow:
        serials_before = uow.serials.list_for_task(UUID(task["id"]))
        production = uow.production.get(UUID(task["id"]))
        box = ProductionBox.create(
            task_id=production.id, operation_id=production.final_operation.id, item=production.item,
            operation_position=production.final_operation.position, operator_id=operator_id, created_at=NOW,
        ).add_output(Decimal(1)).close(format_box_number(1), "Оператор", NOW)
        uow.boxes.save(box)
        uow.commit()
    vswr = client.post("/api/production/vswr", json=_body(task, accessory, GOOD))
    qc = client.post(f"/api/production/boxes/{box.id}/qc", json={
        "version": box.version, "accepted": 1, "rejected": 0, "instrument_ids": [accessory["id"]],
    })
    for refused in (vswr, qc):
        assert refused.status_code == 422, refused.text
        assert "принадлежность, не средство измерений" in refused.json()["detail"]
    with app.state.production_uow_factory() as uow:
        assert uow.qc.by_box(box.id) is None
        assert uow.serials.list_for_task(UUID(task["id"])) == serials_before
    assert client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1").json()["measurements"] == []
