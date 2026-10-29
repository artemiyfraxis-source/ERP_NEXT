"""Уровни доступа, T2 (docs/migration/access-levels-plan.md): every route dependency checks exactly what the contract
`level_migration.GATES` says under the new model — the section mode alone, superadmin/admin pass everything, the
administrator's tools are theirs alone — and no route keeps a dependency outside the contract."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity import domain
from app.modules.identity.domain import ACCESS_LEVELS, FULL_ACCESS_LEVELS, LEVEL_SECTION_LABELS
from app.modules.identity.level_migration import GATES, OLD_SECTIONS, passes_after
from app.modules.references.api import _map_rights
from app.modules.shipping import api as shipping_api
from app.modules.warehouse import scan_jobs_api
from app.shared import http_auth
from app.shared.http_auth import Principal, authenticated_principal
from app.workflows import employee_card_api
from tests.access_levels import principal_as

# Every route dependency of the contract; references_suppliers is `_map_rights` inside references/api.py.
DEPENDENCIES = {
    **{gate.key: getattr(http_auth, f"{gate.key}_principal", None) for gate in GATES},
    "shipping_editor": shipping_api.shipping_editor_principal,
    "staffdir_view": employee_card_api.staffdir_view_principal,
    # «Сканирование документов» (0159, after the transfer): its own section, no role-era gate behind it.
    "scan_edit": scan_jobs_api.scan_edit_principal,
}


def _principal(level: str, permissions: dict[str, str]) -> Principal:
    return Principal(
        actor=principal_as("operator").actor, email="gate@example.test", roles=frozenset({level}),
        permissions=permissions,
    )


def _permission_sets() -> list[dict[str, str]]:
    sections = [*LEVEL_SECTION_LABELS, "staffdir"]
    sets: list[dict[str, str]] = [{}, {s: "view" for s in sections}, {s: "edit" for s in sections}]
    sets += [{section: mode} for section in sections for mode in ("view", "edit")]
    return sets


def _passes(dependency, principal: Principal) -> bool:
    try:
        dependency(principal)
    except HTTPException as error:
        assert error.status_code == 403
        return False
    return True


# Gates widened after the levels transfer: today's rule replaces the contract's `after` (the transfer stays as it was).
WIDENED = {"warehouse_journal": (("warehouse",), "edit")}  # owner 12.10.2026: storekeepers see the lot history


@pytest.mark.parametrize("gate", GATES, ids=[gate.key for gate in GATES])
def test_every_gate_dependency_follows_the_contract(gate):
    dependency = DEPENDENCIES.get(gate.key)
    if gate.key in WIDENED:
        gate = replace(gate, after=WIDENED[gate.key])
    for level in ACCESS_LEVELS:
        for permissions in _permission_sets():
            principal = _principal(level, permissions)
            expected = passes_after(level, permissions, gate)
            if gate.key == "references_suppliers":
                actual = _map_rights(principal)["suppliers"] or level in FULL_ACCESS_LEVELS
            else:
                assert dependency is not None, gate.key
                actual = _passes(dependency, principal)
            assert actual == expected, (gate.key, level, permissions)


def _gate_dependencies() -> set:
    return {dependency for dependency in DEPENDENCIES.values() if dependency is not None}


def _walk(dependant):
    for child in dependant.dependencies:
        if child.call is not None:
            yield child.call
        yield from _walk(child)


def _api_routes(routes, inherited=()):
    """Every APIRoute with the dependencies its include_router adds (FastAPI keeps included routers apart)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route, inherited
            continue
        inner = getattr(route, "original_router", None)
        if inner is not None:
            context = getattr(route, "include_context", None)
            extra = tuple(item.dependency for item in (getattr(context, "dependencies", None) or ()))
            yield from _api_routes(inner.routes, inherited + extra)


def test_no_route_takes_a_gate_outside_the_contract():
    known = _gate_dependencies()
    builders = ("require_section.<locals>.dependency", "require_full_access.<locals>.dependency")
    seen = set()
    routes = list(_api_routes(app.routes))
    assert len(routes) > 100
    for route, inherited in routes:
        for call in (*inherited, *_walk(route.dependant)):
            if getattr(call, "__qualname__", "") in builders:
                assert call in known, (route.path, sorted(route.methods))
                seen.add(call)
    # Every contract dependency guards at least one route (production_view_work: start / pause / submit).
    assert {DEPENDENCIES["production_view_work"], DEPENDENCIES["shipping_editor"], DEPENDENCIES["staffdir_view"]} <= seen
    built = {
        name for name, value in vars(http_auth).items()
        if name.endswith("_principal") and getattr(value, "__qualname__", "") in builders
    }
    assert built == {f"{gate.key}_principal" for gate in GATES if hasattr(http_auth, f"{gate.key}_principal")}


def test_route_role_lists_are_gone():
    with pytest.raises(TypeError):
        http_auth.require_section("warehouse", "view", roles=frozenset({"receiver"}))  # type: ignore[call-arg]


def test_level_sections_are_the_old_ones_without_staffdir_plus_the_split_sections():
    # «Кадры» (`hr`, 0089), «Сканирование документов» / «Печать документов» (`scan`, `print`, 0159), «Отгрузка без
    # УПД — разрешение» (`ship_no_upd`, 0162) came after the transfer: the matrix offers them, the transfer never saw them.
    assert set(LEVEL_SECTION_LABELS) == (set(OLD_SECTIONS) - {"staffdir"}) | {
        "myops", "quality", "shipping_goods", "hr", "scan", "print", "ship_no_upd"}
    # "admin" stays full only as the «Режим админа» session level; it is no level a person holds (14.10.2026).
    assert domain.FULL_ACCESS_LEVELS == {"superadmin", domain.ADMIN_MODE_LEVEL} == {"superadmin", "admin"}
    assert domain.LEVEL_LABELS == {
        "superadmin": "Суперадминистратор", "commercial": "Коммерческое руководство",
        "tech_lead": "Технический руководитель", "pm": "Менеджер по проектам", "storekeeper": "Кладовщик",
        "foreman": "Бригадир", "operator": "Оператор",
    }
    assert domain.ACCESS_LEVELS == tuple(domain.LEVEL_LABELS)
    assert set(domain.LEVEL_SEED) == set(domain.ACCESS_LEVELS) - domain.FULL_ACCESS_LEVELS


def test_admin_is_no_level_to_give_but_the_admin_mode_session_keeps_full_access_and_its_name():
    with pytest.raises(domain.IdentityValidationError, match="Уровня «Администратор» больше нет"):
        domain.normalize_level("admin")
    assert domain.normalize_level("pm") == "pm" and domain.normalize_level("storekeeper") == "storekeeper"
    assert domain.level_label("admin") == "Администратор" and domain.level_label("storekeeper") == "Кладовщик"
    full = domain.level_permissions(domain.ADMIN_MODE_LEVEL, {}, {"warehouse": domain.AccessMode.NONE})
    assert full == {section: domain.AccessMode.EDIT for section in domain.FULL_LEVEL_SECTIONS}


# ── The split sections over HTTP: myops (reads / writes), quality, shipping_goods ─────────────────────

ANY = UUID("99999999-9999-9999-9999-999999999999")


@pytest.fixture()
def as_level():
    def use(level: str, permissions: dict[str, str]) -> TestClient:
        app.dependency_overrides[authenticated_principal] = lambda: _principal(level, permissions)
        return TestClient(app, raise_server_exceptions=False)

    yield use
    app.dependency_overrides.pop(authenticated_principal, None)


def _refused(response) -> bool:
    return response.status_code == 403


def test_myops_view_reads_the_own_tasks_but_does_not_start_pause_or_submit(as_level):
    assert not _refused(as_level("operator", {"myops": "view"}).get("/api/production/my"))
    assert _refused(as_level("operator", {"myops": "view"}).post(f"/api/production/work/{ANY}/pause"))
    assert _refused(as_level("operator", {"myops": "view"}).post(f"/api/production/work/{ANY}/submit", json={}))
    assert not _refused(as_level("operator", {"myops": "edit"}).post(f"/api/production/work/{ANY}/pause"))
    # Planning (`production`) does not open the own tasks, and the own tasks do not open planning.
    assert _refused(as_level("foreman", {"production": "edit"}).get("/api/production/my"))
    assert _refused(as_level("operator", {"myops": "edit"}).get("/api/production/tasks"))


def test_quality_and_the_storekeepers_shipping_are_sections_of_their_own(as_level):
    qc = {"version": 1, "good": "1", "defect": "0"}
    assert _refused(as_level("foreman", {"production": "edit", "quality": "view"}).post(
        f"/api/production/boxes/{ANY}/qc", json=qc))
    assert not _refused(as_level("foreman", {"quality": "edit"}).post(f"/api/production/boxes/{ANY}/qc", json=qc))
    ship = {"version": 1}
    assert _refused(as_level("commercial", {"shipping": "edit"}).post(f"/api/shipping/shipments/{ANY}/pick", json=ship))
    assert not _refused(as_level("foreman", {"shipping_goods": "edit"}).post(
        f"/api/shipping/shipments/{ANY}/pick", json=ship))
    # The list and the head / lines / boxes open from either side.
    assert not _refused(as_level("foreman", {"shipping_goods": "view"}).get("/api/shipping/shipments"))
    for permissions in ({"shipping_goods": "edit"}, {"shipping": "edit"}):
        assert not _refused(as_level("foreman", permissions).put(f"/api/shipping/shipments/{ANY}/head", json=ship))
    assert _refused(as_level("foreman", {"shipping_goods": "view", "shipping": "view"}).put(
        f"/api/shipping/shipments/{ANY}/head", json=ship))


def test_full_levels_pass_the_administrators_tools_and_nobody_else_does(as_level):
    for level in ("superadmin", "admin"):
        assert not _refused(as_level(level, {}).get("/api/auth/access"))
    everything = {section: "edit" for section in [*LEVEL_SECTION_LABELS, "staffdir"]}
    for level in ("commercial", "tech_lead", "foreman", "operator"):
        client = as_level(level, everything)
        assert _refused(client.get("/api/auth/access"))
        assert _refused(client.get("/api/auth/staff"))
        assert _refused(client.get(f"/api/employee-card/{ANY}"))
