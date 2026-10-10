"""Publishing into the component library of the harness drawing: «Проекты: правка» creates a template (revision 1)
and publishes its next revision on the newest one; a stale base is a 409 naming who published the newest, the same
content is the same row, a model that is not a template v3–v5 is refused in Russian, and no stored row ever changes —
a drawing that placed revision 1 keeps it. After TECHMAP-GRAPHER ComponentTemplateEndpoints.cs (© Василий)."""

import base64
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.techcards.harness.components import template_identity
from app.modules.techcards.harness_infrastructure import (
    HarnessComponentTemplateRow, SqlAlchemyHarnessComponentRepository,
)
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import AuditEventRow

from .test_harness_api import BASE, _principal, harness_api  # noqa: F401 — fixture
from .test_harness_components import LIBRARY, SEED, _seed_entry, _with_placed

CODE = "TEST-04V"


def _model(code=CODE):
    """The SHR-04V-S seed model bound to another article (a template the fixture library does not hold)."""
    model = deepcopy(_seed_entry()["content"])
    for variant in model["articleVariants"]:
        variant["articleKey"] = code
    return model


def _png():
    return (SEED.parent / _seed_entry()["picture"]).read_bytes()


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode()


def _create(client, model=None, **extra):
    return client.post(LIBRARY, json={"model": _model() if model is None else model, **extra})


def _rows(factory):
    """Every stored revision with every column (model and picture included)."""
    with factory() as uow:
        rows = uow.session.scalars(select(HarnessComponentTemplateRow)
                                   .order_by(HarnessComponentTemplateRow.connector_ref,
                                             HarnessComponentTemplateRow.revision)).all()
        return {row.id: {column.name: getattr(row, column.name) for column in HarnessComponentTemplateRow.__table__.columns}
                for row in rows}


def _events(factory):
    with factory() as uow:
        return list(uow.session.execute(
            select(AuditEventRow.event_type, AuditEventRow.details)
            .where(AuditEventRow.event_type.like("techcards.harness_component%"))
            .order_by(AuditEventRow.occurred_at)
        ).all())


def _renamed(model, name="Контакт А"):
    model = deepcopy(model)
    model["logicalContacts"][0]["name"] = name
    return model


def test_create_makes_revision_1_readable_like_the_catalog_with_its_picture_and_a_journal_event(harness_api):
    client, factory, _clock = harness_api
    created = _create(client, png_base64="data:image/png;base64," + _b64(_png()), name="Корпус тестовый")
    assert created.status_code == 200, created.text
    body = created.json()
    assert body == client.get(f"{LIBRARY}/{body['templateId']}").json()
    assert body["templateId"] == str(template_identity(CODE)) and body["version"] == 1
    assert (body["code"], body["connectorRef"], body["name"], body["hasImage"]) == (CODE, CODE, "Корпус тестовый", True)
    assert body["articleBindings"] == [{"sourceId": "erp-connectors", "entityType": "connector", "articleKey": CODE}]
    assert body["content"]["schemaVersion"] == 3 and body["assets"] == body["content"]["assets"]
    image = client.get(f"{LIBRARY}/{body['templateId']}/versions/1/assets/{body['assets'][0]['assetId']}")
    assert image.status_code == 200 and image.content == _png()
    assert any(item["templateId"] == body["templateId"] for item in client.get(LIBRARY).json())
    [(event, details)] = _events(factory)
    assert event == "techcards.harness_component_created"
    assert details["code"] == CODE and details["revision"] == "1" and details["version_sha256"] == body["versionSha256"]


def test_create_without_a_name_or_code_takes_the_article_and_a_new_picture_takes_the_models_one_png(harness_api):
    client, _factory, _clock = harness_api
    model = _model()
    model["assets"][0]["sha256"] = "0" * 64  # the editor does not hash: the server does
    created = _create(client, model, png_base64=_b64(_png())).json()
    assert created["name"] == CODE and created["hasImage"] is True
    assert created["assets"][0]["sha256"] == created["content"]["assets"][0]["sha256"] != "0" * 64


def test_next_version_on_the_newest_adds_revision_2_and_keeps_revision_1_and_the_picture(harness_api):
    client, factory, _clock = harness_api
    first = _create(client, png_base64=_b64(_png())).json()
    template = first["templateId"]
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"}, name="Мастер Петров")
    second = client.post(f"{LIBRARY}/{template}/versions", json={"base_revision": 1, "model": _renamed(first["content"])})
    assert second.status_code == 200, second.text
    body = second.json()
    assert (body["version"], body["templateId"], body["code"], body["connectorRef"]) == (2, template, CODE, CODE)
    assert body["hasImage"] is True and body["versionSha256"] != first["versionSha256"]
    assert client.get(f"{LIBRARY}/{template}").json() == body
    assert client.get(f"{LIBRARY}/{template}/versions/1").json() == first
    asset = body["assets"][0]["assetId"]
    assert client.get(f"{LIBRARY}/{template}/versions/2/assets/{asset}").content == _png()
    assert next(item for item in client.get(LIBRARY).json() if item["templateId"] == template)["version"] == 2
    assert [event for event, _ in _events(factory)] == [
        "techcards.harness_component_created", "techcards.harness_component_published"]


def test_a_stale_base_is_a_409_naming_who_published_the_newest(harness_api):
    client, _factory, _clock = harness_api
    first = _create(client).json()
    url = f"{LIBRARY}/{first['templateId']}/versions"
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"}, name="Мастер Петров")
    assert client.post(url, json={"base_revision": 1, "model": _renamed(first["content"])}).status_code == 200
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})
    stale = client.post(url, json={"base_revision": 1, "model": _renamed(first["content"], "Контакт Б")})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Шаблон уже изменил Мастер Петров — версия 2"
    ahead = client.post(url, json={"base_revision": 5, "model": _renamed(first["content"], "Контакт Б")})
    assert ahead.status_code == 409
    assert client.post(f"{LIBRARY}/{uuid4()}/versions", json={"base_revision": 1, "model": _model()}).status_code == 404


def test_the_same_content_as_the_newest_is_that_row_no_new_revision(harness_api):
    client, factory, _clock = harness_api
    first = _create(client, png_base64=_b64(_png())).json()
    again = _create(client, png_base64=_b64(_png()))
    assert again.status_code == 200 and again.json() == first
    url = f"{LIBRARY}/{first['templateId']}/versions"
    unchanged = client.post(url, json={"base_revision": 1, "model": first["content"]})
    assert unchanged.status_code == 200 and unchanged.json() == first
    second = client.post(url, json={"base_revision": 1, "model": _renamed(first["content"])}).json()
    retried = client.post(url, json={"base_revision": 1, "model": _renamed(first["content"])})
    assert retried.status_code == 200 and retried.json() == second  # a retry after success is no conflict
    assert len([row for row in _rows(factory).values() if row["connector_ref"] == CODE]) == 2
    assert len(_events(factory)) == 2


def test_create_of_a_key_the_library_holds_with_other_content_is_a_409(harness_api):
    client, _factory, _clock = harness_api
    _create(client)
    taken = _create(client, _renamed(_model()))
    assert taken.status_code == 409
    assert taken.json()["detail"] == f"Шаблон «{CODE}» уже есть в библиотеке (Технолог, версия 1) — опубликуйте его новую версию"


@pytest.mark.parametrize("model, message", [
    ({"schemaVersion": 2, "views": []}, "версии схемы 3–5, а у этой версия схемы 2"),
    ({"schemaVersion": 6}, "версии схемы 3–5, а у этой версия схемы 6"),
    ({"schemaVersion": "3"}, 'версия схемы "3"'),
    ({"views": []}, "версия схемы не указана"),
    ([1, 2], "Модель компонента не передана"),
    (None, "Модель компонента не передана"),
])
def test_a_model_that_is_not_a_template_v3_v5_is_refused_in_russian(harness_api, model, message):
    client, factory, _clock = harness_api
    before = _rows(factory)
    refused = client.post(LIBRARY, json={"model": model})
    assert refused.status_code == 422 and message in refused.json()["detail"], refused.text
    first = _create(client).json()
    version = client.post(f"{LIBRARY}/{first['templateId']}/versions", json={"base_revision": 1, "model": model})
    assert version.status_code == 422 and message in version.json()["detail"]
    assert len(_rows(factory)) == len(before) + 1


@pytest.mark.parametrize("extra, message", [
    ({"png_base64": _b64(b"GIF89a-not-a-png")}, "Рисунок шаблона — не PNG"),
    ({"png_base64": "@@@"}, "Рисунок шаблона передан не в base64"),
    ({"code": "  "}, None),
])
def test_pictures_and_codes_that_cannot_be_stored_are_refused(harness_api, extra, message):
    client, _factory, _clock = harness_api
    if message is None:  # a blank code falls back to the article
        assert _create(client, **extra).json()["code"] == CODE
        return
    refused = _create(client, **extra)
    assert refused.status_code == 422 and refused.json()["detail"] == message


def test_a_model_without_an_article_needs_a_code_and_a_picture_needs_one_png_asset(harness_api):
    client, _factory, _clock = harness_api
    model = _model()
    model["articleVariants"] = []
    refused = _create(client, model)
    assert refused.status_code == 422 and "нет кода разъёма" in refused.json()["detail"]
    assert _create(client, model, code="  xs-7 ").json()["connectorRef"] == "XS-7"
    no_asset = _model("TEST-05V")
    no_asset["assets"] = []
    refused = _create(client, no_asset, png_base64=_b64(_png()))
    assert refused.status_code == 422 and "Рисунок не к чему привязать" in refused.json()["detail"]


def test_no_stored_row_ever_changes_and_a_placed_drawing_keeps_its_revision(harness_api):
    client, factory, _clock = harness_api
    first = _create(client, png_base64=_b64(_png())).json()
    placed = SimpleNamespace(template_id=first["templateId"], revision=1, version_sha256=first["versionSha256"])
    saved = client.put(f"{BASE}/КБ1", json={"design": _with_placed(placed)})
    assert saved.status_code == 200, saved.text
    before = _rows(factory)

    url = f"{LIBRARY}/{first['templateId']}/versions"
    assert client.post(url, json={"base_revision": 1, "model": _renamed(first["content"])}).status_code == 200
    assert client.post(url, json={"base_revision": 1, "model": _renamed(first["content"], "В")}).status_code == 409
    assert client.post(url, json={"base_revision": 2, "model": {"schemaVersion": 1}}).status_code == 422
    after = _rows(factory)
    assert {key: after[key] for key in before} == before and len(after) == len(before) + 1

    [snapshot] = client.get(f"{BASE}/КБ1/components").json()["snapshots"]
    assert snapshot["sourceVersion"] == 1 and snapshot["sourceVersionSha256"] == first["versionSha256"]
    assert snapshot["content"] == first["content"]


@pytest.mark.parametrize("permissions", [{"projects": "view"}, {"techcards": "edit"}, {"warehouse": "edit"}])
def test_view_only_cannot_publish(harness_api, permissions):
    client, factory, _clock = harness_api
    first = _create(client).json()
    before = _rows(factory)
    app.dependency_overrides[authenticated_principal] = _principal(permissions)
    assert _create(client, _model("TEST-09V")).status_code == 403
    url = f"{LIBRARY}/{first['templateId']}/versions"
    assert client.post(url, json={"base_revision": 1, "model": _renamed(first["content"])}).status_code == 403
    assert _rows(factory) == before


def test_a_publish_racing_another_on_the_same_base_is_a_409_not_a_second_revision_2(harness_api, monkeypatch):
    client, factory, _clock = harness_api
    first = _create(client).json()
    url = f"{LIBRARY}/{first['templateId']}/versions"
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"}, name="Мастер Петров")
    assert client.post(url, json={"base_revision": 1, "model": _renamed(first["content"])}).status_code == 200
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})
    # This request read the library before the other publish landed: it still sees revision 1 as the newest.
    real = SqlAlchemyHarnessComponentRepository.revision
    stale = iter([1])
    monkeypatch.setattr(SqlAlchemyHarnessComponentRepository, "revision",
                        lambda self, template_id, revision=None: real(self, template_id, next(stale, revision)))
    raced = client.post(url, json={"base_revision": 1, "model": _renamed(first["content"], "Контакт Б")})
    assert raced.status_code == 409 and raced.json()["detail"] == "Шаблон уже изменил Мастер Петров — версия 2"
    assert len([row for row in _rows(factory).values() if row["connector_ref"] == CODE]) == 2
