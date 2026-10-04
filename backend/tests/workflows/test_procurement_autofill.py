"""«Взять в работу» → пустые поля строки закупки заполняются сами (SUPPLYMAP Guards.gs `inWorkSideEffects_`).

ПОСТАВЩИК — по домену ссылки, тут же, в самом «Взять в работу». НАИМЕНОВАНИЕ (САЙТ), ИЗГОТОВИТЕЛЬ и СТРАНА по нему —
фоном (`procurement_autofill_tick`): сайт подменяет фейковый PageInfoFetcher, httpx-адаптер — MockTransport.
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from app.modules.procurement.application import (
    PageInfo,
    SaveProcurementFactsCommand,
    TakeProcurementInWorkCommand,
    consume_procurement_requested,
    save_procurement_facts,
    take_procurement_in_work,
    take_procurement_in_work_batch,
)
from app.modules.procurement.domain import AUTOFILL_TOPIC, ProcurementFacts, ProcurementStatus, ProcurementTraceability
from app.modules.references.application import ReferencePair, ReplaceMapsCommand, replace_maps
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from app.workflows import procurement_autofill as autofill
from app.workflows.procurement_autofill import HttpPageInfoFetcher, page_brand, procurement_autofill_tick
from app.workflows.supplier_directory import ReferencesSupplierDirectory
from tests.factories import Actor
from tests.procurement.test_application import requested_message
from tests.procurement.test_backorder_flow import db  # noqa: F401

NOW = datetime(2026, 10, 4, 12, 0)
BUYER = Actor(name="Петров", department="Закупки")
CHIPDIP = "https://www.chipdip.ru/product/amp-1"


class FakePages:
    """Фейковый сайт: url → PageInfo; `on_fetch` — что случилось, пока бот ходил по сайту; `error` — сеть легла."""

    def __init__(self, pages: dict[str, PageInfo] | None = None, *, on_fetch=None, error: Exception | None = None):
        self.pages = pages or {}
        self.on_fetch = on_fetch
        self.error = error
        self.calls: list[str] = []

    def info(self, url: str) -> PageInfo:
        self.calls.append(url)
        if self.on_fetch:
            self.on_fetch(url)
        if self.error:
            raise self.error
        return self.pages.get(url, PageInfo())


class BrokenDirectory:
    def supplier_map(self):
        raise RuntimeError("references are down")

    def country_for_manufacturer(self, manufacturer):
        raise RuntimeError("references are down")

    def learn(self, link, supplier, actor, now):
        return False


@pytest.fixture()
def directory(db):  # noqa: F811
    factory = lambda: SqlAlchemyReferencesUnitOfWork(db.sessions)
    replace_maps(
        ReplaceMapsCommand(
            suppliers=(ReferencePair("https://www.chipdip.ru", "ЧипДип"),),
            countries=(ReferencePair("Amphenol", "США"), ReferencePair("Molex", "США")),
            gost_awg=(),
            actor=Actor(name="Админ", department="ИТ"),
            now=NOW,
        ),
        factory(),
    )
    return ReferencesSupplierDirectory(factory)


def new_line(db, n: int = 1, link: str | None = CHIPDIP, **fields):  # noqa: F811
    base = requested_message(n)
    line = consume_procurement_requested(replace(base, payload={**base.payload, "link": link}), db.procurement())
    if fields:
        with db.procurement() as uow:
            uow.procurement.add(replace(line, **fields))
            uow.commit()
            line = uow.procurement.get(line.id)
    return line


def take(db, *lines, directory=None):  # noqa: F811
    return take_procurement_in_work(
        TakeProcurementInWorkCommand(tuple(line.id for line in lines), BUYER, NOW), db.procurement(), directory,
    )


def stored(db, line):  # noqa: F811
    with db.procurement() as uow:
        return uow.procurement.get(line.id)


def events(db, event_type: str):  # noqa: F811
    with db.sessions() as session:
        return session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == event_type).order_by(AuditEventRow.occurred_at)
        ).all()


def queue(db):  # noqa: F811
    with db.sessions() as session:
        return session.scalars(select(OutboxMessageRow).where(OutboxMessageRow.topic == AUTOFILL_TOPIC)).all()


def tick(db, pages, directory=None, **kwargs):  # noqa: F811
    return procurement_autofill_tick(db.sessions, pages, directory, clock=lambda: NOW, **kwargs)


# ── ПОСТАВЩИК: по домену, в самом «Взять в работу» ──


def test_take_fills_the_supplier_from_the_domain_at_once_and_queues_the_page_fields(db, directory):
    line = new_line(db)
    (taken,) = take(db, line, directory=directory)

    assert (taken.status, taken.supplier) == (ProcurementStatus.IN_WORK, "ЧипДип")
    assert stored(db, line).supplier == "ЧипДип"
    (filled,) = events(db, "procurement.autofilled")
    assert filled.details == {"legacy_id": line.legacy_id, "fields": ["supplier"]}
    assert filled.actor_name == "Петров"  # у кнопки один автор: тот, кто нажал
    (message,) = queue(db)
    assert message.payload == {"line_id": str(line.id), "version": str(taken.version), "legacy_id": line.legacy_id}
    assert message.published_at is None  # сайт ещё не тронут: это дело фонового тика


def test_take_never_overwrites_a_supplier_and_an_unknown_domain_fills_nothing(db, directory):
    typed = new_line(db, 1, supplier="Мой поставщик")
    unknown = new_line(db, 2, link="https://elitan.ru/item/5")

    taken_typed, taken_unknown = take(db, typed, unknown, directory=directory)

    assert taken_typed.supplier == "Мой поставщик"
    assert taken_unknown.supplier is None
    assert events(db, "procurement.autofilled") == []
    assert len(queue(db)) == 2  # сайт по-прежнему может дать имя и бренд


def test_take_without_a_link_or_directory_queues_nothing_and_still_takes(db, directory):
    no_link = new_line(db, 1, link=None)
    (taken,) = take(db, no_link, directory=directory)
    assert taken.status is ProcurementStatus.IN_WORK and taken.supplier is None
    assert queue(db) == []

    linked = new_line(db, 2)
    (without_map,) = take(db, linked)  # справочник не подключён — как раньше: статус и закупщик
    assert (without_map.status, without_map.supplier) == (ProcurementStatus.IN_WORK, None)
    assert len(queue(db)) == 1


def test_take_survives_a_broken_supplier_map(db):
    line = new_line(db)
    (taken,) = take(db, line, directory=BrokenDirectory())
    assert (taken.status, taken.supplier) == (ProcurementStatus.IN_WORK, None)


def test_a_retry_take_is_read_only_and_queues_nothing_twice(db, directory):
    line = new_line(db)
    take(db, line, directory=directory)
    take(db, line, directory=directory)
    assert len(queue(db)) == 1
    assert len(events(db, "procurement.autofilled")) == 1


def test_fifty_lines_are_taken_without_touching_the_network(db, directory, monkeypatch):
    lines = [new_line(db, n, link=f"https://www.chipdip.ru/product/{n}") for n in range(1, 51)]

    def no_network(*args, **kwargs):
        raise AssertionError("«Взять в работу» не ходит на сайты")

    monkeypatch.setattr(HttpPageInfoFetcher, "info", no_network)
    taken, skipped = take_procurement_in_work_batch(
        TakeProcurementInWorkCommand(tuple(line.id for line in lines), BUYER, NOW), db.procurement(), directory,
    )

    assert len(taken) == 50 and skipped == []
    assert {line.supplier for line in taken} == {"ЧипДип"}
    assert len(queue(db)) == 50


# ── фон: имя на сайте, изготовитель, страна ──


def test_tick_fills_only_empty_fields_from_the_page(db, directory):
    line = new_line(db)
    (taken,) = take(db, line, directory=directory)
    pages = FakePages({CHIPDIP: PageInfo(title="Разъём SMA-J Amphenol 132289", brand="Amphenol")})

    assert tick(db, pages, directory) == 1

    after = stored(db, line)
    assert pages.calls == [CHIPDIP]
    assert (after.site_name, after.trace.manufacturer, after.trace.country) == ("Разъём SMA-J Amphenol 132289", "Amphenol", "США")
    assert after.supplier == "ЧипДип"
    # Фон не двигает версию: открытая форма «Заполнить данные» остаётся годной и не ловит 409.
    assert after.version == taken.version
    (system,) = events(db, "procurement.autofilled")[-1:]
    assert system.details == {"legacy_id": line.legacy_id, "fields": ["site_name", "manufacturer", "country"]}
    assert system.actor_name == "ERP Integration"
    assert [message.published_at for message in queue(db)] == [NOW]
    assert tick(db, pages, directory) == 0 and pages.calls == [CHIPDIP]  # сделано — повторно сайт не трогают


def test_tick_never_overwrites_what_is_already_there(db, directory):
    named = new_line(db, 1, site_name="Моё имя", trace=ProcurementTraceability(country="Китай"))
    made = new_line(db, 2, trace=ProcurementTraceability(manufacturer="Molex"))
    take(db, named, made, directory=directory)
    pages = FakePages({
        CHIPDIP: PageInfo(title="Имя с сайта", brand="Amphenol"),
    })

    assert tick(db, pages, directory) == 2

    first, second = stored(db, named), stored(db, made)
    # имя и страна — набранные человеком — остаются; пустой изготовитель берёт бренд, страну набранной не трогает
    assert (first.site_name, first.trace.manufacturer, first.trace.country) == ("Моё имя", "Amphenol", "Китай")
    # изготовитель набран — бренд сайта его не заменяет, страна — по набранному (Molex → США)
    assert (second.site_name, second.trace.manufacturer, second.trace.country) == ("Имя с сайта", "Molex", "США")


def test_a_complete_line_does_not_go_to_the_site(db, directory):
    line = new_line(db, 1, site_name="Имя", trace=ProcurementTraceability(manufacturer="Molex", country="США"))
    take(db, line, directory=directory)
    assert queue(db) == []  # и в очередь не ставится
    pages = FakePages()
    assert tick(db, pages, directory) == 0 and pages.calls == []


def test_a_typed_manufacturer_without_a_country_takes_the_country_without_a_request(db, directory):
    line = new_line(db, 1, site_name="Имя", trace=ProcurementTraceability(manufacturer="amphenol"))
    take(db, line, directory=directory)
    pages = FakePages()

    assert tick(db, pages, directory) == 1

    assert pages.calls == []  # имя и изготовитель на месте — сайт не нужен
    assert stored(db, line).trace.country == "США"


def test_tick_skips_a_line_changed_since_the_take(db, directory):
    line = new_line(db)
    (taken,) = take(db, line, directory=directory)
    saved = save_procurement_facts(
        SaveProcurementFactsCommand(
            line.id, ProcurementFacts(supplier="ООО Поставка", actual_quantity=Decimal("2"), actual_unit="шт"),
            taken.version, BUYER, NOW,
        ),
        db.procurement(),
    )
    pages = FakePages({CHIPDIP: PageInfo(title="Имя с сайта", brand="Amphenol")})

    assert tick(db, pages, directory) == 0

    assert pages.calls == []  # строку тронули после «Взять в работу» — сайт не нужен
    after = stored(db, line)
    assert (after.site_name, after.trace.manufacturer, after.supplier) == (None, None, "ООО Поставка")
    assert after.version == saved.version
    assert [message.published_at for message in queue(db)] == [NOW]  # закрыто, а не висит в очереди


def test_tick_skips_a_line_changed_while_the_page_was_being_fetched(db, directory):
    line = new_line(db)
    (taken,) = take(db, line, directory=directory)

    def buyer_saves(url):
        save_procurement_facts(
            SaveProcurementFactsCommand(line.id, ProcurementFacts(site_name="Набрал сам"), taken.version, BUYER, NOW),
            db.procurement(),
        )

    pages = FakePages({CHIPDIP: PageInfo(title="Имя с сайта", brand="Amphenol")}, on_fetch=buyer_saves)

    assert tick(db, pages, directory) == 0

    after = stored(db, line)
    assert pages.calls == [CHIPDIP]
    assert (after.site_name, after.trace.manufacturer, after.trace.country) == ("Набрал сам", None, None)
    assert [message.published_at for message in queue(db)] == [NOW]
    assert [event.details["fields"] for event in events(db, "procurement.autofilled")] == [["supplier"]]


@pytest.mark.parametrize(
    "pages",
    [
        FakePages(error=httpx.ConnectTimeout("timeout")),
        FakePages(error=RuntimeError("fake client broke")),
        FakePages({}),  # сайт ответил ошибкой или пустой страницей — HttpPageInfoFetcher отдаёт пустой PageInfo
    ],
    ids=["timeout", "broken-client", "empty-answer"],
)
def test_network_errors_are_a_no_op_and_never_fail_the_tick(db, directory, pages):
    line = new_line(db)
    (taken,) = take(db, line, directory=directory)

    assert tick(db, pages, directory) == 0

    after = stored(db, line)
    assert (after.site_name, after.trace.manufacturer, after.trace.country, after.version) == (None, None, None, taken.version)
    assert [event.details["fields"] for event in events(db, "procurement.autofilled")] == [["supplier"]]
    (message,) = queue(db)
    assert message.published_at == NOW and message.attempt_count == 0  # как в SUPPLYMAP: одна попытка, без повторов


def test_a_broken_country_map_still_leaves_the_name_and_brand(db, directory):
    line = new_line(db)
    take(db, line, directory=directory)
    pages = FakePages({CHIPDIP: PageInfo(title="Имя с сайта", brand="Amphenol")})

    assert tick(db, pages, BrokenDirectory()) == 1

    after = stored(db, line)
    assert (after.site_name, after.trace.manufacturer, after.trace.country) == ("Имя с сайта", "Amphenol", None)


def test_the_tick_respects_its_cap_and_an_empty_queue_is_free(db, directory):
    lines = [new_line(db, n, link=f"https://www.chipdip.ru/product/{n}") for n in (1, 2, 3)]
    pages = FakePages()
    assert tick(db, pages, directory) == 0 and pages.calls == []  # очередь пуста

    take(db, *lines, directory=directory)
    assert tick(db, pages, directory, cap=2) == 0
    assert len(pages.calls) == 2
    assert sum(message.published_at is None for message in queue(db)) == 1


def test_a_failure_of_ours_is_retried_with_backoff_and_never_raises(db, directory, monkeypatch):
    line = new_line(db)
    take(db, line, directory=directory)

    def broken(command, uow):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(autofill, "apply_autofill", broken)
    pages = FakePages({CHIPDIP: PageInfo(title="Имя с сайта")})

    assert tick(db, pages, directory) == 0

    (message,) = queue(db)
    assert (message.attempt_count, message.published_at) == (1, None)
    assert message.next_attempt_at > NOW and "database is gone" in message.last_error
    assert tick(db, pages, directory) == 0 and len(pages.calls) == 1  # пока выдержка — строку не берут
    assert stored(db, line).site_name is None


def test_a_malformed_message_is_quarantined_without_stopping_the_tick(db, directory):
    with db.sessions() as session:
        session.add(OutboxMessageRow(
            id=UUID(int=7), topic=AUTOFILL_TOPIC, idempotency_key="autofill:broken", created_at=NOW,
            payload={"line_id": "not-a-uuid", "version": "x"},
        ))
        session.commit()
    line = new_line(db)
    take(db, line, directory=directory)
    pages = FakePages({CHIPDIP: PageInfo(title="Имя с сайта")})

    assert tick(db, pages, directory) == 1

    broken = next(message for message in queue(db) if message.idempotency_key == "autofill:broken")
    assert (broken.attempt_count, broken.published_at) == (1, None)
    assert stored(db, line).site_name == "Имя с сайта"


def test_a_line_that_is_gone_closes_its_message(db, directory):
    line = new_line(db)
    take(db, line, directory=directory)
    with db.procurement() as uow:
        uow.procurement.delete(line.id, stored(db, line).version)
        uow.commit()
    pages = FakePages({CHIPDIP: PageInfo(title="Имя")})
    assert tick(db, pages, directory) == 0 and pages.calls == []
    assert [message.published_at for message in queue(db)] == [NOW]


# ── изготовитель со страницы (Schema.gs pageBrand_) ──


@pytest.mark.parametrize(
    ("html", "brand"),
    [
        ('<script type="application/ld+json">{"@type":"Product","brand":{"@type":"Brand","name":"Amphenol"}}</script>', "Amphenol"),
        ('{"name":"X","brand":"Wago"}', "Wago"),
        ('{"manufacturer":{"@type":"Organization","name":"Phoenix Contact"}}', "Phoenix Contact"),
        ('<meta property="product:brand" content="Harting">', "Harting"),
        ('<meta content="Lapp" name="brand">', "Lapp"),
        ('<span itemprop="brand" content="Weidmuller"></span>', "Weidmuller"),
        ('<span itemprop="brand">TE Connectivity</span>', "TE Connectivity"),
        ("<tr><th>Производитель</th><td>Molex</td></tr>", "Molex"),
        ('<div><label>Бренд:</label> <span class="v">«Кабель-Про»</span></div>', "Кабель-Про"),
        ('{"brand":"Wago &amp; Co."}', "Wago & Co"),
        # мусор — хуже пустого (поле прослеживаемости): обрывки разметки, нет букв, предложение, слишком длинно
        ('{"brand":"href=/catalog"}', ""),
        ('{"brand":"12345"}', ""),
        ('{"brand":"один два три четыре пять шесть"}', ""),
        ('{"brand":"' + "x" * 41 + '"}', ""),
        ("<title>Разъём</title><p>Производитель разъёма — неизвестен</p>", ""),
        ("", ""),
    ],
)
def test_page_brand_takes_only_reliable_sources(html, brand):
    assert page_brand(html) == brand


def test_page_brand_goes_on_to_the_next_source_when_the_first_is_junk():
    assert page_brand('{"brand":"http://x"} <meta property="og:brand" content="Wago">') == "Wago"


# ── httpx-адаптер: один запрос на имя и бренд ──


def _client(handler) -> httpx.Client:
    from app.modules.techcards.kitset_names import USER_AGENT, _refuse_private_host

    return httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True, headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [_refuse_private_host]},
    )


def _public_dns(monkeypatch, address: str = "93.184.216.34"):
    monkeypatch.setattr("socket.getaddrinfo", lambda host, port, *args, **kwargs: [(2, 1, 6, "", (address, 0))])


def test_http_fetcher_gives_title_and_brand_from_one_request(monkeypatch):
    _public_dns(monkeypatch)
    calls = []
    html = '<title>Разъём SMA | Магазин</title><meta property="product:brand" content="Amphenol">'

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=html.encode(), headers={"content-type": "text/html; charset=utf-8"})

    info = HttpPageInfoFetcher(_client(handler)).info("https://shop.example/p?id=5&utm_source=ya")

    assert info == PageInfo(title="Разъём SMA", brand="Amphenol")
    assert calls == ["https://shop.example/p?id=5"]  # трекинг срезан, запрос один


def test_http_fetcher_reads_a_windows_1251_page(monkeypatch):
    _public_dns(monkeypatch)
    body = '<meta charset="windows-1251"><title>Клемма ВК-1</title><meta name="brand" content="Элитан">'.encode("cp1251")
    info = HttpPageInfoFetcher(_client(lambda request: httpx.Response(200, content=body))).info("https://shop.example/")
    assert info == PageInfo(title="Клемма ВК-1", brand="Элитан")


def test_http_fetcher_returns_an_empty_answer_on_errors_and_internal_hosts(monkeypatch):
    _public_dns(monkeypatch)
    assert HttpPageInfoFetcher(_client(lambda r: httpx.Response(403, text="<title>Доступ</title>"))).info("https://a.example") == PageInfo()

    def down(request):
        raise httpx.ConnectTimeout("timeout", request=request)

    assert HttpPageInfoFetcher(_client(down)).info("https://a.example") == PageInfo()

    _public_dns(monkeypatch, "169.254.169.254")
    calls = []
    internal = HttpPageInfoFetcher(_client(lambda r: calls.append(r) or httpx.Response(200, text="<title>secret</title>")))
    assert internal.info("http://metadata.example/") == PageInfo()
    assert calls == []
