from dataclasses import replace

from sqlalchemy import select

from app.main import app
from app.modules.production.infrastructure import ProductionTaskRow, SqlAlchemyProductionUnitOfWork
from app.modules.production.resource_infrastructure import ActiveResourceRow, ResourceGenerationRow, ResourceReservationRow
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.workflows.production_resource_context import production_resources
from tests.production.test_autoplan_api import client, issue, preview


def enable(sessions):
    with sessions() as session:
        session.add(ResourceGenerationRow(id=1, version=0))
        session.commit()
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions, production_resources)
    with app.state.references_uow_factory() as uow:
        for number, site in (("ОБ.027.3", "ЦАР"), ("ОБ.027.4", "ЖУК")):
            uow.tech_operations.save(TechOperationRecord.create(
                tab=TechOperationTab.EQUIPMENT, display_text=number, search_text=number,
                source_sheet="БД.ОБ", sort_key=number,
                fields={"Тип": "Оборудование", "Инв. номер": number, "Наименование": "Пресс", "Расположение": site},
            ))
        uow.commit()


def test_future_preview_exposes_exact_choices_without_creating_task_or_claims(client):
    http, sessions = client
    card = issue("КБ1")
    enable(sessions)
    result = preview(http)
    machine = next(item for item in result["proposals"] if item["resources"]["requirements"])
    assert machine["new"]
    assert machine["resources"]["site"] == "ЦАР"
    assert machine["resources"]["allocations"] == []
    assert machine["resources"]["blocked_reason"] is None
    requirements = machine["resources"]["requirements"]
    assert len(requirements) == 1
    assert requirements[0]["etk_position"] == 1
    assert [item["id"] for item in requirements[0]["machines"]] == ["ОБ.027.3"]
    assert result["new_tasks"][0]["techcard_revision"] == card.revision
    unresolved = next(item for item in result["proposals"] if not item["resources"]["requirements"])
    assert unresolved["resources"]["blocked_reason"]  # Missing tooling is not confirmed manual work.
    with sessions() as session:
        assert list(session.scalars(select(ProductionTaskRow))) == []
        assert list(session.scalars(select(ActiveResourceRow))) == []
        assert list(session.scalars(select(ResourceReservationRow))) == []


def test_future_preview_never_substitutes_current_revision(client, monkeypatch):
    from app.modules.production import autoplan_application

    http, sessions = client
    issue("КБ1")
    enable(sessions)
    original = autoplan_application._new_task
    monkeypatch.setattr(autoplan_application, "_new_task", lambda *args: replace(
        original(*args), techcard_revision=999,
    ))
    result = preview(http)
    assert result["proposals"]
    for item in result["proposals"]:
        assert item["resources"]["requirements"] == []
        assert "не подставляется" in item["resources"]["blocked_reason"]


def test_preview_choice_catalog_changes_invalidate_fingerprint(client):
    http, sessions = client
    issue("КБ1")
    enable(sessions)
    before = preview(http)
    with app.state.references_uow_factory() as uow:
        machine = next(row for row in uow.tech_operations.list(tab=TechOperationTab.EQUIPMENT, query="", limit=100)
            if row.fields.get("Инв. номер") == "ОБ.027.3")
        uow.tech_operations.save(replace(machine, fields={**machine.fields, "Расположение": "ЖУК"}))
        uow.commit()
    after = preview(http)
    assert after["fingerprint"] != before["fingerprint"]
