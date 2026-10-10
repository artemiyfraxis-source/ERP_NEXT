"""The harness drawing history (migration 0090): every save appends a revision, the integer revision is the
concurrency token (the old hash for one release), a drawing that must not be stored is refused with Russian reasons,
references a save brings in are looked up in the ERP books, an earlier revision is restored as a new one."""

import base64
from datetime import datetime, timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import BUNDLED_MIRROR, load_mirror
from app.modules.techcards.harness.schema import HARNESS_CONTENT_TYPE, legacy_version
from app.modules.techcards.harness.schema import HarnessWriteConflict
from app.modules.techcards.harness_infrastructure import HarnessRevisionRow
from app.modules.techcards.kitset_infrastructure import SqlAlchemyKitsetRepository
from app.modules.techcards.kitsets import KitDocument
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import AuditEventRow

from . import grapher_fixture as fx
from .test_harness_api import BASE, NOW, PROJECT, _design, _principal, _rows, harness_api  # noqa: F401 — fixture


def _revisions(factory):
    with factory() as uow:
        rows = uow.session.scalars(select(HarnessRevisionRow).order_by(HarnessRevisionRow.revision))
        return [(row.revision, row.kind, row.saved_by, row.restored_from) for row in rows]


def _edited(length):
    design = _design()
    design["wires"][0]["lengthMm"] = length
    return design


def test_each_save_appends_a_revision_and_the_history_lists_them_newest_first(harness_api):
    client, factory, clock = harness_api
    first = client.put(f"{BASE}/КБ1", json={"design": _design(), "revision": None})
    assert first.status_code == 200, first.text
    assert (first.json()["revision"], first.json()["version"], first.json()["created"]) == (1, "1", True)
    clock["now"] = NOW + timedelta(minutes=5)
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"}, name="Мастер")
    second = client.put(f"{BASE}/КБ1", json={"design": _edited(333), "revision": 1}).json()
    assert (second["revision"], second["created"]) == (2, False)
    assert client.get(f"{BASE}/КБ1").json()["revision"] == 2

    history = client.get(f"{BASE}/КБ1/revisions")
    assert history.status_code == 200, history.text
    body = history.json()
    assert (body["current"], body["total"], body["document_id"]) == (2, 2, first.json()["document"]["id"])
    assert [(item["revision"], item["saved_by"], item["kind"]) for item in body["revisions"]] == [
        (2, "Мастер", "saved"), (1, "Технолог", "saved")]
    assert body["revisions"][0]["saved_at"].startswith("2026-10-06T10:05")
    assert body["revisions"][0]["size"] > 1000
    one = client.get(f"{BASE}/КБ1/revisions/1").json()
    assert one["design"]["wires"][0]["lengthMm"] == 300 and one["revision"] == 1
    assert client.get(f"{BASE}/КБ1/revisions/9").status_code == 404
    # One КД row, its bytes the latest revision's.
    [row] = _rows(factory)
    with factory() as uow:
        stored = uow.harness_revisions.get(row.id, 2)[1]
        assert uow.kitsets.document_content(PROJECT, row.id)[1] == stored
    events = [details["revision"] for details in _saved_events(factory)]
    assert events == [1, 2]


def _saved_events(factory):
    with factory() as uow:
        return list(uow.session.scalars(
            select(AuditEventRow.details).where(AuditEventRow.event_type == "techcards.kitset_harness_saved")
            .order_by(AuditEventRow.occurred_at)))


def test_a_save_on_a_stale_revision_is_a_conflict_and_appends_nothing(harness_api):
    client, factory, clock = harness_api
    client.put(f"{BASE}/КБ1", json={"design": _design()})
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"}, name="Мастер")
    clock["now"] = NOW + timedelta(minutes=1)
    assert client.put(f"{BASE}/КБ1", json={"design": _edited(111), "revision": 1}).status_code == 200

    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})
    stale = client.put(f"{BASE}/КБ1", json={"design": _edited(999), "revision": 1})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Чертёж жгута уже сохранил Мастер 06.10.2026 10:01 — обновите страницу и повторите правку"
    assert client.put(f"{BASE}/КБ1", json={"design": _edited(999)}).status_code == 409  # blind
    assert client.put(f"{BASE}/КБ1", json={"design": _edited(999), "revision": 7}).status_code == 409  # from the future
    assert [item[0] for item in _revisions(factory)] == [1, 2]
    assert client.get(f"{BASE}/КБ1").json()["design"]["wires"][0]["lengthMm"] == 111
    stale_unlink = client.post(f"{BASE}/КБ1/unlink", json={"revision": 1})
    assert stale_unlink.status_code == 409 and stale_unlink.json()["detail"].endswith("обновите страницу")


def test_the_old_hash_token_still_saves_for_one_release(harness_api):
    """A browser tab opened before the update holds the hash of the bytes it read and sends it as `version`."""
    client, factory, _clock = harness_api
    client.put(f"{BASE}/КБ1", json={"design": _design()})
    [row] = _rows(factory)
    with factory() as uow:
        token = legacy_version(uow.kitsets.document_content(PROJECT, row.id)[1])
    saved = client.put(f"{BASE}/КБ1", json={"design": _edited(333), "version": token})
    assert saved.status_code == 200, saved.text
    # Its next save names what this release handed out as `version` — the revision as text.
    assert saved.json()["version"] == "2"
    assert client.put(f"{BASE}/КБ1", json={"design": _edited(334), "version": "2"}).status_code == 200
    assert client.put(f"{BASE}/КБ1", json={"design": _edited(335), "version": token}).status_code == 409


def test_a_drawing_that_must_not_be_stored_is_refused_with_every_reason(harness_api):
    client, factory, _clock = harness_api
    dangling = _design()
    dangling["wires"][0]["to"] = fx.at("x2", "x2c99")
    dangling["wires"].append(dict(dangling["wires"][1]))
    response = client.put(f"{BASE}/КБ1", json={"design": dangling})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["errors"] == [
        "Провода: id «w2» повторяется",
        "Провод +5V (w1), конец Б: контакта «x2c99» нет в соединителе «x2»",
    ]
    assert detail["message"] == "Чертёж не сохранён: Провода: id «w2» повторяется (и ещё 1)"
    assert _rows(factory) == [] and _revisions(factory) == []

    client.put(f"{BASE}/КБ1", json={"design": _design()})
    broken = _design()
    broken["junctions"][0]["wireIds"] = ["w2"]
    refused = client.put(f"{BASE}/КБ1", json={"design": broken, "revision": 1})
    assert refused.status_code == 422
    assert "Сросток j1: нужно не меньше 2 проводов, в нём 1" in refused.json()["detail"]["errors"]
    assert [item[0] for item in _revisions(factory)] == [1]
    # The import obeys the same rules.
    imported = client.post(f"{BASE}/КБ1/import", json={"file_name": "x.json", "revision": 1, "content_b64": _b64(
        {"harnesses": [{"designation": "КБ1", "design": {"schemaVersion": 1, "content": broken}}]})})
    assert imported.status_code == 422 and imported.json()["detail"]["errors"]
    assert [item[0] for item in _revisions(factory)] == [1]
    assert client.put(f"{BASE}/КБ1", json={"design": _design(), "revision": 1}).status_code == 200


def test_restore_saves_an_earlier_revision_as_a_new_one(harness_api):
    client, factory, clock = harness_api
    client.put(f"{BASE}/КБ1", json={"design": _design(), "checkpoint": True})  # «Сохранить версию» — its own row
    client.put(f"{BASE}/КБ1", json={"design": _edited(333), "revision": 1})
    clock["now"] = NOW + timedelta(hours=1)
    restored = client.post(f"{BASE}/КБ1/revisions/1/restore", json={"revision": 2})
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["revision"] == 3 and body["design"]["wires"][0]["lengthMm"] == 300
    assert client.get(f"{BASE}/КБ1").json()["design"]["wires"][0]["lengthMm"] == 300
    assert _revisions(factory) == [(1, "saved", "Технолог", None), (2, "saved", "Технолог", None),
                                   (3, "restored", "Технолог", 1)]
    history = client.get(f"{BASE}/КБ1/revisions").json()["revisions"]
    assert history[0] == {**history[0], "revision": 3, "kind": "restored", "restored_from": 1}
    # The history keeps revision 2 unchanged; the bytes of 3 are those of 1.
    with factory() as uow:
        [row] = _rows(factory)
        assert uow.harness_revisions.get(row.id, 3)[1] == uow.harness_revisions.get(row.id, 1)[1]

    stale = client.post(f"{BASE}/КБ1/revisions/2/restore", json={"revision": 2})
    assert stale.status_code == 409
    same = client.post(f"{BASE}/КБ1/revisions/3/restore", json={"revision": 3})
    assert same.status_code == 422 and same.json()["detail"]["message"] == "Версию 3 не восстановить: версия 3 — текущая"
    assert client.post(f"{BASE}/КБ1/revisions/8/restore", json={"revision": 3}).status_code == 404
    with factory() as uow:
        events = list(uow.session.scalars(select(AuditEventRow.details).where(
            AuditEventRow.event_type == "techcards.kitset_harness_revision_restored")))
    assert [(item["revision"], item["restored_from"]) for item in events] == [(3, 1)]

    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.get(f"{BASE}/КБ1/revisions").status_code == 200
    assert client.post(f"{BASE}/КБ1/revisions/2/restore", json={"revision": 3}).status_code == 403
    app.dependency_overrides[authenticated_principal] = _principal({"techcards": "edit"})
    assert client.get(f"{BASE}/КБ1/revisions").status_code == 403


def test_a_drawing_older_than_its_history_gets_its_bytes_as_revision_one_on_the_first_save(harness_api):
    client, factory, _clock = harness_api
    old = _design()
    with factory() as uow:
        uow.kitsets.add_document(KitDocument(
            id=uuid4(), project=PROJECT, item_index="КБ1", file_name="Чертёж", content_type=HARNESS_CONTENT_TYPE,
            size=2, uploaded_at=NOW - timedelta(days=3), uploaded_by="Василий"), _encoded(old))
        uow.commit()
    read = client.get(f"{BASE}/КБ1").json()
    assert read["revision"] == 0
    saved = client.put(f"{BASE}/КБ1", json={"design": _edited(333), "revision": 0})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    assert _revisions(factory) == [(1, "initial", "Василий", None), (2, "saved", "Технолог", None)]
    assert client.get(f"{BASE}/КБ1/revisions/1").json()["design"]["wires"][0]["lengthMm"] == 300


def _encoded(design):
    return json.dumps(design, ensure_ascii=False).encode()


def _b64(value):
    return base64.b64encode(_encoded(value)).decode()


def test_the_database_refuses_a_second_revision_with_the_same_number(harness_api):
    client, factory, _clock = harness_api
    client.put(f"{BASE}/КБ1", json={"design": _design()})
    [row] = _rows(factory)
    with factory() as uow, pytest.raises(HarnessWriteConflict):
        uow.harness_revisions.append(row.id, 1, b"{}", kind="saved", saved_by="Мастер", saved_at=NOW)
    with factory() as uow, pytest.raises(HarnessWriteConflict):
        uow.harness_revisions.append(row.id, 0, b"{}", kind="saved", saved_by="Мастер", saved_at=NOW)
    with factory() as uow, pytest.raises(HarnessWriteConflict):
        uow.harness_revisions.append(row.id, 2, b"{}", kind="saved", saved_by="Мастер", saved_at=NOW, restored_from=1)


def test_saves_hold_with_foreign_keys_enforced_as_in_postgres():
    """SQLite checks FKs only when asked: here it is, so the first save must write the КД row before its revision."""
    from decimal import Decimal
    from uuid import UUID
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.modules.techcards.harness import application
    from app.modules.techcards.harness.schema import Expected
    from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
    from app.modules.techcards.kitsets import KitAssembly, Kitset
    from app.shared.identity import Actor
    from app.shared.persistence import create_schema

    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)  # noqa: E731
    with factory() as uow:
        uow.kitsets.save(Kitset(project=PROJECT, customer="", calc_source="", updated_at=NOW, updated_by="Технолог",
                                created_at=NOW, assemblies=(KitAssembly(index="КБ1", name="", run=Decimal(1)),)))
        uow.commit()
    fx.library(factory)
    actor = Actor(id=UUID(int=1), name="Технолог", department="Производство")
    for expected, number in ((Expected(), 1), (Expected(revision=1), 2)):
        saved = application.save(factory, PROJECT, "КБ1", _design(), expected, actor=actor,
                                 now=NOW + timedelta(minutes=11 * number), references=None)
        assert saved.drawing.revision == number
    restored = application.restore_revision(factory, PROJECT, "КБ1", 1, Expected(revision=2), actor=actor, now=NOW,
                                            references=None)
    assert restored.drawing.revision == 3
    engine.dispose()


@pytest.mark.parametrize("racer", ["Технолог", "Мастер"])
def test_a_save_racing_another_save_on_the_same_revision_is_a_conflict(harness_api, monkeypatch, racer):
    """Both read revision 1; the other one committed revision 2 between this one's check and its write. The same
    person's autosave would fold into the row the other save already moved on; another person's would append
    revision 2 that is taken — either way the database refuses it."""
    client, factory, _clock = harness_api
    client.put(f"{BASE}/КБ1", json={"design": _design()})
    import app.modules.techcards.harness.application as harness_module
    read = harness_module.current
    stale = {}

    def current_before_the_other_save(uow, project, index):
        found = read(uow, project, index)
        stale.setdefault("drawing", found)
        return stale["drawing"]

    with factory() as uow:
        stale["drawing"] = read(uow, PROJECT, "КБ1")
    client.put(f"{BASE}/КБ1", json={"design": _edited(111), "revision": 1})
    monkeypatch.setattr(harness_module, "current", current_before_the_other_save)
    # The КД row's own guard (uploaded_at) let through as well: the revision number is the rule that holds.
    monkeypatch.setattr(SqlAlchemyKitsetRepository, "replace_content", lambda self, *args, **kwargs: 1)
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"}, name=racer)
    raced = client.put(f"{BASE}/КБ1", json={"design": _edited(999), "revision": 1})
    assert raced.status_code == 409, raced.text
    assert raced.json()["detail"] == "Чертёж жгута только что сохранили — обновите страницу и повторите правку"
    assert [item[0] for item in _revisions(factory)] == [2]  # the other save folded into the first row
    assert client.get(f"{BASE}/КБ1/revisions/2").json()["design"]["wires"][0]["lengthMm"] == 111


# ── Thinned history: autosaves of one person within 10 minutes share a row ─────────────────────────────────────


def _rows_of(client):
    return [(item["first_revision"], item["revision"], item["saved_by"], item["kind"], item["closed"])
            for item in client.get(f"{BASE}/КБ1/revisions").json()["revisions"]]


def test_rapid_autosaves_of_one_person_fold_into_one_row_the_revision_still_grows(harness_api):
    client, factory, clock = harness_api
    revision = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()["revision"]
    for step in range(1, 6):
        clock["now"] = NOW + timedelta(seconds=step)
        saved = client.put(f"{BASE}/КБ1", json={"design": _edited(300 + step), "revision": revision})
        assert saved.status_code == 200, saved.text
        revision = saved.json()["revision"]
    assert revision == 6
    assert _rows_of(client) == [(1, 6, "Технолог", "saved", False)]
    history = client.get(f"{BASE}/КБ1/revisions").json()
    assert (history["current"], history["total"]) == (6, 1)
    row = history["revisions"][0]
    assert row["started_at"].startswith("2026-10-06T10:00:00") and row["saved_at"].startswith("2026-10-06T10:00:05")
    assert client.get(f"{BASE}/КБ1/revisions/6").json()["design"]["wires"][0]["lengthMm"] == 305
    assert client.get(f"{BASE}/КБ1/revisions/3").status_code == 404  # folded away
    with factory() as uow:
        [stored] = uow.session.scalars(select(HarnessRevisionRow)).all()
        assert stored.content_sha256 == hashlib.sha256(stored.content).hexdigest() and stored.size == len(stored.content)
    # A stale autosave is still a conflict.
    assert client.put(f"{BASE}/КБ1", json={"design": _edited(1), "revision": 5}).status_code == 409


def test_another_person_ten_minutes_or_a_checkpoint_start_a_new_row(harness_api):
    client, _factory, clock = harness_api
    revision = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()["revision"]

    def save(length, minutes, name="Технолог", **extra):
        nonlocal revision
        clock["now"] = NOW + timedelta(minutes=minutes)
        app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"}, name=name)
        response = client.put(f"{BASE}/КБ1", json={"design": _edited(length), "revision": revision, **extra})
        assert response.status_code == 200, response.text
        revision = response.json()["revision"]

    save(301, 2, name="Мастер")              # another person — a new row
    save(302, 3, name="Мастер")              # folds
    save(303, 13, name="Мастер")             # more than 10 minutes since that row began — a new row
    save(304, 14, name="Мастер", checkpoint=True)  # «Сохранить версию» with the save — its own closed row
    save(305, 15, name="Мастер")             # after a checkpoint — a new row
    save(306, 16, name="Мастер")             # folds
    assert _rows_of(client) == [
        (6, 7, "Мастер", "saved", False), (5, 5, "Мастер", "saved", True), (4, 4, "Мастер", "saved", False),
        (2, 3, "Мастер", "saved", False), (1, 1, "Технолог", "saved", False),
    ]

    # «Сохранить версию» without a save: the current row is fixed, the next autosave starts a new one.
    stale = client.post(f"{BASE}/КБ1/checkpoint", json={"revision": 6})
    assert stale.status_code == 409
    fixed = client.post(f"{BASE}/КБ1/checkpoint", json={"revision": 7})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["row"] == {**fixed.json()["row"], "revision": 7, "closed": True}
    assert client.post(f"{BASE}/КБ1/checkpoint", json={"revision": 7}).json()["row"]["closed"] is True  # idempotent
    save(307, 17, name="Мастер")
    assert _rows_of(client)[:2] == [(8, 8, "Мастер", "saved", False), (6, 7, "Мастер", "saved", True)]
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.post(f"{BASE}/КБ1/checkpoint", json={"revision": 8}).status_code == 403


def test_imported_restored_and_initial_rows_never_take_autosaves(harness_api):
    client, _factory, clock = harness_api
    client.put(f"{BASE}/КБ1", json={"design": _design(), "checkpoint": True})
    imported = client.post(f"{BASE}/КБ1/import", json={"file_name": "x.json", "revision": 1, "content_b64": _b64(
        {"harnesses": [{"designation": "КБ1", "design": {"schemaVersion": 1, "content": _edited(310)}}]})})
    assert imported.json()["revision"] == 2
    clock["now"] = NOW + timedelta(seconds=5)
    assert client.put(f"{BASE}/КБ1", json={"design": _edited(311), "revision": 2}).json()["revision"] == 3
    restored = client.post(f"{BASE}/КБ1/revisions/1/restore", json={"revision": 3}).json()
    assert restored["revision"] == 4
    clock["now"] = NOW + timedelta(seconds=9)
    assert client.put(f"{BASE}/КБ1", json={"design": _edited(312), "revision": 4}).json()["revision"] == 5
    assert [(row[0], row[1], row[3]) for row in _rows_of(client)] == [
        (5, 5, "saved"), (4, 4, "restored"), (3, 3, "saved"), (2, 2, "imported"), (1, 1, "saved")]


# ── References to the ERP books ────────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def books(harness_api):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.shared.persistence import create_schema

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    load_mirror(BUNDLED_MIRROR, app.state.references_uow_factory(), dry_run=False, now=datetime(2026, 10, 6, 12, 0))
    yield harness_api
    del app.state.references_uow_factory
    engine.dispose()


def _in_books(design):
    """The fixture drawing with references the bundled books have."""
    for wire in design["wires"]:
        wire["materialBinding"]["sourceKey"] = "UL1061 28AWG"
        wire.pop("stripProfiles", None)
    design["cables"][0]["materialBinding"]["sourceKey"] = "ПВВС 4×0,35"
    for connector in design["connectors"]:
        for contact in connector["contacts"]:
            contact["terminalArticle"] = ""
    design["physicalTopology"]["coverings"][0].pop("material")
    return design


def test_a_save_bringing_in_a_reference_the_books_lack_is_refused_one_already_stored_is_not(books):
    client, factory, _clock = books
    assert client.put(f"{BASE}/КБ1", json={"design": _in_books(_design())}).status_code == 200

    unknown = _in_books(_design())
    unknown["wires"][0]["materialBinding"]["sourceKey"] = "ПРОВОД-ПРИЗРАК"
    refused = client.put(f"{BASE}/КБ1", json={"design": unknown, "revision": 1})
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"] == ["Провод +5V (w1): провода «ПРОВОД-ПРИЗРАК» нет в БД.ПРОВ"]

    # An import does not ask our books about the author's catalog: it lands with a warning.
    imported = client.post(f"{BASE}/КБ1/import", json={
        "file_name": "x.json", "revision": 1,
        "content_b64": _b64({"harnesses": [{"designation": "КБ1", "design": {"schemaVersion": 1, "content": unknown}}]}),
    })
    assert imported.status_code == 200, imported.text
    assert "Провод +5V (w1): провода «ПРОВОД-ПРИЗРАК» нет в БД.ПРОВ" in imported.json()["warnings"]
    # Once stored, it does not lock the drawing: later saves only bring in what they change.
    unknown["wires"][1]["lengthMm"] = 151
    assert client.put(f"{BASE}/КБ1", json={"design": unknown, "revision": 2}).status_code == 200
    assert [item[1] for item in _revisions(factory)] == ["saved", "imported", "saved"]


def test_a_save_without_the_books_connected_warns_instead_of_guessing(harness_api):
    client, _factory, _clock = harness_api
    saved = client.put(f"{BASE}/КБ1", json={"design": _design()}).json()
    assert "Справочники ERP не подключены — новые ссылки на провода и терминалы не проверены" in saved["warnings"]


def test_revision_hash_is_the_full_sha256_of_the_bytes(harness_api):
    client, factory, _clock = harness_api
    client.put(f"{BASE}/КБ1", json={"design": _design()})
    [row] = _rows(factory)
    with factory() as uow:
        stored = uow.session.scalar(select(HarnessRevisionRow))
        assert stored.content_sha256 == hashlib.sha256(stored.content).hexdigest() and stored.size == len(stored.content)
