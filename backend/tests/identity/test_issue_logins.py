"""Short logins and passwords for everybody (owner 01.10.2026: «давай на логины перейдём… список логинов и паролей»)."""

import csv
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.identity.application import login
from app.modules.identity.domain import IdentityUser, hash_password
from app.modules.identity.infrastructure import SqlAlchemyIdentityUnitOfWork, provision_user
from app.modules.identity.issue_logins import issue, short_login, translit
from app.shared.persistence import create_schema

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add(sessions, name: str, email: str, role: str = "staff") -> None:
    with sessions() as session:
        user = IdentityUser(id=uuid4(), login=email, email=email, name=name, department="Цех", role=role, site=None, active=True)
        provision_user(session, user, hash_password("старый-пароль-xx"))
        session.commit()


def test_translit_and_clashes():
    assert translit("Щукина") == "shchukina"
    assert translit("Васильев") == "vasilev"
    taken = {"ivanov"}
    assert short_login("Иванов Пётр", taken) == "ivanovp"
    taken |= {"ivanovp"}
    assert short_login("Иванов Павел", taken) == "ivanovp2"


def test_issues_short_logins_and_passwords_that_sign_in(sessions, tmp_path):
    add(sessions, "Безменов Никита", "bezmenov.nikita@fraxis.local")
    add(sessions, "Васильев Артемий", "artemiyfraxis@gmail.com", role="admin")
    with sessions() as session:
        rows = issue(session, reset=False)
        session.commit()
    by_name = {row["ФИО"]: row for row in rows}
    assert by_name["Безменов Никита"]["Логин"] == "bezmenov"
    assert by_name["Васильев Артемий"]["Логин"] == "vasilev"
    password = by_name["Безменов Никита"]["Пароль"]
    assert len(password) == 10

    uow = SqlAlchemyIdentityUnitOfWork(sessions)
    assert login("bezmenov", password, NOW, uow, client_key="t").principal.email == "bezmenov.nikita@fraxis.local"
    # The e-mail still works as a login.
    assert login("bezmenov.nikita@fraxis.local", password, NOW, SqlAlchemyIdentityUnitOfWork(sessions), client_key="t2")

    # A second run keeps logins and passwords; --reset changes only the passwords.
    with sessions() as session:
        again = issue(session, reset=False)
        session.commit()
    assert {row["Пароль"] for row in again} == {"(прежний)"}
    with sessions() as session:
        reset = issue(session, reset=True)
        session.commit()
    assert [row["Логин"] for row in reset] == [row["Логин"] for row in rows]
    assert all(row["Пароль"] != "(прежний)" for row in reset)


def test_the_list_opens_with_cyrillic(sessions, tmp_path, monkeypatch):
    from app.modules.identity import issue_logins

    add(sessions, "Безменов Никита", "bezmenov.nikita@fraxis.local")
    engine = sessions.kw["bind"]
    monkeypatch.setattr(issue_logins, "build_engine", lambda url: engine)
    monkeypatch.setattr(engine, "dispose", lambda: None)
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    out = tmp_path / "logins.csv"
    monkeypatch.setattr("sys.argv", ["issue_logins", "--out", str(out)])
    issue_logins.main()
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM: Excel and Notepad show Cyrillic as is
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig"), delimiter=";"))
    assert rows[0]["ФИО"] == "Безменов Никита" and rows[0]["Логин"] == "bezmenov"


def test_a_run_without_new_logins_keeps_the_list(sessions, tmp_path, monkeypatch):
    """update.sh runs the command after every deploy: the list with real passwords must survive."""
    from app.modules.identity import issue_logins

    add(sessions, "Безменов Никита", "bezmenov.nikita@fraxis.local")
    engine = sessions.kw["bind"]
    monkeypatch.setattr(issue_logins, "build_engine", lambda url: engine)
    monkeypatch.setattr(engine, "dispose", lambda: None)
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    out = tmp_path / "logins.csv"
    monkeypatch.setattr("sys.argv", ["issue_logins", "--out", str(out)])
    issue_logins.main()
    first = out.read_bytes()
    issue_logins.main()
    assert out.read_bytes() == first

    add(sessions, "Иванов Пётр", "ivanov@fraxis.local")
    issue_logins.main()
    kept = [p for p in tmp_path.iterdir() if p.name.startswith("logins-")]
    assert len(kept) == 1 and kept[0].read_bytes() == first
    assert "ivanov" in out.read_text(encoding="utf-8-sig")


def test_the_demo_preview_account_is_switched_off(sessions):
    """A local base moved to the server brings the «preview» admin with the password printed in scripts/demo."""
    with sessions() as session:
        user = IdentityUser(id=uuid4(), login="preview", email="preview@example.com", name="Артемий",
                            department="Руководство", role="admin", site=None, active=True)
        provision_user(session, user, hash_password("Preview-2026!"))
        session.commit()
    with sessions() as session:
        rows = issue(session, reset=False)
        session.commit()
    assert "preview" not in {r["Логин"] for r in rows}
    with pytest.raises(Exception):
        login("preview", "Preview-2026!", NOW, SqlAlchemyIdentityUnitOfWork(sessions), client_key="t")


def test_the_command_saves_the_switch_off_even_without_new_logins(sessions, tmp_path, monkeypatch):
    """Server 01.10: the list existed, nothing new was issued, and the early exit dropped preview's active=False."""
    from app.modules.identity import issue_logins

    engine = sessions.kw["bind"]
    monkeypatch.setattr(issue_logins, "build_engine", lambda url: engine)
    monkeypatch.setattr(engine, "dispose", lambda: None)
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    out = tmp_path / "logins.csv"
    out.write_text("old list", encoding="utf-8")
    with sessions() as session:
        user = IdentityUser(id=uuid4(), login="preview", email="preview@example.com", name="Артемий",
                            department="Руководство", role="admin", site=None, active=True)
        provision_user(session, user, hash_password("Preview-2026!"))
        session.commit()
    monkeypatch.setattr("sys.argv", ["issue_logins", "--out", str(out)])
    issue_logins.main()
    assert out.read_text(encoding="utf-8") == "old list"
    with pytest.raises(Exception):
        login("preview", "Preview-2026!", NOW, SqlAlchemyIdentityUnitOfWork(sessions), client_key="t")
