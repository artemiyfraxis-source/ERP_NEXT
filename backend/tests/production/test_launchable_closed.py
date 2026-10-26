"""«Почему на доске до сих пор есть отгруженные» (владелец 26.10): КБ после склада и КБ закрытых проектов не
предлагаются к запуску — ни в /launchable (доска мастера), ни в автоплане (тот же ready_cards)."""

from datetime import date, datetime
from decimal import Decimal

from app.main import app
from app.modules.production import autoplan_api
from app.modules.production.plan import PLAN_KINDS, PlanEvent
from app.modules.shipping.domain import FinishedGood, MoveContext
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.workflows.project_kitset import closed_kb_keys

from tests.production.test_api import issue_card, production_api  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 6, 10, 0)
OPS = [("OP-1", "Резка", 30, 60)]
CTX = MoveContext(actor="Кладовщик", occurred_at=NOW)
PROJECTS = (("800К.1", ("КБ1", "КБ2", "КБ3")), ("800К.2", ("КБ1",)), ("800К.3", ("КБ1",)))


def _kitset(project, *indexes):
    return Kitset(
        project=project, customer="Заказчик", calc_source="", updated_at=NOW, updated_by="Технолог", created_at=NOW,
        assemblies=tuple(KitAssembly(index=index, name="Жгут", run=Decimal("10")) for index in indexes),
    )


def _good(project, index):
    return FinishedGood.create(
        project=project, item_index=index, customer="Заказчик", article="", name="Жгут", ordered=Decimal("10"), unit="шт",
    )


def _seed():
    for project, indexes in PROJECTS:
        for index in indexes:
            issue_card(project, index, OPS)
    with app.state.techcards_uow_factory() as uow:
        for project, indexes in PROJECTS:
            uow.kitsets.save(_kitset(project, *indexes))
        uow.commit()
    with app.state.shipping_uow_factory() as uow:
        uow.goods.add(_good("800К.1", "КБ1").accept(Decimal("10"), date(2026, 10, 5), "ГП.26.0000001"), CTX)  # Принято складом
        uow.goods.add(_good("800К.1", "КБ2").ship_before_erp(Decimal("10")), CTX)  # Отгружено до ERP: тираж есть, заданий нет
        uow.goods.add(_good("800К.1", "КБ3"))  # ещё в работе
        uow.goods.add(_good("800К.2", "КБ1").ship_before_erp(Decimal("10")), CTX)
        uow.commit()
    with app.state.production_uow_factory() as uow:
        uow.plan_events.add(PlanEvent("800К.3", NOW, "РП", PLAN_KINDS["status"], value="Отменён"))
        uow.commit()


def _listed(client):
    return {(item["project"], item["index"]) for item in client.get("/api/production/launchable").json()["items"]}


def test_launchable_skips_kbs_after_the_warehouse_and_cancelled_projects(production_api):  # noqa: F811
    client, _operator = production_api
    _seed()

    assert _listed(client) == {("800К.1", "КБ3")}


def test_launchable_keeps_a_kb_without_a_kitset(production_api):  # noqa: F811
    client, _operator = production_api
    issue_card("800К.9", "КБ1", OPS)

    assert _listed(client) == {("800К.9", "КБ1")}


def test_autoplan_gets_the_same_list(production_api):  # noqa: F811
    _client, _operator = production_api
    _seed()

    closed = closed_kb_keys(
        app.state.techcards_uow_factory, app.state.production_uow_factory,
        app.state.fulfillment_uow_factory, app.state.shipping_uow_factory,
    )
    _cards, ready = autoplan_api._techcards(app.state.techcards_uow_factory, closed)

    assert {(card.project, card.index) for card in ready} == {("800К.1", "КБ3")}
