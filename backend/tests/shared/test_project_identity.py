"""Project identity over the rename audit trail: a rename-back is one identity, a real conflict still refuses."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.shared.persistence import AuditEventRow, Base
from app.shared.project_identity import ProjectIdentityConflict, current_project_name, project_aliases, stable_project_key


def session_with(*renames, same_instant=False):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AuditEventRow.__table__])
    session = Session(engine)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for index, (old, new) in enumerate(renames):
        session.add(AuditEventRow(id=uuid4(), aggregate_id=uuid4(), event_type="techcards.project_renamed",
            actor_id=uuid4(), actor_name="t", actor_department="", occurred_at=start if same_instant else start + timedelta(days=index),
            details={"old_project": old, "project": new}))
    session.flush()
    return session


def test_rename_back_history_is_one_identity_not_a_conflict():
    session = session_with(("101-1", "102-1"), ("102-1", "101-1"))
    assert stable_project_key(session, "101-1") == "101-1" == stable_project_key(session, "102-1")
    assert current_project_name(session, "102-1") == "101-1" == current_project_name(session, "101-1")


def test_rename_chain_and_back_then_forward_again():
    session = session_with(("101-1", "102-1"), ("102-1", "101-1"), ("101-1", "103-1"))
    assert {stable_project_key(session, name) for name in ("101-1", "102-1", "103-1")} == {"101-1"}
    assert current_project_name(session, "101-1") == "103-1" == current_project_name(session, "102-1")


def test_real_conflicts_still_raise():
    with pytest.raises(ProjectIdentityConflict):
        project_aliases(session_with(("101-1", "102-1"), ("101-1", "103-1")))  # A no longer carries that name
    with pytest.raises(ProjectIdentityConflict):
        project_aliases(session_with(("101-1", "103-1"), ("102-1", "103-1")))  # C belongs to another live identity


def test_a_rename_pair_stamped_with_one_instant_is_deterministic_whatever_the_event_ids():
    results = set()
    for _ in range(12):  # event ids are random: the outcome must not depend on them
        session = session_with(("101-1", "102-1"), ("102-1", "101-1"), same_instant=True)
        results.add((stable_project_key(session, "101-1"), stable_project_key(session, "102-1"),
                     current_project_name(session, "102-1")))
    assert len(results) == 1 and next(iter(results))[0] == next(iter(results))[1]


def test_a_reused_name_keeps_the_two_identities_apart():
    session = session_with(("101-1", "102-1"), ("103-1", "101-1"))
    assert stable_project_key(session, "102-1") == "101-1"  # the first project, one step to its original key
    assert stable_project_key(session, "101-1") == "103-1"  # the name now belongs to the second one


def test_finished_goods_hand_in_with_a_conflicting_rename_history_is_422_not_a_crash(monkeypatch):
    from types import SimpleNamespace
    from app.modules.shipping.domain import MoveKind, ShippingValidationError
    from app.modules.warehouse.grn import GrnCategory
    from app.workflows import finished_goods
    monkeypatch.setattr(finished_goods, "production_grn_category", lambda session, project: (GrnCategory.FRX, ""))
    session = session_with(("101-1", "102-1"), ("101-1", "103-1"))
    with pytest.raises(ShippingValidationError, match="неоднозначна"):
        finished_goods.allocate_production_act(session, SimpleNamespace(project="102-1"),
                                               [SimpleNamespace(kind=MoveKind.ACCEPT, id=uuid4())])
