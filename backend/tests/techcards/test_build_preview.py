"""«Схема полуфабрикатов» of the generator (owner 22.10.2026: «нормальной схемы полуфабрикатов как на 713К»):
POST /api/techcards/projects/{id}/build/preview builds the ETK from the generator config exactly as «Создать» does and
returns the card view, writing nothing — no document, no revision, no audit event, no ТД kit."""

from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, table

from app.main import app
from tests.techcards.test_assembly_drafts_api import _card, _csrf, _token, drafts_api  # noqa: F401
from tests.techcards.test_scheme_corrections import CORRECTED, GENERATOR


def _counts(sessions) -> dict[str, int]:
    with sessions() as session:
        names = inspect(session.get_bind()).get_table_names()
        return {name: session.scalar(select(func.count()).select_from(table(name))) for name in names}


def _preview(admin: TestClient, card: dict, config: dict, version: int | None = None):
    return admin.post(f"/api/techcards/projects/{card['id']}/build/preview", headers=_csrf(admin), json={
        "version": card["version"] if version is None else version, "config": deepcopy(config),
    })


def _build(admin: TestClient, card: dict, config: dict) -> dict:
    built = admin.post(f"/api/techcards/projects/{card['id']}/build", headers=_csrf(admin), json={
        "version": card["version"], "config": deepcopy(config),
    })
    assert built.status_code == 200, built.text
    return built.json()


def test_preview_returns_what_create_builds_and_writes_nothing(drafts_api):  # noqa: F811
    sessions, _clock, admin = drafts_api
    card = _card(admin)
    before = _counts(sessions)

    preview = _preview(admin, card, GENERATOR)

    assert preview.status_code == 200, preview.text
    assert _counts(sessions) == before
    stored = admin.get(f"/api/techcards/projects/{card['id']}").json()
    assert stored["document"] is None and stored["version"] == card["version"]

    shown = preview.json()["document"]
    built = _build(admin, card, GENERATOR)["document"]
    assert shown["route"] == built["route"]
    assert shown["operations"] == built["operations"]
    assert shown["build_config"] == built["build_config"]
    assert shown["topology"] == built["topology"]
    assert shown["kit"] == built["kit"]
    assert [block["ops"] for block in shown["route"]["blocks"]] == [[1], [2], [3], [4]]


def test_preview_follows_a_hand_edited_scheme_and_leaves_an_issued_card_as_it_is(drafts_api):  # noqa: F811
    sessions, _clock, admin = drafts_api
    card = _card(admin)
    built = _build(admin, card, GENERATOR)
    issued = admin.post(f"/api/techcards/projects/{card['id']}/issue", headers=_csrf(admin), json={"version": built["version"]})
    assert issued.status_code == 200, issued.text
    card = issued.json()
    before = _counts(sessions)

    preview = _preview(admin, card, {**GENERATOR, "route": CORRECTED})

    assert preview.status_code == 200, preview.text
    assert [block["ops"] for block in preview.json()["document"]["route"]["blocks"]] == [[1], [2], [3, 4]]
    assert _counts(sessions) == before
    stored = admin.get(f"/api/techcards/projects/{card['id']}").json()
    assert stored["revision"] == card["revision"] and stored["version"] == card["version"]
    assert [block["ops"] for block in stored["document"]["route"]["blocks"]] == [[1], [2], [3], [4]]
    revisions = admin.get(f"/api/techcards/projects/{card['id']}/revisions").json()["items"]
    assert [item["revision"] for item in revisions] == [1]


def test_preview_is_shown_for_an_older_form_and_refuses_what_create_refuses(drafts_api):  # noqa: F811
    _sessions, _clock, admin = drafts_api
    card = _card(admin)
    built = _build(admin, card, GENERATOR)
    # The form still holds the version it opened with: the preview is shown, «Создать» would check the version.
    stale = _preview(admin, card, GENERATOR, version=card["version"])
    assert stale.status_code == 200, stale.text
    assert stale.json()["document"]["route"] == built["document"]["route"]

    assert _preview(admin, {"id": "00000000-0000-0000-0000-00000000abcd", "version": 1}, GENERATOR).status_code == 404
    assert _preview(admin, card, {**GENERATOR, "ops": "не список"}).status_code in (400, 422)
    # An API token never builds a project ETK, the preview included.
    agent = TestClient(app)
    refused = agent.post(f"/api/techcards/projects/{card['id']}/build/preview", headers=_token(admin, "tcgen"),
                         json={"version": card["version"], "config": GENERATOR})
    assert refused.status_code == 403
