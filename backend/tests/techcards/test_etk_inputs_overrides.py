"""Входы и правки ЭТК хранятся рядом с документом (автоматическая ревизия, владелец 19.10.2026): конфиг генератора как
пришёл — до норм справочников; правка операции и ручное деление на ПФ — по ключам операций, переживают пересборку."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.modules.techcards.application import (
    AutoBuildProjectTechcardCommand, ChangeProjectTechcardCommand, CreateProjectTechcardCommand,
    EditTechcardOperationCommand, RebuildProjectTechcardCommand, SaveTechcardRouteCommand,
    auto_build_project_techcard, create_project_techcard, edit_project_operation, issue_project_techcard,
    rebuild_project_techcard, save_project_route,
)
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import operation_key
from app.shared.clock import MOSCOW
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.techcards.test_scheme_corrections import GENERATOR

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=MOSCOW)
ACTOR = Actor(name="Технолог", department="Цех")


def _app(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'erp.db'}")
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)  # noqa: E731
    with factory() as uow:
        uow.operation_cards.replace((), source_commit="lib-1", imported_at=NOW)
        uow.commit()
    card = create_project_techcard(CreateProjectTechcardCommand(
        project="P-1", item_index="1", product_name="Жгут", source_calculation="", deadline=None,
        actor=ACTOR, now=NOW,
    ), factory())
    return engine, factory, card


def _build(factory, card, config, *, inputs, minutes=0):
    return auto_build_project_techcard(AutoBuildProjectTechcardCommand(
        card_id=card.id, expected_version=card.version, actor=ACTOR, now=NOW + timedelta(minutes=minutes),
        config=config, inputs=inputs,
    ), factory())


def _change(card, minutes=0):
    return dict(card_id=card.id, expected_version=card.version, actor=ACTOR, now=NOW + timedelta(minutes=minutes))


def test_inputs_are_stored_as_received_not_enriched(tmp_path):
    engine, factory, card = _app(tmp_path)
    raw = {**GENERATOR}
    enriched = {**GENERATOR, "norms": {"filled": "by references"}}
    built = _build(factory, card, enriched, inputs=raw)
    assert built.inputs == raw and "norms" not in built.inputs
    with factory() as uow:
        assert uow.techcards.get(card.id).inputs == raw
    # A build without raw inputs keeps what the card has.
    again = _build(factory, built, enriched, inputs=None, minutes=1)
    assert again.inputs == raw
    engine.dispose()


def test_operation_edit_is_stored_as_override_and_survives_rebuild(tmp_path):
    engine, factory, card = _app(tmp_path)
    built = _build(factory, card, GENERATOR, inputs=GENERATOR)
    document = built.document
    # The library here is empty: the operation gets its card by hand, then the ETK is issued.
    document["operations"][0]["card"] = {"name": "Резка", "steps": [{"kind": "Операция", "text": "Отрезать", "tolerance": ""}], "tooling": [], "safety": "", "defects": ""}
    rebuilt = rebuild_project_techcard(RebuildProjectTechcardCommand(document=document, **_change(built, 1)), factory())
    issued = issue_project_techcard(ChangeProjectTechcardCommand(**_change(rebuilt, 2)), factory())
    operation = issued.document["operations"][0]
    key = operation_key(operation)
    edited = {**operation, "card": {**operation["card"], "safety": "Перчатки"}}
    saved = edit_project_operation(EditTechcardOperationCommand(
        operation_key=key, operation=edited, **_change(issued, 3)), factory())
    assert "Перчатки" in saved.overrides["operations"][key]["operation"]["card"]["safety"]
    assert saved.overrides["operations"][key]["base_template_version"] == ""
    again = _build(factory, saved, GENERATOR, inputs=GENERATOR, minutes=4)
    assert again.overrides == saved.overrides and key in again.manual_operation_keys
    assert "Перчатки" in again.document["operations"][0]["card"]["safety"]
    engine.dispose()


def test_hand_route_is_stored_as_blocks_of_operation_keys_and_survives_rebuild(tmp_path):
    engine, factory, card = _app(tmp_path)
    built = _build(factory, card, GENERATOR, inputs=GENERATOR)
    saved = save_project_route(SaveTechcardRouteCommand(route=built.document["route"], **_change(built, 1)), factory())
    keys = [operation_key(op) for op in saved.document["operations"]]
    route = saved.overrides["route"]
    assert route["source"] == "manual" and route["edges"]
    assert sorted(key for block in route["blocks"] for key in block["ops"]) == sorted(keys)
    assert {"key", "name", "final", "ops"} <= set(route["blocks"][0])
    again = _build(factory, saved, GENERATOR, inputs=GENERATOR, minutes=2)
    assert again.overrides["route"] == saved.overrides["route"]
    engine.dispose()


def test_revision_source_defaults_to_manual_and_schema_has_the_new_columns(tmp_path):
    engine, factory, card = _app(tmp_path)
    built = _build(factory, card, GENERATOR, inputs=GENERATOR)
    issued = issue_project_techcard(ChangeProjectTechcardCommand(**_change(built, 1)), factory())
    with factory() as uow:
        [revision] = uow.techcards.revisions(issued.id)
    assert revision.source == "manual" and revision.diff is None
    inspector = inspect(engine)
    assert {"inputs", "overrides", "review_note", "derived_hash"} <= {c["name"] for c in inspector.get_columns("techcard_projects")}
    assert {"source", "diff"} <= {c["name"] for c in inspector.get_columns("techcard_revisions")}
    assert "techcard_refresh_requests" in inspector.get_table_names()
    engine.dispose()
