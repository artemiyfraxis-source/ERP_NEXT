"""Moving the existing ETKs onto ПФ (owner 13.10: «старые сам переделай»): `python -m app.workflows.pf_split_existing`
— a dry run prints the split, --apply writes it as a revision «Правка ПФ (перенос на ПФ)» through the «Сохранить ПФ»
path, skips «Сложная сборка» and a running ПФ task it would change, and writes nothing the second time."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import parse_operation_library
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor
from app.shared.persistence import create_schema
from app.workflows import pf_split_existing as cli


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((ROOT / "tests" / "techcards" / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
NOW = datetime(2026, 10, 13, 18, 0)
ACTOR = Actor(id=uuid4(), name="перенос ЭТК на ПФ", department="")


def document(name: str) -> dict:
    config = deepcopy(FIXTURES["files"][name]["payload"]["config"])
    return build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])


@pytest.fixture()
def shop():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)  # noqa: E731
    app.state.techcards_uow_factory = factory
    app.dependency_overrides[moscow_now] = lambda: NOW
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=UUID("30000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство"),
        email="tech@example.test", roles=frozenset({"engineer"}), permissions={"techcards": "edit"},
    )
    yield SimpleNamespace(api=TestClient(app), factory=factory)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    engine.dispose()


def issued(api: TestClient, item: str, etk: dict) -> dict:
    card = api.post("/api/techcards/projects", json={
        "project": "720К.", "item_index": item, "product_name": "Жгут", "source_calculation": "",
    }).json()
    built = api.put(f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": etk})
    assert built.status_code == 200, built.text
    result = api.post(f"/api/techcards/projects/{card['id']}/issue", json={"version": built.json()["version"]})
    assert result.status_code == 200, result.text
    return result.json()


def run(shop, apply: bool, tasks=(), **options) -> tuple[str, int]:
    return cli.run(shop.factory, lambda: list(tasks), ACTOR, NOW, apply=apply, **options)


def card(shop, card_id: str) -> dict:
    return shop.api.get(f"/api/techcards/projects/{card_id}").json()


# Cut, crimp A, side A, crimp B, side B (owner 14.10) — a ПФ per operation, as the derived scheme, but named ПФ.
SPLIT = ("ПФ-1 · Нарезанные провода (01) · ПФ-2 · Обжатые провода, сторона А (02) · ПФ-3 · Сторона А собрана (03) · "
         "ПФ-4 · Обжатые провода, сторона В (04) · ПФ-5 · Кабельная сборка 39012140 to 39012141 16AWG 250mm (05)")


def test_dry_run_prints_the_split_and_writes_nothing(shop):
    typical = issued(shop.api, "КБ1", document("kit-720К.-КБ1.json"))
    text, code = run(shop, apply=False)

    assert code == 0
    assert f"720К. · КБ1 · Выдана ред. 1 · 5 оп. → {SPLIT}" in text
    assert "Перенести: 1 · уже по ПФ: 0 · пропущено: 0" in text
    assert "Проверка без записи" in text
    after = card(shop, typical["id"])
    assert (after["version"], after["revision"], after["document"]["route"]["source"]) == (typical["version"], 1, "derived")


def test_apply_writes_a_revision_once_and_a_second_apply_writes_nothing(shop):
    typical = issued(shop.api, "КБ1", document("kit-720К.-КБ1.json"))
    text, code = run(shop, apply=True)

    assert code == 0 and f"перенесено {SPLIT}" in text and "Перенесено: 1" in text
    after = card(shop, typical["id"])
    assert after["revision"] == 2 and after["document"]["route"]["source"] == "generated"
    assert [block["ops"] for block in after["document"]["route"]["blocks"]] == [[1], [2], [3], [4], [5]]
    revisions = shop.api.get(f"/api/techcards/projects/{typical['id']}/revisions").json()["items"]
    moved = next(item for item in revisions if item["revision"] == 2)
    assert moved["reason"] == "Правка ПФ (перенос на ПФ)" and moved["actor_name"] == "перенос ЭТК на ПФ"

    again, code = run(shop, apply=True)
    assert code == 0 and "Перенесено: 0 · уже по ПФ: 1 · пропущено: 0" in again
    assert "720К. · КБ1 · Выдана ред. 2 · 5 оп. — уже по ПФ" in again
    assert card(shop, typical["id"])["version"] == after["version"]


def test_complex_assembly_is_skipped_unless_asked(shop):
    complex_card = issued(shop.api, "КБ2", document("kit-708К.2-КБ1.json"))
    text, _ = run(shop, apply=True)

    assert "720К. · КБ2 · Выдана ред. 1 · 15 оп. — Сложная сборка — ПФ настраивает владелец" in text
    assert card(shop, complex_card["id"])["revision"] == 1

    text, _ = run(shop, apply=True, include_complex=True, card_id=UUID(complex_card["id"]))
    assert "Перенесено: 1" in text
    assert card(shop, complex_card["id"])["document"]["route"]["source"] == "generated"


def test_a_running_pf_task_that_would_change_skips_the_card_and_a_task_by_operations_does_not(shop):
    typical = issued(shop.api, "КБ1", document("kit-720К.-КБ1.json"))

    def task(number: str, *compositions):
        operations = [SimpleNamespace(etk_ops=tuple(SimpleNamespace(position=p) for p in ops)) for ops in compositions]
        return SimpleNamespace(number=number, techcard_id=UUID(typical["id"]), techcard_revision=1,
                               project="720К.", item="КБ1", operations=operations)

    by_pf = task("ПЗ-7", (1,), (2, 3), (4, 5))
    text, _ = run(shop, apply=True, tasks=[by_pf])
    assert "идёт задание ПЗ-7 по ПФ этой ревизии — состав ПФ стал бы другим" in text
    assert card(shop, typical["id"])["revision"] == 1

    by_operations = task("ПЗ-8", (), (), (), (), ())
    text, _ = run(shop, apply=True, tasks=[by_operations])
    assert "идут задания ПЗ-8 по операциям ЭТК — остаются по своему снимку" in text
    assert card(shop, typical["id"])["revision"] == 2


def test_card_filter_and_unknown_card(shop):
    first = issued(shop.api, "КБ1", document("kit-720К.-КБ1.json"))
    issued(shop.api, "КБ3", document("kit-729К.2-КБ1.json"))

    text, code = run(shop, apply=False, card_id=UUID(first["id"]))
    assert code == 0 and "КБ1" in text and "КБ3" not in text
    text, code = run(shop, apply=False, card_id=UUID("00000000-0000-0000-0000-000000000009"))
    assert code == 2 and "нет" in text


def test_main_needs_the_database_url_and_a_mode(monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert cli.main(["--dry-run"]) == 2
    with pytest.raises(SystemExit):
        cli.main([])
