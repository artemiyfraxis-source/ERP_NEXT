"""Instrument-equivalent permissions, registration audit and scoped immutable downloads."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.rf_kit_characterization_infrastructure import RfKitRepository
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.production.test_api import MASTER_ID, production_api  # noqa: F401
from tests.production.test_rf_kit_characterization_storage import append, seed_kit


def as_user(permissions):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=MASTER_ID, name="Контролёр", department="ОТК"), email="qc@example.test",
        roles=frozenset({"qc"}), permissions=permissions)


@pytest.fixture
def kit_api(production_api):
    client, _ = production_api
    with app.state.production_uow_factory() as uow:
        kit_id = seed_kit(uow.session)
        uow.commit()
    return client, kit_id, f"/api/production/instruments/{kit_id}/standards"


def body(**changes):
    return {"piece_label": "OPEN-F", "kind": "OPEN", "gender": "female", "f_min_hz": 10e6,
            "f_max_hz": 3e9, "expected_kit_version": 1, **changes}


@pytest.mark.parametrize("permissions,get_status,post_status", [
    ({}, 403, 403), ({"production": "view"}, 200, 403), ({"production": "edit"}, 200, 403),
    ({"production": "view", "quality": "view"}, 200, 403),
    ({"production": "view", "quality": "edit"}, 200, 201),
])
def test_permissions_match_instruments(kit_api, permissions, get_status, post_status):
    client, _, url = kit_api
    as_user(permissions)
    assert client.get(url).status_code == get_status
    assert client.post(url, json=body()).status_code == post_status


def test_anonymous_cannot_list_register_or_download(kit_api):
    client, _, url = kit_api
    app.dependency_overrides.pop(authenticated_principal)
    assert client.get(url).status_code == 401
    assert client.post(url, json=body()).status_code == 401
    assert client.get(f"{url}/{uuid4()}/revisions/{uuid4()}/file").status_code == 401


def test_registration_has_cas_audit_and_russian_domain_errors(kit_api):
    client, kit_id, url = kit_api
    as_user({"production": "view", "quality": "edit"})
    invalid = client.post(url, json=body(f_max_hz=3e9 + 1))
    assert invalid.status_code == 422 and "3 ГГц" in invalid.json()["detail"]
    response = client.post(url, json=body())
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["version"] == 2 and saved["generation"] == 0 and saved["hash"] is None
    assert saved["standards"][0]["current_revision"] is None
    assert client.post(url, json=body(piece_label="SHORT-F", kind="SHORT")).status_code == 409
    assert client.post(url, json=body(expected_kit_version=2)).status_code == 422
    with app.state.production_uow_factory() as uow:
        events = list(uow.session.scalars(select(AuditEventRow).where(AuditEventRow.aggregate_id == kit_id)))
        assert len(events) == 1 and events[0].event_type == "production.rf_kit_standard_registered"
        assert events[0].details["standard_id"] == saved["standards"][0]["id"]


def test_download_is_bound_to_kit_piece_and_revision_and_requires_read_permission(kit_api):
    client, kit_id, url = kit_api
    as_user({"production": "view", "quality": "edit"})
    assert client.post(url, json=body()).status_code == 201
    with app.state.production_uow_factory() as uow:
        repo = RfKitRepository(uow.session)
        standard = repo.standards(kit_id)[0]
        revision = append(repo, standard)
        standard_id, revision_id, content = standard.id, revision.id, revision.content
        uow.commit()
    download = f"{url}/{standard_id}/revisions/{revision_id}/file"
    as_user({"production": "view"})
    response = client.get(download)
    assert response.status_code == 200 and response.content == content
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(download.replace(str(kit_id), str(uuid4()))).status_code == 404
    assert client.get(download.replace(str(standard_id), str(uuid4()))).status_code == 404
    as_user({"quality": "edit"})
    assert client.get(download).status_code == 403
    assert client.get(f"/api/production/instruments/{uuid4()}/standards").status_code == 403
