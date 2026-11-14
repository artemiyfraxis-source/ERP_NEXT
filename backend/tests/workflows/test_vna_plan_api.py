from dataclasses import asdict, replace
import base64
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.shared.persistence import AuditEventRow
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.rf_interfaces import propose_details
from app.workflows.coax_vswr_api import CoaxRefs
from app.workflows.rf_interfaces_fill import fill
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_instruments_api import create
from tests.production.test_vna_planner import fixture, one_port_fixture
from tests.workflows.test_coax_vswr_api import NORM, _as, _card, _launch


def _rf_url(item):
    return f"/api/production/instruments/{item['id']}/rf-interfaces"


def _confirmed():
    proposal = propose_details("СИ.002", "Planar S50180")
    return asdict(replace(proposal, confirmed=True, max_points=100001,
                         interfaces=tuple(replace(p, f_confirmed=True) for p in proposal.interfaces)))


def test_rf_confirmation_version_and_permissions(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    item = create(client, name="Planar S50180", number="СИ.002")
    assert client.get(_rf_url(item)).json()["rf"]["version"] == 0
    payload = _confirmed()
    response = client.put(_rf_url(item), json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["rf"]["confirmed"] and response.json()["rf"]["version"] == 1
    assert client.put(_rf_url(item), json=payload).status_code == 409
    assert client.get(_rf_url(item)).json()["rf"]["version"] == 1
    with app.state.production_uow_factory() as uow:
        assert len(list(uow.session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.rf_interfaces_updated")))) == 1
    _as({"quality": "view", "production": "view"})
    assert client.put(_rf_url(item), json={**payload, "version": 1}).status_code == 403
    assert client.get(_rf_url(item)).status_code == 200


@pytest.mark.parametrize("changes", [
    {"source": " "}, {"interfaces": []}, {"max_points": 1}, {"role": "guess"},
    {"interfaces": [{"series": "N", "body": "розетка", "center": "гнездо"}] * 3},
])
def test_invalid_confirmations_are_rejected(production_api, changes):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client)
    response = client.put(_rf_url(item), json={**_confirmed(), **changes})
    assert response.status_code == 422, response.text
    assert client.get(_rf_url(item)).json()["rf"]["version"] == 0


def test_fill_dry_run_apply_and_existing_proposals_are_preserved(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client, number="СИ.002", name="Planar S50180")
    log = []
    with app.state.production_uow_factory() as uow:
        assert fill(uow.session, emit=log.append) == 1
        assert not RfRepository(uow.session).list()
        assert fill(uow.session, apply=True, emit=log.append) == 1
        uow.commit()
    saved = client.get(_rf_url(item)).json()["rf"]
    assert not saved["confirmed"] and not any(p["f_confirmed"] for p in saved["interfaces"])
    with app.state.production_uow_factory() as uow:
        assert fill(uow.session, apply=True, emit=log.append) == 0
        uow.commit()
    assert client.get(_rf_url(item)).json()["rf"] == saved
    payload = {**_confirmed(), "version": saved["version"]}
    assert client.put(_rf_url(item), json=payload).status_code == 200
    with app.state.production_uow_factory() as uow:
        assert fill(uow.session, apply=True, emit=log.append) == 0
    assert client.get(_rf_url(item)).json()["rf"]["confirmed"]


def _plan_setup(client, monkeypatch, series=None, cables=2, split_adapters=False):
    _as({"quality": "edit", "production": "edit"})
    card = _card({"coaxVswrNorm": NORM}, config={"coax": {"sideA": {"article": "SMA-J-1"}, "sideB": {"article": "SMA-P-2"}}})
    task = _launch(client, card)
    vna, cable, kit = fixture()
    registry_assets = ((vna, "СИ.002"), (cable, "СИ.100"), (kit, "СИ.003"))
    if series:
        vna, cable, adapter, load, kit, acm = one_port_fixture(series, cables=cables)
        registry_assets = ((vna, "СИ.002"), (cable, "СИ.100"), (adapter, "СИ.020"), (load, "СИ.004"), (kit, "СИ.003"), (acm, "СИ.104"))
        if split_adapters:
            registry_assets = tuple((replace(a, quantity=1) if a is adapter else a, n) for a, n in registry_assets)
            registry_assets += ((replace(adapter, quantity=1, id=uuid4()), "СИ.021"),)
    instrument = create(client, number="СИ.002", name="Planar S50180", status="Действует",
                        verification_number="ПВ-2026", verified_at="2026-01-01T00:00:00", verification_due="2027-01-01T00:00:00")
    for rfasset, number in registry_assets:
        item = instrument if rfasset is vna else create(client, number=number, name=rfasset.label,
                    kind="Принадлежность", parent_id=instrument["id"], quantity=rfasset.quantity, status="Действует",
                    verification_number=None, verified_at=None, verification_due=None)
        payload = asdict(replace(rfasset.rf, max_points=100001 if rfasset is vna else None))
        response = client.put(_rf_url(item), json=payload)
        assert response.status_code == 200, response.text
    rows = tuple({"coaxArticle": article, "coaxType": f"{series or 'SMA'} male", "Волновое сопротивление, Ом": "50", "Частота до, ГГц": "18"}
                 for article in ("SMA-J-1", "SMA-P-2"))
    coax_card = SimpleNamespace(name="RG-316", name_key="RG316", aliases=(), params={"velocity_pct": 70, "impedance_ohm": 50})
    monkeypatch.setattr(CoaxRefs, "get", lambda self: (rows, (coax_card,), ()))
    # The exact-card lookup is exercised separately by the references suite; retain the workflow composition here.
    monkeypatch.setattr("app.workflows.vna_plan_api.cable_card", lambda cards, name: cards[0])
    params = dict(task_id=task["id"], operation_id=task["operations"][0]["id"], instrument_id=instrument["id"])
    return params


def test_plan_get_is_read_only_and_validates_task_operation(production_api, monkeypatch):
    client, _ = production_api
    params = _plan_setup(client, monkeypatch)
    with app.state.production_uow_factory() as uow:
        before = list(uow.session.scalars(select(AuditEventRow.id)))
    result = client.get("/api/production/vna-plan", params=params)
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["status"] == "ready", plan
    assert plan["profile"]["limit_line"]["vswr_max"] == NORM["vswrMax"]
    assert len(plan["chains"]["measurement_connections"]) == 2
    with app.state.production_uow_factory() as uow:
        assert list(uow.session.scalars(select(AuditEventRow.id))) == before
    for field in params:
        assert client.get("/api/production/vna-plan", params={**params, field: str(uuid4())}).status_code == 404
    _as({})
    assert client.get("/api/production/vna-plan", params=params).status_code == 403


def test_unknown_acm_and_missing_card_properties_never_claim_ready(production_api, monkeypatch):
    client, _ = production_api
    params = _plan_setup(client, monkeypatch)
    monkeypatch.setattr(CoaxRefs, "get", lambda self: ((), (), ()))
    monkeypatch.setattr("app.workflows.vna_plan_api.cable_card", lambda cards, name: None)
    response = client.get("/api/production/vna-plan", params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "blocked" and not result["automatic_acceptance"]
    assert result["profile"]["problems"] and result["chains"]["problems"]


def test_current_customer_norm_overrides_pinned_profile_without_replacing_assembly(production_api, monkeypatch):
    from tests.workflows.test_coax_vswr_customer_api import CUSTOMER_NORM, CUSTOMER, _issue_new_revision
    client, _ = production_api
    params = _plan_setup(client, monkeypatch)
    with app.state.production_uow_factory() as uow:
        task = uow.production.get(UUID(params["task_id"]))
    norm = {**CUSTOMER_NORM, "vswrMax": 1.25, "bandMhz": [1200, 2400],
            "customer": {**CUSTOMER, "vswrMax": 1.25, "fromMhz": 1200, "toMhz": 2400}}
    _issue_new_revision(task.techcard_id, {"coaxVswrNorm": norm})
    result = client.get("/api/production/vna-plan", params=params)
    assert result.status_code == 200, result.text
    profile = result.json()["profile"]
    assert profile["band_mhz"] == [1200, 2400] and profile["limit_line"]["vswr_max"] == 1.25
    assert profile["norm"]["lengthM"] == NORM["lengthM"]
    assert "ревизии 2" in profile["norm"]["basis"]


@pytest.mark.parametrize("cables,content,deembed", [
    (1, b"# GHz S RI R 50\n.001 .1 0 .9 0 .9 0 .1 0\n18 .1 0 .9 0 .9 0 .1 0", True),
    (2, b"# GHz S RI R 50\n.001 .1 0 .9 0 .9 0 .1 0\n18 .1 0 .9 0 .9 0 .1 0", True),
    (1, b"# GHz S RI R 50\n.001 .1 0\n18 .1 0", False),
    (1, b"# GHz S RI R 50\n.001 .1 0 .9 0 .9 0 .1 0\n1 .1 0 .9 0 .9 0 .1 0", False),
])
def test_characterization_file_is_checked_and_one_port_profile_requires_both_orientations(production_api, monkeypatch, cables, content, deembed):
    client, _ = production_api
    params = _plan_setup(client, monkeypatch, series="BNC", cables=cables, split_adapters=True)
    for adapter in client.get("/api/production/instruments").json()["instruments"]:
        if adapter["inventory_number"] in ("СИ.020", "СИ.021"):
            response = client.post(f"/api/production/instruments/{adapter['id']}/files", json={
                "kind": "Прочее", "file_name": "adapter.s2p", "content_base64": base64.b64encode(content).decode()})
            assert response.status_code == 200, response.text
    result = client.get("/api/production/vna-plan", params=params)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "qualified" and not data["automatic_acceptance"]
    assert ("де-эмбеддинг" in data["chains"]["method"]) == deembed
    if cables == 1:
        assert data["profile"]["traces"] == ["S11"]
        assert data["profile"]["required_orientations"] == ["А", "Б"]
        assert data["profile"]["one_attempt"]
        assert all(o["termination"]["load_label"] for o in data["chains"]["orientations"])


def test_owner_confirmed_acm_fill_keeps_unknown_band_explicit(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client, number="СИ.104", name="АКМ ACM2520-111")
    with app.state.production_uow_factory() as uow:
        assert fill(uow.session, apply=True, emit=lambda line: None) == 1
        uow.commit()
    rf = client.get(_rf_url(item)).json()["rf"]
    assert rf["confirmed"] and rf["f_min_ghz"] is None
    assert all(p["series"] == "3.5mm" and p["body"] == "розетка" and not p["f_confirmed"] for p in rf["interfaces"])
