"""Project identity repair: real aggregates, FK enforcement, dry run and atomic refusals."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.exc import IntegrityError

from app.modules.fulfillment.application import HoldKitReserveCommand, hold_kit_reserve
from app.modules.production.infrastructure import ProductionPlanProjectRow
from app.modules.production.plan import PlanEvent
from app.modules.shipping.application import CreateShipmentCommand, QueueProjectCommand, RequestedGood, create_shipment, queue_project
from app.modules.shipping.domain import ProjectAssembly
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.modules.techcards.project_cards import ProjectTechcard, TechcardRevision
from app.modules.techcards.td_issues import TdIssue, parse_input
from app.shared.persistence import Base
from app.workflows import rename_project as cli
from app.workflows.project_kitset import delete_kitset
from tests.techcards.test_project_cards import _document
from tests.workflows.test_project_tails import ACTOR, NOW, OPERATOR, _handed, _kit, _request, _task, erp  # noqa: F401

OLD, NEW = "228К.20", "228К.21"


@pytest.fixture()
def db(erp):
    engine = erp.sessions.kw["bind"]
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    return erp, engine


def _store_card(state, project=OLD):
    document = {**_document(), "header": {"project": project, "item": project + "КБ1"},
                "build_config": {"projectNo": project, "itemId": project + "КБ1"},
                "comment": f"history mentions {project}"}
    card = ProjectTechcard.create(project=project, item_index="КБ1", product_name="Жгут",
                                  source_calculation="Расчёт 228К.21", deadline=None, now=NOW)
    card = card.rebuild(document, NOW).issue(NOW)
    with state.techcards_uow_factory() as uow:
        uow.kitsets.save(Kitset(project=project, customer="Заказчик", calc_source="source-id",
            assemblies=(KitAssembly(index="КБ1", name="Жгут", run=Decimal(5)),),
            updated_at=NOW, updated_by=ACTOR.name, created_at=NOW))
        uow.techcards.save(card)
        uow.techcards.add_revision(TechcardRevision(id=uuid4(), project_card_id=card.id,
            revision=card.revision, reason=f"выдана {project}", actor_name=ACTOR.name, occurred_at=NOW,
            document=document))
        issue = TdIssue.open(card_id=card.id,
            value=parse_input({"method_code": "85", "workshop": "Цех", "site": "ЦАР"}),
            snapshot={"project": project, "revision": card.revision, "document": document},
            existing=[], next_sequence=1 if project == OLD else 2, actor=ACTOR.name, now=NOW)
        uow.td_issues.add(issue)
        uow.commit()
    return card, issue


def _seed(state, project=OLD):
    card, issue = _store_card(state, project)
    with state.production_uow_factory() as uow:
        uow.plan_events.add(PlanEvent(project=project, occurred_at=NOW, who=ACTOR.name,
                                     kind="Комментарий", text=f"original text: {project}"))
        uow.session.add(ProductionPlanProjectRow(project=project, customer="Заказчик", status="",
                                                terminal=False, imported_at=NOW))
        uow.commit()
    queue_project(QueueProjectCommand(project=project, customer="Заказчик", assemblies=(
        ProjectAssembly(item_index="КБ1", name="Жгут", run=Decimal(5), article=project + "КБ1"),
    ), actor=ACTOR, now=NOW), state.shipping_uow_factory())
    with state.shipping_uow_factory() as uow:
        good = uow.goods.by_project(project)[0]
    create_shipment(CreateShipmentCommand(customer="Заказчик", receiver="Получатель", shipping_point="Москва",
        contact="", phone="", terms=None, carrier="", deadline=None, upd_type=None, upd_number="", upd_date=None,
        upd_url="", note="", lines=(RequestedGood(good_id=good.id, quantity=Decimal(1)),),
        actor=ACTOR, now=NOW), state.shipping_uow_factory())
    with state.fulfillment_uow_factory() as uow:
        reserve = hold_kit_reserve(HoldKitReserveCommand(project=project, item_index="КБ1",
            article=state.lot.article, lot_id=state.lot.id, quantity=Decimal(2), unit="шт",
            actor=ACTOR, now=NOW), uow)
        uow.commit()
    assert reserve is not None
    kit = _kit(state, f"{project} · КБ2 · Жгут")
    task = _task(state, project, project + "КБ1", box=True)
    request = _request(state, base=project)
    purchase = _handed(state, request)
    with state.sessions() as session:
        baseline_id, work_id = uuid4(), uuid4()
        session.execute(Base.metadata.tables["production_plan_baselines"].insert().values(
            id=baseline_id, number=1 if project == OLD else 2, created_at=NOW,
            created_by_id=ACTOR.id, created_by=ACTOR.name, skipped=0))
        session.execute(Base.metadata.tables["production_plan_baseline_lines"].insert().values(
            baseline_id=baseline_id, task_id=task.id, task_number=task.number, project=project, item=project + "КБ1",
            planned_start=NOW, planned_finish=NOW + timedelta(hours=1), quantity=2))
        operation = task.operations[0]
        session.execute(Base.metadata.tables["production_work_sessions"].insert().values(
            id=work_id, task_id=task.id, operation_id=operation.id, operator_id=OPERATOR, started_at=NOW,
            status="Завершена", pause_seconds=0))
        session.execute(Base.metadata.tables["production_work_time_log"].insert().values(
            id=uuid4(), idempotency_key=str(uuid4()), session_id=work_id, sequence=0, event="start", occurred_at=NOW,
            task_id=task.id, operation_id=operation.id, operator_id=OPERATOR, operator_name="Монтажник",
            actor_id=OPERATOR, actor_name="Монтажник", project=project, item=project + "КБ1", task_number=task.number,
            operation_code=operation.code, stage=operation.stage, position=1,
            seconds_per_unit=60, setup_seconds=0, planned_norm_seconds=120))
        marks = Base.metadata.tables["operations_job_marks"]
        if session.execute(select(marks).where(marks.c.key == "tc_name_queue")).first() is None:
            session.execute(marks.insert().values(key="tc_name_queue", value=json.dumps([OLD, OLD + ".1"]), marked_at=NOW))
        session.commit()
    return card, issue, kit, reserve, task, request, purchase


def _snapshot(engine):
    metadata = MetaData()
    with engine.connect() as connection:
        metadata.reflect(bind=connection)
        return {name: sorted((repr(dict(row)) for row in connection.execute(select(table)).mappings()))
                for name, table in metadata.tables.items()}


def _rows(engine, name):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(Base.metadata.tables[name])).mappings()]


def test_delete_issued_card_with_td_issue_and_revision(db):
    state, engine = db
    card, issue = _store_card(state)
    assert len(_rows(engine, "td_issues")) == 1
    result = delete_kitset(OLD, ACTOR, NOW, state.techcards_uow_factory, None, None)
    assert result["cards"] == {"cards": 1, "revisions": 1, "td_issues": 1}
    for name in ("techcard_kitsets", "techcard_projects", "techcard_revisions", "td_issues"):
        assert _rows(engine, name) == []
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_rename_moves_aggregates_and_keeps_sibling_and_history(db):
    state, engine = db
    card, issue, kit, reserve, task, request, purchase = _seed(state)
    sibling = _seed(state, OLD + ".1")
    audit_before = _rows(engine, "audit_events")
    versions_before = {name: {row["id"]: row["version"] for row in _rows(engine, name)}
                       for name in ("techcard_projects", "td_issues", "fulfillment_requests", "production_tasks", "procurement_lines")}
    # Same guards as migrated production: no UPDATE, no trigger bypass by the CLI.
    with engine.begin() as connection:
        for name in ("production_plan_events", "production_work_time_log", "production_plan_baseline_lines"):
            connection.exec_driver_sql(f"CREATE TRIGGER {name}_no_update BEFORE UPDATE ON {name} BEGIN SELECT RAISE(ABORT, 'immutable'); END")
    before = cli.rename_project(engine, OLD, NEW, now=NOW)
    report = cli.rename_project(engine, OLD, NEW, apply=True, now=NOW)
    assert report.applied and report.counts == before.counts
    for name in ("techcard_kitsets", "techcard_projects", "production_plan_projects", "production_plan_baseline_lines",
                 "production_work_time_log", "finished_goods", "shipment_lines", "shipping_project_addressees",
                 "production_tasks", "fulfillment_kit_reserves"):
        assert {row["project"] for row in _rows(engine, name)} == {NEW, OLD + ".1"}, name
    with state.techcards_uow_factory() as uow:
        moved = uow.techcards.get(card.id)
        assert moved.project == NEW and moved.status == card.status and moved.revision == card.revision
        assert moved.document["build_config"] == {"projectNo": NEW, "itemId": NEW + "КБ1"}
        assert moved.document["comment"] == f"history mentions {OLD}"
        assert uow.td_issues.get(issue.id).snapshot["project"] == NEW
        assert uow.techcards.get(sibling[0].id).project == OLD + ".1"
        assert uow.techcards.revisions(card.id)[0].document["header"]["project"] == NEW
    for name, versions in versions_before.items():
        for row in _rows(engine, name):
            if row["id"] in {card.id, issue.id, kit.id, task.id, purchase.id}:
                assert row["version"] == versions[row["id"]] + 1
    assert {row["article"] for row in _rows(engine, "finished_goods")} == {NEW + "КБ1", OLD + ".1КБ1"}
    assert {row["item"] for row in _rows(engine, "production_boxes")} == {NEW + "КБ1", OLD + ".1КБ1"}
    assert {row["purpose"] for row in _rows(engine, "fulfillment_requests")} == {f"{NEW} · КБ2 · Жгут", f"{OLD}.1 · КБ2 · Жгут"}
    assert {row["designation"] for row in _rows(engine, "requests")} == {NEW + ".001", OLD + ".1.001"}
    assert {row["designation"] for row in _rows(engine, "procurement_lines")} == {NEW + ".001", OLD + ".1.001"}
    assert _rows(engine, "operations_job_marks")[0]["value"] == json.dumps([NEW, OLD + ".1"], ensure_ascii=False)
    journal = _rows(engine, "production_plan_events")
    assert any(row["project"] == NEW and row["text"] == f"original text: {OLD}" for row in journal)
    assert any(row["project"] == NEW and row["text"] == f"проект переименован {OLD} → {NEW}" for row in journal)
    audit_after = _rows(engine, "audit_events")
    assert all(row in audit_after for row in audit_before)
    assert len(audit_after) == len(audit_before) + 1
    assert next(row for row in audit_after if row not in audit_before)["actor_name"] == "администратор (CLI)"
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        with pytest.raises(IntegrityError, match="immutable"):
            connection.exec_driver_sql("UPDATE production_plan_events SET text='edited'")


def test_dry_run_changes_nothing_and_reports_per_table(db):
    state, engine = db
    _seed(state)
    before = _snapshot(engine)
    report = cli.rename_project(engine, OLD, NEW, now=NOW)
    assert _snapshot(engine) == before
    assert report.counts["td_issues"] == 1
    assert report.counts["production_plan_events"] == 1
    assert "DRY RUN" in report.format() and "techcard_projects | 1" in report.format()


@pytest.mark.parametrize("old,new", [(OLD, OLD), (OLD, "228K.21"), (OLD, "bad"), ("999К.1", NEW), (OLD, " 228к.20 ")])
def test_refusals_write_nothing(db, old, new):
    state, engine = db
    _seed(state)
    before = _snapshot(engine)
    with pytest.raises(cli.RenameRefused):
        cli.rename_project(engine, old, new, apply=True)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("where", ["kitset", "plan", "shipping", "fulfillment", "purchase", "outbox", "techcards_only"])
def test_destination_collision_anywhere_writes_nothing(db, where):
    state, engine = db
    _store_card(state)
    if where == "kitset":
        _store_card(state, NEW)
    elif where == "techcards_only":
        _store_card(state, NEW)
        with state.techcards_uow_factory() as uow:
            uow.kitsets.delete(NEW)
            uow.commit()
    elif where == "fulfillment":
        _kit(state, f"228k.21 · КБ1 · Жгут")
    elif where == "shipping":
        queue_project(QueueProjectCommand(project=NEW, customer="", assemblies=(
            ProjectAssembly(item_index="КБ1", name="Жгут", run=Decimal(1), article=NEW + "КБ1"),
        ), actor=ACTOR, now=NOW), state.shipping_uow_factory())
    elif where == "purchase":
        _request(state, reference=NEW)
    else:
        with state.sessions() as session:
            if where == "plan":
                session.add(ProductionPlanProjectRow(project="228k.21", customer="", status="", terminal=False, imported_at=NOW))
            elif where == "outbox":
                table = Base.metadata.tables["outbox_messages"]
                session.execute(table.insert().values(id=uuid4(), topic="test", idempotency_key="test", created_at=NOW,
                                                      payload={"project": NEW}))
            session.commit()
    before = _snapshot(engine)
    with pytest.raises(cli.RenameRefused, match="NEW already exists"):
        cli.rename_project(engine, OLD, NEW, apply=True)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("value,expected", [
    (OLD, NEW), (" 228k.20 ", NEW), (OLD + "КБ1", NEW + "КБ1"),
    (OLD + " · КБ1 · Жгут", NEW + " · КБ1 · Жгут"),
    (OLD + ".1", OLD + ".1"), (OLD + "1", OLD + "1"), (OLD + ".1КБ1", OLD + ".1КБ1"),
    (OLD + ".1 · КБ1", OLD + ".1 · КБ1"), ("ref " + OLD, "ref " + OLD),
    ("https://example.test/" + OLD, "https://example.test/" + OLD),
    (OLD + "КБ", OLD + "КБ"), (OLD + ".001", OLD + ".001"),
])
def test_exact_compound_boundaries(value, expected):
    assert cli.rename_token(value, OLD, NEW) == expected


@pytest.mark.parametrize("value,expected", [(OLD + ".001", NEW + ".001"), (OLD + ".1", OLD + ".1"),
                                           (OLD + ".1.001", OLD + ".1.001")])
def test_designation_boundary(value, expected):
    assert cli.rename_token(value, OLD, NEW, designation=True) == expected


def test_normalized_base_form_and_json_keys():
    assert cli.rename_token("2000KКБ1", "2000К.1", NEW) == NEW + "КБ1"
    assert cli.rename_json({OLD: {"designation": OLD + ".001", "n": 1, "empty": None}}, OLD, NEW) == {
        NEW: {"designation": NEW + ".001", "n": 1, "empty": None}}


@pytest.mark.parametrize("name", sorted(cli.POLICIES))
def test_every_inventoried_column_exact_match_and_sibling(name):
    table = Base.metadata.tables[name]
    row = {column: None for column in table.c.keys()}
    row.update({"key": "tc_name_queue", "published_at": None})
    for column, policy in cli.POLICIES[name].items():
        value = {"projectNo": OLD, "itemId": OLD + "КБ1"} if policy == "j" else OLD
        row[column] = json.dumps(value) if name == "operations_job_marks" else value
    moved = cli._changes(table, row, OLD, NEW)
    assert set(moved) == set(cli.POLICIES[name])
    assert cli._changes(table, row, OLD + ".1", NEW) == {}


def test_failure_after_first_write_rolls_back_everything(db):
    state, engine = db
    _seed(state)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TRIGGER refuse_rename BEFORE UPDATE ON techcard_kitsets BEGIN SELECT RAISE(ABORT, 'refuse'); END")
    before = _snapshot(engine)
    with pytest.raises(IntegrityError):
        cli.rename_project(engine, OLD, NEW, apply=True)
    assert _snapshot(engine) == before


def test_cli_requires_explicit_url_and_uses_exit_two(db, monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert cli.main([OLD, NEW]) == 2
    assert "ERP_DATABASE_URL" in capsys.readouterr().err


def test_pending_outbox_moves_published_payload_stays(db):
    state, engine = db
    _store_card(state)
    table = Base.metadata.tables["outbox_messages"]
    with engine.begin() as connection:
        for published in (None, NOW):
            connection.execute(table.insert().values(id=uuid4(), topic="test", idempotency_key=str(uuid4()),
                created_at=NOW, published_at=published, payload={"project": OLD, "designation": OLD + ".001"}))
    cli.rename_project(engine, OLD, NEW, apply=True)
    rows = _rows(engine, "outbox_messages")
    assert next(row for row in rows if row["published_at"] is None)["payload"] == {"project": NEW, "designation": NEW + ".001"}
    assert next(row for row in rows if row["published_at"] is not None)["payload"] == {"project": OLD, "designation": OLD + ".001"}


def test_composition_hash_keeps_recorded_request_links_on_unchanged_reimport(db):
    from app.modules.requests.composition import CompositionSeed, save_composition

    state, engine = db
    _store_card(state)
    rows = [{"source_row": 10, "name": "Провод", "request_id": None, "request_link_state": "unlinked"}]
    request_id = uuid4()
    with state.uow_factory() as uow:
        saved = save_composition(CompositionSeed(OLD, "source-id", rows), 0, ACTOR, NOW, uow, request_ids={10: request_id})
        uow.commit()
    cli.rename_project(engine, OLD, NEW, apply=True)
    with state.uow_factory() as uow:
        reimported = save_composition(CompositionSeed(NEW, "source-id", rows), saved.version + 1, ACTOR, NOW, uow)
        assert reimported.version == saved.version + 1
        assert reimported.rows[0]["request_id"] == str(request_id)
        assert reimported.rows[0]["request_link_state"] == "recorded"


def test_draft_json_and_optimistic_versions_move(db):
    state, engine = db
    _store_card(state)
    table = Base.metadata.tables["techcard_generator_drafts"]
    with engine.begin() as connection:
        connection.execute(table.insert().values(id=uuid4(), user_id=OPERATOR, project=OLD, item_index="КБ1",
            payload={"projectNo": OLD, "itemId": OLD + "КБ1"}, build_fingerprint="old", version=7, created_at=NOW, updated_at=NOW))
    cli.rename_project(engine, OLD, NEW, apply=True)
    [row] = _rows(engine, "techcard_generator_drafts")
    assert row["project"] == NEW and row["payload"] == {"projectNo": NEW, "itemId": NEW + "КБ1"}
    assert row["version"] == 8 and row["build_fingerprint"] == ""


@pytest.mark.parametrize("status", ["pending", "taken", "printed"])
def test_label_jobs_rerender_pending_refuse_taken_preserve_printed(db, status):
    state, engine = db
    _store_card(state)
    with state.fulfillment_uow_factory() as uow:
        lot = uow.warehouse.get(state.lot.id)
        uow.warehouse.add(replace(lot, article=OLD + "КБ1", purpose=OLD, version=lot.version + 1))
        uow.commit()
    table = Base.metadata.tables["warehouse_label_jobs"]
    with engine.begin() as connection:
        connection.execute(table.insert().values(id=uuid4(), created_at=NOW, created_by=ACTOR.name, lot_id=state.lot.id,
            kind="lot", copies=2, tspl=b"old raster", status=status, taken_at=NOW if status != "pending" else None,
            lease_until=NOW + timedelta(minutes=2) if status == "taken" else None,
            finished_at=NOW if status == "printed" else None))
    before = _snapshot(engine)
    if status == "taken":
        with pytest.raises(cli.RenameRefused, match="label job is taken"):
            cli.rename_project(engine, OLD, NEW, apply=True)
        assert _snapshot(engine) == before
        return
    report = cli.rename_project(engine, OLD, NEW)
    assert _snapshot(engine) == before
    actual = cli.rename_project(engine, OLD, NEW, apply=True)
    assert actual.counts == report.counts
    [job] = _rows(engine, "warehouse_label_jobs")
    assert job["copies"] == 2 and job["status"] == status
    assert (job["tspl"] != b"old raster") == (status == "pending")
    assert report.counts["warehouse_label_jobs"] == int(status == "pending")


def test_unknown_project_column_refuses_without_writes(db):
    state, engine = db
    _store_card(state)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE future_module (id INTEGER PRIMARY KEY, project TEXT)")
    before = _snapshot(engine)
    with pytest.raises(cli.RenameRefused, match="Uninventoried project column"):
        cli.rename_project(engine, OLD, NEW, apply=True)
    assert _snapshot(engine) == before


def test_pending_finished_goods_act_label_moves_with_its_stable_act_id(db):
    from app.modules.shipping.application import AcceptGoodCommand, accept_good
    from app.workflows.finished_goods_label import act_label_data

    state, engine = db
    _seed(state)
    with state.shipping_uow_factory() as uow:
        good = uow.goods.by_project(OLD)[0]
    accept_good(AcceptGoodCommand(good_id=good.id, expected_version=good.version,
        quantity=Decimal(1), received_at=NOW.date(), actor=ACTOR, now=NOW), state.shipping_uow_factory())
    [act] = _rows(engine, "finished_goods_acts")
    jobs = Base.metadata.tables["warehouse_label_jobs"]
    job_id = uuid4()
    with engine.begin() as connection:
        connection.execute(jobs.insert().values(id=job_id, created_at=NOW, created_by=ACTOR.name,
            lot_id=act["id"], kind="lot", copies=1, tspl=b"old act raster", status="pending"))
    report = cli.rename_project(engine, OLD, NEW, apply=True)
    assert report.counts["warehouse_label_jobs"] == 1
    [job] = _rows(engine, "warehouse_label_jobs")
    assert (job["id"], job["lot_id"]) == (job_id, act["id"])
    assert job["tspl"] != b"old act raster"
    label = act_label_data(state.sessions, act["grn"])
    assert label.project == NEW and label.data.sku == NEW + " КБ1"


def test_receiving_designation_and_pending_payload_both_move(db):
    state, engine = db
    _store_card(state)
    table = Base.metadata.tables["receiving_lines"]
    row = {column: None for column in table.c.keys()}
    row.update(purpose=OLD + ".001", goal=OLD, purpose_project_key=OLD, goal_project_key=OLD, article=OLD + "КБ1")
    assert cli._changes(table, row, OLD, NEW) == {
        "purpose": NEW + ".001", "goal": NEW, "purpose_project_key": NEW, "goal_project_key": NEW, "article": NEW + "КБ1"}
    assert cli.rename_json({"purpose": OLD + ".001", "goal": OLD, "name": "mention " + OLD}, OLD, NEW) == {
        "purpose": NEW + ".001", "goal": NEW, "name": "mention " + OLD}


def test_fresh_process_cli_dry_run_apply_and_refusal_with_pending_labels(db, tmp_path):
    state, engine = db
    _seed(state)
    lots, jobs = Base.metadata.tables["warehouse_lots"], Base.metadata.tables["warehouse_label_jobs"]
    with engine.begin() as connection:
        connection.execute(lots.update().where(lots.c.id == state.lot.id).values(article=OLD + "КБ1", purpose=OLD))
        connection.execute(jobs.insert().values(id=uuid4(), created_at=NOW, created_by=ACTOR.name,
            lot_id=state.lot.id, kind="lot", copies=1, tspl=b"old raster", status="pending"))
    path = tmp_path / "rename.db"
    with engine.connect() as source, closing(sqlite3.connect(path)) as destination:
        source.connection.driver_connection.backup(destination)
    url = f"sqlite:///{path.as_posix()}"
    env = {**os.environ, "ERP_DATABASE_URL": url}
    command = [sys.executable, "-m", "app.workflows.rename_project", OLD, NEW]
    copy = create_engine(url)
    try:
        before = _snapshot(copy)
        dry = subprocess.run(command, env=env, cwd=Path(__file__).resolve().parents[2],
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert dry.returncode == 0, dry.stderr
        assert "DRY RUN" in dry.stdout and "warehouse_label_jobs | 1" in dry.stdout
        assert _snapshot(copy) == before
        applied = subprocess.run([*command, "--apply"], env=env, cwd=Path(__file__).resolve().parents[2],
                                 capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert applied.returncode == 0, applied.stderr
        assert applied.stdout.replace("APPLIED", "DRY RUN") == dry.stdout
        assert _rows(copy, "techcard_kitsets")[0]["project"] == NEW
        after = _snapshot(copy)
        refused = subprocess.run([*command, "--apply"], env=env, cwd=Path(__file__).resolve().parents[2],
                                 capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert refused.returncode == 2
        assert _snapshot(copy) == after
    finally:
        copy.dispose()


def test_open_generated_task_title_moves_closed_and_custom_text_stays(db):
    from app.workflows.arbiter import PLAN

    state, engine = db
    _store_card(state)
    table = Base.metadata.tables["project_tasks"]
    with engine.begin() as connection:
        for status, title in (("open", PLAN.text(OLD, "КБ1")), ("done", PLAN.text(OLD, "КБ1")), ("open", "Custom " + OLD)):
            connection.execute(table.insert().values(id=uuid4(), project=OLD, item_index="КБ1", module=PLAN.module,
                kind=PLAN.key, title=title, source_event_id=uuid4(), source_type="test", status=status, created_at=NOW))
    cli.rename_project(engine, OLD, NEW, apply=True)
    rows = _rows(engine, "project_tasks")
    assert all(row["project"] == NEW for row in rows)
    assert {row["title"] for row in rows} == {PLAN.text(NEW, "КБ1"), PLAN.text(OLD, "КБ1"), "Custom " + OLD}
