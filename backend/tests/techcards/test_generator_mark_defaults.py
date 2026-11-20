"""Последний выбор маркировки проводов в проекте (владелец 20.11): GET /api/techcards/generator-mark-defaults отдаёт
`{kind, tapeMm, tubeByDia}` — `kind` последней собранной КБ проекта («none» / «laser» тоже), размеры — последней КБ, где
они были. «Последняя» — по последней сборке рецепта (журнал), не по `updated_at` карты."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.mark_defaults import defaults_of
from app.modules.techcards.project_cards import ProjectTechcard
from app.shared.persistence import AuditEventRow

from .test_generator_drafts import MASTER_ID, NOW, _as, drafts  # noqa: F401

URL = "/api/techcards/generator-mark-defaults"
EMPTY = {"kind": None, "tapeMm": None, "tubeByDia": {}}
TUBE = {"enabled": True, "kind": "tube", "tubeMm": "3,2", "tubeByDia": {"1,6": "3,2", "2.1": 4.8, "x": 6, "1e999": 3}}
TAPE = {"enabled": True, "kind": "tape", "tapeMm": "9"}


def _card(sessions, project: str, index: str, inputs: dict | None, minutes: int, built: int | None = None) -> UUID:
    """A card saved `minutes` after NOW; `built` — when its recipe was last built (a build event), None — no event."""
    card = ProjectTechcard.create(project=project, item_index=index, product_name="Жгут", source_calculation="",
                                  deadline=None, now=NOW)
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.techcards.save(replace(card, inputs=inputs, updated_at=NOW + timedelta(minutes=minutes)))
        uow.commit()
    if built is not None:
        with sessions() as session:
            session.add(AuditEventRow(
                id=uuid4(), aggregate_id=card.id, event_type="techcards.project_auto_built", actor_id=MASTER_ID,
                actor_name="Мастер", actor_department="Производство", occurred_at=NOW + timedelta(minutes=built),
                details={}))
            session.commit()
    return card.id


def _get(project: str) -> dict:
    response = TestClient(app).get(URL, params={"project": project})
    assert response.status_code == 200, response.text
    return response.json()


def test_kind_is_the_last_built_recipe_and_sizes_the_last_that_had_them(drafts):
    sessions, _clock = drafts
    _card(sessions, "708К.5", "КБ1", {"tab": "complex", "wireMarks": TUBE}, 1, built=1)
    _card(sessions, "708К.5", "КБ2", {"tab": "complex", "wireMarks": TAPE}, 2, built=2)
    _card(sessions, "708К.5", "КБ3", {"tab": "complex", "wireMarks": {"enabled": True, "kind": "laser"}}, 3, built=3)
    _card(sessions, "708К.5", "КБ9", {"tab": "coax"}, 50, built=50)  # коаксиал маркировки проводов не знает
    _card(sessions, "713К.4", "КБ1", {"tab": "complex", "wireMarks": TUBE}, 20, built=20)  # другой проект

    assert _get("708К.5") == {"kind": "laser", "tapeMm": 9.0, "tubeByDia": {"1.6": 3.2, "2.1": 4.8}}
    assert _get("713К.4") == {"kind": "tube", "tapeMm": None, "tubeByDia": {"1.6": 3.2, "2.1": 4.8}}
    assert _get("720К.") == EMPTY


def test_a_complex_recipe_without_marks_is_none_and_wins_when_built_last(drafts):
    sessions, _clock = drafts
    _card(sessions, "708К.5", "КБ1", {"tab": "complex", "wireMarks": TAPE}, 1, built=1)
    _card(sessions, "708К.5", "КБ2", {"tab": "complex", "ops": []}, 2, built=2)
    assert _get("708К.5") == {"kind": "none", "tapeMm": 9.0, "tubeByDia": {}}


def test_newest_is_the_last_build_not_the_last_status_change(drafts):
    sessions, _clock = drafts
    # КБ1 собрана раньше, но выдана (updated_at) позже: последний выбор — КБ2, собранная последней.
    _card(sessions, "708К.5", "КБ1", {"tab": "complex", "wireMarks": TAPE}, 30, built=1)
    _card(sessions, "708К.5", "КБ2", {"tab": "complex", "wireMarks": TUBE}, 5, built=5)
    assert _get("708К.5")["kind"] == "tube"
    # Без события сборки (карта до журнала) — по updated_at.
    _card(sessions, "708К.5", "КБ3", {"tab": "complex", "wireMarks": {"enabled": False}}, 40)
    assert _get("708К.5")["kind"] == "none"


def test_a_recipe_wrapped_in_config_and_one_without_kind_is_a_tube(drafts):
    sessions, _clock = drafts
    _card(sessions, "708К.5", "КБ1", {"config": {"wireMarks": {"enabled": True, "tubeByDia": {"1": "2,4"}}}}, 1)
    assert _get("708К.5") == {"kind": "tube", "tapeMm": None, "tubeByDia": {"1": 2.4}}


def test_non_finite_and_bad_numbers_are_dropped():
    marks = {"enabled": True, "kind": "tape", "tapeMm": "1e999",
             "tubeByDia": {"NaN": 3, "Infinity": 3, "1,6": "nan", "2": "-1", "2,5": "1e999", "3": "4,8"}}
    assert defaults_of(marks) == {"kind": "tape", "tapeMm": None, "tubeByDia": {"3": 4.8}}
    assert defaults_of({"enabled": False, "kind": "tape", "tapeMm": "12"}) == {"kind": "none", "tapeMm": 12.0, "tubeByDia": {}}


def test_the_project_is_required_and_the_generator_section_is_needed(drafts):
    client = TestClient(app)
    assert client.get(URL).status_code == 422
    _as(MASTER_ID, {"techcards": "edit"})
    assert client.get(URL, params={"project": "708К.5"}).status_code == 403
