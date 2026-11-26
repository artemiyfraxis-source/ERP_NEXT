"""«ВАЦ — рабочее место» → «Экраны» (S5b): list, CAS saves, rights, journal, the bench draws the stored screen and
«Показать на ВАЦ» puts a saved screen on S2VNA without a sweep."""

import pytest
from sqlalchemy import select

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from app.modules.production.vna_session_infrastructure import VnaSessionRow
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401
from tests.production.test_api import MASTER_ID, production_api  # noqa: F401
from tests.warehouse.test_vna_jobs import CLAIM, HEARTBEAT, NAME, S2_INSTRUMENT, capture  # noqa: F401
from tests.workflows.test_vna_bench_api import calibrate, env, measure, post, session_path, start_old_flex, state  # noqa: F401

URL = "/api/production/vna-bench/screens"


def _as(permissions):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=MASTER_ID, name="Контролёр", department="ОТК"),
        email="qc@example.test", roles=frozenset({"qc"}), permissions=permissions)


def screens(client):
    response = client.get(URL)
    assert response.status_code == 200, response.text
    return response.json()["screens"]


def body(screen, **changes):
    """A save of `screen` (with its version); `version=None` — a new screen."""
    keys = ("name", "purpose", "split", "traces", "markers_hz", "note", "is_default", "version")
    data = {**{k: screen[k] for k in keys}, **changes}
    return {k: v for k, v in data.items() if not (k == "version" and v is None)}


def test_list_seeds_the_presets_and_saves_are_compare_and_set_and_journaled(production_api):
    client, _ = production_api
    _as({"quality": "edit"})
    rows = screens(client)
    assert [(r["purpose"], r["is_default"], r["version"]) for r in rows][:2] == [("adapter", True, 1), ("assembly", True, 1)]
    assert len(rows) == 6 and len(screens(client)) == 6        # seeded once
    adapter = rows[0]
    traces = [dict(t) for t in adapter["traces"]]
    traces[0]["scale"] = {"pdiv": 0.01, "rlev": 1.0, "rpos": 0}
    saved = client.put(f"{URL}/{adapter['id']}", json=body(adapter, traces=traces))
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2 and saved.json()["traces"][0]["scale"]["pdiv"] == 0.01
    assert saved.json()["updated_by"] == "Контролёр"
    stale = client.put(f"{URL}/{adapter['id']}", json=body(adapter))            # version 1 again
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale"
    bad = client.put(f"{URL}/{adapter['id']}", json=body(saved.json(), traces=[{**adapter["traces"][0], "format": "MLOG"}]))
    assert bad.status_code == 422 and bad.json()["detail"]["message"].startswith("График 1")
    assert "по норме" in bad.json()["detail"]["message"]
    clash = client.post(URL, json=body(adapter, name="ОТК — кабельная сборка", is_default=False, version=None))
    assert clash.status_code == 422 and "уже есть" in clash.json()["detail"]["message"]
    with app.state.production_uow_factory() as uow:
        events = uow.session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type.like("production.vna_screen_%"))).all()
        assert [e.event_type for e in events] == ["production.vna_screen_saved"]
        details = events[0].details
        assert details["before"]["version"] == 1 and details["after"]["traces"][0]["scale"]["pdiv"] == 0.01


def test_default_moves_between_screens_and_a_default_cannot_be_switched_off(production_api):
    client, _ = production_api
    _as({"quality": "edit"})
    adapter = screens(client)[0]
    created = client.post(URL, json=body(adapter, name="Переход — крупно", is_default=False, version=None))
    assert created.status_code == 200, created.text
    extra = created.json()
    assert not extra["is_default"] and extra["version"] == 1
    off = client.post(f"{URL}/{adapter['id']}/deactivate", json={"version": adapter["version"]})
    assert off.status_code == 409 and "Основной" in off.json()["detail"]["message"]
    undefault = client.put(f"{URL}/{adapter['id']}", json=body(adapter, is_default=False))
    assert undefault.status_code == 409
    promoted = client.put(f"{URL}/{extra['id']}", json=body(extra, is_default=True))
    assert promoted.status_code == 200 and promoted.json()["is_default"]
    old = client.get(f"{URL}/{adapter['id']}").json()
    assert not old["is_default"]
    off = client.post(f"{URL}/{adapter['id']}/deactivate", json={"version": old["version"]})
    assert off.status_code == 200 and not off.json()["active"]
    on = client.post(f"{URL}/{adapter['id']}/activate", json={"version": off.json()["version"]})
    assert on.status_code == 200 and on.json()["active"]


def test_the_screen_losing_default_gets_a_new_version_and_a_journal_row(production_api):
    client, _ = production_api
    _as({"quality": "edit"})
    adapter = screens(client)[0]                                   # the default, version 1, open in a stale editor
    extra = client.post(URL, json=body(adapter, name="Переход — крупно", is_default=True, version=None)).json()
    assert extra["is_default"]
    old = client.get(f"{URL}/{adapter['id']}").json()
    assert (old["is_default"], old["version"], old["updated_by"]) == (False, 2, "Контролёр")
    # The stale editor still thinks it is the default: saving it back is a conflict, never a silent takeover.
    stale = client.put(f"{URL}/{adapter['id']}", json=body(adapter))
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale"
    assert client.get(f"{URL}/{extra['id']}").json()["is_default"]
    with app.state.production_uow_factory() as uow:
        moved = uow.session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "production.vna_screen_default_moved")).all()
        assert [str(e.aggregate_id) for e in moved] == [adapter["id"]]
        assert moved[0].details["before"]["is_default"] and not moved[0].details["after"]["is_default"]


@pytest.mark.parametrize("change,message", [
    (lambda s: {"markers_hz": [float(i + 1) * 1e9 for i in range(16)]}, "маркеры: не больше 15 элементов"),
    (lambda s: {"traces": [{**s["traces"][1], "scale": {"pdiv": 0, "rlev": 0.0, "rpos": 10}}]}, "График 1, деление: должно быть больше 0"),
    (lambda s: {"traces": [{**s["traces"][1], "scale": {"pdiv": 0.1, "rlev": 0.0, "rpos": 11}}]}, "График 1, позиция: должно быть не больше 10"),
    (lambda s: {"name": " "}, "название: пустое значение или служебные символы"),
    (lambda s: {"name": "Я" * 121}, "название: не больше 120 символов"),
    (lambda s: {"split": 17}, "расположение: должно быть не больше 16"),
    (lambda s: {"markers_hz": [2e10]}, "Маркер вне диапазона ВАЦ"),
])
def test_refusals_are_in_russian(production_api, change, message):
    client, _ = production_api
    _as({"quality": "edit"})
    adapter = screens(client)[0]
    response = client.post(URL, json=body(adapter, name="Новый", is_default=False, version=None) | change(adapter))
    assert response.status_code == 422 and message in response.json()["detail"]["message"], response.text


@pytest.mark.parametrize("permissions,read,write", [
    ({"quality": "view"}, 200, 403), ({"production": "edit"}, 403, 403), ({"quality": "edit"}, 200, 200)])
def test_rights_view_for_otk_edit_for_quality_edit(production_api, permissions, read, write):
    client, _ = production_api
    _as({"quality": "edit"})
    adapter = screens(client)[0]
    _as(permissions)
    assert client.get(URL).status_code == read
    assert client.post(URL, json=body(adapter, name="Мой экран", is_default=False, version=None)).status_code == write


def _adapter(env):
    rows = env["admin"].get(URL).json()["screens"]
    return next(r for r in rows if r["purpose"] == "adapter")


def test_bench_draws_the_stored_screen_and_shows_a_saved_one_without_a_sweep(env):
    adapter = _adapter(env)
    traces = [dict(t) for t in adapter["traces"]]
    traces[1]["scale"] = {"pdiv": 0.02, "rlev": 0.0, "rpos": 10}
    response = env["admin"].put(f"{URL}/{adapter['id']}", json=body(adapter, name="Переход (правка)", traces=traces),
                                headers=_csrf(env["admin"]))
    assert response.status_code == 200, response.text
    # «Показать на ВАЦ» before a calibration is refused: the calibration is never touched from here.
    post(env, "/sessions")
    env["stub"].drive()
    view = state(env)
    refused = post(env, session_path(view, "/screen"), {"version": view["session"]["version"], "screen_id": adapter["id"]},
                   expect=409)
    assert refused["detail"]["code"] == "calibration_expired"
    for key in ("calibration", "load_port1", "load_port2"):
        post(env, session_path(view, f"/steps/{key}"), {"version": view["session"]["version"]})
        env["stub"].drive()
        view = state(env)
    plan = post(env, session_path(view, "/plan"), {"target": dict(kind="instrument", instrument_id=str(env["ids"]["dut"]))})
    assert plan["screen_name"] == "Переход (правка)" and plan["traces"][1]["scale"] == "-0,2…0 дБ"
    view = measure(env, view)
    env["stub"].drive()
    view = state(env)
    with env["sessions"]() as session:
        row = session.scalar(select(VnaSessionRow))
        applied = [s for s in row.steps if s["op"] == "apply_screen"]
        assert applied[-1]["args"]["screen"]["traces"][1]["scale"]["pdiv"] == 0.02
        steps_before = len(row.steps)
    engineer = next(r for r in env["admin"].get(URL).json()["screens"] if r["purpose"] == "engineer")
    view = post(env, session_path(view, "/screen"), {"version": view["session"]["version"], "screen_id": engineer["id"]})
    assert view["session"]["command"]["label"] == "экран «Подробно (инженер)» на ВАЦ"
    assert view["session"]["view_screen_id"] == engineer["id"]
    env["stub"].drive()
    view = state(env)
    assert view["calibration"]["valid"] and view["session"]["command"] is None
    with env["sessions"]() as session:
        row = session.scalar(select(VnaSessionRow))
        added = row.steps[steps_before:]
        assert [s["op"] for s in added] == ["apply_screen"]          # no sweep, no calibration step
        shown = added[0]["args"]["screen"]
        assert len(shown["traces"]) == 12 and shown["traces"][0]["limits"]   # the СИ's norm lines on the view
        assert row.calibrated_at is not None
        assert session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "production.vna_screen_shown")).all()


def test_a_repeat_connection_sweeps_on_the_view_and_a_check_restores_its_own_screen(env):
    view = calibrate(env)
    env["stub"].dut_vswr = 1.065   # «на границе»: a repeat connection follows
    measure(env, view)
    env["stub"].drive()
    item = state(env)["session"]["items"][0]
    engineer = next(r for r in env["admin"].get(URL).json()["screens"] if r["purpose"] == "engineer")
    view = state(env)
    post(env, session_path(view, "/screen"), {"version": view["session"]["version"], "screen_id": engineer["id"]})
    env["stub"].drive()
    view = state(env)
    post(env, session_path(view, f"/items/{item['id']}/connections"), {"version": view["session"]["version"]})
    env["stub"].drive()
    item = state(env)["session"]["items"][0]
    assert [c["status"] for c in item["connections"]][:2] == ["passed", "passed"]   # the sweep on the view is accepted
    with env["sessions"]() as session:
        row = session.scalar(select(VnaSessionRow))
        captures = [s for s in row.steps if s["op"] == "capture"]
        shown = [s for s in row.steps if s["op"] == "apply_screen"][-1]
        assert captures[-1]["args"]["screen_hash"] == shown["args"]["screen_hash"]
    # A check started, then a view, then its next phase: the check's screen goes back before the sweep.
    start_old_flex(env)
    view = state(env)
    post(env, session_path(view, "/screen"), {"version": view["session"]["version"], "screen_id": engineer["id"]})
    env["stub"].drive()
    view = state(env)
    check = view["session"]["checks"][0]
    env["stub"].connected = ("load",)
    post(env, session_path(view, f"/checks/{check['id']}/next"), {"version": view["session"]["version"]})
    env["stub"].drive()
    with env["sessions"]() as session:
        row = session.scalar(select(VnaSessionRow))
        tail = [s["op"] for s in row.steps[-3:]]
        assert tail == ["device_status", "apply_screen", "capture"]
        assert row.steps[-1]["args"]["screen_hash"] == row.steps[-2]["args"]["screen_hash"]
    assert state(env)["session"]["checks"][0]["phases"][0]["status"] == "passed"


def test_a_flex_screen_without_memory_skips_the_memory_step(env, monkeypatch):
    from app.modules.production.vna_screens import definition
    from app.workflows import vna_bench_api
    stored = definition("flex")
    # Only a screen stored before the memory rule (or written past the API) can lack memory: the check still runs.
    bare = stored.model_construct(**{**dict(stored), "traces": [t.model_copy(update={"memory": False}) for t in stored.traces]})
    original = vna_bench_api._screen
    monkeypatch.setattr(vna_bench_api, "_screen", lambda uow, purpose: bare if purpose == "flex" else original(uow, purpose))
    calibrate(env)
    start_old_flex(env)
    check = state(env)["session"]["checks"][0]
    for connected in (("load",), ("load",), ("thru",), ("thru",)):
        env["stub"].connected = connected
        view = state(env)
        post(env, session_path(view, f"/checks/{check['id']}/next"), {"version": view["session"]["version"]})
        env["stub"].drive()
    assert state(env)["session"]["checks"][0]["status"] == "passed"
    with env["sessions"]() as session:
        assert "store_memory" not in [s["op"] for s in session.scalar(select(VnaSessionRow)).steps]
