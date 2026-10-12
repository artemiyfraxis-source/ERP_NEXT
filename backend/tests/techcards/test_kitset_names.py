"""Фоновый добор наименований набора — порт TcKitset.gs tcKitFetchNames / tcKitNamesEnqueue_ / tcKitNamesTick_.

Сеть в тестах не трогается: сайты подменяет фейковый PageFetcher, httpx-адаптер — MockTransport.
"""

from datetime import datetime
from decimal import Decimal
import socket

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import outbox_worker
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.kitset_names import (
    USER_AGENT,
    HttpPageFetcher,
    apply_names,
    clean_site_title,
    fetch_names,
    name_looks_real,
    page_text,
    page_title,
    strip_tracking_params,
)
from app.modules.techcards.kitsets import KitAssembly, KitComponent, Kitset
from app.shared.persistence import create_schema
from app.workflows import project_kitset as flow
from app.workflows.kitset_names import KitsetNameQueue, kitset_names_tick


NOW = datetime(2026, 9, 29, 10, 0)


class FakeSites:
    def __init__(self, titles: dict[str, str], on_fetch=None):
        self.titles = titles
        self.calls: list[str] = []
        self.on_fetch = on_fetch

    def title(self, url: str) -> str:
        self.calls.append(url)
        if self.on_fetch:
            self.on_fetch(url)
        return self.titles.get(url, "")


def _kitset(*components: KitComponent, index: str = "КБ1") -> Kitset:
    return Kitset(
        project="708К.4", customer="ООО Заказчик", calc_source="calc",
        assemblies=(KitAssembly(index=index, name="Жгут", run=Decimal("10"), components=components),),
        updated_at=NOW, updated_by="Технолог", created_at=NOW,
    )


# ── чистые функции (Schema.gs / Triggers.gs) ──


def test_strip_tracking_params_keeps_other_params_and_hash():
    assert strip_tracking_params("https://s.ru/p?id=5&utm_source=ya&ysclid=abc#spec") == "https://s.ru/p?id=5#spec"
    assert strip_tracking_params("https://s.ru/p?from=main&ref=x") == "https://s.ru/p"
    assert strip_tracking_params("https://s.ru/p") == "https://s.ru/p"


def test_page_title_decodes_and_cuts_the_site_suffix():
    html = "<html><head><TITLE>\n ★ Провод НВ-4 0,5 мм&sup2; &amp; K | СнабКабель</title></head></html>"
    assert page_title(html) == "Провод НВ-4 0,5 мм&sup2; & K"
    assert page_title("<title>Корпус&#32;Molex — магазин</title>") == "Корпус Molex"
    assert page_title("<p>без заголовка</p>") == ""


def test_page_text_reads_the_meta_charset():
    body = '<meta charset="windows-1251"><title>Клемма ВК-1</title>'.encode("cp1251")
    assert page_title(page_text(body, "text/html")) == "Клемма ВК-1"
    assert page_title(page_text("<title>Кабель</title>".encode(), "text/html; charset=utf-8")) == "Кабель"
    assert page_text(b"<title>x</title>", "text/html; charset=nonsense-9") == "<title>x</title>"


def test_title_equal_to_the_article_is_not_a_name():
    assert not name_looks_real("43025-0400", "43025 0400")
    assert not name_looks_real("  ", "X")
    assert name_looks_real("Корпус Molex 43025-0400", "43025-0400")


# ── tcKitFetchNames ──


def test_fetch_names_only_empty_names_with_a_link_and_respects_the_cap():
    kitset = _kitset(
        KitComponent(article="A-1", link="https://shop.example/a1"),
        KitComponent(article="B-2", name="Вписал человек", link="https://shop.example/b2"),
        KitComponent(article="C-3", link="Склад"),
        KitComponent(article="D-4", link="https://shop.example/search?q=D-4"),
        KitComponent(article="E-5", link="https://shop.example/e5"),
        KitComponent(article="F-6", link="https://shop.example/f6"),
        KitComponent(article="", link="https://shop.example/none"),
    )
    sites = FakeSites({
        "https://shop.example/a1": "Провод A-1 красный",
        "https://shop.example/b2": "Не должно попасть",
        "https://shop.example/search?q=D-4": "D-4",
        "https://shop.example/f6": "Не дошли — потолок",
    })
    result = fetch_names(kitset, sites, cap=3)
    assert sites.calls == ["https://shop.example/a1", "https://shop.example/search?q=D-4", "https://shop.example/e5"]
    assert (result.filled, result.tried, result.left) == (1, 3, 1)
    assert result.skipped == [
        {"art": "C-3", "why": "нет ссылки"},
        {"art": "D-4", "why": "на странице только артикул (ссылка на поиск)"},
        {"art": "E-5", "why": "сайт не отдал название"},
    ]
    updated, written = apply_names(kitset, result.fills)
    assert written == 1 and updated.version == kitset.version + 1
    assert [c.name for c in updated.assemblies[0].components][:2] == ["Провод A-1 красный", "Вписал человек"]
    assert (updated.updated_by, updated.updated_at) == (kitset.updated_by, kitset.updated_at)


@pytest.mark.parametrize(("raw", "clean"), [
    # The owner's examples (12.10.2026).
    ("Автомобильный провод ПВАМ (медный). Продажа от 5 м!", "Автомобильный провод ПВАМ (медный)"),
    (
        "Трубка термоусадочная ТУТ нг 3,0/1,5 черная, термоусаживаемая трубка в метровой нарезке, не поддерживает "
        "горение Rexant купить оптом и в розницу",
        "Трубка термоусадочная ТУТ нг 3,0/1,5 черная (Rexant)",
    ),
    (
        'Трубка 85205 ТТК "Flex" 6/2 термоусаживаемая с клеем чёрная L=1м (КВТ)',
        'Трубка 85205 ТТК "Flex" 6/2 термоусаживаемая с клеем чёрная L=1м (КВТ)',
    ),
    ("ParLan Patch U/UTP Cat5e PVC 2x2x0,48-145 - кабель для СКС и IP-сетей", "ParLan Patch U/UTP Cat5e PVC 2x2x0,48-145"),
    # Shops in brackets go, makers stay; prices and delivery go; whitespace collapses.
    ("Корпус XHP-4 (Чип и Дип)", "Корпус XHP-4"),
    ("Клемма ВК-1 (www.elitan.ru)", "Клемма ВК-1"),
    ("  Провод   НВ-4  0,5 ,  цена за метр ", "Провод НВ-4 0,5"),
    ("Разъём M12, в наличии, доставка по России", "Разъём M12"),
    ("Провод МГТФ 0,12", "Провод МГТФ 0,12"),
    ("", ""),
    # Too long: cut at a word, marked with «…».
    (
        "Кабель монтажный многожильный экранированный с изоляцией из поливинилхлоридного пластиката для стационарной "
        "прокладки",
        "Кабель монтажный многожильный экранированный с изоляцией из…",
    ),
])
def test_clean_site_title(raw, clean):
    cleaned = clean_site_title(raw)
    assert cleaned == clean
    assert len(cleaned) <= 80
    assert clean_site_title(cleaned) == cleaned  # a second pass changes nothing


def test_fetch_names_does_not_write_a_title_that_reads_as_another_article():
    """A Latin title in «Наименование» would be `kit_article`'s replacement code and the generator would build on it."""
    kitset = _kitset(
        KitComponent(article="XHP-4", link="https://shop.example/xhp"),
        KitComponent(article="ПВАМ 0,35 красный", link="https://shop.example/pvam"),
        KitComponent(article="SXH-001T-P0.6", link="https://shop.example/sxh"),
    )
    sites = FakeSites({
        "https://shop.example/xhp": "JST XHP-4 housing",
        "https://shop.example/pvam": "Провод ПВАМ красный",  # a short worded name over a worded КД article
        "https://shop.example/sxh": "Контакт SXH-001T-P0.6 для корпусов XH",
    })
    result = fetch_names(kitset, sites)
    assert [fill.article for fill in result.fills] == ["SXH-001T-P0.6"]
    assert result.skipped == [
        {"art": "XHP-4", "why": "название с сайта читается как другой артикул"},
        {"art": "ПВАМ 0,35 красный", "why": "название с сайта читается как другой артикул"},
    ]


def test_fetch_names_by_assembly_index():
    kitset = _kitset(KitComponent(article="A-1", link="https://shop.example/a1"), index="КБ2")
    assert fetch_names(kitset, FakeSites({}), index="кб1").tried == 0
    assert fetch_names(kitset, FakeSites({}), index="кб2").tried == 1


# ── очередь и тик (tcKitNamesEnqueue_ / tcKitNamesTick_) ──


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def _save(sessions, kitset: Kitset) -> None:
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.kitsets.save(kitset)
        uow.commit()


def _names(sessions, project="708К.4") -> list[str]:
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        return [c.name for c in uow.kitsets.get(project).assemblies[0].components]


def test_enqueue_is_fifo_without_duplicates(sessions):
    queue = KitsetNameQueue(sessions)
    for project in ("1К.1", "2К.1", "1К.1", " "):
        queue.enqueue(project, NOW)
    assert queue.items() == ["1К.1", "2К.1"]


def test_tick_fills_empty_names_keeps_the_project_until_done_and_drops_it(sessions, monkeypatch):
    components = tuple(
        KitComponent(article=f"A-{n}", link=f"https://shop.example/{n}") for n in range(17)
    )
    _save(sessions, _kitset(*components))
    queue = KitsetNameQueue(sessions)
    queue.enqueue("708К.4", NOW)
    queue.enqueue("999К.1", NOW)
    sites = FakeSites({f"https://shop.example/{n}": f"Изделие номер {n}" for n in range(17)})

    assert kitset_names_tick(NOW, sessions, sites) == 15  # потолок тика
    assert queue.items() == ["708К.4", "999К.1"]  # есть что добирать — остаётся в голове
    assert kitset_names_tick(NOW, sessions, sites) == 2
    assert queue.items() == ["999К.1"]
    assert _names(sessions) == [f"Изделие номер {n}" for n in range(17)]
    assert kitset_names_tick(NOW, sessions, sites) == 0  # карточки нет — прочь из очереди
    assert queue.items() == []
    assert kitset_names_tick(NOW, sessions, sites) == 0
    assert len(sites.calls) == 17


def test_tick_without_a_single_name_does_not_hold_the_head_of_the_queue(sessions):
    """Поиск elitan отдаёт в заголовке сам артикул: 17 таких ссылок раньше навсегда держали проект в голове."""
    components = tuple(KitComponent(article=f"A-{n}", link=f"https://search.example/{n}") for n in range(17))
    _save(sessions, _kitset(*components))
    queue = KitsetNameQueue(sessions)
    queue.enqueue("708К.4", NOW)
    queue.enqueue("999К.1", NOW)

    assert kitset_names_tick(NOW, sessions, FakeSites({f"https://search.example/{n}": f"A-{n}" for n in range(17)})) == 0
    assert queue.items() == ["999К.1"]


def test_one_shot_fill_enqueues_every_kitset_with_empty_names_and_fills_it_whole(sessions, monkeypatch):
    from app.workflows import kitset_names as flow

    components = tuple(KitComponent(article=f"A-{n}", link=f"https://shop.example/{n}") for n in range(17))
    _save(sessions, _kitset(*components))
    assert flow.enqueue_missing(sessions, NOW) == ["708К.4"]
    sites = FakeSites({f"https://shop.example/{n}": f"Изделие номер {n}" for n in range(17)})
    result = flow.fill_project_names("708К.4", lambda: flow.SqlAlchemyTechcardsUnitOfWork(sessions), sites, cap=100_000)
    assert result.filled == 17 and _names(sessions) == [f"Изделие номер {n}" for n in range(17)]
    assert flow.enqueue_missing(sessions, NOW) == []


def test_tick_never_overwrites_a_name_typed_while_it_was_fetching(sessions):
    _save(sessions, _kitset(
        KitComponent(article="A-1", link="https://shop.example/a1"),
        KitComponent(article="B-2", link="https://shop.example/b2"),
    ))
    KitsetNameQueue(sessions).enqueue("708К.4", NOW)

    def person_types(url):
        if url.endswith("b2"):
            with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
                current = uow.kitsets.get("708К.4")
                edited, _ = current.with_edits([{"index": "КБ1", "art": "A-1", "per": 1, "scrap": 5, "name": "Моё"}],
                                               who="Технолог", now=NOW)
                uow.kitsets.save(edited)
                uow.commit()

    sites = FakeSites({"https://shop.example/a1": "С сайта A", "https://shop.example/b2": "С сайта B"}, person_types)
    assert kitset_names_tick(NOW, sessions, sites) == 2
    assert _names(sessions) == ["Моё", "С сайта B"]


def test_broken_project_does_not_hold_the_queue(sessions, caplog):
    _save(sessions, _kitset(KitComponent(article="A-1", link="https://shop.example/a1")))
    queue = KitsetNameQueue(sessions)
    queue.enqueue("708К.4", NOW)
    queue.enqueue("1К.1", NOW)

    class Broken:
        def title(self, url):
            raise RuntimeError("boom")

    assert kitset_names_tick(NOW, sessions, Broken()) == 0
    assert queue.items() == ["1К.1"]
    assert "tcKitNamesTick_:708К.4" in caplog.text


def test_broken_queue_value_is_cleared(sessions):
    from app.modules.operations.infrastructure import SqlAlchemyJobMarks

    with sessions() as session:
        SqlAlchemyJobMarks(session).put("tc_name_queue", "{not json", NOW)
        session.commit()
    assert kitset_names_tick(NOW, sessions, FakeSites({})) == 0
    assert KitsetNameQueue(sessions).items() == []
    with sessions() as session:
        assert SqlAlchemyJobMarks(session).get("tc_name_queue") is None


def test_load_from_calculation_enqueues_the_project(sessions):
    from app.modules.techcards.kitsets import CalcKitset
    from tests.factories import Actor
    from uuid import UUID

    queue = KitsetNameQueue(sessions)
    data = CalcKitset(project="708К.4", customer="ООО", calc_source="calc", assemblies=_kitset().assemblies)
    actor = Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство")
    result = flow.load_kitset(
        flow.LoadKitsetCommand(data=data, number="", deadline=None, ship={}, actor=actor, now=NOW),
        lambda: SqlAlchemyTechcardsUnitOfWork(sessions), None, None, queue.enqueue,
    )
    assert result.project == "708К.4"
    assert queue.items() == ["708К.4"]

    def broken(project):
        raise RuntimeError("queue down")

    # Очередь имён заведение не роняет.
    flow.load_kitset(
        flow.LoadKitsetCommand(data=data, number="", deadline=None, ship={}, actor=actor, now=NOW),
        lambda: SqlAlchemyTechcardsUnitOfWork(sessions), None, None, broken,
    )


# ── httpx-адаптер fetchPageInfo_ ──


def _public_dns(monkeypatch, address="93.184.216.34"):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a, **k: [(2, 1, 6, "", (address, 0))])


def _client(handler) -> httpx.Client:
    from app.modules.techcards.kitset_names import _refuse_private_host

    return httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True, headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [_refuse_private_host]},
    )


def test_http_fetcher_reads_the_title_in_the_page_charset(monkeypatch):
    _public_dns(monkeypatch)
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["user-agent"]))
        return httpx.Response(200, headers={"content-type": "text/html; charset=windows-1251"},
                              content="<title>Провод МГТФ 0,12 | Сайт</title>".encode("cp1251"))

    assert HttpPageFetcher(_client(handler)).title("https://shop.example/p?id=1&utm_source=x") == "Провод МГТФ 0,12"
    assert seen == [("https://shop.example/p?id=1", USER_AGENT)]


def test_http_fetcher_returns_empty_on_errors(monkeypatch):
    _public_dns(monkeypatch)
    assert HttpPageFetcher(_client(lambda r: httpx.Response(403, text="<title>Доступ</title>"))).title("https://a.example") == ""

    def down(request):
        raise httpx.ConnectTimeout("timeout", request=request)

    assert HttpPageFetcher(_client(down)).title("https://a.example") == ""


def test_http_fetcher_refuses_internal_hosts(monkeypatch):
    _public_dns(monkeypatch, "169.254.169.254")
    calls = []
    fetcher = HttpPageFetcher(_client(lambda r: calls.append(r) or httpx.Response(200, text="<title>secret</title>")))
    assert fetcher.title("http://metadata.example/") == ""
    assert calls == []


# ── фоновый шаг воркера (ownerTailTick) ──


def test_tail_jobs_run_on_a_thread_once_a_minute_and_swallow_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(outbox_worker, "kitset_names_tick", lambda now, sessions: calls.append(sessions) or 3)
    monkeypatch.setattr(outbox_worker, "_last_tail", float("-inf"))
    monkeypatch.setattr(outbox_worker, "_tail_thread", None)
    clock = iter([1000.0, 1000.0, 1010.0, 1061.0, 1061.0])
    monkeypatch.setattr(outbox_worker.time, "monotonic", lambda: next(clock))
    assert outbox_worker.start_tail_jobs("s") is True
    outbox_worker._tail_thread.join()
    assert outbox_worker.start_tail_jobs("s") is False  # не прошла минута
    assert outbox_worker.start_tail_jobs("s") is True
    outbox_worker._tail_thread.join()
    assert calls == ["s", "s"]

    def boom(now, sessions):
        raise RuntimeError("boom")

    monkeypatch.setattr(outbox_worker, "kitset_names_tick", boom)
    assert outbox_worker.run_tail_jobs("s") == 0
