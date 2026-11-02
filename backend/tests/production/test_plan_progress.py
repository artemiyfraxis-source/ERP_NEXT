from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import IntegrityError, OperationalError

from app.modules.production import plan, plan_application as application
from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import ProductionPlanEventRow, SqlAlchemyPlanEventRepository
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor
from tests.techcards.test_kitset_api import kitset_api, _load, _principal
from app.main import app
from app.shared.http_auth import authenticated_principal
from app.workflows import project_kitset as flow
from app.modules.techcards.project_cards import ProjectTechcard
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.modules.techcards.kitset_infrastructure import SqlAlchemyKitsetRepository
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.shared.advisory_lock import lock_key, lock_project_run

NOW = datetime(2026, 11, 2, 12)
ACTOR = Actor(name="Мастер", department="Производство")
PROJECT = "708К.4"


def test_later_increase_raises_earlier_counts_with_one_notice_and_unknowns_stay_unknown():
    initial = plan.KbProgress(run=200, started=80)
    result, notices = plan.change_kb_progress(initial, {"done": 120}, 200)
    assert result.counts() == {"started": 120, "done": 120, "qc": None, "passed": None}
    assert len(notices) == 1 and "Начато: 80 → 120" in notices[0]
    assert plan.kb_progress_stage(result) == "В работе"


def test_lowering_never_cascades_or_repairs_preexisting_inconsistency():
    initial = plan.KbProgress(run=200, started=150, done=140, qc=130, passed=100)
    result, notices = plan.change_kb_progress(initial, {"done": 20}, 200)
    assert result.counts() == {"started": 150, "done": 20, "qc": 130, "passed": 100}
    assert notices == ()
    assert result.warnings() and "Готово" in " ".join(result.warnings())
    unchanged, notices = plan.change_kb_progress(result, {"qc": 130}, 200)
    assert unchanged == result and notices == ()


@pytest.mark.parametrize("value", [-1, 201, 1.5, "12", True, None])
def test_progress_rejects_junk_and_above_run(value):
    with pytest.raises(ProductionValidationError):
        plan.change_kb_progress(None, {"done": value}, 200)


def test_progress_refuses_unknown_fields_empty_updates_and_unknown_run():
    for changes, run in [({"other": 1}, 200), ({}, 200), ({"done": 1}, None)]:
        with pytest.raises(ProductionValidationError):
            plan.change_kb_progress(None, changes, run)


def test_qc_counts_do_not_need_stage_order_and_manual_stage_still_wins():
    progress, _ = plan.change_kb_progress(None, {"passed": 200}, 200)
    assert progress.counts() == {"started": 200, "done": 200, "qc": 200, "passed": 200}
    assert plan.kb_progress_stage(progress) == "ОТК пройден"
    key = plan.kit_stage_key(PROJECT, "КБ1")
    event = plan.PlanEvent(PROJECT, NOW, "Мастер", "Количество КБ", item_index="КБ1", progress=progress)
    manual = plan.PlanEvent(PROJECT, NOW, "Мастер", plan.PLAN_KINDS["stage"], item_index="КБ1", value="Новый")
    marks = plan.summarize_kit_marks([event, manual])
    assert marks[key].progress == progress
    assert plan.project_stage(PROJECT, ["КБ1", "КБ2"], {}, marks=marks).kits[0].status == "Новый"
    auto = plan.summarize_kit_marks([event, manual, replace(manual, value="авто")])
    stages = plan.project_stage(PROJECT, ["КБ1", "КБ2"], {}, marks=auto).kits
    assert [stage.status for stage in stages] == ["ОТК пройден", "Новый"]
    assert stages[0].progress == progress and stages[1].progress is None


@pytest.fixture()
def production():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        SqlAlchemyKitsetRepository(session).save(Kitset(
            project=PROJECT, customer="Заказчик", calc_source="", assemblies=(KitAssembly("КБ1", "Жгут", Decimal(200)),),
            updated_at=NOW, updated_by=ACTOR.name, created_at=NOW,
        ))
        session.commit()
    yield (lambda: flow.SqlAlchemyKbProgressUnitOfWork(sessions)), sessions
    engine.dispose()


def command(changes, *, sequence=0, expected_run=200, kb="КБ1"):
    return application.ChangeKbProgressCommand(
        project=PROJECT, kb=kb, changes=changes, expected_sequence=sequence, expected_run=expected_run,
        actor=ACTOR, now=NOW,
    )


def test_progress_persists_one_snapshot_notice_and_audit_and_rejects_stale_changes(production):
    factory, sessions = production
    application.change_kb_progress(command({"started": 80}), factory())
    event, notices = application.change_kb_progress(command({"done": 120}, sequence=1), factory())
    assert event.progress.counts() == {"started": 120, "done": 120, "qc": None, "passed": None}
    assert notices and "Начато: 80 → 120" in event.text
    with factory() as uow:
        stored = uow.plan_events.list(PROJECT)
    assert len(stored) == 2 and stored[-1].progress == event.progress
    with sessions() as session:
        assert len(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.kb_progress_changed")).all()) == 2
    with pytest.raises(application.StageChangeConflict):
        application.change_kb_progress(command({"qc": 130}, sequence=1), factory())
    with pytest.raises(application.StageChangeConflict):
        application.change_kb_progress(command({"qc": 130}, sequence=2, expected_run=150), factory())
    with factory() as uow:
        assert len(uow.plan_events.list(PROJECT)) == 2


def test_progress_snapshot_can_keep_later_counts_when_an_earlier_one_is_lowered(production):
    factory, sessions = production
    application.change_kb_progress(command({"qc": 150}), factory())
    event, notices = application.change_kb_progress(command({"done": 20}, sequence=1), factory())
    assert event.progress.counts() == {"started": 150, "done": 20, "qc": 150, "passed": None}
    assert event.progress.warnings() and notices == ()
    with sessions() as session:
        rows = session.scalars(select(ProductionPlanEventRow).order_by(ProductionPlanEventRow.sequence)).all()
        assert len(rows) == 2 and rows[-1].progress_done == 20 and rows[-1].progress_qc == 150


def test_progress_race_after_the_version_check_cannot_be_silently_renumbered(production, monkeypatch):
    factory, _ = production
    application.change_kb_progress(command({"started": 80}), factory())
    # Simulate a version check that ran before the other writer committed sequence 1.
    monkeypatch.setattr(SqlAlchemyPlanEventRepository, "last_sequence", lambda self, project: 0)
    with pytest.raises(IntegrityError):
        application.change_kb_progress(command({"done": 120}), factory())
    with factory() as uow:
        assert len(uow.plan_events.list(PROJECT)) == 1


def test_progress_api_uses_known_run_and_one_history_entry_for_the_auto_raise(kitset_api):
    client, _ = kitset_api
    assert _load(client).status_code == 200
    path = f"/api/techcards/kitsets/{PROJECT}/progress/КБ1"
    initial = client.get(path).json()
    assert initial["run"] == 10 and initial["counts"] == dict.fromkeys(plan.KB_PROGRESS_FIELDS)
    def save(counts, state):
        return client.post(path, json={"counts": counts, "expected_sequence": state["sequence"], "expected_run": state["run"]})
    started = save({"started": 5}, initial)
    assert started.status_code == 200, started.text
    done = save({"done": 8}, started.json())
    assert done.status_code == 200, done.text
    assert done.json()["counts"] == {"started": 8, "done": 8, "qc": None, "passed": None}
    assert "Начато: 5 → 8" in done.json()["notices"][0]
    assert done.json()["sequence"] == initial["sequence"] + 2
    assert save({"qc": 9}, initial).status_code == 409
    for bad in [-1, 11, "3", 1.5, True, None]:
        assert save({"qc": bad}, done.json()).status_code == 422
    assert save({"other": 1}, done.json()).status_code == 422
    # Any stage may be set manually; quantities do not remove that override or write stock.
    response = client.post(f"/api/techcards/kitsets/{PROJECT}/marks", json={"kb": "КБ1", "kind": "stage", "value": "Новый", "reason": "Проверяем ввод"})
    assert response.status_code == 200, response.text
    passed = save({"passed": 10}, client.get(path).json())
    assert passed.status_code == 200, passed.text
    card = client.get(f"/api/techcards/kitsets/{PROJECT}").json()
    assert card["assemblies"][0]["stage"]["status"] == "Новый"
    assert card["assemblies"][0]["stage"]["progress"]["counts"]["passed"] == 10
    assert card["assemblies"][1]["stage"].get("progress") is None
    with app.state.shipping_uow_factory() as uow:
        assert all(good.register.accepted_total == 0 for good in uow.goods.list())
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.get(path).status_code == 200
    assert save({"started": 1}, passed.json()).status_code == 403


def test_plan_progress_api_shares_projection_and_enforces_its_own_permission(kitset_api):
    client, _ = kitset_api
    _load(client)
    app.dependency_overrides[authenticated_principal] = _principal({"production": "edit"})
    path = "/api/production/plan/kb-progress"
    initial = client.get(path, params={"project": PROJECT, "kb": "КБ1"}).json()
    response = client.post(path, json={"project": PROJECT, "kb": "КБ1", "counts": {"qc": 4}, "expected_sequence": initial["sequence"], "expected_run": initial["run"]})
    assert response.status_code == 200, response.text
    assert response.json()["counts"] == {"started": 4, "done": 4, "qc": 4, "passed": None}
    row = client.get("/api/production/plan").json()["rows"][0]
    assert row["kits"][0]["status"] == "ОТК"
    assert row["kits"][0]["progress"]["counts"]["qc"] == 4
    app.dependency_overrides[authenticated_principal] = _principal({"production": "view"})
    assert client.get(path, params={"project": PROJECT, "kb": "КБ1"}).status_code == 200
    assert client.post(path, json={"project": PROJECT, "kb": "КБ1", "counts": {"qc": 4}, "expected_sequence": 0, "expected_run": 10}).status_code == 403


def test_run_change_since_preview_is_a_conflict_without_any_journal_write(production):
    factory, sessions = production
    key = plan.kit_stage_key(PROJECT, "КБ1")
    with sessions() as session:
        repository = SqlAlchemyKitsetRepository(session)
        kitset = repository.get(PROJECT)
        repository.save(replace(kitset, version=kitset.version + 1, assemblies=(replace(kitset.assemblies[0], run=Decimal(150)),)))
        session.commit()
    state = flow.ProjectStageState(project=PROJECT, kits=(), runs={key: 150})
    with pytest.raises(application.StageChangeConflict):
        flow.save_project_kb_progress(state, kb="КБ1", changes={"done": 100}, expected_sequence=0, expected_run=200,
                                      actor=ACTOR, now=NOW, uow_factory=factory)
    unknown = flow.preview_project_kb_progress(state, "КБ2")
    assert unknown["run"] is None and unknown["unavailable"] and unknown["counts"] == dict.fromkeys(plan.KB_PROGRESS_FIELDS)
    with factory() as uow:
        assert not uow.plan_events.list(PROJECT)


@pytest.mark.parametrize("source", ["kitset", "issued_card"])
@pytest.mark.parametrize("endpoint", ["kitset", "plan"])
def test_run_shrinks_after_stage_read_before_progress_transaction(kitset_api, monkeypatch, source, endpoint):
    """Deterministic interleaving, not a claim that SQLite exercises PostgreSQL advisory locks."""
    client, _ = kitset_api
    _load(client)
    if source == "issued_card":
        with app.state.techcards_uow_factory() as uow:
            kitset = uow.kitsets.get(PROJECT)
            uow.kitsets.save(replace(kitset, version=kitset.version + 1, assemblies=tuple(
                replace(item, run=Decimal(0)) for item in kitset.assemblies
            )))
            card = ProjectTechcard.create(project=PROJECT, item_index="КБ1", product_name="Жгут",
                                          source_calculation="", deadline=None, now=NOW)
            card = card.rebuild({"kit": {"part_quantity": "10"}, "operations": [{"code": "CUT"}]}, NOW).issue(NOW)
            uow.techcards.save(card)
            uow.commit()
    initial = client.get(f"/api/techcards/kitsets/{PROJECT}/progress/КБ1").json()
    assert initial["run"] == 10
    original = flow.change_kb_progress

    def shrink_then_save(command, uow):
        # The HTTP workflow already read run=10; the other transaction commits 5 now.
        with app.state.techcards_uow_factory() as writer:
            if source == "kitset":
                kitset = writer.kitsets.get(PROJECT)
                writer.kitsets.save(replace(kitset, version=kitset.version + 1, assemblies=tuple(
                    replace(item, run=Decimal(5)) for item in kitset.assemblies
                )))
            else:
                card = writer.techcards.for_project(PROJECT)[0]
                writer.techcards.save(card.rebuild({"kit": {"part_quantity": "5"}, "operations": [{"code": "CUT"}]}, NOW))
            writer.commit()
        return original(command, uow)

    monkeypatch.setattr(flow, "change_kb_progress", shrink_then_save)
    payload = {"counts": {"done": 8}, "expected_sequence": initial["sequence"], "expected_run": 10}
    if endpoint == "plan":
        app.dependency_overrides[authenticated_principal] = _principal({"production": "edit"})
        response = client.post("/api/production/plan/kb-progress", json={**payload, "project": PROJECT, "kb": "КБ1"})
    else:
        response = client.post(f"/api/techcards/kitsets/{PROJECT}/progress/КБ1", json=payload)
    assert response.status_code == 409, response.text
    with app.state.production_uow_factory() as uow:
        assert not [event for event in uow.plan_events.list(PROJECT) if event.progress is not None]
        assert not uow.session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "production.kb_progress_changed",
        )).all()


@pytest.mark.parametrize("run", [100, None])
def test_changed_run_projection_warns_without_rewriting_history_or_claiming_completion(run):
    key = plan.kit_stage_key(PROJECT, "КБ1")
    historical = plan.KbProgress(run=200, started=150, done=150, qc=150, passed=150)
    event = plan.PlanEvent(PROJECT, NOW, "Мастер", plan.PLAN_KINDS["progress"], item_index="КБ1", progress=historical)
    state = flow.project_stage_state(PROJECT, ["КБ1"], flow.KitsetFacts(
        events=(event,), runs={} if run is None else {key: run},
    ))
    preview = flow.preview_project_kb_progress(state, "КБ1")
    assert state.progress[key].run == run
    assert preview["counts"] == historical.counts() and preview["warnings"]
    assert bool(preview["unavailable"]) == (run is None)
    assert state.kits[0].status == "В работе"
    assert event.progress == historical and event.progress.run == 200


def test_sqlite_excludes_run_writer_until_progress_event_and_audit_commit(tmp_path, monkeypatch):
    # Separate connections to a real SQLite file: StaticPool's shared in-memory connection cannot prove exclusion.
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'run-lock.db').as_posix()}", connect_args={"timeout": 0.01})
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        kitset = Kitset(project=PROJECT, customer="Заказчик", calc_source="",
                        assemblies=(KitAssembly("КБ1", "Жгут", Decimal(200)),),
                        updated_at=NOW, updated_by=ACTOR.name, created_at=NOW)
        with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
            uow.kitsets.save(kitset)
            uow.commit()
        shrunk = replace(kitset, version=2, assemblies=(replace(kitset.assemblies[0], run=Decimal(100)),))
        original = SqlAlchemyPlanEventRepository.last_sequence
        attempts = []

        def sequence_while_refresh_attempts(repository, project):
            with SqlAlchemyTechcardsUnitOfWork(sessions) as writer:
                writer.kitsets.save(shrunk)
                with pytest.raises(OperationalError, match="locked"):
                    writer.commit()
            attempts.append(project)
            return original(repository, project)

        monkeypatch.setattr(SqlAlchemyPlanEventRepository, "last_sequence", sequence_while_refresh_attempts)
        event, _ = application.change_kb_progress(command({"done": 150}), flow.SqlAlchemyKbProgressUnitOfWork(sessions))
        assert attempts == [PROJECT]
        with sessions() as session:
            assert session.get(ProductionPlanEventRow, event.id).progress_done == 150
            assert len(session.scalars(select(AuditEventRow).where(
                AuditEventRow.event_type == "production.kb_progress_changed",
            )).all()) == 1
        # The run may change AFTER the count commit; this does not rewrite its historical denominator.
        with SqlAlchemyTechcardsUnitOfWork(sessions) as writer:
            writer.kitsets.save(shrunk)
            writer.commit()
        with sessions() as session:
            assert SqlAlchemyKitsetRepository(session).get(PROJECT).assemblies[0].run == 100
            assert session.get(ProductionPlanEventRow, event.id).progress_run == 200
    finally:
        engine.dispose()


def test_postgresql_project_run_lock_uses_canonical_key_in_the_callers_transaction():
    # Contract test only; does not simulate PostgreSQL blocking or transaction isolation.
    session = MagicMock(spec=Session)
    session.get_bind.return_value.dialect.name = "postgresql"
    for project in ("708K.4", " 708к.4 "):
        lock_project_run(session, project)
    assert len(session.execute.call_args_list) == 2
    for call in session.execute.call_args_list:
        statement, parameters = call.args
        assert str(statement) == "SELECT pg_advisory_xact_lock(:key)"
        assert parameters == {"key": lock_key("project-kb-run", PROJECT)}
    session.commit.assert_not_called()
    session.close.assert_not_called()


@pytest.mark.parametrize("endpoint", ["kitset", "plan"])
def test_progress_preview_and_partial_save_keep_latest_snapshot_beyond_journal_cap(kitset_api, endpoint):
    client, _ = kitset_api
    _load(client)
    with app.state.production_uow_factory() as uow:
        # Bulk seed the real cap; do not spend 10,000 per-row repository queries on fixture setup.
        uow.session.execute(insert(ProductionPlanEventRow), [
            {"id": uuid4(), "project": PROJECT, "sequence": sequence, "occurred_at": NOW,
             "who": ACTOR.name, "kind": plan.PLAN_KINDS["note"], "blocker": "", "text": "История"}
            for sequence in range(1, 10_001)
        ])
        uow.plan_events.add(plan.PlanEvent(
            PROJECT, NOW, ACTOR.name, plan.PLAN_KINDS["progress"], item_index="КБ1", sequence=10_001,
            progress=plan.KbProgress(run=10, started=9, done=8, qc=6),
        ))
        # A newer snapshot of another KB must not become the source for this update.
        uow.plan_events.add(plan.PlanEvent(
            PROJECT, NOW, ACTOR.name, plan.PLAN_KINDS["progress"], item_index="КБ2", sequence=10_002,
            progress=plan.KbProgress(run=10, started=1),
        ))
        uow.commit()
    if endpoint == "kitset":
        path = f"/api/techcards/kitsets/{PROJECT}/progress/КБ1"
        params = {}
        identity = {}
    else:
        app.dependency_overrides[authenticated_principal] = _principal({"production": "edit"})
        path = "/api/production/plan/kb-progress"
        params = identity = {"project": PROJECT, "kb": "КБ1"}
    preview = client.get(path, params=params).json()
    assert preview["sequence"] == 10_002
    assert preview["counts"] == {"started": 9, "done": 8, "qc": 6, "passed": None}
    response = client.post(path, json={**identity, "counts": {"passed": 4}, "expected_sequence": 10_002, "expected_run": 10})
    assert response.status_code == 200, response.text
    assert response.json()["counts"] == {"started": 9, "done": 8, "qc": 6, "passed": 4}
    assert client.get(path, params=params).json()["counts"] == response.json()["counts"]
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view", "production": "view"})
    card = client.get(f"/api/techcards/kitsets/{PROJECT}").json()
    tile = next(item for item in client.get("/api/techcards/kitsets").json()["items"] if item["project"] == PROJECT)
    row = next(item for item in client.get("/api/production/plan").json()["rows"] if item["project"] == PROJECT)
    for stage in (card["assemblies"][0]["stage"], tile["kb_stages"][0], row["kits"][0]):
        assert stage["progress"]["counts"] == response.json()["counts"]
        assert stage["status"] == "ОТК"
    for stage in (card["assemblies"][1]["stage"], tile["kb_stages"][1], row["kits"][1]):
        assert stage["progress"]["counts"] == {"started": 1, "done": None, "qc": None, "passed": None}
        assert stage["status"] == "В работе"
    with app.state.production_uow_factory() as uow:
        projected = uow.plan_events.list_with_latest_progress()
        assert len(projected) == 10_002  # 10,000 ordinary rows plus ONE latest snapshot for each KB.
        assert [event.sequence for event in projected if event.progress is not None] == [10_002, 10_003]
