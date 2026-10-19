"""The ETK refresh job (owner 19.10.2026): a reference or rule change issues a new revision of an issued card by itself,
the technologist's edits kept; one batch — one revision; a rerun is a no-op; a dry run writes nothing."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.modules.references.domain import TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import load_mirror
from app.modules.techcards import etk_derive
from app.modules.techcards.etk_read import card_view, pinned_card
from app.modules.techcards.infrastructure import (
    ProjectTechcardRow, RefreshRequestRow, SqlAlchemyTechcardsUnitOfWork, TechcardRevisionRow,
)
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard, TechcardRevision, TechcardStatus, operation_key
from app.workflows import etk_refresh
from app.workflows.etk_refresh import NOTE_PREFIX, run_refresh
from app.workflows.techcard_build import load_refs, with_technology
from tests.references.test_load_mirror import sessions  # noqa: F401  (fixture)

BACKEND = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((BACKEND / "data" / "techcard-library.json").read_text(encoding="utf-8"))
ERP_LIBRARY = json.loads((BACKEND / "data" / "techcard-library-erp.json").read_text(encoding="utf-8"))
CARDS = tuple(parse_operation_library(LIBRARY["templates"] + ERP_LIBRARY["templates"]))
CONFIG = json.loads((BACKEND.parent / "frontend" / "src" / "fixtures" / "coaxSmaRg316Rev1.json").read_text(
    encoding="utf-8"))["document"]["build_config"]
MIRROR = BACKEND / "data" / "reference-mirror"
DEPTH = "Заглубление контакта, мм"
ARTICLE = "SMA-C174P"
T0 = datetime(2026, 10, 19, 9, 0)
T1 = datetime(2026, 10, 19, 12, 0)


def _set_depth(sessions, value: str) -> None:
    with SqlAlchemyReferencesUnitOfWork(sessions) as uow:
        for record in uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=10_000):
            if str(record.fields.get("coaxArticle") or record.fields.get("Артикул") or "").casefold() == ARTICLE.casefold():
                uow.tech_operations.save(replace(record, fields={**record.fields, DEPTH: value}))
        uow.commit()


def _derive(sessions, overrides=None):
    refs = load_refs(lambda: SqlAlchemyReferencesUnitOfWork(sessions))
    document, conflicts = etk_derive.derive(
        CONFIG, overrides, lambda config: with_technology(config, refs=refs)[0], CARDS,
        operation_library_commit="lib", technology_source=dict(refs.snapshot))
    assert document is not None and conflicts == []
    return document


def _store(sessions, document, *, issued=True, overrides=None, manual_keys=()) -> ProjectTechcard:
    card = ProjectTechcard.create(
        project="228К.20", item_index="КБ1", product_name="Коаксиальный кабель", source_calculation="",
        deadline=None, now=T0).rebuild(document, T0)
    if issued:
        card = card.issue(T0)
    card = replace(card, inputs=deepcopy(CONFIG), overrides=overrides, manual_operation_keys=tuple(manual_keys))
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.techcards.save(card)
        if issued:
            uow.techcards.add_revision(TechcardRevision(
                id=card.id, project_card_id=card.id, revision=1, reason="Первая выдача", actor_name="т", occurred_at=T0,
                document=document))
        uow.commit()
    return card


@pytest.fixture()
def base(sessions):  # noqa: F811
    load_mirror(MIRROR, SqlAlchemyReferencesUnitOfWork(sessions), dry_run=False, now=T0)
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.operation_cards.load(tuple(CARDS), imported_at=T0)
        uow.commit()
    _set_depth(sessions, "0,5")
    return sessions


def _settle(sessions) -> None:
    """The first run after the seeding: the freshly built card is already what the builder gives — nothing to issue."""
    report = run_refresh(sessions, now=T0, everything=True)
    assert report.count("revised", "refreshed") == 0, report.text()


def _row(sessions) -> ProjectTechcardRow:
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        row = uow.session.scalars(select(ProjectTechcardRow)).one()
        uow.commit()  # no rollback on exit: the loaded row stays readable
        return row


def _revisions(sessions) -> list[TechcardRevisionRow]:
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        rows = list(uow.session.scalars(select(TechcardRevisionRow).order_by(TechcardRevisionRow.revision)))
        uow.commit()
        return rows


def _pending(sessions) -> list[RefreshRequestRow]:
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        rows = list(uow.session.scalars(select(RefreshRequestRow).where(RefreshRequestRow.processed_at.is_(None))))
        uow.commit()
        return rows


def _depths(document) -> set[str]:
    return {str(((op.get("fields") or {}).get("coaxEnd") or {}).get("contactDepth") or "") for op in document["operations"]}


def test_a_reference_write_queues_one_request_in_its_own_transaction(base):
    assert sorted(item.reason for item in _pending(base)) == ["reference", "template"]  # the library load queued the second
    _set_depth(base, "0,6")
    _set_depth(base, "0,7")
    assert sorted(item.reason for item in _pending(base)) == ["reference", "template"]  # one pending request per reason


def test_a_coax_depth_edit_issues_the_next_revision_with_the_diff(base):
    _store(base, _derive(base))
    _settle(base)
    _set_depth(base, "0,9")

    report = run_refresh(base, now=T1)

    assert report.count("revised") == 1, report.text()
    row = _row(base)
    assert row.revision == 2 and row.derived_hash
    revisions = _revisions(base)
    assert [item.revision for item in revisions] == [1, 2]
    new = revisions[1]
    assert new.source == "auto" and new.reason.startswith("обновлено по справочнику: ") and "Заглубление контакта" in new.reason and new.reason.endswith("(19.10.2026)")
    assert "Заглубление контакта" in {item["field"] for item in new.diff["changes"]}
    assert "0,9" in _depths(new.document) and "0,5" not in _depths(new.document)
    assert "0,5" in _depths(revisions[0].document)  # the first revision stays frozen
    assert new.diff["reason"] == new.reason
    with SqlAlchemyTechcardsUnitOfWork(base) as uow:  # the screen shows the stored revision as is
        view = card_view(uow.techcards.get(row.id), frozen=True)
    assert "0,9" in _depths(view["document"])
    assert _pending(base) == []


def test_a_rerun_is_a_no_op(base):
    _store(base, _derive(base))
    _settle(base)
    _set_depth(base, "0,9")
    run_refresh(base, now=T1)

    again = run_refresh(base, now=T1)
    full = run_refresh(base, now=T1, everything=True)

    assert again.requests == 0 and again.cards == []
    assert full.count("revised", "refreshed") == 0
    assert [item.revision for item in _revisions(base)] == [1, 2]


def test_several_edits_in_one_batch_make_one_revision(base):
    _store(base, _derive(base))
    _settle(base)
    _set_depth(base, "0,6")
    _set_depth(base, "0,9")

    run_refresh(base, now=T1)

    assert [item.revision for item in _revisions(base)] == [1, 2]


def test_a_hand_edit_of_an_operation_survives_into_the_next_revision(base):
    document = _derive(base)
    target = next(op for op in document["operations"] if (op.get("card") or {}).get("steps"))
    edited = deepcopy(target)
    edited["card"]["steps"][0]["text"] = "Правка технолога: шаг"
    key = operation_key(target)
    overrides = {"operations": {key: {
        "operation": edited,
        "base_template_version": document["source"]["templates"].get(str(target.get("template_id") or "").strip(), "")}}}
    document = _derive(base, overrides)
    _store(base, document, overrides=overrides, manual_keys=(key,))
    _settle(base)
    _set_depth(base, "0,9")

    run_refresh(base, now=T1)

    new = _revisions(base)[1].document
    kept = next(op for op in new["operations"] if operation_key(op) == key)
    assert kept["card"]["steps"][0]["text"] == "Правка технолога: шаг"
    assert "0,9" in _depths(new)
    assert _row(base).overrides == overrides


def test_a_hand_edit_known_only_by_manual_keys_is_kept_too(base):
    document = _derive(base)
    target = next(op for op in document["operations"] if (op.get("card") or {}).get("steps"))
    key = operation_key(target)
    old = deepcopy(document)
    for op in old["operations"]:
        if operation_key(op) == key:
            op["card"]["steps"][0]["text"] = "Старая правка до появления overrides"
    _store(base, old, manual_keys=(key,))
    _set_depth(base, "0,9")

    run_refresh(base, now=T1)

    new = _revisions(base)[1].document
    kept = next(op for op in new["operations"] if operation_key(op) == key)
    assert kept["card"]["steps"][0]["text"] == "Старая правка до появления overrides"


def test_a_missing_operation_gets_a_note_for_the_technologist_and_the_edit_stays(base):
    document = _derive(base)
    overrides = {"operations": {"НЕТ_ТАКОЙ::x::A": {"base_template_version": "", "operation": {"code": "НЕТ_ТАКОЙ"}}}}
    _store(base, document, overrides=overrides)
    _set_depth(base, "0,9")

    report = run_refresh(base, now=T1)

    row = _row(base)
    assert row.revision == 2 and row.review_note.startswith(NOTE_PREFIX)
    assert row.overrides == overrides
    assert report.cards[0].conflicts[0]["kind"] == "missing_operation"


def test_a_launched_task_keeps_the_revision_it_started_with(base):
    _store(base, _derive(base))
    _settle(base)
    _set_depth(base, "0,9")
    run_refresh(base, now=T1)

    with SqlAlchemyTechcardsUnitOfWork(base) as uow:
        card = uow.techcards.get(_row(base).id)
        shown, info = pinned_card(uow, card, 1)
        current, _ = pinned_card(uow, card, 2)

    assert info["newer"] is True and "0,5" in _depths(shown.document) and "0,9" not in _depths(shown.document)
    assert "0,9" in _depths(current.document)


def test_a_draft_is_refreshed_in_place_without_a_revision(base):
    _store(base, _derive(base), issued=False)
    _settle(base)
    _set_depth(base, "0,9")

    report = run_refresh(base, now=T1)

    row = _row(base)
    assert report.count("refreshed") == 1 and row.status == TechcardStatus.DRAFT.value and row.revision == 0
    assert "0,9" in _depths(row.document)
    assert _revisions(base) == []


def test_a_builder_change_issues_a_revision_named_after_the_rules(base, monkeypatch):
    _store(base, _derive(base))
    _settle(base)
    real = etk_derive.derive

    def new_rule(*args, **kwargs):
        document, conflicts = real(*args, **kwargs)
        document = deepcopy(document)
        document["operations"][0].setdefault("card", {})["safety"] = "Новое правило сборки"
        return document, conflicts

    monkeypatch.setattr(etk_derive, "derive", new_rule)
    monkeypatch.setattr(etk_derive, "builder_hash", lambda: "изменённые-правила")

    run_refresh(base, now=T1, everything=True)

    revisions = _revisions(base)
    assert [item.revision for item in revisions] == [1, 2]
    assert revisions[1].reason == "обновлено по правилам сборки (19.10.2026)" and revisions[1].source == "auto"
    assert {item["field"] for item in revisions[1].diff["changes"]} == {"Безопасность"}


def test_a_dry_run_writes_nothing_and_says_what_would_happen(base):
    _store(base, _derive(base))
    _settle(base)
    _set_depth(base, "0,9")
    version = _row(base).version

    report = run_refresh(base, now=T1, dry_run=True)

    assert report.count("revised") == 1 and "было бы выпущено" in report.text() and "Заглубление контакта" in report.text()
    assert _row(base).version == version and _row(base).revision == 1
    assert [item.revision for item in _revisions(base)] == [1]
    assert len(_pending(base)) == 1


def test_a_card_edited_during_the_run_is_skipped_and_its_request_stays(base, monkeypatch):
    _store(base, _derive(base))
    _settle(base)
    _set_depth(base, "0,9")
    real = etk_refresh.evaluate

    def with_a_concurrent_save(card, ctx):
        outcome = real(card, ctx)
        with SqlAlchemyTechcardsUnitOfWork(base) as other:  # an edit form saved while the job was deriving
            other.techcards.save(replace(other.techcards.get(card.id), version=card.version + 1, updated_at=T1))
            other.commit()
        return outcome

    monkeypatch.setattr(etk_refresh, "evaluate", with_a_concurrent_save)
    report = run_refresh(base, now=T1)
    monkeypatch.setattr(etk_refresh, "evaluate", real)

    assert report.skipped == 1 and [item.revision for item in _revisions(base)] == [1]
    assert len(_pending(base)) == 1
    assert run_refresh(base, now=T1).count("revised") == 1  # the next run takes it
    assert [item.revision for item in _revisions(base)] == [1, 2]


def test_the_worker_tick_does_nothing_until_the_owner_switches_it_on(base, monkeypatch):
    _store(base, _derive(base))
    _settle(base)
    _set_depth(base, "0,9")

    monkeypatch.delenv("ETK_AUTO_REVISION", raising=False)
    assert etk_refresh.refresh_tick(base) == 0 and [item.revision for item in _revisions(base)] == [1]

    monkeypatch.setenv("ETK_AUTO_REVISION", "1")
    assert etk_refresh.refresh_tick(base) == 1 and [item.revision for item in _revisions(base)] == [1, 2]
