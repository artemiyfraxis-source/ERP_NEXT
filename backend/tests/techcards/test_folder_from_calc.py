"""Папка проекта по ссылке на расчёт: id файла из ссылки, родитель на Диске, ручка карточки и workflow."""

from datetime import datetime
from uuid import UUID

import httpx
import pytest

from app.main import app
from app.modules.requests.google_drive import DriveError, GoogleDriveReader, drive_file_id
from app.modules.requests.google_sheets import ServiceAccount
from app.shared.http_auth import authenticated_principal
from app.workflows import folders_from_calc as workflow
from tests.factories import Actor
from tests.requests.test_calc_link import key_pem  # noqa: F401 — fixture
from tests.requests.test_google_drive import drive_handler
from tests.techcards.test_kitset_api import CALC, _load, _principal, kitset_api  # noqa: F401 — fixture

FOLDER = "https://drive.google.com/drive/folders/fold123"
URL = "/api/techcards/kitsets/708К.4/folder/from-calc"
NOW = datetime(2026, 10, 14, 10, 0)
ACTOR = Actor(id=UUID("40000000-0000-0000-0000-000000000009"), name="папки по расчёту", department="")


class FakeDrive:
    def __init__(self, parent=("fold123", "708К.4 Сенит"), error=None):
        self.parent, self.error, self.asked = parent, error, []

    def parent_folder(self, file_id):
        self.asked.append(file_id)
        if self.error:
            raise self.error
        return self.parent


@pytest.mark.parametrize("url", [
    "https://docs.google.com/spreadsheets/d/1AbC-calc_id/edit#gid=0",
    "https://drive.google.com/file/d/1AbC-calc_id/view?usp=sharing",
    "https://drive.google.com/open?id=1AbC-calc_id",
    "https://drive.google.com/uc?export=download&id=1AbC-calc_id",
    "  https://docs.google.com/spreadsheets/d/1AbC-calc_id  ",
])
def test_file_id_is_taken_from_sheets_and_drive_links(url):
    assert drive_file_id(url) == "1AbC-calc_id"


@pytest.mark.parametrize("url", ["", "https://example.test/x", "https://drive.google.com/open?id=short", "не ссылка"])
def test_no_file_id_in_foreign_links(url):
    assert drive_file_id(url) == ""


def _reader(key_pem, handler):  # noqa: F811
    account = ServiceAccount.from_info({"client_email": "erp-reader@x.iam.gserviceaccount.com", "private_key": key_pem[0]})
    return GoogleDriveReader(account, client=httpx.Client(transport=httpx.MockTransport(handler)), clock=lambda: 1)


def _parents_handler(files, names, calls):
    base = drive_handler({key: (key, "x", b"") for key in [*files, *names]})

    def handler(request):
        calls.append(request)
        file_id = request.url.path.rsplit("/", 1)[-1]
        if request.url.host != "oauth2.googleapis.com" and file_id in files:
            assert request.url.params["fields"] == "parents,name"
            return httpx.Response(200, json={"name": file_id, **({"parents": files[file_id]} if files[file_id] else {})})
        if request.url.host != "oauth2.googleapis.com" and file_id in names:
            return httpx.Response(200, json={"name": names[file_id]})
        return base(request)

    return handler


def test_parent_folder_reads_the_direct_parent_and_its_name_with_get_only(key_pem):  # noqa: F811
    calls: list[httpx.Request] = []
    reader = _reader(key_pem, _parents_handler({"calc": ["fold123"]}, {"fold123": "708К.4 Сенит"}, calls))
    assert reader.parent_folder("calc") == ("fold123", "708К.4 Сенит")
    assert {call.method for call in calls if call.url.host != "oauth2.googleapis.com"} == {"GET"}


def test_parent_folder_none_without_parent_and_missing_without_access(key_pem):  # noqa: F811
    reader = _reader(key_pem, _parents_handler({"lonely": []}, {}, []))
    assert reader.parent_folder("lonely") is None
    with pytest.raises(DriveError) as gone:
        reader.parent_folder("nope")
    assert gone.value.kind == "missing"


def test_parent_folder_keeps_the_id_when_the_folder_name_is_unreadable(key_pem):  # noqa: F811
    reader = _reader(key_pem, _parents_handler({"calc": ["hidden"]}, {}, []))
    assert reader.parent_folder("calc") == ("hidden", "")


def test_endpoint_saves_the_folder_found_by_the_calculation_and_journals_it(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    _load(client)
    drive = app.state.drive_reader = FakeDrive()
    try:
        res = client.post(URL)
        assert res.status_code == 200, res.text
        card = res.json()["card"]
        assert card["folder_url"] == FOLDER and drive.asked == ["1AbC-calc_id"]
        assert "папка найдена по расчёту" in card["journal"][0]["text"]
        # Второй раз — папка уже есть: Диск не спрашиваем, журнал не растёт.
        again = client.post(URL).json()["card"]
        assert len(again["journal"]) == len(card["journal"]) and drive.asked == ["1AbC-calc_id"]
    finally:
        del app.state.drive_reader


def test_endpoint_does_not_overwrite_a_hand_set_folder(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    _load(client)
    client.put("/api/techcards/kitsets/708К.4/folder", json={"url": "http://server/projects/708"})
    app.state.drive_reader = FakeDrive()
    try:
        assert client.post(URL).json()["card"]["folder_url"] == "http://server/projects/708"
    finally:
        del app.state.drive_reader


def test_endpoint_without_a_calculation_link_is_422(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    _load(client)
    with app.state.techcards_uow_factory() as uow:
        from dataclasses import replace
        kitset = uow.kitsets.get("708К.4")
        uow.kitsets.save(replace(kitset, calc_source="", version=kitset.version + 1))
        uow.commit()
    res = client.post(URL)
    assert res.status_code == 422 and res.json()["detail"] == "Нет ссылки на расчёт"


@pytest.mark.parametrize("error", [DriveError("нет", "missing"), DriveError("ключ", "config")])
def test_endpoint_without_drive_access_is_422_in_russian(kitset_api, error):  # noqa: F811
    client, _lot = kitset_api
    _load(client)
    app.state.drive_reader = FakeDrive(error=error)
    try:
        res = client.post(URL)
        assert res.status_code == 422
        assert res.json()["detail"] == "Нет доступа к файлу расчёта у сервисного аккаунта ERP"
        assert client.get("/api/techcards/kitsets/708К.4").json()["folder_url"] is None
    finally:
        del app.state.drive_reader


def test_endpoint_needs_edit_right_and_a_known_project(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    _load(client)
    assert client.post("/api/techcards/kitsets/нет/folder/from-calc").status_code == 404
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.post(URL).status_code == 403


def _folder(project="708К.4"):
    with app.state.techcards_uow_factory() as uow:
        return uow.kitsets.get(project).folder_url


def _run(kitset_api_client, reader, **options):
    return workflow.run(app.state.techcards_uow_factory, app.state.production_uow_factory, lambda: reader, ACTOR, NOW, **options)


def test_workflow_dry_run_writes_nothing(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    _load(client)
    text, code = _run(client, FakeDrive(), apply=False)
    assert code == 0 and "708К.4 → 708К.4 Сенит · " + FOLDER in text and "ничего не записано" in text
    assert not _folder()


def test_workflow_apply_fills_only_empty_and_the_second_run_is_a_no_op(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    _load(client)
    text, code = _run(client, FakeDrive(), apply=True)
    assert code == 0 and "Записано папок: 1" in text and _folder() == FOLDER
    version = client.get("/api/techcards/kitsets/708К.4").json()["version"]
    drive = FakeDrive(parent=("other", "Другая"))
    text, _code = _run(client, drive, apply=True)
    assert "без папки, но с расчётом: 0" in text and drive.asked == []
    assert _folder() == FOLDER and client.get("/api/techcards/kitsets/708К.4").json()["version"] == version


def test_workflow_keeps_a_hand_set_folder_and_reports_failures(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    _load(client)
    client.put("/api/techcards/kitsets/708К.4/folder", json={"url": "http://server/projects/708"})
    drive = FakeDrive()
    _run(client, drive, apply=True)
    assert _folder() == "http://server/projects/708" and drive.asked == []
    client.put("/api/techcards/kitsets/708К.4/folder", json={"url": ""})
    text, code = _run(client, FakeDrive(error=DriveError("нет", "missing")), apply=True, project="708К.4")
    assert code == 1 and "не найдена: Нет доступа к файлу расчёта" in text and not _folder()


def test_workflow_stops_when_the_drive_key_is_unreadable(kitset_api):  # noqa: F811
    client, _lot = kitset_api
    _load(client)

    def broken():
        raise DriveError("нет ключа", "config")

    text, code = workflow.run(app.state.techcards_uow_factory, app.state.production_uow_factory, broken, ACTOR, NOW, apply=True)
    assert code == 2 and "Google Диск не читается" in text and not _folder()
