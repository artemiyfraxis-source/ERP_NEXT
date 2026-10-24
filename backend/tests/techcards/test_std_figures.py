"""Рисунки стандартов к строкам СТО: замена набора, список, картинка, удаление, проверки, ограничения таблицы, права.

Картинки — крошечные синтетические PNG/JPEG, сделанные в тесте: вырезки ГОСТ/IPC в репозиторий не попадают."""

import base64
import os
import sqlite3
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.std_figures_infrastructure import StdFigureRow
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor
from app.shared.persistence import create_schema
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
CODE = "4.3.14"


def _image(width: int = 12, height: int = 8, kind: str = "PNG", noise: bool = False) -> bytes:
    if noise:
        image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    else:
        image = Image.new("RGB", (width, height), (200, 30, 30))
    out = BytesIO()
    image.save(out, kind)
    return out.getvalue()


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode()


def _figure(role="norm", kind="gost", caption="ГОСТ Р 72182.2-2025, табл. А.1, в)", content: bytes | None = None) -> dict:
    return {"role": role, "source_kind": kind, "caption": caption, "content_base64": _b64(content or _image())}


def _person(permissions: dict[str, str]) -> Principal:
    return Principal(
        actor=Actor(id=UUID("30000000-0000-0000-0000-000000000009"), name="Технолог", department="Производство"),
        email="tech@example.test", roles=frozenset({"operator"}), permissions=permissions,
    )


@pytest.fixture()
def figures():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), engine
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    engine.dispose()


def test_put_replaces_the_whole_set_and_the_list_carries_no_bytes(figures):
    client, _engine = figures
    first = client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": [
        _figure("norm", "gost", content=_image(30, 20)),
        {**_figure("defect", "ipc", "IPC/WHMA-A-620E, рис. 3-12", _image(40, 10, "JPEG")), "content_type": "image/jpeg"},
    ]})
    assert first.status_code == 200, first.text
    items = first.json()["items"]
    assert [(i["position"], i["role"], i["source_kind"], i["content_type"], i["width"], i["height"]) for i in items] == [
        (1, "norm", "gost", "image/png", 30, 20), (2, "defect", "ipc", "image/jpeg", 40, 10),
    ]
    assert [i["customer_allowed"] for i in items] == [True, False]
    assert items[0]["created_by"] == "Инженер" and len(items[0]["sha256"]) == 64

    second = client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": [_figure("ref", "gost", "  ГОСТ Р 72182.3-2025,\n рис. 5 ")]})
    assert second.status_code == 200
    listed = client.get("/api/techcards/std-figures").json()["items"]
    assert [(i["sto_code"], i["position"], i["role"], i["caption"]) for i in listed] == [
        (CODE, 1, "ref", "ГОСТ Р 72182.3-2025, рис. 5"),
    ]
    assert "content" not in listed[0] and "content_base64" not in listed[0]
    # The old ids are gone with the old set.
    assert client.get(f"/api/techcards/std-figures/{items[0]['id']}/image").status_code == 404


def test_image_is_served_with_a_long_private_cache_and_an_etag(figures):
    client, _engine = figures
    content = _image(16, 9)
    [item] = client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": [_figure(content=content)]}).json()["items"]
    response = client.get(f"/api/techcards/std-figures/{item['id']}/image")
    assert response.status_code == 200 and response.content == content
    assert response.headers["content-type"] == "image/png"
    assert response.headers["etag"] == f'"{item["sha256"]}"'
    assert "private" in response.headers["cache-control"] and "max-age=31536000" in response.headers["cache-control"]
    assert response.headers["x-content-type-options"] == "nosniff"
    again = client.get(f"/api/techcards/std-figures/{item['id']}/image", headers={"If-None-Match": response.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    assert client.get(f"/api/techcards/std-figures/{uuid4()}/image").status_code == 404


def test_delete_removes_every_figure_of_the_code_only(figures):
    client, _engine = figures
    client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": [_figure(), _figure("defect")]})
    client.put("/api/techcards/std-figures/4.1.2", json={"figures": [_figure()]})
    assert client.delete(f"/api/techcards/std-figures/{CODE}").json() == {"deleted": 2}
    assert [i["sto_code"] for i in client.get("/api/techcards/std-figures").json()["items"]] == ["4.1.2"]
    assert client.delete(f"/api/techcards/std-figures/{CODE}").json() == {"deleted": 0}
    assert client.delete("/api/techcards/std-figures/9.9.9").status_code == 422


@pytest.mark.parametrize(("figures_payload", "message"), [
    ([], "удалите их"),
    ([_figure() for _ in range(7)], "не больше 6"),
    ([_figure(caption="   ")], "подпись источника"),
    ([_figure(content=b"GIF89a" + b"\x00" * 40)], "PNG или JPEG"),
    ([_figure(content=b"<svg xmlns='http://www.w3.org/2000/svg'/>")], "PNG или JPEG"),
    ([_figure(content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)], "повреждена"),
    ([_figure(content=_image(1000, 1000, noise=True))], "больше 2 МБ"),
    ([{**_figure(), "content_type": "image/jpeg"}], "по содержимому это image/png"),
])
def test_bad_sets_are_refused_and_nothing_changes(figures, figures_payload, message):
    client, _engine = figures
    client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": [_figure()]})
    response = client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": figures_payload})
    assert response.status_code == 422 and message in response.json()["detail"], response.text
    assert len(client.get("/api/techcards/std-figures").json()["items"]) == 1


def test_unknown_code_role_and_broken_base64_are_refused(figures):
    client, _engine = figures
    unknown = client.put("/api/techcards/std-figures/9.9.9", json={"figures": [_figure()]})
    assert unknown.status_code == 422 and "нет в СТО" in unknown.json()["detail"]
    assert client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": [_figure(role="good")]}).status_code == 422
    assert client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": [_figure(kind="din")]}).status_code == 422
    broken = client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": [{**_figure(), "content_base64": "@@@"}]})
    assert broken.status_code == 422 and "base64" in broken.json()["detail"]


def test_the_table_keeps_one_figure_per_position_and_its_checks(figures):
    _client, engine = figures
    row = {
        "sto_code": CODE, "position": 1, "role": "norm", "source_kind": "gost", "caption": "ГОСТ", "content_type": "image/png",
        "sha256": "a" * 64, "width": 1, "height": 1, "size": 1, "content": b"x", "created_at": NOW, "created_by": "Тест",
    }
    with engine.begin() as connection:
        connection.execute(insert(StdFigureRow).values(id=uuid4(), **row))
    for broken in ({}, {"role": "good", "position": 2}, {"source_kind": "din", "position": 2}, {"position": 0},
                   {"caption": "", "position": 2}, {"content_type": "image/gif", "position": 2},
                   {"sha256": "a", "position": 2}, {"width": 0, "position": 2}, {"size": 3_000_000, "position": 2}):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert(StdFigureRow).values(id=uuid4(), **{**row, **broken}))


def test_only_template_editors_write_and_anyone_signed_in_reads(figures):
    client, _engine = figures
    client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": [_figure()]})
    app.dependency_overrides[authenticated_principal] = lambda: _person({"techcards": "edit", "erptctpl": "view"})
    try:
        refused = client.put(f"/api/techcards/std-figures/{CODE}", json={"figures": [_figure()]})
        assert refused.status_code == 403 and "Шаблоны техкарт" in refused.json()["detail"]
        assert client.delete(f"/api/techcards/std-figures/{CODE}").status_code == 403
        # A fitter with only «Задания» reads the figures of his ETK.
        app.dependency_overrides[authenticated_principal] = lambda: _person({"myops": "view"})
        [item] = client.get("/api/techcards/std-figures").json()["items"]
        assert client.get(f"/api/techcards/std-figures/{item['id']}/image").status_code == 200
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
    assert TestClient(app).get("/api/techcards/std-figures").status_code == 401


def test_the_upload_token_replaces_figures_but_never_deletes(tokens_api):  # noqa: F811
    app.dependency_overrides.pop(authenticated_principal, None)  # the techcards conftest signs in an engineer
    admin = _login("admin")
    uploader = _bearer(_issue(admin, scopes=("figures:write",), name="Рисунки стандартов")["token"])
    other = _bearer(_issue(admin, scopes=("tcgen",), name="Генератор")["token"])
    service = TestClient(app)
    payload = {"figures": [_figure()]}
    stored = service.put(f"/api/techcards/std-figures/{CODE}", json=payload, headers=uploader)
    assert stored.status_code == 200, stored.text
    assert stored.json()["items"][0]["created_by"] == "API: Рисунки стандартов"
    assert service.put(f"/api/techcards/std-figures/{CODE}", json=payload, headers=other).status_code == 403
    assert service.delete(f"/api/techcards/std-figures/{CODE}", headers=uploader).status_code == 403
    assert service.get("/api/techcards/std-figures", headers=uploader).status_code == 403
    assert admin.delete(f"/api/techcards/std-figures/{CODE}", headers=_csrf(admin)).json() == {"deleted": 1}


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_migration_creates_the_empty_table_with_its_checks_and_downgrade_drops_it(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    command.upgrade(_config(path), "0148_techcard_std_figures")
    with sqlite3.connect(path) as connection:
        [sql] = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'techcard_std_figures'").fetchone()
        assert connection.execute("SELECT count(*) FROM techcard_std_figures").fetchone() == (0,)
    for name in ("ck_techcard_std_figures_role", "ck_techcard_std_figures_source_kind", "ck_techcard_std_figures_content_type",
                 "ck_techcard_std_figures_caption", "ck_techcard_std_figures_dimensions", "uq_techcard_std_figures_code_position"):
        assert name in sql
    command.downgrade(_config(path), "0147_procurement_payment_files")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'techcard_std_figures'").fetchone() is None
