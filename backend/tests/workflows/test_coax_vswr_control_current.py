"""«Контроль КСВН» запущенного задания — по текущей выданной ревизии ЭТК (владелец 19.11.2026, 708К.: «мы не будем это
мерить, у нас нет переходников»): технолог снял контроль — задание уходит из «Замеров КСВН» и из требований ОТК, не
перезапускаясь; «не проверять» без замеров — заданий в списке нет вовсе. Задание с замерами остаётся на виду
с прежним контролем; черновик ЭТК никогда не перекрывает закреплённую ревизию; ужесточение действует всегда."""

from dataclasses import replace
from uuid import uuid4

from app.main import app
from app.modules.techcards.project_cards import TechcardRevision, TechcardStatus
from tests.production.test_api import production_api  # noqa: F401
from tests.workflows.test_coax_vswr_api import COAX_CONFIG, NORM, NOW, _as, _body, _both, _card, _instrument, _launch
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


def test_a_task_with_measurements_keeps_its_check_when_a_later_revision_turns_it_off(production_api):  # noqa: F811
    client, _ = production_api
    card, task = _pinned_all(client, "vswr-measured-none")
    instrument = _instrument(client)
    bad = client.post("/api/production/vswr", json=_body(task, instrument, _both([(1000, 1.1), (2000, 1.6), (3000, 1.2)])))
    assert bad.status_code == 200, bad.text
    assert bad.json()["measurement"]["verdict"] == "fail"

    _issue_new_revision(card.id, {"coaxVswrNorm": {**NORM, "control": NONE},
                                  "build_config": {"coax": {**COAX_CONFIG["coax"], "vswrControl": NONE}}})

    row = _listed(client)[task["id"]]
    assert row["control"] == {"mode": "all", "every": None}
    assert row["required"] == {"total": 2, "measured": 1, "failed": 1, "max_vswr": 1.6}
    overview = client.get(f"/api/production/vswr/tasks/{task['id']}").json()
    assert overview["control"]["mode"] == "all"
    plan = client.get(f"/api/production/qc/plan/{task['id']}").json()
    assert plan["vswr"]["mode"] == "all" and plan["vswr"]["failed"] == 1 and [unit["verdict"] for unit in plan["vswr"]["units"]] == ["fail"]
    assert next(check for check in plan["checks"] if check["kind"] == "vswr")["required"] is True


def test_a_draft_never_overrides_the_pinned_revision(production_api):  # noqa: F811
    client, _ = production_api
    card, task = _pinned_all(client, "vswr-draft-none")
    with app.state.techcards_uow_factory() as uow:
        issued = uow.techcards.get(card.id)
        uow.techcards.add_revision(TechcardRevision(
            id=uuid4(), project_card_id=card.id, revision=issued.revision, reason="Тест", actor_name="Технолог",
            occurred_at=NOW, document=issued.document))
        uow.techcards.save(replace(issued, status=TechcardStatus.DRAFT, revision=issued.revision + 1,
                                   version=issued.version + 1,
                                   document={**issued.document, "coaxVswrNorm": {**NORM, "control": NONE}}))
        uow.commit()

    assert _listed(client)[task["id"]]["control"] == {"mode": "all", "every": None}


def test_turning_the_check_back_on_requires_measurements_again(production_api):  # noqa: F811
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    config = {"coax": {**COAX_CONFIG["coax"], "vswrControl": NONE}}
    card = _card({"coaxVswrNorm": {**NORM, "control": NONE}}, config=config)
    task = _launch(client, card, key="vswr-none-to-all", quantity=2)
    assert task["id"] not in _listed(client)

    _issue_new_revision(card.id, {"coaxVswrNorm": {**NORM, "control": {"mode": "all", "every": None}},
                                  "build_config": {"coax": {**COAX_CONFIG["coax"], "vswrControl": {"mode": "all"}}}})

    row = _listed(client)[task["id"]]
    assert row["control"] == {"mode": "all", "every": None} and row["required"]["total"] == 2
