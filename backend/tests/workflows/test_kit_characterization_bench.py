"""«Характеристика мер» SMA-MF06 on the Planar bench and its consumption by Arinst, end to end against stub agents.

Physical truth is synthetic: smooth offset-delay standards and lossy F–F fixtures; the bench must store the pieces
themselves (fixtures removed) on its 1 MHz grid, refuse what is not proven and publish only a complete kit.
"""

import cmath
import math
from collections import Counter
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.modules.production import kit_characterization_run as kc
from app.modules.production.infrastructure import ProductionInstrumentRow
from app.modules.production.kit_characterization_data import CharacterizationCurve, parse_characterization
from app.modules.production.mating_infrastructure import InstrumentMatingRow
from app.modules.production.rf_infrastructure import RfDetailsRow, RfRepository
from app.modules.production.rf_interfaces import RfDetails
from app.modules.production.rf_kit_characterization import KitStandard
from app.modules.production.rf_kit_characterization_infrastructure import (RfKitRepository, RfKitStandardRow,
                                                                           RfStandardCharacterizationRow)
from app.modules.production.vna_dsp import from_t, multiply, to_t
from app.modules.production.vna_session_infrastructure import VnaSessionRow
from app.shared.persistence import AuditEventRow
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.production.test_api import production_api  # noqa: F401
from tests.warehouse.test_vna_jobs import capture  # noqa: F401
from tests.production.test_arinst_session_protocol import receipt
from tests.production.test_rf_kit_characterization import provenance
from tests.workflows.test_arinst_bench_api import action as arinst_action, env as arinst_env, poll, ready, send  # noqa: F401
from tests.workflows.test_arinst_bench_api import post as arinst_post, state as arinst_state
from tests.workflows.test_vna_bench_api import Agent, env as bench_env, grid, port, post, session_path, state, touchstone  # noqa: F401
from tests.workflows.test_vna_bench_api import calibrate as calibrate_adapter_session

NOW_ACTOR = "Контролёр"
PS = 1e-12


def delay(f, ps):
    return cmath.exp(-2j * math.pi * f * ps * PS)


GAMMA = {("OPEN", "female"): lambda f: 0.99 * delay(f, 2 * 20), ("SHORT", "female"): lambda f: -0.99 * delay(f, 2 * 18),
         ("LOAD", "female"): lambda f: complex(0.01, 0.002), ("OPEN", "male"): lambda f: 0.995 * delay(f, 2 * 15),
         ("SHORT", "male"): lambda f: -0.995 * delay(f, 2 * 14), ("LOAD", "male"): lambda f: complex(0.012, -0.001)}
THRU = {"female": (0.01, 0.98, 60), "male": (0.012, 0.985, 70)}
FIXTURE = {"A": (0.02, 0.97, 50), "B": (0.015, 0.96, 55)}


def two_port(match, loss, ps):
    return lambda f: (complex(match), loss * delay(f, ps), loss * delay(f, ps), complex(match))


def cascade(*parts):
    def network(f):
        matrix = to_t(*parts[0](f))
        for part in parts[1:]:
            matrix = multiply(matrix, to_t(*part(f)))
        return from_t(matrix)
    return network


def through(fixture, gamma):
    def network(f):
        s11, s21, s12, s22 = fixture(f)
        g = gamma(f)
        return (s11 + s21 * s12 * g / (1 - s22 * g), 1e-6, 1e-6, complex(0.9))
    return network


def direct(gamma):
    return lambda f: (gamma(f), 1e-6, 1e-6, complex(0.9))


class KitAgent(Agent):
    """The stub agent; `network(f) -> (S11, S21, S12, S22)` is what the operator has connected."""

    def __init__(self, env):
        super().__init__(env)
        self.network = None
        self.high_offset = 0j        # reconnection + recalibration: what the «3–6 ГГц» pass adds to S11

    def sweep(self, profile):
        if self.network is None:
            return super().sweep(profile)
        f = grid(profile)
        offset = self.high_offset if profile["start_hz"] == kc.BANDS["high"][0] else 0j
        values = [(lambda v: (v[0] + offset, *v[1:]))(self.network(hz)) for hz in f]
        return touchstone(f, *([v[i] for v in values] for i in range(4)))


@pytest.fixture()
def env(bench_env):
    ids = bench_env["ids"]
    ids.update(mf06=uuid4(), fa=uuid4(), fb=uuid4())
    with bench_env["sessions"]() as session:
        for key, number, name in (("mf06", "СИ.904", "Набор SMA-MF06"), ("fa", "СИ.908", "Переход SMA розетка–розетка"),
                                  ("fb", "СИ.909", "Переход SMA розетка–розетка")):
            session.add(ProductionInstrumentRow(id=ids[key], version=1, inventory_number=number, name=name,
                        instrument_type="Оснастка", kind="Принадлежность", status="Действует", serial_number=number))
        session.flush()
        session.add(RfDetailsRow(instrument_id=ids["mf06"], version=1, role="load_kit", confirmed=False, source="Test",
                                 kit_definition="SMA-MF06-loads"))
        rf = RfRepository(session)
        for key in ("fa", "fb"):
            rf.save(ids[key], RfDetails((port("SMA", False, 18), port("SMA", False, 18)), "adapter", True, "Паспорт"))
        session.flush()
        repo = RfKitRepository(session)
        pieces = {}
        for kind, gender in [(k, g) for k in ("OPEN", "SHORT", "LOAD", "THRU") for g in ("female", "male")]:
            top = 3e9 if gender == "female" and kind in ("OPEN", "SHORT") else 6e9
            row = repo.register(ids["mf06"], KitStandard(f"{kind}-{gender[0].upper()}", kind, gender,
                                gender if kind == "THRU" else None, 0.0, top),
                                expected_kit_version=session.get(RfDetailsRow, ids["mf06"]).version, actor=NOW_ACTOR,
                                now=bench_env["clock"].now)
            pieces[(kind, gender)] = str(row.id)
        session.commit()
    bench_env["pieces"] = pieces
    bench_env["stub"] = KitAgent(bench_env)
    return bench_env


def act(env, view, path, body=None, expect=200):
    return post(env, session_path(view, path), {"version": view["session"]["version"], **(body or {})}, expect)


def calibrate(env, view):
    env["stub"].network = None
    env["stub"].drive()
    view = state(env)
    act(env, view, "/steps/calibration")   # no confidence test and no LOAD since 26.11.2026
    env["stub"].drive()
    view = state(env)
    assert view["calibration"]["valid"], view["session"]["steps"]
    return view


def open_kit_session(env):
    view = post(env, "/sessions", {"characterization": {"kit_id": str(env["ids"]["mf06"])}})
    assert view["session"]["characterization"]["band"] == "low"
    return calibrate(env, view)


def sweep(env, view, network, body, expect=200):
    env["stub"].network = network
    answer = act(env, view, "/characterization/captures", body, expect)
    if expect != 200:
        return answer
    env["stub"].drive()
    return state(env)


def fixture_networks():
    return {"fa": two_port(*FIXTURE["A"]), "fb": two_port(*FIXTURE["B"])}


def piece_network(kind, gender, fixtures):
    if kind == "THRU":
        thru = two_port(*THRU[gender])
        return thru if gender == "female" else cascade(fixtures["fa"], thru, fixtures["fb"])
    gamma = GAMMA[(kind, gender)]
    return direct(gamma) if gender == "female" else through(fixtures["fa"], gamma)


def fixtures_body(env, kind, gender):
    if gender == "female":
        return []
    chosen = [{"instrument_id": str(env["ids"]["fa"])}]
    return chosen + ([{"instrument_id": str(env["ids"]["fb"])}] if kind == "THRU" else [])


def measure_pass(env, view, band):
    networks = fixture_networks()
    for key in ("fa", "fb"):
        view = sweep(env, view, networks[key], {"fixture_id": str(env["ids"][key])})
    for (kind, gender), standard_id in env["pieces"].items():
        if band == "high" and gender == "female" and kind in ("OPEN", "SHORT"):
            continue
        view = sweep(env, view, piece_network(kind, gender, networks),
                     {"standard_id": standard_id, "fixtures": fixtures_body(env, kind, gender)})
        assert not view["session"]["error"], view["session"]["error"]
    return view


def pieces(view):
    return {(p["kind"], p["gender"]): p for p in view["session"]["characterization"]["pieces"]}


def revision_curve(env, standard_id):
    with env["sessions"]() as session:
        row = session.scalar(select(RfStandardCharacterizationRow).where(
            RfStandardCharacterizationRow.standard_id == UUID(standard_id)).order_by(
            RfStandardCharacterizationRow.revision.desc()).limit(1))
        return row, parse_characterization(row.content, kind=session.get(RfKitStandardRow, UUID(standard_id)).kind,
                                           source_hash=row.sha256)


def characterize_all(env):
    view = open_kit_session(env)
    view = measure_pass(env, view, "low")
    view = act(env, view, "/characterization/band", {"band": "high"})
    assert view["session"]["characterization"]["band"] == "high" and not view["calibration"]["valid"]
    view = calibrate(env, view)
    view = measure_pass(env, view, "high")
    for standard_id in env["pieces"].values():
        view = act(env, view, f"/characterization/pieces/{standard_id}/save")
    return view


def test_two_passes_store_the_pieces_themselves_and_activate_the_complete_kit(env):
    view = characterize_all(env)
    by_piece = pieces(view)
    assert {p["state"] for p in by_piece.values()} == {"validated"}
    assert all(p["latest_revision"]["valid"] and not p["can_save"] for p in by_piece.values())
    characterization = view["session"]["characterization"]
    assert characterization["can_activate"] and characterization["missing"] == []
    assert [c["number"] for c in characterization["fixture_choices"]] == ["СИ.908", "СИ.909"]
    # Female OPEN: direct, only «до 3 ГГц», stored up to exactly 3 GHz on the 1 MHz grid.
    row, curve = revision_curve(env, env["pieces"][("OPEN", "female")])
    assert curve.frequencies[0] == 2e6 and curve.frequencies[-1] == 3e9 and len(curve.frequencies) == 2999
    assert row.validation["checks"]["midpoints"] and row.validation["midpoints"]["max_residual"] < 1e-6
    assert [a["band"] for a in row.provenance["acquisitions"]] == ["low"] and row.provenance["fixtures"] == []
    # Male OPEN: through the F–F fixture in both passes, stored de-embedded at the fixture's outer plane.
    row, curve = revision_curve(env, env["pieces"][("OPEN", "male")])
    assert curve.frequencies[-1] == 6e9 and len(curve.frequencies) == 5999
    for index in (0, 1500, 2998, 2999, 5998):
        f = curve.frequencies[index]
        assert abs(curve.s11[index] - GAMMA[("OPEN", "male")](f)) < 1e-9
    assert {(f["number"], f["band"], f["orientation"]) for f in row.provenance["fixtures"]} == {
        ("СИ.908", "low", "forward"), ("СИ.908", "high", "forward")}
    assert row.raw_artifact_id is not None and row.provenance["plane"].startswith("Внешняя розетка оснастки СИ.908")
    # THRU M–M: both fixtures removed, port order kept.
    _, curve = revision_curve(env, env["pieces"][("THRU", "male")])
    s11, loss, ps = THRU["male"]
    assert abs(curve.s21[4000] - loss * delay(curve.frequencies[4000], ps)) < 1e-9 and abs(curve.s11[10] - s11) < 1e-9
    response = env["admin"].post(f"/api/production/instruments/{env['ids']['mf06']}/standards/activate",
                                 json=characterization["activation"], headers=_csrf(env["admin"]))
    assert response.status_code == 200, response.text
    assert response.json()["generation"] == 1 and response.json()["kit_definition"] == "SMA-MF06-characterized"
    view = state(env)
    assert {p["state"] for p in pieces(view).values()} == {"active"}
    assert not view["session"]["characterization"]["can_activate"]
    manifest = env["admin"].get(view["session"]["characterization"]["kit"]["manifest"])
    assert manifest.status_code == 200 and manifest.headers["content-disposition"].startswith("attachment;")
    body = manifest.json()
    assert body["generation"] == 1 and len(body["standards"]) == 8
    assert all(s["revision"]["validation"]["valid"] for s in body["standards"])
    with env["sessions"]() as session:
        events = {e.event_type for e in session.scalars(select(AuditEventRow))}
        activated = session.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "production.rf_kit_activated"))
        kit_version = session.get(RfDetailsRow, env["ids"]["mf06"]).version
    assert {"production.rf_kit_revision_saved", "production.rf_kit_activated"} <= events
    # The audit names the kit version the activation created, the one the card now carries.
    assert activated.details["version"] == kit_version == response.json()["version"]


def test_activation_needs_all_eight_validated_and_a_failed_check_blocks_it(env):
    view = open_kit_session(env)
    networks = fixture_networks()
    female_open = env["pieces"][("OPEN", "female")]
    # A wobbling curve: the 2 MHz source cannot predict the measured 1 MHz midpoints.
    view = sweep(env, view, lambda f: (0.99 * delay(f, 40) * (1 + 0.01 * (round(f / 1e6) % 2)), 1e-6, 1e-6, 0.9),
                 {"standard_id": female_open})
    view = act(env, view, f"/characterization/pieces/{female_open}/save")
    piece = pieces(view)[("OPEN", "female")]
    assert piece["state"] == "captured" and not piece["latest_revision"]["valid"]
    assert "Невязка интерполяции" in piece["latest_revision"]["reason"]
    act(env, view, f"/characterization/pieces/{female_open}/save", expect=422)   # the same data is not saved twice
    male_open = env["pieces"][("OPEN", "male")]
    act(env, view, f"/characterization/pieces/{male_open}/save", expect=422)     # not captured at all
    assert view["session"]["characterization"]["activation"] is None
    with env["sessions"]() as session:
        kit = session.get(RfDetailsRow, env["ids"]["mf06"])
        standards = RfKitRepository(session).standards(env["ids"]["mf06"])
        latest = {s.id: session.scalar(select(RfStandardCharacterizationRow.id).where(
            RfStandardCharacterizationRow.standard_id == s.id)) for s in standards}
        body = dict(expected_kit_version=kit.version, revisions={str(k): str(v or uuid4()) for k, v in latest.items()},
                    expected_standard_versions={str(s.id): s.version for s in standards})
    response = env["admin"].post(f"/api/production/instruments/{env['ids']['mf06']}/standards/activate", json=body,
                                 headers=_csrf(env["admin"]))
    assert response.status_code == 422
    assert env["admin"].get(f"/api/production/instruments/{env['ids']['mf06']}/standards/manifest").status_code == 404
    # A good recapture replaces the failed evidence as the newest revision.
    view = sweep(env, view, direct(GAMMA[("OPEN", "female")]), {"standard_id": female_open})
    view = act(env, view, f"/characterization/pieces/{female_open}/save")
    assert pieces(view)[("OPEN", "female")]["state"] == "validated"


def test_refusals_fixture_first_band_and_ill_conditioned_removal(env):
    view = open_kit_session(env)
    networks = fixture_networks()
    male_open, male_thru = env["pieces"][("OPEN", "male")], env["pieces"][("THRU", "male")]
    refused = sweep(env, view, None, {"standard_id": male_open, "fixtures": fixtures_body(env, "OPEN", "male")}, 422)
    assert refused["detail"]["code"] == "no_fixture_file" and "СИ.908" in refused["detail"]["message"]
    assert sweep(env, view, None, {"standard_id": male_open}, 422)["detail"]["code"] == "invalid"
    assert sweep(env, view, None, {"standard_id": male_thru, "fixtures": [
        {"instrument_id": str(env["ids"]["fa"])}, {"instrument_id": str(env["ids"]["fa"])}]}, 422)
    assert sweep(env, view, None, {"fixture_id": str(env["ids"]["dut"])}, 422)["detail"]["code"] == "station_setup"
    # A broken fixture (no transmission): its capture is kept, but removing it is refused point by point.
    view = sweep(env, view, lambda f: (0.02, 1e-14, 1e-14, 0.02), {"fixture_id": str(env["ids"]["fa"])})
    view = sweep(env, view, through(networks["fa"], GAMMA[("OPEN", "male")]),
                 {"standard_id": male_open, "fixtures": fixtures_body(env, "OPEN", "male")})
    assert "Неустойчивое исключение оснастки" in view["session"]["error"]
    assert pieces(view)[("OPEN", "male")]["captures"] == {}
    # Female OPEN/SHORT are not measured in «3–6 ГГц».
    view = act(env, view, "/characterization/band", {"band": "high"})
    view = calibrate(env, view)
    answer = sweep(env, view, None, {"standard_id": env["pieces"][("OPEN", "female")]}, 422)
    assert "3–6 ГГц" in answer["detail"]["message"]
    # The ordinary ОТК items are not measured on the characterization grid.
    plan = post(env, session_path(view, "/plan"), {"target": dict(kind="instrument", instrument_id=str(env["ids"]["dut"]))})
    assert plan["refusal"]["code"] == "other_calibration"


def test_only_sma_mf06_opens_a_characterization_session(env):
    answer = post(env, "/sessions", {"characterization": {"kit_id": str(env["ids"]["kit"])}}, 422)   # 6650F27
    found = post(env, "/resolve", {"query": "СИ.904"})["candidates"]
    assert [(c["number"], c["kit"]) for c in found] == [("СИ.904", "SMA-MF06")]
    assert "SMA-MF06" in answer["detail"]["message"]


def test_a_reconnection_offset_between_the_passes_is_judged_at_the_join_not_by_midpoints(env):
    """Real passes differ by the reconnection (0.001–0.003): the piece still validates; the step at 3000 MHz is its own
    check with its own limit, shown with the revision. A step above the allocated uncertainty fails the piece."""
    load = env["pieces"][("LOAD", "female")]
    view = open_kit_session(env)
    view = sweep(env, view, direct(GAMMA[("LOAD", "female")]), {"standard_id": load})
    view = act(env, view, "/characterization/band", {"band": "high"})
    view = calibrate(env, view)
    for offset, valid in ((0.003, True), (0.02, False)):
        env["stub"].high_offset = offset
        view = sweep(env, view, direct(GAMMA[("LOAD", "female")]), {"standard_id": load})
        view = act(env, view, f"/characterization/pieces/{load}/save")
        revision = pieces(view)[("LOAD", "female")]["latest_revision"]
        assert revision["valid"] is valid, revision
        assert revision["join_step"] == pytest.approx(offset) and revision["join_limit"] == kc.JOIN_STEP_LIMIT
        assert revision["max_residual"] < revision["limit"]   # within each pass the 2 MHz nodes still hold
    assert "Скачок на стыке проходов 3000 МГц" in revision["reason"]
    row, curve = revision_curve(env, load)
    assert row.validation["checks"] == dict(grid=True, band=True, finite=True, midpoints=True, join=False)


def ends(env):
    with env["sessions"]() as session:
        counts = Counter()
        for row in session.scalars(select(InstrumentMatingRow)):
            counts[(str(row.instrument_id), row.side)] += row.delta
        return counts


def labels(env, view):
    with env["sessions"]() as session:
        row = session.get(VnaSessionRow, UUID(view["session"]["id"]))
        return [step["label"] for step in row.steps if step.get("label", "").startswith("Развёртка")], row.snapshot


def test_fixture_wear_counts_only_connections_made_and_the_hint_names_each_side(env):
    view = open_kit_session(env)
    fa = str(env["ids"]["fa"])
    _, snapshot = labels(env, view)
    cable1, cable2 = ((c["instrument_id"], c["end"]) for c in snapshot["cables"])
    start = ends(env)

    def delta():
        now = ends(env)
        return {key: now[key] - start[key] for key in (cable1, cable2, (fa, "A"), (fa, "B")) if now[key] != start[key]}

    networks = fixture_networks()
    view = sweep(env, view, networks["fa"], {"fixture_id": fa})
    assert delta() == {cable1: 1, cable2: 1, (fa, "A"): 1, (fa, "B"): 1}
    assert "сторона 1 (маркировка) — к кабелю порта 1, сторона 2 — к кабелю порта 2" in labels(env, view)[0][-1]
    # Male OPEN: the fixture stays on the port-1 cable by its side 1; only the piece mates its side 2.
    for kind in ("OPEN", "LOAD"):
        view = sweep(env, view, through(networks["fa"], GAMMA[(kind, "male")]),
                     {"standard_id": env["pieces"][(kind, "male")], "fixtures": [{"instrument_id": fa}]})
    assert delta() == {cable1: 1, cable2: 1, (fa, "A"): 1, (fa, "B"): 3}
    assert "СИ.908: сторона 1 (маркировка) — к кабелю порта 1" in labels(env, view)[0][-1]
    # «Оснастка развёрнута»: side 2 now faces the cable — a new connection of the cable and side B, the piece on side A.
    view = sweep(env, view, through(networks["fa"], GAMMA[("SHORT", "male")]),
                 {"standard_id": env["pieces"][("SHORT", "male")], "fixtures": [{"instrument_id": fa, "reversed": True}]})
    assert not view["session"]["error"], view["session"]["error"]
    assert delta() == {cable1: 2, cable2: 1, (fa, "A"): 2, (fa, "B"): 4}
    assert "СИ.908: сторона 2 — к кабелю порта 1, SHORT-M — на её сторону 1 (маркировка)" in labels(env, view)[0][-1]
    # A female piece goes straight on the cable: the fixture is off, so the next male piece remounts it.
    view = sweep(env, view, direct(GAMMA[("OPEN", "female")]), {"standard_id": env["pieces"][("OPEN", "female")]})
    assert delta() == {cable1: 3, cable2: 1, (fa, "A"): 2, (fa, "B"): 4}
    view = sweep(env, view, through(networks["fa"], GAMMA[("OPEN", "male")]),
                 {"standard_id": env["pieces"][("OPEN", "male")], "fixtures": [{"instrument_id": fa}]})
    assert delta() == {cable1: 4, cable2: 1, (fa, "A"): 3, (fa, "B"): 5}


def test_an_acm_that_does_not_reach_the_grid_is_refused_before_any_mating_is_written(monkeypatch):
    """The receipt check normally stops such an ACM first; the bench's own refusal must not write matings either
    (record_batch also queues audit events and takes the registry lock)."""
    from types import SimpleNamespace
    from app.modules.production.domain import ProductionValidationError
    from app.workflows import vna_bench_api
    written = []
    monkeypatch.setattr(vna_bench_api, "_matings", lambda *args: written.append(args))
    row = SimpleNamespace(snapshot=dict(source="planar", profile_kind="characterization",
                                        profile=kc.band_profile("low").model_dump(mode="json"),
                                        acm=dict(id=str(uuid4())), cables=[]))
    command = SimpleNamespace(id=uuid4(), op="acm_calibrate", result=dict(module_min_hz=9e6))
    with pytest.raises(ProductionValidationError, match="АКМ калибрует от 9 МГц"):
        vna_bench_api._judge(None, None, row, command, {}, None)
    assert written == []
    command.result["module_min_hz"] = 1e5
    assert vna_bench_api._judge(None, None, row, command, {}, None) == {} and len(written) == 1


def test_the_monthly_check_takes_only_a_6650f27_even_when_the_card_is_named(env):
    """An activated SMA-MF06 is also «Набор SOLT», but the monthly check judges the 6650F27 passport description."""
    with env["sessions"]() as session:
        row = session.get(RfDetailsRow, env["ids"]["mf06"])
        row.role, row.kit_definition = "solt_kit", "SMA-MF06-characterized"
        row.characterization_generation, row.characterization_hash = 1, "a" * 64
        session.commit()
    view = calibrate_adapter_session(env)
    answer = post(env, session_path(view, "/checks"), {"version": view["session"]["version"], "kind": "monthly",
                                                       "instrument_id": str(env["ids"]["mf06"])}, 409)
    assert answer["detail"]["code"] == "station_setup" and "6650F27" in answer["detail"]["message"]


# ---- Arinst: calibration from the ACTIVE generation -------------------------------------------------------------------

ARINST_PROFILE = dict(start_hz=10_000_000, stop_hz=2_990_000_000, points=101, if_bandwidth_hz=1000)
D, S, T = complex(0.05, 0.01), complex(0.1, -0.05), complex(0.8, 0.1)


def truth(kind, gender):
    if kind == "THRU":
        _, loss, ps = THRU[gender]
        return lambda f: loss * delay(f, ps)
    return GAMMA[(kind, gender)]


def stored_curve(kind, gender, top, start=2e6):
    grid = tuple(start + i * 1e6 for i in range(round((top - start) / 1e6) + 1))
    if kind == "THRU":
        match, loss, ps = THRU[gender]
        s21 = tuple(loss * delay(f, ps) for f in grid)
        return CharacterizationCurve("THRU", grid, (complex(match),) * len(grid), s21, s21, (complex(match),) * len(grid))
    return CharacterizationCurve(kind, grid, tuple(GAMMA[(kind, gender)](f) for f in grid))


def pass_parts(kind, gender, top):
    """The same truth measured in each pass the piece needs (no reconnection offset)."""
    return {band: stored_curve(kind, gender, min(top, kc.BANDS[band][1]), start=kc.BANDS[band][0])
            for band in kc.bands_for(0.0, top)}


def publish_kit(sessions, now):
    """A registered SMA-MF06 with one validated revision per piece, activated as generation 1."""
    kit_id = uuid4()
    with sessions() as session:
        session.add(ProductionInstrumentRow(id=kit_id, version=1, inventory_number="СИ.904", name="Набор SMA-MF06",
                    instrument_type="Оснастка", kind="Принадлежность", status="Действует"))
        session.flush()
        session.add(RfDetailsRow(instrument_id=kit_id, version=1, role="load_kit", confirmed=False, source="Test",
                                 kit_definition="SMA-MF06-loads"))
        session.flush()
        repo = RfKitRepository(session)
        for kind in ("OPEN", "SHORT", "LOAD", "THRU"):
            for gender in ("female", "male"):
                top = 3e9 if gender == "female" and kind in ("OPEN", "SHORT") else 6e9
                piece = repo.register(kit_id, KitStandard(f"{kind}-{gender[0].upper()}", kind, gender,
                                      gender if kind == "THRU" else None, 0.0, top),
                                      expected_kit_version=session.get(RfDetailsRow, kit_id).version, actor=NOW_ACTOR,
                                      now=now)
                curve = stored_curve(kind, gender, top)
                repo.append_revision(piece.id, expected_version=piece.version, format="s2p" if kind == "THRU" else "s1p",
                                     content=kc.content(curve), provenance=provenance(),
                                     validation=kc.evidence(curve, pass_parts(kind, gender, top))[0],
                                     actor=NOW_ACTOR, now=now)
        session.commit()
    activate(sessions, kit_id, now)
    return kit_id


def activate(sessions, kit_id, now):
    with sessions() as session:
        repo = RfKitRepository(session)
        standards = repo.standards(kit_id)
        latest = {s.id: session.scalar(select(RfStandardCharacterizationRow.id).where(
            RfStandardCharacterizationRow.standard_id == s.id).order_by(RfStandardCharacterizationRow.revision.desc())
            .limit(1)) for s in standards}
        repo.activate(kit_id, latest, expected_kit_version=session.get(RfDetailsRow, kit_id).version,
                      expected_standard_versions={s.id: s.version for s in standards}, actor=NOW_ACTOR, now=now)
        session.commit()


def set_profile(env, view, profile):
    arinst_action(env, view, "arinst/profile", profile=profile)
    send(env, poll(env))
    return arinst_state(env)


def standard_result(command, gender):
    """What the raw Arinst port sees: m = D + T·Γ/(1 − S·Γ); THRU S21 = 0.9·S21."""
    result = receipt("measure_standard", command["args"])
    frequencies = result["acquisition"]["frequencies_hz"]
    kind = command["args"]["standard"]
    actual = [truth(kind, gender)(f) for f in frequencies]
    s11 = [complex(0.02) if kind == "THRU" else D + T * g / (1 - S * g) for g in actual]
    s21 = [0.9 * g if kind == "THRU" else complex(1e-4) for g in actual]
    result["acquisition"]["data"] = {"S11": [[v.real, v.imag] for v in s11], "S21": [[v.real, v.imag] for v in s21]}
    return result


def run_standards(env, view, gender, before_last=None):
    for standard in ("OPEN", "SHORT", "LOAD", "THRU"):
        arinst_action(env, view, "arinst/confirm")
        command = poll(env)
        assert command["args"]["standard"] == standard
        if standard == "THRU" and before_last:
            before_last()
        send(env, command, standard_result(command, gender))
        view = arinst_state(env)
    return view


def start_calibration(env, kit_id, view, expected=200, **extra):
    body = dict(kit_id=str(kit_id), reference_plane="Вилки SMA концов кабелей", transmission=True,
                standard_gender="female")
    body.update(extra)
    return arinst_post(env, f"/sessions/{view['session']['id']}/arinst/calibration",
                       dict(version=view["session"]["version"], **body), expected)


def session_snapshot(sessions, view):
    with sessions() as session:
        return session.get(VnaSessionRow, UUID(view["session"]["id"])).snapshot


def test_arinst_solves_with_the_active_generation_on_its_exact_grid(arinst_env):
    kit_id = publish_kit(arinst_env[3], arinst_env[2].now)
    view = set_profile(arinst_env, ready(arinst_env), ARINST_PROFILE)
    listed = next(k for k in view["kits"] if k["id"] == str(kit_id))
    assert listed["active"] and listed["generation"] == 1
    female = next(s for s in listed["sets"] if s["gender"] == "female")
    assert female["sol_mhz"] == [2.0, 3000.0] and female["thru_mhz"] == [2.0, 6000.0]
    view = start_calibration(arinst_env, kit_id, view)
    assert view["calibration_kit"]["generation"] == 1 and len(view["calibration_kit"]["pieces"]) == 4
    open_standard = session_snapshot(arinst_env[3], view)["characterized_standards"]["OPEN"]
    assert open_standard["coefficient_version"].startswith("1:") and len(open_standard["response"]) == 101
    frequency = open_standard["frequencies"][37]
    assert frequency % 2e6 and abs(complex(*open_standard["response"][37]) - truth("OPEN", "female")(frequency)) < 1e-6
    view = run_standards(arinst_env, view, "female")
    assert view["calibration"]["status"] == "verification_required", view["calibration"]
    assert view["acceptance"]["eligible"] is False


def test_arinst_refuses_missing_gender_and_grids_outside_the_characterized_band(arinst_env):
    kit_id = publish_kit(arinst_env[3], arinst_env[2].now)
    view = set_profile(arinst_env, ready(arinst_env), dict(ARINST_PROFILE, stop_hz=4_000_000_000))
    answer = start_calibration(arinst_env, kit_id, view, 422)
    assert "до 3 ГГц" in answer["detail"]["message"] and "OPEN-F" in answer["detail"]["message"]
    assert "Укажите" in start_calibration(arinst_env, kit_id, view, 422, standard_gender=None)["detail"]["message"]
    view = set_profile(arinst_env, view, dict(ARINST_PROFILE, start_hz=1_000_000))
    assert "выходит за характеристику" in start_calibration(arinst_env, kit_id, view, 422)["detail"]["message"]
    view = set_profile(arinst_env, view, dict(ARINST_PROFILE, stop_hz=4_000_000_000))
    male = start_calibration(arinst_env, kit_id, view, standard_gender="male")   # male pieces reach 6 GHz
    assert male["calibration_kit"]["standard_gender"] == "male"


def test_arinst_refuses_a_kit_that_is_not_active(arinst_env):
    publish_kit(arinst_env[3], arinst_env[2].now)
    with arinst_env[3]() as session:
        loads = uuid4()
        session.add(ProductionInstrumentRow(id=loads, version=1, inventory_number="СИ.905", name="SMA-MF06",
                    instrument_type="Оснастка", kind="Принадлежность", status="Действует"))
        session.flush()
        session.add(RfDetailsRow(instrument_id=loads, version=1, role="load_kit", confirmed=False, source="Test",
                                 kit_definition="SMA-MF06-loads"))
        session.commit()
    view = set_profile(arinst_env, ready(arinst_env), ARINST_PROFILE)
    assert not next(k for k in view["kits"] if k["id"] == str(loads))["active"]
    assert "не охарактеризован" in start_calibration(arinst_env, loads, view, 422)["detail"]["message"]


def test_a_newer_activation_makes_a_running_calibration_stale(arinst_env):
    sessions, now = arinst_env[3], arinst_env[2].now
    kit_id = publish_kit(sessions, now)
    view = start_calibration(arinst_env, kit_id, set_profile(arinst_env, ready(arinst_env), ARINST_PROFILE))
    view = run_standards(arinst_env, view, "female", before_last=lambda: activate(sessions, kit_id, now))
    assert view["calibration"]["status"] == "blocked" and "переактивирован" in view["calibration"]["reason"]
    snapshot = session_snapshot(sessions, view)
    assert "characterized_standards" not in snapshot and "calibration_context" not in snapshot
    # A run pinned to generation 2 is stale once generation 3 appears: the view says so and confirm refuses.
    view = start_calibration(arinst_env, kit_id, view)
    activate(sessions, kit_id, now)
    view = arinst_state(arinst_env)
    assert view["calibration"]["status"] == "stale"
    refused = arinst_post(arinst_env, f"/sessions/{view['session']['id']}/arinst/confirm",
                          dict(version=view["session"]["version"]), 409)
    assert "переактивирован" in refused["detail"]["message"]


def test_a_valid_calibration_on_a_replaced_generation_is_shown_stale(arinst_env):
    sessions, now = arinst_env[3], arinst_env[2].now
    kit_id = publish_kit(sessions, now)
    view = start_calibration(arinst_env, kit_id, set_profile(arinst_env, ready(arinst_env), ARINST_PROFILE))
    with sessions() as session:   # a calibration that already passed verification on generation 1
        row = session.get(VnaSessionRow, UUID(view["session"]["id"]))
        row.snapshot = {**row.snapshot, "calibration": dict(valid=True, status="valid", coefficient_artifact_id=str(uuid4()))}
        session.commit()
    assert arinst_state(arinst_env)["calibration"]["status"] == "valid"
    activate(sessions, kit_id, now)
    calibration = arinst_state(arinst_env)["calibration"]
    assert calibration["status"] == "stale" and not calibration["valid"] and "переактивирован" in calibration["reason"]
