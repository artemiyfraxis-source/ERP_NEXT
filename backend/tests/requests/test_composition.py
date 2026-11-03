"""A saved full calculation is evidence, not a procurement obligation."""

from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.modules.requests.application import CalcSpreadsheet, extract_from_calc
from app.modules.requests.composition import CompositionConflict, composition_seed, save_composition
from app.modules.requests.domain import ValidationError
from app.modules.requests.infrastructure import RequestRow, SqlAlchemyCompositionRepository
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from tests.factories import Actor
from tests.procurement.test_backorder_flow import db  # noqa: F401
from tests.workflows.test_kitset_requests_to_procurement import FakeCalc, kitset_requests

URL = "https://docs.google.com/spreadsheets/d/1calc/edit"
NOW = datetime(2026, 11, 2, 12)
ACTOR = Actor(name="Закупщик", department="Закупки")


def seed():
    return composition_seed("708К.4", URL, extract_from_calc(URL, FakeCalc()))


def save(db, value=None, version=0):
    with db.requests() as uow:
        result = save_composition(value or seed(), version, ACTOR, NOW, uow)
        uow.commit()
        return result


def test_refresh_keeps_full_source_without_creating_obligations(db):
    result = save(db)
    assert result.version == 1
    assert len(result.rows) == 3
    assert result.rows[0]["price"] == "1000.50"
    assert result.rows[0]["link"] == "https://x.test/a"
    assert result.rows[0]["source_row"] == 2
    assert result.rows[2]["quantity"] is None
    assert result.rows[2]["issues"] == ["quantity", "unit"]
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RequestRow)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxMessageRow)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 1


def test_same_content_is_noop_and_stale_version_is_conflict(db):
    first = save(db)
    assert save(db, version=1) == first
    with pytest.raises(CompositionConflict):
        save(db, version=0)
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 1


def test_initial_request_import_records_exact_links_and_refresh_does_not_guess(db):
    kitset_requests(db)
    with db.requests() as uow:
        old = uow.compositions.get("708К.4")
        requests = uow.requests.for_project("708К.4")
    assert {row["request_id"] for row in old.rows if row["request_id"]} == {str(row.id) for row in requests}
    # Exact identical refresh retains the recorded relation; changed source does not re-match names.
    assert save(db, version=old.version).rows == old.rows
    changed_rows = [dict(row) for row in seed().rows]
    changed_rows[0]["quantity"] = "15"
    changed = save(db, replace(seed(), rows=changed_rows), old.version)
    assert all(row["request_id"] is None for row in changed.rows)
    assert all(row["request_link_state"] == "source_changed" for row in changed.rows)
    with db.requests() as uow:
        assert len(uow.requests.for_project("708К.4")) == 2


def test_project_mismatch_and_unbounded_source_are_rejected():
    with pytest.raises(ValidationError, match="проект"):
        composition_seed("709К.1", URL, extract_from_calc(URL, FakeCalc()))
    extracted = extract_from_calc(URL, FakeCalc())
    with pytest.raises(ValidationError, match="5000"):
        composition_seed("708К.4", URL, replace(extracted, items=extracted.items * 2000))


def test_import_persistence_failure_rolls_back_requests_outbox_and_audit(db, monkeypatch):
    def fail(*args, **kwargs):
        raise CompositionConflict("concurrent source")
    monkeypatch.setattr(SqlAlchemyCompositionRepository, "save", fail)
    with pytest.raises(CompositionConflict):
        kitset_requests(db)
    with db.sessions() as session:
        for model in (RequestRow, OutboxMessageRow, AuditEventRow):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_repository_cas_rejects_stale_update_and_concurrent_create(db):
    first = save(db)
    with db.requests() as uow:
        with pytest.raises(CompositionConflict):
            uow.compositions.save(replace(first, id=ACTOR.id), 0)
    updated = replace(first, version=2)
    with db.requests() as uow:
        uow.compositions.save(updated, 1)
        uow.commit()
    with db.requests() as uow:
        with pytest.raises(CompositionConflict):
            uow.compositions.save(updated, 1)
    with db.requests() as uow:
        assert uow.compositions.get(first.project).version == 2


def test_refresh_before_initial_import_can_record_explicit_request_links(db):
    save(db)
    kitset_requests(db)
    with db.requests() as uow:
        composition = uow.compositions.get("708К.4")
    assert composition.version == 2
    assert sum(row["request_id"] is not None for row in composition.rows) == 2


def test_invalid_numeric_suffix_does_not_become_an_asserted_quantity():
    extract = extract_from_calc(URL, FakeCalc())
    extract.items[0] = replace(extract.items[0], quantity="12 unknown", price="NaN", price_with_vat="Infinity")
    row = composition_seed("708К.4", URL, extract).rows[0]
    assert row["quantity"] is row["price"] is row["price_with_vat"] is None
    assert row["issues"] == ["quantity", "price", "price_with_vat"]


def test_explicit_unit_does_not_change_legacy_import_unit():
    from app.modules.requests.domain import parse_zpk
    items = parse_zpk([
        ["Тип", "Обозначение", "На проект", "Без НДС", "Ед. изм."],
        ["Провод", "Провод TEST", "1", "10", "кг"],
        ["Неизвестный", "Component TEST", "1", "10", "кг"],
    ])
    assert [item.unit for item in items] == ["м", "шт"]
    assert [item.source_unit for item in items] == ["кг", "кг"]
    row = seed().rows[0]
    assert row["unit_source"] == "derived_known" and row["unit"] == "м" and row["raw"]["unit"] == ""
