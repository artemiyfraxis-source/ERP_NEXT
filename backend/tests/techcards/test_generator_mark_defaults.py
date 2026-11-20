"""Последний выбор печатной маркировки проводов в проекте (владелец 20.11): GET /api/techcards/generator-mark-defaults
отдаёт `{kind, tapeMm, tubeByDia}` самого нового рецепта генератора проекта с трубкой или лентой (`inputs.wireMarks`)."""

from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.mark_defaults import defaults_of
from app.modules.techcards.project_cards import ProjectTechcard

from .test_generator_drafts import MASTER_ID, NOW, _as, drafts  # noqa: F401

URL = "/api/techcards/generator-mark-defaults"
EMPTY = {"kind": None, "tapeMm": None, "tubeByDia": {}}


def _card(sessions, project: str, index: str, inputs: dict | None, minutes: int) -> None:
    card = ProjectTechcard.create(project=project, item_index=index, product_name="Жгут", source_calculation="",
                                  deadline=None, now=NOW)
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.techcards.save(replace(card, inputs=inputs, updated_at=NOW + timedelta(minutes=minutes)))
        uow.commit()


def test_the_newest_printed_choice_of_the_project_wins(drafts):
    sessions, _clock = drafts
    tube = {"enabled": True, "kind": "tube", "tubeMm": "3,2", "tubeByDia": {"1,6": "3,2", "2.1": 4.8, "x": 6}}
    tape = {"enabled": True, "kind": "tape", "tapeMm": "9"}
    _card(sessions, "708К.5", "КБ1", {"ops": [], "wireMarks": tube}, 1)
    _card(sessions, "708К.5", "КБ2", {"ops": [], "wireMarks": tape}, 5)
    _card(sessions, "708К.5", "КБ3", {"ops": [], "wireMarks": {"enabled": True, "kind": "laser"}}, 9)  # не печать
    _card(sessions, "708К.5", "КБ4", {"ops": []}, 10)  # без маркировки
    _card(sessions, "713К.4", "КБ1", {"ops": [], "wireMarks": tube}, 20)  # другой проект
    client = TestClient(app)

    assert client.get(URL, params={"project": "708К.5"}).json() == {"kind": "tape", "tapeMm": 9.0, "tubeByDia": {}}
    assert client.get(URL, params={"project": "713К.4"}).json() == {
        "kind": "tube", "tapeMm": None, "tubeByDia": {"1.6": 3.2, "2.1": 4.8}}
    assert client.get(URL, params={"project": "720К."}).json() == EMPTY


def test_a_recipe_wrapped_in_config_and_one_without_kind_is_a_tube(drafts):
    sessions, _clock = drafts
    _card(sessions, "708К.5", "КБ1", {"config": {"wireMarks": {"enabled": True, "tubeByDia": {"1": "2,4"}}}}, 1)
    assert TestClient(app).get(URL, params={"project": "708К.5"}).json() == {
        "kind": "tube", "tapeMm": None, "tubeByDia": {"1": 2.4}}


def test_disabled_marks_are_no_choice():
    assert defaults_of({"enabled": False, "kind": "tape", "tapeMm": "12"}) == {"kind": None, "tapeMm": 12.0, "tubeByDia": {}}


def test_the_project_is_required_and_the_generator_section_is_needed(drafts):
    client = TestClient(app)
    assert client.get(URL).status_code == 422
    _as(MASTER_ID, {"techcards": "edit"})
    assert client.get(URL, params={"project": "708К.5"}).status_code == 403
