"""ОТК: замер КСВН и паспорт коаксиальной сборки по HTTP. Норма — из закреплённой ревизии ЭТК задания, не от клиента."""

import base64
from datetime import datetime
from uuid import UUID, uuid4

from app.main import app
from app.modules.techcards.project_cards import ProjectTechcard, TechcardRevision
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.production.test_api import MASTER_ID, production_api  # noqa: F401
from tests.production.test_job_flow_api import OPS

NOW = datetime(2026, 9, 22, 9, 0)
NORM = {
    "status": "ok", "reason": "", "vswrMax": 1.35, "bandMhz": [1000, 3000], "s21Db": 0.9, "lengthM": 0.5,
    "cable": {"name": "RG-316", "lossDbM": 1.2},
    "ends": [
        {"side": "A", "article": "SMA-J-1", "vswrText": "1,2 до 6 ГГц", "vswr": 1.2, "maxGhz": 6.0},
        {"side": "B", "article": "SMA-P-2", "vswrText": "1,25 до 6 ГГц", "vswr": 1.25, "maxGhz": 6.0},
    ],
}
UNDEFINED = {"status": "undefined", "reason": "Паспортной нормы для этого класса нет", "vswrMax": None}


def _csv(points):
    lines = ["Частота, МГц;КСВН"] + [f"{mhz:g};{vswr:.4f}".replace(".", ",") for mhz, vswr in points]
    return "\n".join(lines).encode("utf-8")


GOOD = _csv([(900, 2.5), (1000, 1.10), (2400, 1.30), (3000, 1.20)])
BAD = _csv([(1000, 1.10), (1800, 1.42), (3000, 1.20)])


def _as(permissions, name="Контролёр"):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=MASTER_ID, name=name, department="ОТК"),
        email="qc@example.test", roles=frozenset({"qc"}), permissions=permissions,
    )


def _card(extra: dict | None):
    card = ProjectTechcard.create(
        project="591K.3", item_index="КБ1", product_name="Кабель ВЧ", source_calculation="", deadline=None, now=NOW,
    ).rebuild({
        "operations": [{"code": f"OP-{i}", "connector": str(i), "side": ""} for i, *_ in enumerate(OPS, 1)],
        "build_config": {"ops": [
            {"type": "assembly", "stage": stage, "opNumber": code, "tOp": t_op, "tPrep": t_prep}
            for code, stage, t_op, t_prep in OPS
        ]},
        "kit": {"part_quantity": "10"}, **(extra or {}),
    }, NOW).issue(NOW)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    return card


def _launch(client, card, key="vswr-1", quantity=3):
    response = client.post("/api/production/tasks", headers={"Idempotency-Key": key},
                           json={"techcard_id": str(card.id), "quantity": quantity})
    assert response.status_code == 200, response.text
    return response.json()["task"]


def _instrument(client):
    response = client.post("/api/production/instruments", json={
        "inventory_number": "ВАЦ-01", "name": "Arinst VNA-DL", "instrument_type": "ВАЦ",
        "verification_number": "ПВ-2026-17", "verified_at": "2026-09-01T00:00:00",
        "verification_due": "2027-09-01T00:00:00",
    })
    assert response.status_code == 200, response.text
    return response.json()["instrument"]


def _body(task, instrument, content, position=1, name="sn1.csv", **extra):
    return {"task_id": task["id"], "position": position, "instrument_id": instrument["id"],
            "file_name": name, "file_base64": base64.b64encode(content).decode(), **extra}


def _setup(client, norm=NORM):
    _as({"quality": "edit", "production": "edit"})
    card = _card({"coaxVswrNorm": norm} if norm is not None else None)
    return card, _launch(client, card), _instrument(client)


def test_norm_comes_from_the_pinned_etk_revision_and_sets_the_verdict(production_api):  # noqa: F811
    client, _ = production_api
    card, task, instrument = _setup(client)
    # ЭТК получает новую ревизию с другой нормой — задание остаётся на своей (1.35).
    with app.state.techcards_uow_factory() as uow:
        current = uow.techcards.get(card.id)
        uow.techcards.add_revision(TechcardRevision(
            id=uuid4(), project_card_id=card.id, revision=current.revision, reason="Тест", actor_name="Технолог",
            occurred_at=NOW, document=current.document,
        ))
        updated = current.rebuild({**current.document, "coaxVswrNorm": {**NORM, "vswrMax": 1.1}}, NOW)
        uow.techcards.save(updated)
        uow.commit()

    good = client.post("/api/production/vswr", json=_body(task, instrument, GOOD))
    bad = client.post("/api/production/vswr", json=_body(task, instrument, BAD, position=2, name="sn2.csv"))

    assert good.status_code == 200, good.text
    measurement = good.json()["measurement"]
    assert measurement["verdict"] == "pass" and measurement["norm_vswr_max"] == 1.35
    assert measurement["max_vswr"] == 1.3 and measurement["max_vswr_mhz"] == 2400.0
    assert measurement["band_mhz"] == [1000.0, 3000.0] and measurement["attempt"] == 1
    assert measurement["inspector"]["name"] == "Контролёр" and measurement["trace"]
    assert bad.json()["measurement"]["verdict"] == "fail"


def test_client_cannot_pass_a_norm_or_override_the_verdict(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client)

    forged = client.post("/api/production/vswr", json=_body(task, instrument, BAD, norm={"vswrMax": 9}))
    override = client.post("/api/production/vswr", json=_body(task, instrument, BAD, verdict="pass", reason="надо"))

    assert forged.status_code == 422
    assert override.status_code == 422 and "задана" in override.json()["detail"]
    assert client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1").json()["measurements"] == []


def test_undefined_norm_needs_a_manual_verdict_with_a_reason(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client, UNDEFINED)

    band = {"band_from_mhz": 1000, "band_to_mhz": 3000}
    none = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, **band))
    no_reason = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, verdict="pass", **band))
    manual = client.post("/api/production/vswr", json=_body(
        task, instrument, GOOD, verdict="pass", reason="Норма по ТЗ заказчика 1,5", band_from_mhz=1000, band_to_mhz=3000))

    assert none.status_code == 422 and "вручную" in none.json()["detail"]
    assert no_reason.status_code == 422
    assert manual.status_code == 200, manual.text
    result = manual.json()["measurement"]
    assert result["verdict_manual"] is True and result["verdict_reason"] == "Норма по ТЗ заказчика 1,5"
    assert result["norm_vswr_max"] is None


def test_old_etk_without_the_key_is_an_undefined_norm_with_the_reason(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client, None)

    refused = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, band_from_mhz=1000, band_to_mhz=3000))
    manual = client.post("/api/production/vswr", json=_body(
        task, instrument, GOOD, verdict="fail", reason="Норма не известна", band_from_mhz=1000, band_to_mhz=3000))
    listed = client.get(f"/api/production/vswr/tasks/{task['id']}").json()

    assert refused.status_code == 422
    assert manual.status_code == 200, manual.text
    assert manual.json()["measurement"]["norm"]["reason"] == "ЭТК выпущена до нормы КСВН"
    assert listed["norm"] == {**listed["norm"], "status": "undefined", "reason": "ЭТК выпущена до нормы КСВН"}


def test_parser_and_domain_errors_are_russian_422(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client)

    garbage = client.post("/api/production/vswr", json=_body(task, instrument, b"abc"))
    not_base64 = client.post("/api/production/vswr", json={**_body(task, instrument, GOOD), "file_base64": "!!!"})
    position = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, position=9))
    unknown = client.post("/api/production/vswr", json={**_body(task, instrument, GOOD), "task_id": str(uuid4())})

    assert garbage.status_code == 422 and "файл" in garbage.json()["detail"].lower()
    assert not_base64.status_code == 422 and "base64" in not_base64.json()["detail"]
    assert position.status_code == 422 and "изделия с 1 по 3" in position.json()["detail"]
    assert unknown.status_code == 404


def test_list_per_task_and_per_serial_and_file_download(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client)
    first = client.post("/api/production/vswr", json=_body(task, instrument, BAD)).json()["measurement"]
    second = client.post("/api/production/vswr", json=_body(task, instrument, GOOD, name="повтор.csv")).json()["measurement"]

    listed = client.get(f"/api/production/vswr/tasks/{task['id']}")
    attempts = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1")
    stored = client.get(f"/api/production/vswr/measurements/{second['id']}/file")

    assert listed.status_code == 200, listed.text
    row = listed.json()["serials"][0]
    assert row["position"] == 1 and row["attempts"] == 2 and row["latest"]["id"] == second["id"]
    assert row["latest"]["verdict"] == "pass" and listed.json()["norm"]["vswr_max"] == 1.35
    assert [item["attempt"] for item in attempts.json()["measurements"]] == [1, 2]
    assert attempts.json()["measurements"][0]["id"] == first["id"]
    assert stored.status_code == 200 and stored.content == GOOD
    assert "attachment" in stored.headers["content-disposition"] and "%D0%BF%D0%BE%D0%B2%D1%82%D0%BE%D1%80" in stored.headers["content-disposition"]
    assert client.get(f"/api/production/vswr/measurements/{uuid4()}/file").status_code == 404


def test_passport_collects_the_product_etk_norm_measurement_instrument_and_inspector(production_api):  # noqa: F811
    client, _ = production_api
    card, task, instrument = _setup(client)
    client.post("/api/production/vswr", json=_body(task, instrument, GOOD))

    passport = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport")
    empty = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/2/passport")

    assert passport.status_code == 200, passport.text
    body = passport.json()
    assert body["product"]["serial_number"] == f"{task['number']}-0001"
    assert body["product"]["cable"] == {"name": "RG-316", "loss_db_m": 1.2} and body["product"]["length_m"] == 0.5
    assert [(end["side"], end["article"], end["vswr"], end["max_ghz"]) for end in body["product"]["ends"]] == [
        ("A", "SMA-J-1", 1.2, 6.0), ("B", "SMA-P-2", 1.25, 6.0)]
    assert body["etk"]["techcard_id"] == str(card.id) and body["etk"]["revision"] == 1
    assert body["norm"]["vswr_max"] == 1.35 and body["norm"]["band_mhz"] == [1000, 3000]
    assert body["norm"]["basis"] == "ЭТК КБ1, ревизия 1" and body["norm"]["status"] == "ok"
    [check] = body["checks"]
    assert check["kind"] == "vswr" and check["verdict_name"] == "Годно"
    assert check["measurement"]["max_vswr"] == 1.3 and check["measurement"]["file_name"] == "sn1.csv"
    assert check["measurement"]["trace"][0] == {"mhz": 900.0, "vswr": 2.5}
    assert check["instrument"]["inventory_number"] == "ВАЦ-01" and check["instrument"]["verification_number"] == "ПВ-2026-17"
    assert check["instrument"]["verification_due"].startswith("2027-09-01")
    assert body["inspector"]["name"] == "Контролёр" and body["inspected_at"].startswith("2026-09-22T09:00")
    # Изделие без замера: паспорт есть, проверок нет, норма — из ЭТК.
    assert empty.status_code == 200 and empty.json()["checks"] == [] and empty.json()["norm"]["vswr_max"] == 1.35
    assert client.get(f"/api/production/vswr/tasks/{task['id']}/serials/7/passport").status_code == 404


def test_rights_follow_the_existing_qc_endpoints(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _setup(client)
    body = _body(task, instrument, GOOD)
    recorded = client.post("/api/production/vswr", json=body).json()["measurement"]

    _as({"production": "edit"}, name="Мастер")  # правки «ОТК» нет — замер не пишет, читает
    refused = client.post("/api/production/vswr", json=body)
    read = client.get(f"/api/production/vswr/tasks/{task['id']}")
    download = client.get(f"/api/production/vswr/measurements/{recorded['id']}/file")
    _as({"myops": "edit"}, name="Монтажник")
    nobody = client.get(f"/api/production/vswr/tasks/{task['id']}")
    nobody_file = client.get(f"/api/production/vswr/measurements/{recorded['id']}/file")
    nobody_post = client.post("/api/production/vswr", json=body)
    _as({"production": "view"}, name="Наблюдатель")
    viewer = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport")
    viewer_post = client.post("/api/production/vswr", json=body)

    assert refused.status_code == 403 and "ОТК" in refused.json()["detail"]
    assert read.status_code == 200 and download.status_code == 200
    assert nobody.status_code == 403 and nobody_file.status_code == 403 and nobody_post.status_code == 403
    assert viewer.status_code == 200 and viewer_post.status_code == 403


def test_sample_control_marks_the_required_positions_and_counts_them(production_api):  # noqa: F811
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    card = _card({"coaxVswrNorm": {**NORM, "control": {"mode": "sample", "every": 2}}})
    task, instrument = _launch(client, card, key="vswr-sample", quantity=3), _instrument(client)
    client.post("/api/production/vswr", json=_body(task, instrument, GOOD))
    outside = client.post("/api/production/vswr", json=_body(task, instrument, BAD, position=2, name="sn2.csv"))

    listed = client.get(f"/api/production/vswr/tasks/{task['id']}").json()

    assert outside.status_code == 200, outside.text  # вне выборки мерить можно
    assert listed["control"] == {"mode": "sample", "every": 2}
    assert [(row["position"], row["in_sample"]) for row in listed["serials"]] == [(1, True), (2, False), (3, True)]
    assert listed["required"] == {"total": 2, "measured": 1}  # №1 и №3 в выборке, замерено №1 (№2 — вне выборки)


def test_without_the_setting_every_unit_is_required(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, _instrument_ = _setup(client)
    listed = client.get(f"/api/production/vswr/tasks/{task['id']}").json()
    assert listed["control"] == {"mode": "all", "every": None}
    assert listed["required"] == {"total": 3, "measured": 0} and all(row["in_sample"] for row in listed["serials"])


def test_passport_of_a_unit_outside_the_sample_refers_to_the_batch_sample(production_api):  # noqa: F811
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    card = _card({"build_config": {"ops": [], "coax": {"vswrControl": {"mode": "sample", "every": 2}}},
                  "coaxVswrNorm": NORM})
    task, instrument = _launch(client, card, key="vswr-passport", quantity=3), _instrument(client)
    client.post("/api/production/vswr", json=_body(task, instrument, BAD))
    client.post("/api/production/vswr", json=_body(task, instrument, GOOD, name="повтор.csv"))

    outside = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/2/passport").json()
    sampled = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport").json()
    waiting = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/3/passport").json()

    assert outside["control"] == {"mode": "sample", "every": 2} and outside["checks"] == []
    sample = outside["vswr_sample"]
    assert sample["every"] == 2 and [unit["position"] for unit in sample["units"]] == [1, 3]
    assert sample["units"][0]["verdict"] == "pass" and sample["units"][0]["serial_number"] == f"{task['number']}-0001"
    assert sample["units"][1]["verdict"] is None
    assert sampled["vswr_sample"] is None and sampled["checks"][0]["verdict"] == "pass"  # замерено — как раньше
    assert waiting["vswr_sample"] is None  # в выборке, но ещё не замерено — ссылки на выборку нет
