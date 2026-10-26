"""«Чертёж → схема сборки» (owner 05.10.2026): an agent with an `assembly` API token uploads a drawing into the КД of a
project position, checks a «Ручная сборка» draft with the real builder, stores it; the master builds the card naming
the draft, which finalises it; the pair plus the lesson is read back as the corpus. Drafts never touch a card."""

import base64
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.domain import IdentityUser, hash_password
from app.modules.identity.infrastructure import SqlAlchemyIdentityUnitOfWork, provision_user, seed_level_matrix
from app.modules.techcards.assembly_drafts_infrastructure import AssemblyDraftRow
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.kitset_infrastructure import KitDocumentRow
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.shared.clock import MOSCOW, moscow_now
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.techcards.library import load_library
from tests.techcards.test_operation_card_api import _template


NOW = datetime(2026, 10, 5, 12, 0, tzinfo=MOSCOW)
ADMIN_ID = UUID("50000000-0000-0000-0000-000000000001")
PROJECT, INDEX = "708К.4", "КБ1"
STATE = ("identity_uow_factory", "techcards_uow_factory", "cookie_secure", "identity_clock")
PDF = b"%PDF-1.4\n% drawing\n"
CONFIG = {
    "mode": "manual", "projectNo": PROJECT, "assemblyIndex": INDEX, "assemblyName": "Жгут питания", "partQty": 10,
    "ops": [
        {"type": "libraryOp", "templateId": "cut-wire", "opNum": "CUT_WIRE", "label": "Резка", "qty": 2,
         "tPrep": "120", "tOp": "6"},
        {"type": "customOp", "label": "Пайка экрана", "tPrep": "300", "tOp": "45",
         "steps": [{"kind": "Операция", "text": "Облудить экран"}, {"kind": "Контроль", "text": "Без наплывов"}]},
    ],
}


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def drafts_api():
    # The real gates and token middleware decide here, not the package's engineer override (conftest.py).
    app.dependency_overrides.pop(authenticated_principal, None)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        provision_user(session, IdentityUser(
            id=ADMIN_ID, login="admin", email="admin@example.test", name="Admin", department="ИТ", role="admin",
            site=None, active=True, access_level="admin",
        ), hash_password("admin-password"))
        seed_level_matrix(session)
        session.commit()
    clock = Clock()
    app.state.identity_uow_factory = lambda: SqlAlchemyIdentityUnitOfWork(sessions)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.cookie_secure = False
    app.state.identity_clock = clock
    app.dependency_overrides[moscow_now] = clock
    with app.state.techcards_uow_factory() as uow:
        uow.kitsets.save(Kitset(
            project=PROJECT, customer="Заказчик", calc_source="",
            assemblies=(KitAssembly(INDEX, "Жгут питания", Decimal(10)), KitAssembly("КБ2", "Кабель", Decimal(5))),
            updated_at=NOW, updated_by="Admin", created_at=NOW,
        ))
        uow.commit()
    admin = TestClient(app)
    assert admin.post("/api/auth/login", json={"login": "admin", "password": "admin-password"}).status_code == 200
    load_library(app.state.techcards_uow_factory, [_template("cut-wire", "CUT_WIRE")])
    yield sessions, clock, admin
    app.dependency_overrides.pop(moscow_now, None)
    for name in STATE:
        if hasattr(app.state, name):
            delattr(app.state, name)
    engine.dispose()


def _csrf(client: TestClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("erp_csrf")}


def _token(admin: TestClient, *scopes: str) -> dict:
    response = admin.post(
        "/api/auth/api-tokens", headers=_csrf(admin), json={"name": "Claude Code", "scopes": list(scopes), "days": 30},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode()


def _draft(client: TestClient, headers: dict | None = None, *, dry_run: bool = False, **changes):
    body = {"project": PROJECT, "item_index": INDEX, "source": "claude-code", "build_config": deepcopy(CONFIG),
            "note": "Чертёж: провод 2 шт., экран паять"}
    body.update(changes)
    return client.post(
        "/api/techcards/assembly/drafts", params={"dry_run": str(dry_run).lower()}, json=body, headers=headers or {},
    )


def _card(admin: TestClient, index: str = INDEX) -> dict:
    response = admin.post("/api/techcards/projects", headers=_csrf(admin), json={
        "project": PROJECT, "item_index": index, "product_name": "Жгут питания",
    })
    assert response.status_code == 201, response.text
    return response.json()


def _count(sessions, row) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(row))


def test_full_flow_drawing_dry_run_draft_build_lesson_corpus(drafts_api):
    sessions, clock, admin = drafts_api
    token = _token(admin, "assembly")
    agent = TestClient(app)  # no cookies: the token alone

    drawing = agent.post("/api/techcards/assembly/drawings", headers=token, json={
        "project": "708к.4", "item_index": "кб1", "file_name": "Чертёж жгута.pdf", "content_base64": _b64(PDF),
    })
    assert drawing.status_code == 201, drawing.text
    stored = drawing.json()
    assert (stored["project"], stored["item_index"], stored["content_type"]) == (PROJECT, INDEX, "application/pdf")
    assert stored["name"] == "708К.4КБ1 — Чертёж жгута.pdf" and stored["size"] == len(PDF)
    assert stored["url"].endswith(f"/documents/{stored['id']}")
    assert admin.get(stored["url"]).content == PDF  # the КД of the position, as «Проекты» shows it

    checked = _draft(agent, token, dry_run=True, drawing_document_id=stored["id"])
    assert checked.status_code == 200, checked.text
    assert checked.json()["dry_run"] is True and checked.json()["draft"] is None
    assert checked.json()["operations"] == 2 and checked.json()["route"]["blocks"]
    assert _count(sessions, AssemblyDraftRow) == 0

    created = _draft(agent, token, drawing_document_id=stored["id"])
    assert created.status_code == 201, created.text
    draft = created.json()["draft"]
    assert (draft["status"], draft["source"], draft["author"]) == ("draft", "claude-code", "API: Claude Code")
    assert draft["draft_config"] == CONFIG and draft["final_config"] is None
    assert draft["drawing_document_id"] == stored["id"] and draft["note"] == "Чертёж: провод 2 шт., экран паять"

    listed = agent.get("/api/techcards/assembly/drafts", headers=token, params={"project": PROJECT, "item_index": "кб1"})
    assert [item["id"] for item in listed.json()["items"]] == [draft["id"]]
    assert "draft_config" not in listed.json()["items"][0]
    assert agent.get("/api/techcards/assembly/drafts", headers=token, params={"item_index": "КБ2"}).json()["items"] == []

    # The master corrects the draft in «Ручная сборка» and builds the card naming it.
    card = _card(admin)
    final = deepcopy(CONFIG)
    final["ops"][1]["tOp"] = "60"
    clock.now = NOW + timedelta(hours=1)
    built = admin.post(f"/api/techcards/projects/{card['id']}/build", headers=_csrf(admin), json={
        "version": card["version"], "config": final, "draft_id": draft["id"],
    })
    assert built.status_code == 200, built.text
    assert built.json()["document"]["build_config"]["ops"][1]["tOp"] == "60"

    detail = agent.get(f"/api/techcards/assembly/drafts/{draft['id']}", headers=token).json()
    assert (detail["status"], detail["final_revision"]) == ("final", built.json()["revision"])
    assert detail["draft_config"] == CONFIG and detail["final_config"] == final

    lesson = agent.put(f"/api/techcards/assembly/drafts/{draft['id']}/lesson", headers=token, json={
        "lesson": "Пайка экрана — 60 с: экран плотный. Правило: экран плотнее 0,35 — норма 60 с.",
    })
    assert lesson.status_code == 200, lesson.text
    assert lesson.json()["lesson"].startswith("Пайка экрана")

    # A second draft stays a draft: the corpus has finalised pairs only, newest first.
    assert _draft(agent, token).status_code == 201
    examples = agent.get("/api/techcards/assembly/corpus", headers=token, params={"project": PROJECT}).json()["items"]
    assert [item["id"] for item in examples] == [draft["id"]]
    [example] = examples
    assert (example["draft_config"], example["final_config"], example["lesson"]) == (CONFIG, final, lesson.json()["lesson"])
    assert agent.get("/api/techcards/assembly/corpus", headers=token, params={"q": "ЭКРАН плотн"}).json()["items"]
    assert agent.get("/api/techcards/assembly/corpus", headers=token, params={"q": "коаксиал"}).json()["items"] == []
    assert agent.get("/api/techcards/assembly/corpus", headers=token, params={"project": "999К.1"}).json()["items"] == []

    with sessions() as session:
        events = [row.event_type for row in session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type.like("techcards.assembly_draft_%"))
            .order_by(AuditEventRow.occurred_at)
        )]
    assert events.count("techcards.assembly_draft_created") == 2
    assert "techcards.assembly_draft_finalised" in events and "techcards.assembly_draft_lesson" in events


def test_a_draft_for_an_issued_card_leaves_its_revision_and_document_untouched(drafts_api):
    _sessions, _clock, admin = drafts_api
    card = _card(admin)
    built = admin.post(f"/api/techcards/projects/{card['id']}/build", headers=_csrf(admin), json={
        "version": card["version"], "config": CONFIG,
    }).json()
    issued = admin.post(f"/api/techcards/projects/{card['id']}/issue", headers=_csrf(admin), json={
        "version": built["version"],
    })
    assert issued.status_code == 200, issued.text
    before = admin.get(f"/api/techcards/projects/{card['id']}").json()
    assert before["status"] == "Выдана" and before["revision"] == 1
    revisions = admin.get(f"/api/techcards/projects/{card['id']}/revisions").json()["items"]

    token = _token(admin, "assembly")
    changed = deepcopy(CONFIG)
    changed["ops"][0]["tOp"] = "9"
    assert _draft(TestClient(app), token, build_config=changed).status_code == 201
    assert _draft(TestClient(app), token, dry_run=True, build_config=changed).status_code == 200

    after = admin.get(f"/api/techcards/projects/{card['id']}").json()
    assert (after["version"], after["revision"], after["status"], after["document"]) == (
        before["version"], before["revision"], before["status"], before["document"],
    )
    assert admin.get(f"/api/techcards/projects/{card['id']}/revisions").json()["items"] == revisions


@pytest.mark.parametrize(("config", "message"), [
    ({"mode": "manual", "ops": []}, "Ручная сборка: добавьте хотя бы одну операцию"),
    ({"mode": "manual", "ops": [{"type": "weld", "label": "Сварка"}]}, "непонятный вид операции"),
    ({"mode": "manual", "ops": [{"type": "customOp", "label": ""}]}, "укажите название своей операции"),
    ({"mode": "manual", "ops": [{"type": "customOp", "label": "Пайка", "qty": "много"}]}, "должно быть числом"),
    ({**CONFIG, "route": {"blocks": [{"key": "a", "name": "А", "ops": [1]}]}}, "операция 2 не входит ни в один блок"),
    ({"ops": CONFIG["ops"]}, "mode = «manual»"),
])
def test_an_invalid_config_is_a_russian_422_and_nothing_is_stored(drafts_api, config, message):
    sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    for dry_run in (True, False):
        response = _draft(TestClient(app), token, dry_run=dry_run, build_config=config)
        assert response.status_code == 422, response.text
        assert message in response.json()["detail"]
    assert _count(sessions, AssemblyDraftRow) == 0


def test_dry_run_stores_nothing_and_returns_the_builders_warnings(drafts_api):
    sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    audit_before = _count(sessions, AuditEventRow)
    config = deepcopy(CONFIG)
    config["ops"].append({"type": "libraryOp", "templateId": "no-such-card", "opNum": "X_1", "label": "Нет карточки"})
    response = _draft(TestClient(app), token, dry_run=True, build_config=config)
    assert response.status_code == 200, response.text
    assert any("нет карточки" in warning for warning in response.json()["warnings"])
    assert _count(sessions, AssemblyDraftRow) == 0 and _count(sessions, AuditEventRow) == audit_before


def test_dry_run_returns_the_built_operations_with_their_input_and_output(drafts_api):
    _sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    config = deepcopy(CONFIG)
    config["ops"][0]["components"] = [{"art": "МГТФ 0,35", "name": "Провод МГТФ", "qty": "0,6", "unit": "М."}]
    for dry_run in (True, False):
        body = _draft(TestClient(app), token, dry_run=dry_run, build_config=config).json()
        assert body["operations"] == 2  # the count stays as it was
        cut, solder = body["operation_details"]
        assert (cut["position"], cut["code"], cut["name"]) == (1, "CUT_WIRE", "Резка")
        assert cut["components"] == [{"art": "МГТФ 0,35", "name": "Провод МГТФ", "norm": "6", "unit": "М."}]
        assert cut["output"] == [{"name": "Полуфабрикат после «Резка»", "norm": "10", "unit": "Шт."}]
        assert cut["input"] == []
        assert solder["input"] == [{"name": "Полуфабрикат после «Резка»", "norm": "10", "unit": "Шт."}]
        assert solder["output"][0]["name"] == "Жгут питания" and solder["output"][0]["norm"] == "10"


def test_draft_needs_a_known_position_a_drawing_of_it_and_a_known_source(drafts_api):
    _sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    agent = TestClient(app)
    assert "не найдена" in _draft(agent, token, project="999К.1").json()["detail"]
    assert _draft(agent, token, item_index="КБ9").json()["detail"] == "В проекте «708К.4» нет сборки КБ9"
    assert "claude-code" in _draft(agent, token, source="robot").json()["detail"]
    other = agent.post("/api/techcards/assembly/drawings", headers=token, json={
        "project": PROJECT, "item_index": "КБ2", "file_name": "кабель.png",
        "content_base64": _b64(b"\x89PNG\r\n\x1a\n" + b"0" * 16),
    })
    assert other.status_code == 201, other.text
    wrong = _draft(agent, token, drawing_document_id=other.json()["id"])
    assert wrong.status_code == 422 and "Чертёж не найден в КД сборки КБ1" in wrong.json()["detail"]


def test_assembly_token_passes_tcgen_token_is_403_no_token_is_401(drafts_api):
    _sessions, _clock, admin = drafts_api
    assembly, tcgen = _token(admin, "assembly"), _token(admin, "tcgen")
    service = TestClient(app)
    calls = [
        ("GET", "/api/techcards/assembly/drafts", None),
        ("GET", "/api/techcards/assembly/corpus", None),
        ("POST", "/api/techcards/assembly/drafts?dry_run=true", {
            "project": PROJECT, "item_index": INDEX, "source": "claude-code", "build_config": CONFIG,
        }),
    ]
    for method, url, body in calls:
        assert service.request(method, url, json=body, headers=assembly).status_code == 200, url
        assert service.request(method, url, json=body, headers=tcgen).status_code == 403, url
        assert service.request(method, url, json=body).status_code == 401, url
    # The generator → «Ручная сборка» conversion stores nothing: both the generator's and the drafting agent's token.
    generator = {"config": {"assemblyName": "Жгут", "partQty": 1, "ops": [
        {"type": "heatTut", "templateId": "cut-wire", "opNum": "CUT_WIRE", "tutCount": 1},
    ]}}
    for headers in (assembly, tcgen):
        converted = service.post("/api/techcards/assembly/from-generator", json=generator, headers=headers)
        assert converted.status_code == 200, converted.text
        assert converted.json()["mode"] == "manual"
    assert service.post("/api/techcards/assembly/from-generator", json=generator).status_code == 401
    # A cookie session with «Генератор техкарт» (the administrator passes every section).
    assert admin.post(
        "/api/techcards/assembly/drafts?dry_run=true", headers=_csrf(admin),
        json={"project": PROJECT, "item_index": INDEX, "source": "manual", "build_config": CONFIG},
    ).status_code == 200


def test_the_assembly_token_never_builds_a_card_even_with_a_draft(drafts_api):
    _sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    card = _card(admin)
    draft = _draft(TestClient(app), token).json()["draft"]
    response = TestClient(app).post(f"/api/techcards/projects/{card['id']}/build", headers=token, json={
        "version": card["version"], "config": CONFIG, "draft_id": draft["id"],
    })
    assert response.status_code == 403
    assert admin.get(f"/api/techcards/assembly/drafts/{draft['id']}").json()["status"] == "draft"


def test_build_with_a_draft_of_another_position_is_refused_before_the_build(drafts_api):
    _sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    draft = _draft(TestClient(app), token).json()["draft"]
    other = _card(admin, "КБ2")
    response = admin.post(f"/api/techcards/projects/{other['id']}/build", headers=_csrf(admin), json={
        "version": other["version"], "config": CONFIG, "draft_id": draft["id"],
    })
    assert response.status_code == 422 and "Черновик относится к 708К.4 КБ1" in response.json()["detail"]
    assert admin.get(f"/api/techcards/projects/{other['id']}").json()["document"] is None
    missing = admin.post(f"/api/techcards/projects/{other['id']}/build", headers=_csrf(admin), json={
        "version": other["version"], "config": CONFIG, "draft_id": str(ADMIN_ID),
    })
    assert missing.status_code == 404 and missing.json()["detail"] == "Черновик схемы сборки не найден"


@pytest.mark.parametrize(("file_name", "content", "message"), [
    ("setup.exe", b"MZ\x90\x00", "только в PDF, JPG или PNG"),
    ("чертёж", PDF, "только в PDF, JPG или PNG"),
    ("чертёж.pdf", b"MZ\x90\x00", "не похож на PDF"),
    ("чертёж.jpg", PDF, "не похож на JPG"),
    ("big.pdf", PDF + b"0" * (20 * 1024 * 1024), "Чертёж больше 20 МБ"),
], ids=["exe", "no-extension", "exe-as-pdf", "pdf-as-jpg", "over-20mb"])
def test_drawing_upload_refuses_other_files_and_over_20_mb(drafts_api, file_name, content, message):
    sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    response = TestClient(app).post("/api/techcards/assembly/drawings", headers=token, json={
        "project": PROJECT, "item_index": INDEX, "file_name": file_name, "content_base64": _b64(content),
    })
    assert response.status_code == 422, response.text
    assert message in response.json()["detail"]
    assert _count(sessions, KitDocumentRow) == 0


def test_drawing_for_an_unknown_position_is_refused(drafts_api):
    _sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    agent = TestClient(app)
    body = {"project": PROJECT, "item_index": "КБ9", "file_name": "a.jpeg", "content_base64": _b64(b"\xff\xd8\xff\xe0")}
    assert agent.post("/api/techcards/assembly/drawings", headers=token, json=body).status_code == 422
    body["project"] = "999К.1"
    assert agent.post("/api/techcards/assembly/drawings", headers=token, json=body).status_code == 404


# --- несколько PDF чертежа → один файл КД (владелец 10.10.2026) ---

def _pdf(*widths: int, encrypt: bool = False) -> bytes:
    """Настоящий PDF: по странице на ширину — по ней видно порядок после сшивки."""
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    for width in widths:
        writer.add_blank_page(width=width, height=100)
    if encrypt:
        writer.encrypt("secret")
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _merge(client, headers, files, **changes):
    body = {"project": PROJECT, "item_index": INDEX,
            "files": [{"file_name": name, "content_base64": _b64(content)} for name, content in files]}
    body.update(changes)
    return client.post("/api/techcards/assembly/drawings/merge", headers=headers, json=body)


def _widths(content: bytes) -> list[int]:
    from io import BytesIO

    from pypdf import PdfReader

    return [int(page.mediabox.width) for page in PdfReader(BytesIO(content)).pages]


def _document_events(sessions):
    with sessions() as session:
        return list(session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "techcards.kitset_document_added")))


def test_merge_stitches_in_the_given_order_counts_pages_and_names_the_file(drafts_api):
    sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    first = "ТСЦМ.685621.047_V00-1 (1).pdf"
    files = [(first, _pdf(101, 102)), ("лист 2.pdf", _pdf(201)), ("лист 3.pdf", _pdf(301, 302, 303))]
    response = _merge(TestClient(app), token, files)
    assert response.status_code == 201, response.text
    stored = response.json()
    assert stored["name"] == "708К.4КБ1 — ТСЦМ.685621.047_V00-1 (1)+2.pdf" and stored["content_type"] == "application/pdf"
    assert stored["pages"] == [{"file_name": first, "pages": 2}, {"file_name": "лист 2.pdf", "pages": 1},
                               {"file_name": "лист 3.pdf", "pages": 3}]
    assert _widths(admin.get(stored["url"]).content) == [101, 102, 201, 301, 302, 303]
    assert _count(sessions, KitDocumentRow) == 1
    (event,) = _document_events(sessions)
    assert event.details["pages"] == "6" and "лист 2.pdf — 1 стр." in event.details["parts"]
    # Порядок задаёт пользователь: меняем местами и называем файл сами.
    reordered = _merge(TestClient(app), token, list(reversed(files)), file_name="Общий чертёж")
    assert reordered.json()["name"] == "708К.4КБ1 — Общий чертёж.pdf"
    assert _widths(admin.get(reordered.json()["url"]).content) == [301, 302, 303, 201, 101, 102]


@pytest.mark.parametrize(("bad", "message"), [
    (("сломан.pdf", b"%PDF-1.4\nnot a pdf at all"), "Файл «сломан.pdf» повреждён"),
    (("защита.pdf", _pdf(100, encrypt=True)), "Файл «защита.pdf» защищён паролем"),
    (("картинка.png", b"\x89PNG\r\n\x1a\n0000"), "Сшиваются только PDF: «картинка.png»"),
    (("не-пдф.pdf", b"MZ\x90\x00"), "Файл «не-пдф.pdf» не похож на PDF"),
], ids=["broken", "encrypted", "not-pdf-extension", "bad-signature"])
def test_merge_refuses_a_bad_part_by_name_and_stores_nothing(drafts_api, bad, message):
    sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    audit_before = _count(sessions, AuditEventRow)
    response = _merge(TestClient(app), token, [("хороший.pdf", _pdf(100)), bad, ("ещё.pdf", _pdf(120))])
    assert response.status_code == 422, response.text
    assert message in response.json()["detail"]
    assert _count(sessions, KitDocumentRow) == 0 and _count(sessions, AuditEventRow) == audit_before


def test_merge_to_an_unknown_position_stores_nothing_and_needs_two_files(drafts_api):
    sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    files = [("а.pdf", _pdf(100)), ("б.pdf", _pdf(110))]
    assert _merge(TestClient(app), token, files, item_index="КБ9").status_code == 422
    assert _merge(TestClient(app), token, files[:1]).status_code == 422
    assert _count(sessions, KitDocumentRow) == 0


def test_single_file_upload_is_unchanged(drafts_api):
    sessions, _clock, admin = drafts_api
    token = _token(admin, "assembly")
    pdf = _pdf(100, 110)
    response = TestClient(app).post("/api/techcards/assembly/drawings", headers=token, json={
        "project": PROJECT, "item_index": INDEX, "file_name": "один.pdf", "content_base64": _b64(pdf),
    })
    assert response.status_code == 201 and response.json()["name"] == "708К.4КБ1 — один.pdf"
    assert admin.get(response.json()["url"]).content == pdf
    (event,) = _document_events(sessions)
    assert "parts" not in event.details
