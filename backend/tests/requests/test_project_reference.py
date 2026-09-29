"""№ ПРОЕКТА in a request (audit RQ-4): SUPPLYMAP Schema.gs projNoValid_/projNoNorm_, Forms.gs registry check."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.production.infrastructure import ProductionPlanProjectRow, SqlAlchemyProductionUnitOfWork
from app.modules.production.plan import PlanProjectSnapshot
from app.modules.requests.application import CreateRequestsCommand, create_requests
from app.modules.requests.domain import (
    RequestDraft,
    ValidationError,
    ensure_projects_registered,
    normalize_project_reference,
    project_reference_valid,
    validate_draft,
)
from app.workflows.project_registry import PlanSnapshotProjectRegistry
from tests.factories import Actor
from tests.requests.test_application import FakeUnitOfWork


def draft(project_reference: str | None) -> RequestDraft:
    return RequestDraft(
        purpose="RND",
        project_reference=project_reference,
        delivery_point="г. Москва, ул. 6-я Радиальная, д. 17",
        name="Разъём",
        item_type="Комплектующие",
        quantity=Decimal("2"),
        unit="шт",
        quality_document="Нет",
        procurement_deadline=date(2026, 9, 18),
        planned_date=date(2026, 9, 25),
    )


@pytest.mark.parametrize(
    "value",
    [
        "591К.3", "366К.2", "48К.13", "591КВ.3", "2000К", "2000К.", "12345К.7",
        "35К.С.13", "35К.Л.1", "34К.ЗЭП.5", "713К.2.2", "70К.26.13.2", "51МФ.1",
        "П2510-1", "П2510-1.2", " 591 к.3 ",
    ],
)
def test_live_project_number_shapes_are_valid(value):
    assert project_reference_valid(value)


@pytest.mark.parametrize(
    "value",
    [
        "591K.3",  # латинская K — отклоняется намеренно, чтобы номер ввели верно
        "0591К.3", "К.3", "591", "591К.0", "591К.АБВГ.1", "П251-1", "П2510-0", "PRJ-1", "591К..3",
    ],
)
def test_malformed_project_numbers_are_rejected(value):
    assert not project_reference_valid(value)
    with pytest.raises(ValidationError, match="№ ПРОЕКТА"):
        validate_draft(draft(value))


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("591к.3", "591К.3"),
        (" 591 К . 3 ", "591К.3"),
        ("2000К", "2000К.1"),
        ("2000К.", "2000К.1"),
        ("35К.С", "35К.С.1"),
        ("51МФ", "51МФ.1"),
        ("591K.3", "591К.3"),  # гомоглиф сворачивается при сравнении с реестром
        ("П2510-1.2", "П2510-1.2"),
        ("713К.2.2", "713К.2.2"),
    ],
)
def test_normalization_is_the_registry_key(raw, canonical):
    assert normalize_project_reference(raw) == canonical
    assert normalize_project_reference(canonical) == canonical


def test_validated_draft_stores_canonical_project_and_keeps_empty_optional():
    assert validate_draft(draft("2000к")).project_reference == "2000К.1"
    assert validate_draft(draft("  ")).project_reference is None
    assert validate_draft(draft(None)).project_reference is None


def test_registry_check_matches_by_canonical_form_and_reports_position():
    ensure_projects_registered(["591К.3", None, "2000К.1"], ["591к.3", "2000К"])
    with pytest.raises(ValidationError, match="Позиция 2: проект «48К.13» не найден"):
        ensure_projects_registered(["591К.3", "48К.13"], ["591К.3"])


def test_unavailable_registry_skips_only_the_existence_check():
    ensure_projects_registered(["48К.13"], None)
    with pytest.raises(ValidationError, match="Позиция 1"):
        ensure_projects_registered(["48К.13"], [])


class StaticRegistry:
    def __init__(self, projects):
        self.projects = projects

    def open_projects(self):
        return self.projects


def command(*drafts: RequestDraft, key: str = "form:1") -> CreateRequestsCommand:
    return CreateRequestsCommand(
        creation_key=key, drafts=drafts,
        actor=Actor(name="Иванов", department="РНД"), now=datetime(2026, 9, 15, 10, 30),
    )


def test_create_accepts_project_missing_from_registry_manual_entry():
    uow = FakeUnitOfWork()
    created = create_requests(command(draft("591К.3"), draft("48К.13")), uow, StaticRegistry(["591К.3"]))
    assert [item.project_reference for item in created] == ["591К.3", "48К.13"]


def test_create_accepts_registered_project_and_stores_canonical_form():
    uow = FakeUnitOfWork()
    created = create_requests(command(draft("2000к"), draft(None)), uow, StaticRegistry(["2000К.1"]))
    assert [item.project_reference for item in created] == ["2000К.1", None]


def test_create_without_registry_port_checks_format_only():
    uow = FakeUnitOfWork()
    assert create_requests(command(draft("48К.13")), uow)[0].project_reference == "48К.13"
    with pytest.raises(ValidationError, match="№ ПРОЕКТА"):
        create_requests(command(draft("48K.13"), key="form:2"), FakeUnitOfWork(), StaticRegistry(["48К.13"]))


def test_plan_snapshot_registry_lists_open_projects_only():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    ProductionPlanProjectRow.__table__.create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    registry = PlanSnapshotProjectRegistry(lambda: SqlAlchemyProductionUnitOfWork(sessions))

    assert registry.open_projects() is None  # снимок реестра ещё не импортирован

    now = datetime(2026, 9, 15, 10, 30)
    base = PlanProjectSnapshot(project="591К.3", customer="Заказчик", status="В работе", imported_at=now)
    with SqlAlchemyProductionUnitOfWork(sessions) as uow:
        uow.plan_projects.replace((
            base,
            replace(base, project="48К.13", status="Закрыт"),
            replace(base, project="366К.2", terminal=True, status="Отгружен"),
        ))
        uow.commit()

    assert registry.open_projects() == ["591К.3"]
    engine.dispose()
