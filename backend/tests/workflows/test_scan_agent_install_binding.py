"""Authorized reinstalls rotate credentials; heartbeat TOFU never takes over a bound PC."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity.infrastructure import IdentityApiTokenRow
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.modules.warehouse.scan_jobs import AgentMachineRefused, AgentTokenRow, agent_machine
from tests.identity.test_api_tokens import _bearer, _csrf, _login, tokens_api  # noqa: F401

BASE = "/api/stock/scan/agent"


def _install(admin):
    command = admin.post(f"{BASE}/install-token", headers=_csrf(admin)).json()["command"]
    secret = command.split("-InstallToken '")[1].split("'")[0]
    response = TestClient(app).post(f"{BASE}/enroll", json={
        "install_token": secret, "machine_id": "pc", "pc_name": "PC",
    })
    assert response.status_code == 200, response.text
    return _bearer(response.json()["token"])


@pytest.mark.parametrize("previous", ["active", "revoked", "expired"])
def test_authorized_reinstall_replaces_only_the_pc_credential_and_preserves_old_binding(tokens_api, previous):  # noqa: F811
    sessions, clock = tokens_api
    admin = _login("admin")
    old = _install(admin)
    with sessions() as session:
        old_id = session.query(AgentTokenRow).one().token_id
        if previous == "expired":
            session.get(IdentityApiTokenRow, old_id).expires_at = clock.now - timedelta(seconds=1)
            session.commit()
    if previous == "revoked":
        assert admin.post(f"/api/auth/api-tokens/{old_id}/revoke", headers=_csrf(admin)).status_code == 200

    new = _install(admin)
    assert new != old
    with sessions() as session:
        bindings = session.query(AgentTokenRow).all()
        assert len(bindings) == 2
        assert {row.machine_id for row in bindings} == {"pc"}
        assert sum(not row.retired for row in bindings) == 1
        assert session.get(AgentTokenRow, old_id).retired
        assert session.get(IdentityApiTokenRow, old_id).revoked_at is not None
    client = TestClient(app)
    assert client.post(f"{BASE}/heartbeat", json={"machine_id": "pc"}, headers=old).status_code == 401
    assert client.post(f"{BASE}/heartbeat", json={"machine_id": "pc"}, headers=new).status_code == 200
    assert client.post(f"{BASE}/heartbeat", json={"machine_id": "other"}, headers=new).status_code == 403
    # Even a request authenticated just before revocation cannot use first-heartbeat TOFU again.
    for machine in ("pc", "other"):
        with pytest.raises(AgentMachineRefused):
            agent_machine(SqlAlchemyWarehouseUnitOfWork(sessions), old_id, machine, bind=True)


def test_reinstall_without_valid_install_token_does_not_revoke_or_replace_existing_binding(tokens_api):  # noqa: F811
    sessions, _ = tokens_api
    admin = _login("admin")
    old = _install(admin)
    client = TestClient(app)
    assert client.post(f"{BASE}/enroll", json={"install_token": "frxi_invalid", "machine_id": "pc"}).status_code == 403
    with sessions() as session:
        binding = session.query(AgentTokenRow).one()
        assert not binding.retired
        assert session.get(IdentityApiTokenRow, binding.token_id).revoked_at is None
    assert client.post(f"{BASE}/heartbeat", json={"machine_id": "pc"}, headers=old).status_code == 200
