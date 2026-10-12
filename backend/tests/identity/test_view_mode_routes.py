"""ID-6 «режим просмотр» across the whole app for «Склад», «Лист закупки», «Приёмка»: every route that touches one
of these sections and changes something refuses a person who holds them on view only, and every read of the three
modules opens on view except the edit tools listed below (each one is the first half of a write)."""

import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity.level_migration import GATE_BY_KEY
from app.shared.http_auth import Principal, authenticated_principal
from tests.access_levels import principal_as
from tests.identity.test_level_gates import ANY, DEPENDENCIES, _api_routes, _walk

SECTIONS = frozenset({"warehouse", "procurement", "receiving"})
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
MODULE_PREFIXES = ("/api/stock", "/api/procurement", "/api/receiving")
# Reads that stay closed in view mode (decided 29.09, tests/*/test_view_mode.py): the tool belongs to a write.
EDIT_ONLY_READS = {
    "/api/stock/journal",  # «Журнал склада» / «История» партии — who works with the warehouse
    "/api/stock/article-info",  # feeds «Внести позиции»
    "/api/stock/item-types",  # «Типы» dictionary editor
    "/api/stock/export",  # first half of «Загрузить правки»
    "/api/procurement/mine",  # the buyer's own taken lines
    "/api/procurement/in-work/mine",
    "/api/procurement/kp",  # КП form
    "/api/procurement/{line_id}/stock-candidates",  # «Закрыть со склада»
    "/api/receiving/putaway-sheet",  # the storekeeper's move list
    "/api/receiving/supplier-return",  # «Возврат поставщику» form
}
GATE_OF = {dependency: GATE_BY_KEY[key] for key, dependency in DEPENDENCIES.items() if dependency is not None}


def _gates(route, inherited):
    return [GATE_OF[call] for call in (*inherited, *_walk(route.dependant)) if call in GATE_OF]


def _touches(gate) -> bool:
    # after=None — superadmin/admin only (the warehouse import and journal).
    rule = gate.after[0] if gate.after else (gate.before.sections if gate.before else ())
    return bool(set(rule) & SECTIONS)


def _routes(read: bool):
    for route, inherited in _api_routes(app.routes):
        methods = route.methods & READ_METHODS if read else route.methods - READ_METHODS
        gates = [gate for gate in _gates(route, inherited) if _touches(gate)]
        for method in sorted(methods):
            if gates:
                yield method, route.path, gates


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", str(ANY), path)


@pytest.fixture()
def viewer():
    """A foreman holding the three sections on view and nothing else."""
    principal = Principal(
        actor=principal_as("foreman").actor, email="viewer@example.test", roles=frozenset({"foreman"}),
        permissions={section: "view" for section in SECTIONS},
    )
    app.dependency_overrides[authenticated_principal] = lambda: principal
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(authenticated_principal, None)


def test_every_write_touching_the_three_sections_takes_edit():
    writes = list(_routes(read=False))
    assert len(writes) > 50
    for method, path, gates in writes:
        assert any(gate.after is None or gate.after[1] == "edit" for gate in gates), (method, path)


def test_view_only_person_is_refused_on_every_write(viewer):
    writes = list(_routes(read=False))
    # Outside the three modules too: the requests import into procurement, the supplier maps.
    assert any(not path.startswith(MODULE_PREFIXES) for _, path, _ in writes)
    for method, path, _ in writes:
        response = viewer.request(method, _url(path), json={})
        assert response.status_code == 403, (method, path, response.status_code, response.text)


def test_every_read_of_the_three_modules_opens_on_view_except_the_edit_tools(viewer):
    reads = [(method, path) for method, path, _ in _routes(read=True) if path.startswith(MODULE_PREFIXES)]
    assert {path for _, path in reads} >= EDIT_ONLY_READS
    for method, path in reads:
        response = viewer.request(method, _url(path))
        if path in EDIT_ONLY_READS:
            assert response.status_code == 403, (method, path, response.status_code)
        else:
            assert response.status_code != 403, (method, path, response.text)
