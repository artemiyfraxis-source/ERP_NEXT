from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentRequest
from app.modules.techcards.kitsets import KitAssembly, KitComponent, Kitset
from app.workflows.fulfillment_links import links_for_requests


def request(purpose="728К.2 · КБ1", article="КД-1", name="Разъём"):
    return FulfillmentRequest.create(
        number=str(uuid4()), source_idempotency_key=str(uuid4()), source_fingerprint="test",
        requester="Алексей", purpose=purpose, deadline=date(2026, 11, 3), created_at=datetime(2026, 10, 1),
        lines=(FulfillmentLine.create(article=article, name=name, unit="шт", requirement=Decimal("4")),),
    )


def kitset(*components, project="728К.2", index="КБ1"):
    return Kitset(project, "", "", (KitAssembly(index, "Жгут", Decimal("1"), components),),
                  datetime(2026, 11, 2), "Технолог", datetime(2026, 10, 1))


def factory_for(*cards):
    repository = SimpleNamespace(for_projects=Mock(return_value={str(i): card for i, card in enumerate(cards)}))
    @contextmanager
    def factory():
        yield SimpleNamespace(kitsets=repository)
    return factory, repository


def test_historic_request_and_split_lines_get_all_unique_current_links_in_one_batch():
    old = request()
    split = replace(old.lines[0], id=uuid4(), requirement=Decimal("2"))
    old = replace(old, lines=(*old.lines, split))
    other = request("900К.1 · КБ2")
    factory, repository = factory_for(
        kitset(KitComponent(" КД-1 ", name=" Разъём ", link="https://supplier.test/one"),
               KitComponent("КД-1", name="Разъём", link="https://supplier.test/two"),
               KitComponent("КД-1", name="Разъём", link="https://supplier.test/one")),
        kitset(KitComponent("КД-1", name="Разъём", link="https://other.test/"), project="900К.1", index="кб-2"),
    )
    result = links_for_requests([old, other], factory)
    for line in old.lines:
        assert result[line.id] == ["https://supplier.test/one", "https://supplier.test/two"]
    assert result[other.lines[0].id] == ["https://other.test/"]
    repository.for_projects.assert_called_once()
    assert set(repository.for_projects.call_args.args[0]) == {"728К.2", "900К.1"}
    assert not hasattr(old.lines[0], "links")


@pytest.mark.parametrize("changes", [
    {"project": "900К.1"}, {"index": "КБ2"}, {"article": "КД-2"}, {"name": "Разъем"},
])
def test_does_not_borrow_links_from_different_project_kb_article_or_name(changes):
    old = request()
    values = {"article": "КД-1", "name": "Разъём", "project": "728К.2", "index": "КБ1", **changes}
    factory, _ = factory_for(kitset(KitComponent(values["article"], name=values["name"], link="https://s.test/"),
                                   project=values["project"], index=values["index"]))
    assert links_for_requests([old], factory) == {}


def test_empty_component_name_uses_the_same_article_fallback_as_issue_tasks():
    old = request(name="КД-1")
    factory, _ = factory_for(kitset(KitComponent(" КД-1 ", name=" ", link="https://s.test/")))
    assert links_for_requests([old], factory) == {old.lines[0].id: ["https://s.test/"]}


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/plain,x", "//s.test/a", "/a", "https:///a",
    "https://user:secret@s.test/a", "https://s.test/\nx", "https://s.test/\x7fx", "https://s.test\\@evil.test/x",
    "https://[bad/a", "https://s.test:bad/a", "https://s.test/a b", "https://s.test/\x80x", ""])
def test_unsafe_urls_are_never_projected(url):
    old = request()
    factory, _ = factory_for(kitset(KitComponent("КД-1", name="Разъём", link=url)))
    assert links_for_requests([old], factory) == {}


def test_missing_source_manual_request_or_unconfigured_techcards_have_no_links():
    old = request()
    factory, repository = factory_for()
    assert links_for_requests([old], factory) == {}
    assert links_for_requests([old], None) == {}
    repository.for_projects.reset_mock()
    assert links_for_requests([request("Ручная")], factory) == {}
    assert links_for_requests([], factory) == {}
    repository.for_projects.assert_not_called()
