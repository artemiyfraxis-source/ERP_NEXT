"""Документы с ознакомлением («Личная карточка», фаза 3): «Кадры» загружают PDF для всех, отдела или должности,
сотрудник подписывает «Ознакомлен» PIN или паролем, новая редакция требует новой подписи, архив не спрашивают,
лист ознакомления, уведомления, проверка PDF, миграция 0100."""

import base64
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.documents import (
    MAX_PDF_BYTES,
    DocumentConflict,
    DocumentError,
    DocumentForbidden,
    acknowledge,
    check_pdf,
    create_document,
    delete_version,
    documents_overview,
    pending_count,
    set_archived,
    signature_sheet,
    upload_version,
    version_file,
)
from app.modules.identity.domain import AccessMode, IdentityUser, hash_password, hash_pin
from app.modules.identity.infrastructure import (
    IdentityDocumentAckRow,
    IdentityDocumentRow,
    IdentityDocumentVersionRow,
    IdentityLoginThrottleRow,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
    seed_level_matrix,
)
from app.modules.identity.leave import SignatureRefused
from app.shared.clock import MOSCOW, moscow_now
from app.shared.identity import Actor
from app.shared.persistence import OutboxMessageRow, create_schema

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=MOSCOW)
PASSWORD = "documents-password-1"
PIN = "573902"
CLIENT = "client:test"

HR = UUID("71000000-0000-0000-0000-000000000001")
FITTER = UUID("71000000-0000-0000-0000-000000000002")
FITTER_2 = UUID("71000000-0000-0000-0000-000000000003")
ENGINEER = UUID("71000000-0000-0000-0000-000000000004")
GONE = UUID("71000000-0000-0000-0000-000000000005")  # уволен (не активен)
NEWCOMER = UUID("71000000-0000-0000-0000-000000000006")


def pdf(text: str = "Должностная инструкция") -> bytes:
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + text.encode("utf-8") + b"\n%%EOF\n"


def _user(user_id, login, name, department, job_title, *, active=True):
    return IdentityUser(
        id=user_id, login=login, email=f"{login}@example.test", name=name, department=department, role="staff",
        site="ЦАР", active=active, access_level="operator", job_title=job_title,
    )


@pytest.fixture()
def erp():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    password, pin = hash_password(PASSWORD), hash_pin(PIN)
    people = (
        (_user(HR, "hr", "Кадровик Ольга", "Администрация", "Специалист по кадрам"), {"hr": AccessMode.EDIT}),
        (_user(FITTER, "fitter", "Монтажник Анна", "Цех", "Монтажник"), {}),
        (_user(FITTER_2, "fitter2", "Монтажник Борис", "Цех", "  монтажник "), {}),
        (_user(ENGINEER, "engineer", "Инженер Вера", "ОГТ", "Инженер-технолог"), {}),
        (_user(GONE, "gone", "Уволенный Глеб", "Цех", "Монтажник", active=False), {}),
    )
    with sessions() as session:
        for user, personal in people:
            provision_user(session, user, password, pin_hash=pin, level_access=personal)
        seed_level_matrix(session)
        session.commit()
    yield SimpleNamespace(
        sessions=sessions, identity=lambda: SqlAlchemyIdentityUnitOfWork(sessions), password=password, pin=pin,
    )
    engine.dispose()


def actor(user_id: UUID) -> Actor:
    return Actor(id=user_id, name=str(user_id), department="Цех")


def upload(erp, title="Инструкция монтажника", kind="job_description", scope="job_title", value="Монтажник",
           content=None, who=HR):
    return create_document(actor(who), title, kind, scope, value, "инструкция.pdf", content or pdf(), NOW, erp.identity())


def ack(erp, version_id, who=FITTER, secret=PIN):
    return acknowledge(version_id, actor(who), secret, NOW, erp.identity(), client_key=CLIENT)


def notifications(erp) -> list[dict]:
    with erp.sessions() as session:
        rows = session.scalars(select(OutboxMessageRow).where(OutboxMessageRow.topic == "notification.requested"))
        return [dict(row.payload) for row in rows]


def own_ids(erp, who) -> list[str]:
    return [item["id"] for item in documents_overview(who, erp.identity()).own]


# ── Кому адресован ───────────────────────────────────────────────────────


def test_scope_is_computed_live_all_department_job_title(erp):
    everyone = upload(erp, title="Положение о премировании", kind="regulation", scope="all", value="ignored")
    shop = upload(erp, title="Правила цеха", kind="other", scope="department", value=" Цех ")
    fitters = upload(erp)
    assert (everyone.scope_value, shop.scope_value, fitters.scope_value) == ("", "Цех", "Монтажник")

    # Должность — без учёта регистра и пробелов по краям; уволенного не спрашивают.
    assert set(own_ids(erp, FITTER)) == set(own_ids(erp, FITTER_2)) == {str(everyone.id), str(shop.id), str(fitters.id)}
    assert own_ids(erp, ENGINEER) == [str(everyone.id)]
    assert own_ids(erp, GONE) == []
    assert pending_count(FITTER, erp.identity()) == 3

    # Новый сотрудник видит документы, которые уже действуют.
    with erp.sessions() as session:
        provision_user(session, _user(NEWCOMER, "newcomer", "Новичок Дмитрий", "Цех", "МОНТАЖНИК"), erp.password,
                       pin_hash=erp.pin)
        session.commit()
    assert pending_count(NEWCOMER, erp.identity()) == 3


def test_a_scope_nobody_is_in_is_refused_as_a_typo(erp):
    with pytest.raises(DocumentError, match="Нет действующих сотрудников"):
        upload(erp, scope="job_title", value="Монтажнег")
    with pytest.raises(DocumentError, match="Укажите отдел"):
        upload(erp, scope="department", value="  ")
    with pytest.raises(DocumentError, match="Кому адресован"):
        upload(erp, scope="site", value="ЦАР")
    with pytest.raises(DocumentError, match="Вид документа"):
        upload(erp, kind="order")


def test_new_version_notifies_everyone_in_scope_but_the_uploader(erp):
    document = upload(erp, scope="all", value="")
    [first] = notifications(erp)
    assert first["type"] == "document" and first["title"] == "Новый документ для ознакомления"
    assert set(first["recipient_user_ids"].split(",")) == {str(FITTER), str(FITTER_2), str(ENGINEER)}
    assert "Должностная инструкция «Инструкция монтажника»" in first["body"]

    upload_version(document.id, actor(HR), "v2.pdf", pdf("редакция 2"), NOW, erp.identity())
    second = notifications(erp)[-1]
    assert second["title"] == "Новая редакция документа" and "редакция 2" in second["body"]
    assert len(notifications(erp)) == 2


# ── Подпись «Ознакомлен» ─────────────────────────────────────────────────


def test_sign_once_per_version_and_a_new_version_needs_a_fresh_signature(erp):
    document = upload(erp)
    v1 = document.current
    signed = ack(erp, v1.id)
    assert (signed.method, signed.sha256, signed.user_id) == ("pin", v1.sha256, FITTER)
    assert pending_count(FITTER, erp.identity()) == 0
    assert ack(erp, v1.id, who=FITTER_2, secret=PASSWORD).method == "password"

    with pytest.raises(DocumentConflict, match="уже ознакомились"):
        ack(erp, v1.id)

    v2 = upload_version(document.id, actor(HR), "v2.pdf", pdf("редакция 2"), NOW, erp.identity()).current
    assert v2.version_no == 2 and v2.sha256 != v1.sha256
    # Старая подпись больше не считается.
    assert pending_count(FITTER, erp.identity()) == 1
    [mine] = documents_overview(FITTER, erp.identity()).own
    assert mine["signed"] is None and mine["signed_earlier_version"] == 1 and mine["current"]["version_no"] == 2
    with pytest.raises(DocumentConflict, match="новая редакция"):
        ack(erp, v1.id)

    sheet = signature_sheet(document.id, HR, erp.identity())
    assert (sheet["version"]["version_no"], sheet["readers"], sheet["signed"]) == (2, 2, 0)
    old = signature_sheet(document.id, HR, erp.identity(), version_no=1)
    assert old["signed"] == 2 and old["is_current"] is False
    assert {row["name"]: row["method_label"] for row in old["rows"]} == {"Монтажник Анна": "PIN", "Монтажник Борис": "пароль"}

    ack(erp, v2.id)
    sheet = signature_sheet(document.id, HR, erp.identity())
    assert [(row["name"], row["signed_at"] is not None) for row in sheet["rows"]] == [
        ("Монтажник Борис", False), ("Монтажник Анна", True),  # не подписавшие — сверху
    ]


def test_the_same_file_again_is_not_a_new_version(erp):
    document = upload(erp)
    with pytest.raises(DocumentConflict, match="уже загружен"):
        upload_version(document.id, actor(HR), "again.pdf", pdf(), NOW, erp.identity())


def test_a_wrong_secret_is_refused_and_counted_like_a_failed_login(erp):
    document = upload(erp)
    with pytest.raises(SignatureRefused, match="Неверный PIN или пароль"):
        ack(erp, document.current.id, secret="000000")
    with erp.identity() as uow:
        assert uow.identity.failure_count("login:fitter", NOW, timedelta(minutes=10)) == 1
    with erp.sessions() as session:
        assert session.scalar(select(func.count()).select_from(IdentityDocumentAckRow)) == 0
        assert session.get(IdentityLoginThrottleRow, "login:fitter").failures == 1
    assert pending_count(FITTER, erp.identity()) == 1


def test_only_the_people_in_scope_sign(erp):
    document = upload(erp)
    with pytest.raises(DocumentForbidden, match="адресован не вам"):
        ack(erp, document.current.id, who=ENGINEER)
    with pytest.raises(DocumentForbidden, match="адресован не вам"):
        version_file(document.current.id, ENGINEER, erp.identity())
    assert version_file(document.current.id, FITTER, erp.identity()) == ("инструкция.pdf", pdf())
    assert version_file(document.current.id, HR, erp.identity())[1] == pdf()


def test_the_secret_is_never_stored(erp):
    document = upload(erp)
    ack(erp, document.current.id)
    ack(erp, document.current.id, who=FITTER_2, secret=PASSWORD)
    with erp.sessions() as session:
        values = [str(value) for row in session.scalars(select(IdentityDocumentAckRow)) for value in row.__dict__.values()]
    assert values and not any(PIN in value or PASSWORD in value for value in values)


# ── «Кадры»: права, архив, удаление редакции ─────────────────────────────


def test_only_hr_uploads_archives_and_sees_the_sheet(erp):
    with pytest.raises(DocumentForbidden, match="Кадры"):
        upload(erp, who=FITTER)
    document = upload(erp)
    for action in (
        lambda: upload_version(document.id, actor(FITTER), "x.pdf", pdf("x"), NOW, erp.identity()),
        lambda: set_archived(document.id, actor(FITTER), True, NOW, erp.identity()),
        lambda: signature_sheet(document.id, FITTER, erp.identity()),
        lambda: delete_version(document.current.id, actor(FITTER), NOW, erp.identity()),
    ):
        with pytest.raises(DocumentForbidden):
            action()
    assert documents_overview(FITTER, erp.identity()).journal is None
    journal = documents_overview(HR, erp.identity()).journal
    assert [(item["title"], item["readers"], item["signed"]) for item in journal] == [("Инструкция монтажника", 2, 0)]


def test_an_archived_document_is_not_asked(erp):
    document = upload(erp)
    set_archived(document.id, actor(HR), True, NOW, erp.identity())
    assert pending_count(FITTER, erp.identity()) == 0 and own_ids(erp, FITTER) == []
    with pytest.raises(DocumentConflict, match="в архиве"):
        ack(erp, document.current.id)
    with pytest.raises(DocumentForbidden):
        version_file(document.current.id, FITTER, erp.identity())
    with pytest.raises(DocumentConflict, match="в архиве"):
        upload_version(document.id, actor(HR), "v2.pdf", pdf("v2"), NOW, erp.identity())
    assert documents_overview(HR, erp.identity()).journal[0]["archived"] is True  # «Кадры» видят архив

    set_archived(document.id, actor(HR), False, NOW, erp.identity())
    assert pending_count(FITTER, erp.identity()) == 1


def test_a_signed_version_is_never_deleted_an_unsigned_latest_one_is(erp):
    document = upload(erp)
    ack(erp, document.current.id)
    with pytest.raises(DocumentConflict, match="уже подписали"):
        delete_version(document.current.id, actor(HR), NOW, erp.identity())

    v2 = upload_version(document.id, actor(HR), "v2.pdf", pdf("v2"), NOW, erp.identity()).current
    with pytest.raises(DocumentConflict, match="последнюю"):
        delete_version(document.versions[0].id, actor(HR), NOW, erp.identity())
    left = delete_version(v2.id, actor(HR), NOW, erp.identity())
    assert [version.version_no for version in left.versions] == [1]
    assert pending_count(FITTER, erp.identity()) == 0  # снова действует подписанная первая редакция

    lonely = upload(erp, title="Ошибочный", scope="all")
    assert delete_version(lonely.current.id, actor(HR), NOW, erp.identity()) is None
    with erp.sessions() as session:
        assert session.get(IdentityDocumentRow, lonely.id) is None


def test_a_signature_makes_the_employee_history(erp):
    document = upload(erp)
    with erp.identity() as uow:
        assert uow.identity.user_history(FITTER) == 0
    ack(erp, document.current.id)
    with erp.identity() as uow:
        assert uow.identity.user_history(FITTER) >= 1


# ── PDF ──────────────────────────────────────────────────────────────────


def test_pdf_type_and_size_are_checked_in_russian():
    with pytest.raises(DocumentError, match="Это не PDF"):
        check_pdf(b"<html><script>alert(1)</script></html>")
    with pytest.raises(DocumentError, match="Файл пустой"):
        check_pdf(b"")
    with pytest.raises(DocumentError, match="больше 20 МБ"):
        check_pdf(b"%PDF-1.4\n" + b"0" * MAX_PDF_BYTES)
    assert len(check_pdf(pdf())) == 64


# ── HTTP ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def api(erp):
    app.state.identity_uow_factory = erp.identity
    app.state.cookie_secure = False
    app.state.identity_clock = lambda: NOW
    app.dependency_overrides[moscow_now] = lambda: NOW

    def login(name: str) -> TestClient:
        client = TestClient(app)
        response = client.post("/api/auth/login", json={"login": name, "password": PASSWORD})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = client.cookies.get("erp_csrf")
        return client

    yield login
    app.dependency_overrides.pop(moscow_now, None)
    for name in ("identity_uow_factory", "cookie_secure", "identity_clock"):
        if hasattr(app.state, name):
            delattr(app.state, name)


def _payload(content: bytes, **overrides) -> dict:
    body = {
        "title": "Инструкция монтажника", "kind": "job_description", "scope": "job_title", "scope_value": "Монтажник",
        "file_name": "C:\\Документы\\инструкция", "content_base64": base64.b64encode(content).decode("ascii"),
    }
    body.update(overrides)
    return body


def test_http_upload_sign_and_sheet(erp, api):
    hr, me = api("hr"), api("fitter")
    assert TestClient(app).get("/api/documents").status_code == 401

    refused = me.post("/api/documents", json=_payload(pdf()))
    assert refused.status_code == 403 and "Кадры" in refused.json()["detail"]
    not_pdf = hr.post("/api/documents", json=_payload(b"PK\x03\x04 word file"))
    assert not_pdf.status_code == 422 and not_pdf.json()["detail"] == "Это не PDF — загрузите документ в формате PDF."
    damaged = hr.post("/api/documents", json=_payload(pdf(), content_base64="не base64"))
    assert damaged.status_code == 422 and "повреждён" in damaged.json()["detail"]

    created = hr.post("/api/documents", json=_payload(pdf()))
    assert created.status_code == 201, created.text
    sheet = created.json()
    assert (sheet["kind_label"], sheet["audience"], sheet["readers"], sheet["signed"]) == (
        "Должностная инструкция", "Должность: Монтажник", 2, 0,
    )
    assert sheet["current"]["file_name"] == "инструкция.pdf"
    version_id = sheet["current"]["id"]

    assert me.get("/api/documents/pending").json() == {"count": 1}
    file = me.get(f"/api/documents/versions/{version_id}/file")
    assert file.status_code == 200 and file.headers["content-type"] == "application/pdf" and file.content == pdf()
    assert me.get(f"/api/documents/{sheet['id']}/sheet").status_code == 403

    wrong = me.post(f"/api/documents/versions/{version_id}/acknowledge", json={"secret": "111111"})
    assert wrong.status_code == 422 and "PIN" in wrong.json()["detail"]
    signed = me.post(f"/api/documents/versions/{version_id}/acknowledge", json={"secret": PIN})
    assert signed.status_code == 200, signed.text
    assert signed.json()["own"][0]["signed"]["method_label"] == "PIN" and signed.json()["journal"] is None
    again = me.post(f"/api/documents/versions/{version_id}/acknowledge", json={"secret": PIN})
    assert again.status_code == 409

    upgraded = hr.post(f"/api/documents/{sheet['id']}/versions", json={"file_name": "v2.pdf",
                       "content_base64": base64.b64encode(pdf("v2")).decode("ascii")})
    assert upgraded.status_code == 201 and upgraded.json()["version"]["version_no"] == 2
    assert upgraded.json()["signed"] == 0
    assert me.get("/api/documents/pending").json() == {"count": 1}
    old = hr.get(f"/api/documents/{sheet['id']}/sheet", params={"version_no": 1}).json()
    assert old["signed"] == 1

    gone = hr.delete(f"/api/documents/versions/{version_id}")
    assert gone.status_code == 409
    archived = hr.post(f"/api/documents/{sheet['id']}/archive", json={"archived": True})
    assert archived.status_code == 200 and archived.json()["archived"] is True
    assert me.get("/api/documents").json()["own"] == []
    assert hr.get("/api/documents").json()["journal"][0]["archived"] is True
    assert hr.get(f"/api/documents/{uuid4()}/sheet").status_code == 404


# ── Миграция 0100 ────────────────────────────────────────────────────────

BEFORE = "0099_worker_heartbeats"
AFTER = "0100_identity_documents"
USER = uuid4()
SHA = "ab" * 32


def _config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "documents.db"
    command.upgrade(_config(path), BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO identity_users (id, login, email, name, department, role, active, job_title, qualification, "
            "access_level, shop_worker, site_master) VALUES (?, 'hr', 'hr@example.test', 'Кадровик', 'Администрация', "
            "'staff', 1, '', '', 'operator', 0, 0)",
            (USER.hex,),
        )
    command.upgrade(_config(path), AFTER)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    yield path, engine
    engine.dispose()


def test_migration_creates_the_tables_as_the_orm_rows_describe_them(migrated):
    path, engine = migrated
    schema = inspect(engine)
    for row in (IdentityDocumentRow, IdentityDocumentVersionRow, IdentityDocumentAckRow):
        table = row.__table__
        columns = {column["name"]: column for column in schema.get_columns(table.name)}
        assert set(columns) == {column.name for column in table.columns}, table.name
        for column in table.columns:
            assert columns[column.name]["nullable"] == column.nullable, (table.name, column.name)
        assert {index["name"] for index in schema.get_indexes(table.name)} >= {index.name for index in table.indexes}
        stored = {check["name"] for check in schema.get_check_constraints(table.name)}
        assert stored == {c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}, table.name
    [unique] = schema.get_unique_constraints("identity_document_acks")
    assert unique["column_names"] == ["version_id", "user_id"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM identity_users").fetchone() == (1,)


def _document(**overrides) -> IdentityDocumentRow:
    values = dict(id=uuid4(), title="Положение", kind="regulation", scope="all", scope_value="", archived=False,
                  created_by=USER, created_at=NOW)
    values.update(overrides)
    return IdentityDocumentRow(**values)


def _version(document_id, **overrides) -> IdentityDocumentVersionRow:
    values = dict(id=uuid4(), document_id=document_id, version_no=1, file_name="a.pdf", size=10, sha256=SHA,
                  content=b"%PDF-1.4", uploaded_by=USER, uploaded_at=NOW)
    values.update(overrides)
    return IdentityDocumentVersionRow(**values)


@pytest.mark.parametrize("bad", [
    {"kind": "order"},
    {"scope": "site", "scope_value": "ЦАР"},
    {"scope": "all", "scope_value": "Цех"},
    {"scope": "department", "scope_value": ""},
    {"title": ""},
])
def test_document_constraints_refuse_bad_states(migrated, bad):
    _path, engine = migrated
    with Session(engine) as session:
        session.add(_document(**bad))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize("bad", [
    {"version_no": 0},
    {"size": 0},
    {"size": 20 * 1024 * 1024 + 1},
    {"sha256": "AB" * 32},
    {"sha256": "zz" * 32},
    {"sha256": "ab" * 31},
])
def test_version_constraints_refuse_bad_states(migrated, bad):
    _path, engine = migrated
    with Session(engine) as session:
        document = _document()
        session.add(document)
        session.flush()
        session.add(_version(document.id, **bad))
        with pytest.raises(IntegrityError):
            session.commit()


def test_one_version_number_per_document_and_one_signature_per_person(migrated):
    _path, engine = migrated
    with Session(engine) as session:
        document = _document()
        session.add(document)
        session.flush()
        version = _version(document.id)
        session.add(version)
        session.commit()
        session.add(_version(document.id))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        def signature(**overrides):
            values = dict(id=uuid4(), version_id=version.id, user_id=USER, signed_at=NOW, method="pin", sha256=SHA)
            values.update(overrides)
            return IdentityDocumentAckRow(**values)

        session.add(signature(method="finger"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(signature())
        session.commit()
        session.add(signature())
        with pytest.raises(IntegrityError):
            session.commit()


def test_downgrade_drops_the_tables(migrated):
    path, engine = migrated
    command.downgrade(_config(path), BEFORE)
    tables = set(inspect(engine).get_table_names())
    assert not tables & {"identity_documents", "identity_document_versions", "identity_document_acks"}
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM identity_users").fetchone() == (1,)
