"""ОТК и «Норма заказчика (КД)» КСВН (владелец 23.10.2026): вердикт — по точкам полосы нормы заказчика, файл обязан её
покрывать; норма заказчика ТЕКУЩЕЙ выданной ревизии ЭТК перекрывает закреплённую для новых замеров."""

from uuid import uuid4

from app.main import app
from app.modules.techcards.project_cards import TechcardRevision
from tests.production.test_api import production_api  # noqa: F401
from tests.workflows.test_coax_vswr_api import (
    COAX_CONFIG, NORM, NOW, _as, _body, _card, _csv, _drop_refs, _instrument, _launch, _old_coax_setup,
)

CUSTOMER = {"vswrMax": 1.35, "fromMhz": 1000.0, "toMhz": 3000.0, "basis": "ТУ 16-505.123, п. 3.4"}
COMPONENTS = {key: NORM[key] for key in ("status", "reason", "vswrMax", "bandMhz", "s21Db", "ends")}
CUSTOMER_NORM = {**NORM, "vswrMax": 1.35, "bandMhz": [1000.0, 3000.0], "source": "customer",
                 "basis": CUSTOMER["basis"], "customer": CUSTOMER, "components": {**COMPONENTS, "vswrMax": 2.06,
                                                                                  "bandMhz": [1.0, 6000.0]}}
# Пик 2,5 на 900 МГц — вне полосы нормы заказчика, внутри неё наибольший 1,30.
SPIKE_OUTSIDE = _csv([(900, 2.5), (1000, 1.10), (2400, 1.30), (3000, 1.20)])
SHORT = _csv([(1000, 1.10), (2000, 1.20)])


def _issue_new_revision(card_id, document_patch: dict) -> None:
    """Технолог пересобирает ЭТК: прежняя ревизия уходит в историю, текущая — с новой нормой."""
    with app.state.techcards_uow_factory() as uow:
        current = uow.techcards.get(card_id)
        uow.techcards.add_revision(TechcardRevision(
            id=uuid4(), project_card_id=card_id, revision=current.revision, reason="Тест", actor_name="Технолог",
            occurred_at=NOW, document=current.document,
        ))
        uow.techcards.save(current.rebuild({**current.document, **document_patch}, NOW))
        uow.commit()


def _customer_setup(client, norm=CUSTOMER_NORM, key="vswr-customer"):
    _as({"quality": "edit", "production": "edit"})
    card = _card({"coaxVswrNorm": norm}, config={"coax": {"sideA": {"article": "SMA-J-1"}, "sideB": {"article": "SMA-P-2"}}})
    return card, _launch(client, card, key=key), _instrument(client)


def test_verdict_uses_only_the_points_of_the_customer_band(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _customer_setup(client)

    # Клиент шлёт полосу шире нормы заказчика — сервер судит только по её полосе.
    good = client.post("/api/production/vswr", json=_body(task, instrument, SPIKE_OUTSIDE, band_from_mhz=800, band_to_mhz=3000))

    assert good.status_code == 200, good.text
    measurement = good.json()["measurement"]
    assert measurement["band_mhz"] == [1000.0, 3000.0]
    assert measurement["max_vswr"] == 1.3 and measurement["max_vswr_mhz"] == 2400.0
    assert measurement["verdict"] == "pass" and measurement["norm_vswr_max"] == 1.35
    assert measurement["norm"]["source"] == "customer" and measurement["norm"]["components"]["vswrMax"] == 2.06


def test_a_file_that_misses_the_customer_band_is_refused(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, instrument = _customer_setup(client)

    refused = client.post("/api/production/vswr", json=_body(task, instrument, SHORT))

    assert refused.status_code == 422
    assert refused.json()["detail"] == "Файл замера не покрывает полосу нормы заказчика 1000…3000 МГц"
    assert client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1").json()["measurements"] == []


def test_overview_and_passport_show_the_customer_norm_and_the_components_for_reference(production_api):  # noqa: F811
    client, _ = production_api
    _card_, task, _instrument_ = _customer_setup(client)

    overview = client.get(f"/api/production/vswr/tasks/{task['id']}").json()
    passport = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1/passport").json()
    listed = {row["id"]: row for row in client.get("/api/production/vswr/tasks").json()["tasks"]}

    assert overview["norm"]["source"] == "customer" and overview["norm"]["vswr_max"] == 1.35
    assert overview["norm"]["basis"] == "ТУ 16-505.123, п. 3.4" and overview["norm"]["band_mhz"] == [1000.0, 3000.0]
    assert overview["norm"]["components"] == {"status": "ok", "reason": "", "vswr_max": 2.06, "band_mhz": [1.0, 6000.0]}
    assert passport["norm"]["basis"] == "ЭТК КБ1, ревизия 1; ТУ 16-505.123, п. 3.4"
    assert passport["norm"]["components"]["vswr_max"] == 2.06
    assert listed[task["id"]]["norm"] == {"status": "ok", "vswr_max": 1.35, "source": "customer"}


def test_a_customer_norm_of_the_current_revision_overrides_the_pinned_one_for_new_measurements(production_api):  # noqa: F811
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    coax = {"coax": {"sideA": {"article": "SMA-J-1"}, "sideB": {"article": "SMA-P-2"}}}
    card = _card({"coaxVswrNorm": NORM}, config=coax)  # закреплённая ревизия — норма по комплектующим 1,35
    task, instrument = _launch(client, card, key="vswr-override"), _instrument(client)
    before = client.post("/api/production/vswr", json=_body(task, instrument, _csv([(1000, 1.1), (1800, 1.3), (3000, 1.2)])))
    assert before.json()["measurement"]["verdict"] == "pass"

    # Технолог задал норму заказчика 1,25 в полосе 1000…3000 — она в текущей ревизии 2, задание закреплено на 1.
    stricter = {**CUSTOMER_NORM, "vswrMax": 1.25, "customer": {**CUSTOMER, "vswrMax": 1.25}}
    _issue_new_revision(card.id, {"coaxVswrNorm": stricter})

    after = client.post("/api/production/vswr", json=_body(
        task, instrument, _csv([(1000, 1.1), (1800, 1.3), (3000, 1.2)]), position=2, name="sn2.csv"))
    overview = client.get(f"/api/production/vswr/tasks/{task['id']}").json()
    listed = {row["id"]: row for row in client.get("/api/production/vswr/tasks").json()["tasks"]}
    passport = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/3/passport").json()
    stored = client.get(f"/api/production/vswr/tasks/{task['id']}/serials/1").json()["measurements"][0]

    assert after.status_code == 200, after.text
    result = after.json()["measurement"]
    assert result["verdict"] == "fail" and result["norm_vswr_max"] == 1.25
    assert result["norm"]["basis"] == "ТУ 16-505.123, п. 3.4; норма заказчика из ревизии 2 ЭТК"
    assert result["norm"]["components"]["vswrMax"] == 1.35  # справочная — из закреплённой ревизии
    assert overview["norm"]["vswr_max"] == 1.25 and "из ревизии 2 ЭТК" in overview["norm"]["basis"]
    assert listed[task["id"]]["norm"] == {"status": "ok", "vswr_max": 1.25, "source": "customer"}
    assert passport["etk"]["revision"] == 1
    assert passport["norm"]["basis"] == "ЭТК КБ1, ревизия 1; ТУ 16-505.123, п. 3.4; норма заказчика из ревизии 2 ЭТК"
    # Сохранённый замер хранит свой снимок нормы.
    assert stored["norm_vswr_max"] == 1.35 and stored["verdict"] == "pass"


def test_the_same_customer_norm_in_the_current_revision_changes_nothing(production_api):  # noqa: F811
    client, _ = production_api
    card, task, _instrument_ = _customer_setup(client, key="vswr-same")
    _issue_new_revision(card.id, {"kit": {"part_quantity": "12"}})  # другая ревизия, та же норма заказчика

    overview = client.get(f"/api/production/vswr/tasks/{task['id']}").json()

    assert overview["norm"]["vswr_max"] == 1.35 and overview["norm"]["basis"] == "ТУ 16-505.123, п. 3.4"


def test_a_current_revision_without_a_customer_norm_keeps_the_pinned_rule(production_api):  # noqa: F811
    client, _ = production_api
    card, task, _instrument_ = _customer_setup(client, key="vswr-dropped")
    _issue_new_revision(card.id, {"coaxVswrNorm": NORM})  # технолог убрал норму заказчика

    overview = client.get(f"/api/production/vswr/tasks/{task['id']}").json()

    assert overview["norm"]["source"] == "customer" and overview["norm"]["vswr_max"] == 1.35


def test_an_old_etk_takes_the_customer_norm_from_its_config(production_api):  # noqa: F811
    client, _ = production_api
    config = {"coax": {**COAX_CONFIG["coax"], "vswrCustomer": {"vswrMax": "1,4", "fromMhz": 1000, "toMhz": 3000,
                                                                "basis": "заказ 77"}}}
    try:
        _card_, task, _instrument_ = _old_coax_setup(client, config, key="vswr-old-customer")
        overview = client.get(f"/api/production/vswr/tasks/{task['id']}").json()
    finally:
        _drop_refs()

    assert overview["norm"]["source"] == "customer" and overview["norm"]["vswr_max"] == 1.4
    assert overview["norm"]["basis"] == "заказ 77" and overview["norm"]["components"]["vswr_max"] == 1.51
