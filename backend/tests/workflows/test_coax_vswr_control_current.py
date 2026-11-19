"""«Контроль КСВН» запущенного задания — по текущей выданной ревизии ЭТК (владелец 19.11.2026, 708К.: «мы не будем это
мерить, у нас нет переходников»): технолог снял контроль — задание уходит из «Замеров КСВН» и из требований ОТК, не
перезапускаясь; «не проверять» — заданий в списке нет вовсе."""

from tests.production.test_api import production_api  # noqa: F401
from tests.workflows.test_coax_vswr_api import COAX_CONFIG, NORM, _as, _card, _launch
from tests.workflows.test_coax_vswr_customer_api import _issue_new_revision

NONE = {"mode": "none", "every": None}


def _listed(client):
    return {row["id"]: row for row in client.get("/api/production/vswr/tasks").json()["tasks"]}


def _pinned_all(client, key):
    _as({"quality": "edit", "production": "edit"})
    card = _card({"coaxVswrNorm": {**NORM, "control": {"mode": "all", "every": None}}}, config=COAX_CONFIG)
    return card, _launch(client, card, key=key, quantity=2)


def test_control_removed_in_the_current_revision_takes_the_launched_task_off_the_vswr_list(production_api):  # noqa: F811
    client, _ = production_api
    card, task = _pinned_all(client, "vswr-current-none")
    assert _listed(client)[task["id"]]["required"]["total"] == 2

    _issue_new_revision(card.id, {"coaxVswrNorm": {**NORM, "control": NONE},
                                  "build_config": {"coax": {**COAX_CONFIG["coax"], "vswrControl": NONE}}})

    assert task["id"] not in _listed(client)
    overview = client.get(f"/api/production/vswr/tasks/{task['id']}").json()
    assert overview["control"]["mode"] == "none"
    plan = client.get(f"/api/production/qc/plan/{task['id']}").json()
    assert plan["vswr"]["mode"] == "none" and plan["vswr"]["total"] == 0
    assert next(check for check in plan["checks"] if check["kind"] == "vswr")["required"] is False


def test_sampling_set_in_the_current_revision_applies_to_the_launched_task(production_api):  # noqa: F811
    client, _ = production_api
    card, task = _pinned_all(client, "vswr-current-sample")

    _issue_new_revision(card.id, {"coaxVswrNorm": {**NORM, "control": {"mode": "sample", "every": 50}}})

    row = _listed(client)[task["id"]]
    assert row["control"] == {"mode": "sample", "every": 50} and row["required"]["total"] == 1


def test_a_current_revision_without_a_control_keeps_the_pinned_one(production_api):  # noqa: F811
    client, _ = production_api
    card, task = _pinned_all(client, "vswr-current-unset")

    _issue_new_revision(card.id, {"coaxVswrNorm": NORM, "build_config": {"coax": COAX_CONFIG["coax"]}})

    assert _listed(client)[task["id"]]["control"] == {"mode": "all", "every": None}


def test_a_task_launched_on_a_revision_without_the_check_is_not_listed(production_api):  # noqa: F811
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    config = {"coax": {**COAX_CONFIG["coax"], "vswrControl": NONE}}
    task = _launch(client, _card({"coaxVswrNorm": {**NORM, "control": NONE}}, config=config), key="vswr-pinned-none")

    assert task["id"] not in _listed(client)
