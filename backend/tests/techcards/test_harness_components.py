"""The component library of the harness drawing (migration 0091): templates by connector key and revision, a placed
connector pins template + revision (no copy in the drawing), pre-0091 copies move into the library on a write and
an old drawing still opens from its copy, the export carries the pinned revisions, the library is readable with
«Проекты» (view), the right the drawing itself is read with."""

import base64
from copy import deepcopy
from datetime import datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.techcards.harness.application import absorb
from app.modules.techcards.harness.components import (
    Candidate, candidate_from_snapshot, placement_graph, split_embedded, version_sha256,
)
from app.modules.techcards.harness.schema import HARNESS_CONTENT_TYPE
from app.modules.techcards.harness_infrastructure import HarnessComponentTemplateRow
from app.modules.techcards.kitsets import KitDocument
from app.shared.http_auth import authenticated_principal

from . import grapher_fixture as fx
from .test_harness_api import BASE, NOW, PROJECT, _design, _principal, _zip, harness_api  # noqa: F401 — fixture

SEED = Path(__file__).parents[2] / "data" / "harness-library" / "seed.json"
LIBRARY = "/api/techcards/harness-components"
PLACED = "5d6b1f0e-7c2a-4b8e-9a31-0c4f2e6d8b10"


def _seed_entry(code="SHR-04V-S"):
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    return next(entry for entry in seed["templates"] if entry["code"] == code)


def _candidate(entry) -> Candidate:
    item = dict(code=entry["code"], name=entry["name"], article_bindings=entry["articleBindings"],
                assets=entry["assets"], model=entry["content"])
    return Candidate(template_id=UUID(entry["templateId"]), revision=entry["revision"],
                     version_sha256=version_sha256(**item), **item)


def _library_template(factory, code="SHR-04V-S"):
    entry = _seed_entry(code)
    picture = (SEED.parent / entry["picture"]).read_bytes()
    with factory() as uow:
        template = absorb(uow.harness_components, _candidate(entry), by="Василий", at=NOW, image=picture,
                          image_sha256=hashlib.sha256(picture).hexdigest())
        uow.commit()
    return template, picture


def _with_placed(template, connector_id=PLACED, design=None):
    design = design or _design()
    connector = deepcopy(design["connectors"][0])
    connector.update(id=connector_id, designation="XS9")
    connector["contacts"] = [{**contact, "id": f"{connector_id}:c{number}"}
                             for number, contact in enumerate(connector["contacts"], start=1)]
    connector["libraryBinding"] = {**connector["libraryBinding"], "templateId": str(template.template_id),
                                   "templateVersion": template.revision, "versionSha256": template.version_sha256}
    design["connectors"].append(connector)
    return design


def _library_rows(factory):
    with factory() as uow:
        return [(row.connector_ref, row.code) for row in uow.session.scalars(
            select(HarnessComponentTemplateRow).order_by(HarnessComponentTemplateRow.connector_ref))]


# ── The library ───────────────────────────────────────────────────────────────────────────────────────────────────


def test_library_lists_the_newest_revision_serves_a_revision_and_its_picture(harness_api):
    client, factory, _clock = harness_api
    template, picture = _library_template(factory)
    listed = client.get(LIBRARY).json()
    shr = next(item for item in listed if item["connectorRef"] == "SHR-04V-S")
    assert shr == {**shr, "templateId": str(template.template_id), "version": 1, "code": "SHR-04V-S", "hasImage": True}

    full = client.get(f"{LIBRARY}/{template.template_id}/versions/1").json()
    assert full["versionSha256"] == template.version_sha256 and full["content"]["schemaVersion"] == 3
    assert client.get(f"{LIBRARY}/{template.template_id}").json() == full
    asset = full["assets"][0]["assetId"]
    image = client.get(f"{LIBRARY}/{template.template_id}/versions/1/assets/{asset}")
    assert image.status_code == 200 and image.content == picture and image.headers["content-type"] == "image/png"
    # the shared serving policy (audit F04)
    assert image.headers["x-content-type-options"] == "nosniff" and "sandbox" in image.headers["content-security-policy"]
    assert client.get(f"{LIBRARY}/{template.template_id}/versions/1/assets/{uuid4()}").status_code == 404
    assert client.get(f"{LIBRARY}/{template.template_id}/versions/2").status_code == 404


def test_the_same_revision_is_one_row_and_another_template_of_the_same_code_gets_its_own_key(harness_api):
    _client, factory, _clock = harness_api
    first, _ = _library_template(factory)
    again, _ = _library_template(factory)
    assert again.id == first.id
    other = _candidate(_seed_entry())
    other = Candidate(**{**other.__dict__, "template_id": uuid4()})
    with factory() as uow:
        stored = absorb(uow.harness_components, other, by="Технолог", at=NOW)
        uow.commit()
    assert stored.connector_ref == f"SHR-04V-S ({str(other.template_id)[:8]})"
    assert [ref for ref, code in _library_rows(factory) if code == "SHR-04V-S"] == [
        "SHR-04V-S", stored.connector_ref]


@pytest.mark.parametrize("permissions, status", [
    ({"projects": "view"}, 200), ({"techcards": "view"}, 403), ({"warehouse": "edit"}, 403),
])
def test_library_reads_with_the_drawing_right_projects_view(harness_api, permissions, status):
    client, _factory, _clock = harness_api
    app.dependency_overrides[authenticated_principal] = _principal(permissions)
    assert client.get(LIBRARY).status_code == status


# ── A placement pins template + revision ─────────────────────────────────────────────────────────────────────────


def test_a_placed_library_connector_survives_autosave_and_reload(harness_api):
    client, factory, _clock = harness_api
    template, _ = _library_template(factory)
    placed = client.put(f"{BASE}/КБ1", json={"design": _with_placed(template)})
    assert placed.status_code == 200, placed.text
    assert "componentSnapshots" not in placed.json()["design"]

    # The editor's autosave sends its own model again (moved connector): the pin is on the connector — nothing to carry.
    moved = deepcopy(placed.json()["design"])
    moved["connectors"][-1]["positions"] = {"e4": {"x": 300, "y": 40}, "drawing": {"x": 310, "y": 50}}
    assert client.put(f"{BASE}/КБ1", json={"design": moved, "revision": placed.json()["revision"]}).status_code == 200

    graph = client.get(f"{BASE}/КБ1/components").json()
    assert [row["placementId"] for row in graph["placements"]] == [PLACED]
    [snapshot] = graph["snapshots"]
    assert snapshot["snapshotId"] == str(template.id) and snapshot["sourceVersion"] == 1
    assert snapshot["sourceVersionSha256"] == template.version_sha256 and snapshot["projectId"] == PROJECT
    assert graph["placements"][0]["instance"]["positions"]["e4"] == {"x": 300, "y": 40}
    assert graph["placements"][0]["harnessId"] == "КБ1"
    # X1 pins the fixture template, which is in the library too, but its id is not one the editor places.
    assert graph["unresolved"] == ["X1"]


def test_a_save_bringing_in_a_pin_the_library_lacks_is_refused_an_import_only_warns(harness_api):
    client, factory, _clock = harness_api
    template, _ = _library_template(factory)
    ghost = Candidate(**{**_candidate(_seed_entry()).__dict__, "revision": 7})
    stray = type(template)(**{**template.__dict__, "revision": 7, "version_sha256": ghost.version_sha256})
    refused = client.put(f"{BASE}/КБ1", json={"design": _with_placed(stray)})
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"] == ["Соединитель XS9: версии 7 его шаблона нет в библиотеке компонентов"]

    snapshot = fx.snapshot()
    snapshot["harnesses"][0]["design"]["content"] = _with_placed(stray)
    imported = client.post(f"{BASE}/КБ1/import", json={"file_name": "p.zip", "content_b64": _zip(snapshot)})
    assert imported.status_code == 200, imported.text
    assert "Соединитель XS9: версии 7 его шаблона нет в библиотеке компонентов" in imported.json()["warnings"]
    # Once stored, later saves of that drawing are not locked by the pin it already had.
    design = imported.json()["design"]
    design["wires"][0]["lengthMm"] = 777
    assert client.put(f"{BASE}/КБ1", json={"design": design, "revision": imported.json()["revision"]}).status_code == 200


# ── Pre-0091 copies inside the drawing ─────────────────────────────────────────────────────────────────────────────


def test_a_write_moves_carried_copies_into_the_library_once_and_stores_the_drawing_without_them(harness_api):
    client, factory, _clock = harness_api
    before = len(_library_rows(factory))
    carried = {**_design(), **{key: value for key, value in fx.snapshot().items()
                               if key in ("componentSnapshots", "componentPlacements")}}
    for index in ("КБ1", "КБ2"):
        saved = client.put(f"{BASE}/{index}", json={"design": carried})
        assert saved.status_code == 200, saved.text
        assert "componentSnapshots" not in saved.json()["design"]
    assert len(_library_rows(factory)) == before  # fx.library put the same revision there already
    with factory() as uow:
        stored = [uow.kitsets.document_content(PROJECT, row.id)[1] for row in uow.kitsets.documents(PROJECT)]
    assert all(b"componentSnapshots" not in content for content in stored)


def test_an_old_drawing_still_opens_from_its_own_copy(harness_api):
    """A drawing stored before 0091 with a copy the library lacks (bytes as the old ERP wrote them)."""
    client, factory, _clock = harness_api
    template, _ = _library_template(factory, "SHR-05V-S")
    entry = _seed_entry("SHR-05V-S")
    foreign = Candidate(**{**_candidate(entry).__dict__, "template_id": UUID("33333333-4444-4555-8666-777777777777")})
    design = _with_placed(template)
    design["connectors"][-1]["libraryBinding"]["templateId"] = str(foreign.template_id)
    copy = {"snapshotId": "44444444-5555-4666-8777-888888888888", "projectId": "x", "sourceTemplateId": str(foreign.template_id),
            "sourceVersion": 1, "sourceVersionSha256": foreign.version_sha256, "code": foreign.code,
            "name": foreign.name, "articleBindings": foreign.article_bindings, "assets": foreign.assets,
            "schemaVersion": 3, "content": foreign.model, "createdUtc": "2026-10-01T00:00:00Z",
            "updatedUtc": "2026-10-01T00:00:00Z"}
    design["componentSnapshots"] = [copy]
    with factory() as uow:
        uow.kitsets.add_document(KitDocument(
            id=uuid4(), project=PROJECT, item_index="КБ1", file_name="Чертёж", content_type=HARNESS_CONTENT_TYPE,
            size=1, uploaded_at=NOW, uploaded_by="Технолог",
        ), json.dumps(design, ensure_ascii=False).encode())
        uow.commit()
    graph = client.get(f"{BASE}/КБ1/components").json()
    assert [row["snapshotId"] for row in graph["snapshots"]] == [copy["snapshotId"]]
    assert graph["snapshots"][0]["projectId"] == PROJECT
    assert "componentSnapshots" not in client.get(f"{BASE}/КБ1").json()["design"]


def test_the_graph_resolves_the_library_first_and_lists_what_it_cannot(harness_api):
    _client, factory, _clock = harness_api
    template, _ = _library_template(factory)
    design = _with_placed(template)
    graph = placement_graph(design, lambda pin: template if pin[0] == str(template.template_id) else None,
                            project=PROJECT, harness="КБ1", updated=datetime(2026, 10, 10))
    assert [row["snapshotId"] for row in graph["placements"]] == [str(template.id)]
    assert graph["unresolved"] == ["X1"]
    assert split_embedded({"a": 1, "componentSnapshots": [{"x": 1}], "componentPlacements": []}) == ({"a": 1}, [{"x": 1}])
    with pytest.raises(ValueError):
        candidate_from_snapshot({"sourceTemplateId": "t1"})


# ── Exchange with TECHMAP-GRAPHER ─────────────────────────────────────────────────────────────────────────────────


def test_the_export_carries_the_pinned_revisions_and_the_import_brings_them_back(harness_api):
    client, factory, _clock = harness_api
    template, _ = _library_template(factory)
    client.put(f"{BASE}/КБ1", json={"design": _with_placed(template)})
    exported = client.get(f"{BASE}/КБ1/export")
    with ZipFile(BytesIO(exported.content)) as archive:
        snapshot = json.loads(archive.read("project/snapshot.json"))
    [harness] = snapshot["harnesses"]
    assert "componentPlacements" not in harness["design"]["content"]
    assert [row["placementId"] for row in snapshot["componentPlacements"]] == [PLACED]
    assert {row["harnessId"] for row in snapshot["componentPlacements"]} == {harness["harnessId"]}
    assert [row["sourceTemplateId"] for row in snapshot["componentSnapshots"]] == [str(template.template_id)]
    assert snapshot["componentSnapshots"][0]["projectId"] == snapshot["project"]["projectId"]

    rows = len(_library_rows(factory))
    imported = client.post(f"{BASE}/КБ2/import", json={
        "file_name": "a.techmap-project.zip", "content_b64": base64.b64encode(exported.content).decode(),
    })
    assert imported.status_code == 200, imported.text
    assert len(_library_rows(factory)) == rows
    assert [row["placementId"] for row in client.get(f"{BASE}/КБ2/components").json()["placements"]] == [PLACED]
