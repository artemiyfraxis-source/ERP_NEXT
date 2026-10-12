"""The harness drawing kept as КД of a position: save over itself, version conflict, «Ручная сборка» from it,
`.techmap-project.zip` export / import."""

import base64
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
import json
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.techcards.domain.route import parse_route, RouteOp
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.grapher_import import read_export
from app.modules.techcards.harness import parse_design
from app.modules.techcards.harness.assembly import assembly_from_harness
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.harness.schema import HARNESS_CONTENT_TYPE, HARNESS_UNLINKED_CONTENT_TYPE
from app.modules.techcards.kitsets import KitAssembly, KitDocument, Kitset, document_kind
from app.modules.techcards.project_cards import _document as checked_document, operation_key
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor

from . import grapher_fixture as fx

PROJECT = "708К.4"
BASE = f"/api/techcards/kitsets/{PROJECT}/harness"
NOW = datetime(2026, 10, 6, 10, 0)


def _principal(permissions, name="Технолог"):
    return lambda: Principal(
        actor=Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name=name, department="Производство"),
        email="engineer@example.test", roles=frozenset({"engineer"}), permissions=permissions,
    )


@pytest.fixture()
def harness_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)  # noqa: E731
    app.state.techcards_uow_factory = factory
    clock = {"now": NOW}
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})
    app.dependency_overrides[moscow_now] = lambda: clock["now"]
    with factory() as uow:
        uow.kitsets.save(Kitset(
            project=PROJECT, customer="ООО Заказчик", calc_source="", updated_at=NOW, updated_by="Технолог",
            created_at=NOW, assemblies=(
                KitAssembly(index="КБ1", name="Жгут питания", run=Decimal("10")),
                KitAssembly(index="КБ2", name="Жгут сигнальный", run=Decimal("5")),
            ),
        ))
        uow.commit()
    fx.library(factory)
    yield TestClient(app), factory, clock
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    engine.dispose()


def _rows(factory, index=None):
    with factory() as uow:
        return [row for row in uow.kitsets.documents(PROJECT) if index is None or row.item_index == index]


def _design(**extra):
    design = fx.design()
    design.update(extra)
    return design


def _zip(snapshot) -> str:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("project/snapshot.json", json.dumps(snapshot, ensure_ascii=False))
    return base64.b64encode(buffer.getvalue()).decode()


# ── Save over itself, version ───────────────────────────────────────────────────────────────────────────────────


def test_first_save_creates_one_harness_kd_row_and_get_returns_it_with_a_version(harness_api):
    client, factory, _clock = harness_api
    empty = client.get(f"{BASE}/КБ1")
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"document": None, "design": None, "revision": None, "version": None, "warnings": []}

    saved = client.put(f"{BASE}/кб1", json={"design": _design(), "version": None})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["created"] is True and body["version"]
    assert body["document"]["name"] == "708К.4КБ1 — Чертёж жгута"
    assert body["document"]["kind"] == "harness" and body["document"]["index"] == "КБ1"

    rows = _rows(factory)
    assert [(row.item_index, row.content_type, row.file_name) for row in rows] == [
        ("КБ1", HARNESS_CONTENT_TYPE, "708К.4КБ1 — Чертёж жгута"),
    ]
    assert document_kind(rows[0]) == "harness"

    read = client.get(f"{BASE}/КБ1").json()
    assert read["version"] == body["version"]
    # Stored in the TECHMAP-GRAPHER document format: every field the model does not cover comes back unchanged.
    assert read["design"]["views"] == fx.DESIGN["views"]
    assert read["design"]["physicalTopology"]["nodes"] == fx.DESIGN["physicalTopology"]["nodes"]
    assert read["design"]["manufacturingRoute"]["rows"][0]["presentation"] == {"backgroundOpacity": 1, "objects": []}
    assert parse_design(read["design"])[0] == parse_design(fx.design())[0]
    # The plain КД download hands the same JSON.
    downloaded = client.get(f"/api/techcards/kitsets/{PROJECT}/documents/{rows[0].id}")
    assert downloaded.headers["content-type"] == "application/octet-stream"  # JSON is a download, never a page
    assert json.loads(downloaded.content) == read["design"]


def test_save_overwrites_in_place_with_the_version_it_was_made_on(harness_api):
    client, factory, clock = harness_api
    first = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()
    clock["now"] = NOW + timedelta(minutes=5)
    edited = _design()
    edited["wires"][0]["lengthMm"] = 333
    second = client.put(f"{BASE}/КБ1", json={"design": edited, "version": first["version"]})
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["version"] != first["version"]

    rows = _rows(factory)
    assert len(rows) == 1 and str(rows[0].id) == first["document"]["id"]
    assert rows[0].uploaded_at == NOW + timedelta(minutes=5)
    assert client.get(f"{BASE}/КБ1").json()["design"]["wires"][0]["lengthMm"] == 333
    # Another position keeps its own drawing.
    assert client.put(f"{BASE}/КБ2", json={"design": _design()}).json()["created"] is True
    assert len(_rows(factory)) == 2


def test_stale_or_missing_version_is_a_conflict_and_changes_nothing(harness_api):
    client, factory, clock = harness_api
    first = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"}, name="Мастер")
    clock["now"] = NOW + timedelta(minutes=1)
    theirs = _design()
    theirs["wires"][0]["lengthMm"] = 111
    assert client.put(f"{BASE}/КБ1", json={"design": theirs, "version": first["version"]}).status_code == 200

    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})
    mine = _design()
    mine["wires"][0]["lengthMm"] = 999
    stale = client.put(f"{BASE}/КБ1", json={"design": mine, "version": first["version"]})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Чертёж жгута уже сохранил Мастер 06.10.2026 10:01 — обновите страницу и повторите правку"
    blind = client.put(f"{BASE}/КБ1", json={"design": mine})
    assert blind.status_code == 409
    assert client.get(f"{BASE}/КБ1").json()["design"]["wires"][0]["lengthMm"] == 111
    assert len(_rows(factory)) == 1

    # The drawing unlinked from the position while it was being edited (a harness drawing is never deleted).
    assert client.delete(f"/api/techcards/kitsets/{PROJECT}/documents/{first['document']['id']}").status_code == 409
    client.post(f"{BASE}/КБ1/unlink", json={})
    gone = client.put(f"{BASE}/КБ1", json={"design": mine, "version": first["version"]})
    assert gone.status_code == 409 and "отвязан" in gone.json()["detail"]


def test_replace_content_refuses_a_row_saved_after_it_was_read(harness_api):
    _client, factory, _clock = harness_api
    with factory() as uow:
        document = KitDocument(id=UUID(int=7), project=PROJECT, item_index="КБ1", file_name="x",
                               content_type=HARNESS_CONTENT_TYPE, size=2, uploaded_at=NOW, uploaded_by="Технолог")
        uow.kitsets.add_document(document, b"{}")
        uow.commit()
    with factory() as uow:
        changed = uow.kitsets.replace_content(
            PROJECT, document.id, file_name="x", content_type=HARNESS_CONTENT_TYPE, content=b"[]",
            uploaded_at=NOW, uploaded_by="Мастер", expected_uploaded_at=NOW - timedelta(seconds=1),
        )
        assert changed == 0
        assert uow.kitsets.replace_content(
            "708К.5", document.id, file_name="x", content_type=HARNESS_CONTENT_TYPE, content=b"[]",
            uploaded_at=NOW, uploaded_by="Мастер", expected_uploaded_at=NOW,
        ) == 0
        assert uow.kitsets.replace_content(
            PROJECT, document.id, file_name="y", content_type=HARNESS_CONTENT_TYPE, content=b"[1]",
            uploaded_at=NOW + timedelta(hours=1), uploaded_by="Мастер", expected_uploaded_at=NOW,
        ) == 1
        uow.commit()
    with factory() as uow:
        stored, content = uow.kitsets.document_content(PROJECT, document.id)
    assert (content, stored.size, stored.file_name, stored.uploaded_by) == (b"[1]", 3, "y", "Мастер")


def test_refusals_are_russian(harness_api):
    client, _factory, _clock = harness_api
    wrong = client.put(f"{BASE}/КБ1", json={"design": _design(schemaVersion=2)})
    assert wrong.status_code == 422
    assert wrong.json()["detail"] == "Поддерживается только схема чертежа 1, в документе — 2"
    missing = client.put(f"{BASE}/КБ9", json={"design": _design()})
    assert missing.status_code == 404 and missing.json()["detail"] == "В наборе «708К.4» нет сборки КБ9"
    assert client.get("/api/techcards/kitsets/999/harness/КБ1").status_code == 404
    assert client.post(f"{BASE}/КБ1/derive", json={}).json()["detail"] == "У сборки КБ1 нет чертежа жгута"
    assert client.get(f"{BASE}/КБ1/export").status_code == 404


def test_rights_view_reads_and_derives_edit_writes(harness_api):
    client, _factory, _clock = harness_api
    version = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()["version"]
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.get(f"{BASE}/КБ1").status_code == 200
    assert client.post(f"{BASE}/КБ1/derive", json={}).status_code == 200
    assert client.get(f"{BASE}/КБ1/export").status_code == 200
    assert client.put(f"{BASE}/КБ1", json={"design": _design(), "version": version}).status_code == 403
    assert client.post(f"{BASE}/КБ1/import", json={
        "file_name": "x.techmap-project.zip", "content_b64": _zip(fx.snapshot()), "version": version,
    }).status_code == 403
    app.dependency_overrides[authenticated_principal] = _principal({"techcards": "edit"})
    assert client.get(f"{BASE}/КБ1").status_code == 403


# ── «Ручная сборка» from the drawing ────────────────────────────────────────────────────────────────────────────


def _block(config, key):
    return next(block for block in config["route"]["blocks"] if block["key"] == key)


def _by_origin(config):
    return {op["origin"]: op for op in config["ops"]}


def test_derive_builds_manual_config_in_route_blocks_with_kd_keys_and_saves_nothing(harness_api):
    client, factory, _clock = harness_api
    client.put(f"{BASE}/КБ1", json={"design": _design()})
    response = client.post(f"{BASE}/КБ1/derive", json={})
    assert response.status_code == 200, response.text
    config = response.json()["build_config"]
    assert (config["mode"], config["assemblyIndex"], config["assemblyName"], config["partQty"]) == (
        "manual", "КБ1", "Жгут питания", 10)
    ops = _by_origin(config)
    assert [op["seq"] for op in config["ops"]] == list(range(1, len(config["ops"]) + 1))
    assert len(ops) == len(config["ops"])  # every КД key once

    # Blocks follow the drawing's route: r1 «Провода нарезанные» → r2 «Жгут КБ1» (final, nothing depends on it);
    # objects the route does not list go to «Не в маршруте КД».
    final = _block(config, "route:r2")
    assert final["final"] is True and final["name"] == "Жгут КБ1" and final["refs"] == ["x1", "x2", "cv1"]
    r1 = _block(config, "route:r1")
    assert r1 == {**r1, "name": "Провода нарезанные", "final": False, "kind": "semiFinished", "refs": ["w1", "w2", "c1"]}
    assert _block(config, "route:-")["name"] == "Не в маршруте КД"
    assert {(edge["from"], edge["to"]) for edge in config["route"]["edges"]} == {
        ("route:r1", "route:r2"), ("route:-", "route:r2")}
    owner = {position: block["key"] for block in config["route"]["blocks"] for position in block["ops"]}

    def block_of(origin):
        return owner[ops[origin]["seq"]]

    # A cut per block and strip pattern, with the wires and their cut lengths (300 + 8 + 4 = 312, 150.2 + 0.4 → 151).
    cut = ops["harness:r1:cutWireStrip.AB"]
    assert cut["type"] == "cutWireStrip" and block_of("harness:r1:cutWireStrip.AB") == "route:r1"
    assert [wire["length"] for wire in cut["wires"]] == [312, 151]
    assert cut["wires"][0] == {"name": "ПВАМ 0,35 красный", "art": "ПВАМ 0,35", "qty": 1, "length": 312,
                               "color": "красный"}
    assert cut["strip"] == {"a": True, "b": True}
    assert cut["label"] == "Резка с разделкой: +5V, GND"
    # Terminal operations per connector and side; X1 has wires in two blocks — the key says which.
    crimp = ops["harness:x1:prsTerm.A@r1"]
    assert crimp["ep"]["connName"] == "X1" and crimp["ep"]["termArt"] == "T-DS1069-SC600"
    assert len(crimp["wires"]) == 2 and crimp["label"] == "Опрессовка терминала (А) — X1"
    assert "harness:x1:prsTerm.A@-" in ops and "harness:x4:prsTerm.A" in ops
    # Shared operations once: the splice, the pair, the cable, the tube.
    assert block_of("harness:j1:splice") == "route:r1"
    assert ops["harness:p1:twist"]["label"] == "Свивка пары D+/D-"
    assert ops["harness:c1:cutWire"]["wires"][0]["cores"] == 2
    assert [ops[f"harness:cv1:{code}"]["type"] for code in ("cutTut", "insTut", "heatTut")] == ["cutTut", "insTut", "heatTut"]
    assert ops["harness:cv1:cutTut"]["tutArt"] == "ТУТ 6/3мм"
    # The route's own steps the wires do not imply: tinning and the assembly itself, with their БД.ОП codes.
    assert ops["harness:op3:assembly"] == {**ops["harness:op3:assembly"], "type": "customOp", "label": "Сборка жгута",
                                           "opNum": "ASM_HARNESS"}
    assert ops["harness:op2:tin"]["note"] == "кончики"
    assert "harness:op1:cut-crimp" not in ops  # covered by the per-wire cuts and crimps
    # In a block: cut before crimp before insert; the assembly step last.
    final_ops = [config["ops"][position - 1]["origin"] for position in final["ops"]]
    assert final_ops[-1] == "harness:op3:assembly"
    assert ops["harness:r1:cutWireStrip.AB"]["seq"] < ops["harness:x1:prsTerm.A@r1"]["seq"] < ops["harness:x1:insTerm.A@r1"]["seq"]
    # The drawing's data rides along.
    assert ops["harness:x4:prsTerm.A"]["kdData"][0]["wire_id"] == "w9"

    # The scheme holds as the ETK build reads it; with cards picked the ETK builds.
    route_ops = [RouteOp(position=op["seq"], type=op["type"], name=op["label"]) for op in config["ops"]]
    parse_route(config["route"], route_ops, manual=True)
    with_cards = deepcopy(config)
    for op in with_cards["ops"]:
        op["opNum"] = op.get("opNum") or f"OP_{op['seq']}"
    document = checked_document(build_etk(with_cards, [], operation_library_commit=""))
    assert len(document["operations"]) == len(config["ops"])
    assert len(_rows(factory)) == 1  # derive saves nothing


def test_derive_from_the_unsaved_drawing_in_the_editor(harness_api):
    client, factory, _clock = harness_api
    response = client.post(f"{BASE}/КБ2/derive", json={"design": _design()})
    assert response.status_code == 200, response.text
    assert response.json()["build_config"]["assemblyName"] == "Жгут сигнальный"
    assert _rows(factory) == []


def test_derive_without_route_puts_everything_in_the_final_block_and_odd_route_falls_back():
    design = fx.design()
    del design["manufacturingRoute"]
    config, _notes = assembly_from_harness(parse_design(design)[0], index="КБ1", name="Жгут", part_qty=1)
    assert config["route"] == {"blocks": [{"key": "b1", "ops": list(range(1, len(config["ops"]) + 1)), "final": True}],
                               "edges": []}

    # Two rows nothing depends on — no single final block: one block and a note.
    forked = fx.design()
    forked["manufacturingRoute"]["rows"][1]["dependsOn"] = []
    config, notes = assembly_from_harness(parse_design(forked)[0], index="КБ1", name="Жгут", part_qty=1)
    assert [block["key"] for block in config["route"]["blocks"]] == ["b1"]
    assert any(note.startswith("Маршрут чертежа не сложился в схему") for note in notes)

    # Nothing to derive from.
    with pytest.raises(ValueError, match="нет проводов"):
        assembly_from_harness(parse_design({"schemaVersion": 1, "wires": []})[0])


def test_derive_keys_are_stable_across_edits_of_lengths():
    design = fx.design()
    first, _ = assembly_from_harness(parse_design(design)[0])
    design["wires"][0]["lengthMm"] = 999
    second, _ = assembly_from_harness(parse_design(design)[0])
    assert [op["origin"] for op in first["ops"]] == [op["origin"] for op in second["ops"]]
    assert _by_origin(second)["harness:r1:cutWireStrip.AB"]["wires"][0]["length"] == 1011


# ── .techmap-project.zip ────────────────────────────────────────────────────────────────────────────────────────


def test_export_and_import_round_trip_through_techmap_project_zip(harness_api):
    client, factory, clock = harness_api
    saved = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()
    exported = client.get(f"{BASE}/КБ1/export")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/octet-stream"  # a download, as every non-viewable file
    assert exported.headers["content-disposition"].startswith("attachment;")
    assert "708%D0%9A.4%D0%9A%D0%911.techmap-project.zip" in exported.headers["content-disposition"]
    with ZipFile(BytesIO(exported.content)) as archive:
        assert archive.namelist() == ["project/snapshot.json"]
        snapshot = json.loads(archive.read("project/snapshot.json"))
    assert snapshot["snapshotFormat"] == 4
    assert snapshot["project"]["designation"] == PROJECT and snapshot["project"]["batchQuantity"] == 10
    [harness] = snapshot["harnesses"]
    assert (harness["designation"], harness["quantity"], harness["harnessId"]) == ("КБ1", 10, saved["document"]["id"])
    assert harness["design"] == {"schemaVersion": 1, "content": saved["design"]}
    # The ERP's own reader of TECHMAP-GRAPHER exports takes it as it takes the author's.
    assert read_export(exported.content, "x.techmap-project.zip")["harnesses"][0]["designation"] == "КБ1"

    # Import into another position: the only harness in the archive lands there, the drawing as it was.
    clock["now"] = NOW + timedelta(hours=1)
    imported = client.post(f"{BASE}/КБ2/import", json={
        "file_name": "708К.4КБ1.techmap-project.zip", "content_b64": base64.b64encode(exported.content).decode(),
    })
    assert imported.status_code == 200, imported.text
    assert imported.json()["created"] is True
    assert client.get(f"{BASE}/КБ2").json()["design"] == saved["design"]
    assert [row.item_index for row in _rows(factory)] == ["КБ1", "КБ2"]


def test_real_size_view_setting_survives_save_export_and_import_unchanged(harness_api):
    """«Реальные размеры, мм» (drawingDocuments.realSize) is the editor's own optional field: the ERP keeps it as
    written through save, the .techmap-project.zip export and an import back, and adds it to no drawing."""
    client, _factory, _clock = harness_api
    plain = client.put(f"{BASE}/КБ2", json={"design": _design()}).json()
    assert "realSize" not in plain["design"]["drawingDocuments"]
    real_size = {"enabled": True, "scaleDenominator": 2.5}
    design = _design()
    design["drawingDocuments"] = {**design["drawingDocuments"], "realSize": real_size}
    saved = client.put(f"{BASE}/КБ1", json={"design": design})
    assert saved.status_code == 200, saved.text
    read = client.get(f"{BASE}/КБ1").json()
    assert read["design"]["drawingDocuments"] == design["drawingDocuments"]
    assert read["design"] == saved.json()["design"]

    exported = client.get(f"{BASE}/КБ1/export")
    with ZipFile(BytesIO(exported.content)) as archive:
        snapshot = json.loads(archive.read("project/snapshot.json"))
    assert snapshot["harnesses"][0]["design"]["content"]["drawingDocuments"]["realSize"] == real_size

    imported = client.post(f"{BASE}/КБ2/import", json={
        "file_name": "708К.4КБ1.techmap-project.zip", "content_b64": base64.b64encode(exported.content).decode(),
        "version": plain["version"],
    })
    assert imported.status_code == 200, imported.text
    assert client.get(f"{BASE}/КБ2").json()["design"] == read["design"]


def test_import_of_the_authors_export_picks_the_harness_of_the_position_and_overwrites(harness_api):
    client, factory, _clock = harness_api
    first = client.put(f"{BASE}/КБ1", json={"design": _design(views={})}).json()
    snapshot = fx.snapshot()
    other = deepcopy(snapshot["harnesses"][0])
    other.update(harnessId="h-kb2", designation="КБ2")
    content = other["design"]["content"]
    content.update(wires=content["wires"][:1], junctions=[], screens=[], diffPairs=[], cables=[])
    content["physicalTopology"]["routes"] = content["physicalTopology"]["routes"][:1]
    snapshot["harnesses"].append(other)

    stale = client.post(f"{BASE}/КБ1/import", json={"file_name": "p.techmap-project.zip", "content_b64": _zip(snapshot)})
    assert stale.status_code == 409
    imported = client.post(f"{BASE}/КБ1/import", json={
        "file_name": "p.techmap-project.zip", "content_b64": _zip(snapshot), "version": first["version"],
    })
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["created"] is False and len(body["design"]["wires"]) == len(fx.DESIGN["wires"])
    assert body["design"]["views"] == fx.DESIGN["views"]  # after the import the ERP copy is the truth
    assert len(_rows(factory, "КБ1")) == 1
    second = client.post(f"{BASE}/КБ2/import", json={"file_name": "p.zip", "content_b64": _zip(snapshot)})
    assert len(second.json()["design"]["wires"]) == 1


@pytest.mark.parametrize(("content", "file_name", "message"), [
    (b"not a zip", "p.techmap-project.zip", "Не удалось разобрать экспорт TECHMAP-GRAPHER"),
    (None, "p.techmap-project.zip", "В архиве нет project/snapshot.json — это не экспорт TECHMAP-GRAPHER"),
    (json.dumps({"project": {}}).encode(), "snapshot.json", "Это не экспорт TECHMAP-GRAPHER: нет списка жгутов"),
    (json.dumps({"harnesses": []}).encode(), "snapshot.json", "В экспорте нет ни одного жгута"),
    (json.dumps({"harnesses": [{"designation": "КБ7", "design": {}}, {"designation": "КБ8", "design": {}}]}).encode(),
     "snapshot.json", "В экспорте 2 жгута(ов) (КБ7, КБ8), жгута «КБ1» среди них нет"),
    (json.dumps({"harnesses": [{"designation": "КБ1", "design": {"schemaVersion": 2, "content": {}}}]}).encode(),
     "snapshot.json", "Поддерживается только схема чертежа 1, в экспорте — 2"),
])
def test_import_refusals_are_russian(harness_api, content, file_name, message):
    client, factory, _clock = harness_api
    if content is None:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("other.json", "{}")
        content = buffer.getvalue()
    response = client.post(f"{BASE}/КБ1/import", json={
        "file_name": file_name, "content_b64": base64.b64encode(content).decode(),
    })
    assert response.status_code == 422
    assert response.json()["detail"] == message
    assert _rows(factory) == []


# Cards the generator picks for the drawing's operations (template hint / category, as the screen does).
_PICKED = {
    "cutWireStrip": "CUT_WIRE+2strip", "cutWire": "CUT_WIRE_auto", "coaxStrip": "STRIP_COAX_auto", "twist": "TWIST",
    "prsTerm": "PRS_TERMINAL_hand", "insTerm": "INS_terminal_1.00", "splice": "SDR_W_W", "insWrap": "INS_WRAP",
    "stripCable": "STRIP_CABLE", "cutTut": "CUT_TUT_short", "insTut": "INS_TUT", "heatTut": "HEAT_TTK_short",
}


def test_a_scheme_derived_from_the_drawing_with_the_generator_cards_builds_with_distinct_etk_keys(harness_api):
    """«Предложить из КД» → cards picked → «Создать»: X1 and X2 are crimped in two blocks, the strip cut is in two —
    one card, connector and side for several operations. The ETK builds with distinct keys: the connector stays in the
    key, the operation's number is its tail; an operation without a twin keeps its key as it was."""
    from tests.techcards.test_etk_manual import CARDS

    client, _factory, _clock = harness_api
    client.put(f"{BASE}/КБ1", json={"design": _design()})
    config = client.post(f"{BASE}/КБ1/derive", json={}).json()["build_config"]
    by_code = {card.code: card for card in CARDS}
    for op in config["ops"]:
        if op["type"] in _PICKED:
            card = by_code[_PICKED[op["type"]]]
            op.update(templateId=card.source_id, opNum=card.code)
    document = checked_document(build_etk(config, CARDS, operation_library_commit=""))
    keys = [operation_key(operation) for operation in document["operations"]]
    assert len(keys) == len(config["ops"]) == len(set(keys))
    ops = _by_origin(config)
    connector = {operation["position"]: operation["connector"] for operation in document["operations"]}
    twins = (ops["harness:x1:prsTerm.A@r1"]["seq"], ops["harness:x1:prsTerm.A@-"]["seq"])
    assert {connector[seq] for seq in twins} == {f"X1 · {seq}" for seq in twins}
    assert connector[ops["harness:x4:prsTerm.A"]["seq"]] == "X4"
    cuts = [op["seq"] for op in config["ops"] if op["type"] == "cutWireStrip"]
    assert len(cuts) == 2 and [connector[seq] for seq in cuts] == [f"разделка · {seq}" for seq in cuts]


# ── «Отвязать» with «Вернуть»: nothing is deleted ──────────────────────────────────────────────────────────────


def _kd(client, index="КБ1"):
    card = client.get(f"/api/techcards/kitsets/{PROJECT}")
    assert card.status_code == 200, card.text
    return next(assembly for assembly in card.json()["assemblies"] if assembly["index"] == index)["kd"]


def _harness_events(factory):
    with factory() as uow:
        return list(uow.session.scalars(
            select(AuditEventRow.event_type)
            .where(AuditEventRow.event_type.like("techcards.kitset_harness%"))
            .order_by(AuditEventRow.occurred_at)
        ))


def test_unlink_keeps_the_drawing_listed_and_restore_links_it_back(harness_api):
    client, factory, clock = harness_api
    saved = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()
    document_id = saved["document"]["id"]

    clock["now"] = NOW + timedelta(minutes=5)
    unlinked = client.post(f"{BASE}/КБ1/unlink", json={"version": saved["version"]})
    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json()["document"]["kind"] == "harness_unlinked"
    # The position no longer reads it: no drawing, nothing to derive or export; the row and its bytes stay.
    assert client.get(f"{BASE}/КБ1").json()["document"] is None
    assert client.post(f"{BASE}/КБ1/derive", json={}).status_code == 404
    assert client.get(f"{BASE}/КБ1/export").status_code == 404
    [row] = _rows(factory, "КБ1")
    assert str(row.id) == document_id and document_kind(row) == "harness_unlinked" and row.stored
    assert [(item["id"], item["kind"]) for item in _kd(client)] == [(document_id, "harness_unlinked")]
    # An unlinked drawing is not hard-deleted either.
    refused = client.delete(f"/api/techcards/kitsets/{PROJECT}/documents/{document_id}")
    assert refused.status_code == 409 and "отвязывают" in refused.json()["detail"]
    # An editor still open on it cannot save over the gap: its version names a drawing the position no longer has.
    stale = client.put(f"{BASE}/КБ1", json={"design": _design(), "version": saved["version"]})
    assert stale.status_code == 409 and "отвязан" in stale.json()["detail"]

    clock["now"] = NOW + timedelta(minutes=10)
    restored = client.post(f"{BASE}/КБ1/restore", json={"document_id": document_id})
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["document"]["id"] == document_id and body["document"]["kind"] == "harness"
    assert body["design"] == saved["design"] and body["version"] == saved["version"]
    assert [item["kind"] for item in _kd(client)] == ["harness"]
    assert _harness_events(factory) == [
        "techcards.kitset_harness_saved", "techcards.kitset_harness_unlinked", "techcards.kitset_harness_restored",
    ]


def test_removal_takes_only_an_unlinked_drawing_with_its_history(harness_api):
    client, factory, _clock = harness_api
    first = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()
    document_id = first["document"]["id"]
    url = f"{BASE}/КБ1/unlinked/{document_id}"
    # A linked drawing is refused: unlink first.
    refused = client.delete(url)
    assert refused.status_code == 409 and "сначала отвяжите" in refused.json()["detail"]
    assert len(_rows(factory, "КБ1")) == 1

    assert client.post(f"{BASE}/КБ1/unlink", json={}).status_code == 200
    # Another position's address does not reach it; no edit right — 403.
    assert client.delete(f"{BASE}/КБ2/unlinked/{document_id}").status_code == 404
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.delete(url).status_code == 403
    assert len(_rows(factory, "КБ1")) == 1
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})

    removed = client.delete(url)
    assert removed.status_code == 200, removed.text
    assert removed.json()["res"]["deleted"] == 1
    assert _rows(factory, "КБ1") == [] and _kd(client) == []
    with factory() as uow:
        assert uow.harness_revisions.count(UUID(document_id)) == 0
    assert _harness_events(factory)[-1] == "techcards.kitset_harness_deleted"
    assert client.delete(url).status_code == 404


def test_unlink_and_restore_refusals(harness_api):
    client, factory, _clock = harness_api
    assert client.post(f"{BASE}/КБ1/unlink", json={}).json()["detail"] == "У сборки КБ1 нет чертежа жгута"
    first = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()
    edited = _design()
    edited["wires"][0]["lengthMm"] = 333
    client.put(f"{BASE}/КБ1", json={"design": edited, "version": first["version"]})
    stale = client.post(f"{BASE}/КБ1/unlink", json={"version": first["version"]})
    assert stale.status_code == 409 and "обновите страницу" in stale.json()["detail"]
    assert client.post(f"{BASE}/КБ1/unlink").status_code == 200  # the card unlinks without a version
    old_id = _rows(factory, "КБ1")[0].id

    # A new drawing on the position: the old one cannot come back on top of it.
    client.put(f"{BASE}/КБ1", json={"design": _design()})
    busy = client.post(f"{BASE}/КБ1/restore", json={"document_id": str(old_id)})
    assert busy.status_code == 409
    assert busy.json()["detail"] == "У сборки КБ1 уже есть чертёж жгута — сначала отвяжите его, потом верните этот"
    # Only an unlinked drawing of this very position comes back.
    linked_id = next(row.id for row in _rows(factory, "КБ1") if document_kind(row) == "harness")
    assert client.post(f"{BASE}/КБ1/restore", json={"document_id": str(linked_id)}).status_code == 404
    assert client.post(f"{BASE}/КБ2/restore", json={"document_id": str(old_id)}).status_code == 404
    assert client.delete(f"/api/techcards/kitsets/{PROJECT}/documents/{linked_id}").status_code == 409
    assert len(_rows(factory, "КБ1")) == 2

    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.post(f"{BASE}/КБ1/unlink", json={}).status_code == 403
    assert client.post(f"{BASE}/КБ1/restore", json={"document_id": str(old_id)}).status_code == 403


# ── One linked drawing per position — a database rule (migration 0089) ─────────────────────────────────────────


def _linked(factory, index="КБ1"):
    return [row for row in _rows(factory, index) if row.content_type == HARNESS_CONTENT_TYPE]


def test_restore_racing_another_restore_is_a_conflict_not_a_second_drawing(harness_api, monkeypatch):
    client, factory, clock = harness_api
    first = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()
    client.post(f"{BASE}/КБ1/unlink", json={})
    clock["now"] = NOW + timedelta(minutes=5)
    second = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()
    client.post(f"{BASE}/КБ1/unlink", json={})
    assert client.post(f"{BASE}/КБ1/restore", json={"document_id": second["document"]["id"]}).status_code == 200

    # The other «Вернуть» checked the position before this one committed: its check saw no linked drawing.
    import app.modules.techcards.harness.application as harness_module
    monkeypatch.setattr(harness_module, "current", lambda uow, project, index: None)
    raced = client.post(f"{BASE}/КБ1/restore", json={"document_id": first["document"]["id"]})
    assert raced.status_code == 409, raced.text
    assert raced.json()["detail"] == (
        "У сборки КБ1 уже есть чертёж жгута — его только что привязали или создали; обновите страницу"
    )
    assert [str(row.id) for row in _linked(factory)] == [second["document"]["id"]]
    assert document_kind(next(row for row in _rows(factory) if str(row.id) == first["document"]["id"])) == (
        "harness_unlinked"
    )
    assert _harness_events(factory).count("techcards.kitset_harness_restored") == 1


def test_first_save_racing_another_first_save_is_a_conflict(harness_api, monkeypatch):
    client, factory, _clock = harness_api
    saved = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()
    import app.modules.techcards.harness.application as harness_module
    monkeypatch.setattr(harness_module, "current", lambda uow, project, index: None)
    raced = client.put(f"{BASE}/КБ1", json={"design": _design(), "version": None})
    assert raced.status_code == 409, raced.text
    assert "уже есть чертёж жгута" in raced.json()["detail"]
    assert [str(row.id) for row in _linked(factory)] == [saved["document"]["id"]]


def test_the_database_refuses_a_second_linked_drawing_but_not_unlinked_ones(harness_api):
    from sqlalchemy.exc import IntegrityError

    _client, factory, _clock = harness_api

    def add(index, content_type, minutes):
        with factory() as uow:
            uow.kitsets.add_document(KitDocument(
                id=uuid4(), project=PROJECT, item_index=index, file_name="Чертёж", content_type=content_type,
                size=2, uploaded_at=NOW + timedelta(minutes=minutes), uploaded_by="Технолог",
            ), b"{}")
            uow.commit()

    add("КБ1", HARNESS_CONTENT_TYPE, 0)
    add("КБ1", HARNESS_UNLINKED_CONTENT_TYPE, 1)
    add("КБ1", HARNESS_UNLINKED_CONTENT_TYPE, 2)
    add("КБ2", HARNESS_CONTENT_TYPE, 3)
    add("КБ1", "application/pdf", 4)
    with pytest.raises(IntegrityError):
        add("КБ1", HARNESS_CONTENT_TYPE, 5)
    assert len(_rows(factory, "КБ1")) == 4 and len(_linked(factory)) == 1
